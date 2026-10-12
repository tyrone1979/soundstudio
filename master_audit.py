# -*- coding: utf-8 -*-
"""大师级质量审计：符号门 + 语料对照 + 听审填分位。

试点默认包：mazurka / fugue / swing-standard。

用法：
    python master_audit.py
    python master_audit.py --packs mazurka fugue swing-standard --per-pack 3 --render
    python master_audit.py --apply-scores scores/master_audit/listening_scores.yaml

符号门（自动）：
    - style_rank 目标包 ∈ top3
    - 语料单曲 NN sim < 0.98
    - 语料片段 NN sim < 0.99

听审门（人工填 listening_scores.yaml 后 --apply-scores）：
    - 通用维平均 ≥ 4，且硬指标无 ≤ 2
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

import yaml

import composition_skills as skill_lib
import evaluate
import style_skeleton
from compose_kernel import run_program
from batch_generate import enumerate_packs

try:
    from llm_compose import spec_to_music21
except Exception:  # pragma: no cover
    spec_to_music21 = None

DEFAULT_PACKS = ['mazurka', 'fugue', 'swing-standard']
RUBRIC_PATH = os.path.join(_ROOT, 'runs', 'listening_rubric.yaml')
DEFAULT_OUT = os.path.join(_ROOT, 'scores', 'master_audit')


def _load_rubric(path: str = RUBRIC_PATH) -> Dict[str, Any]:
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _resolve_pack_meta(query: str) -> Optional[Dict[str, Any]]:
    q = query.lower().strip()
    for pk in enumerate_packs():
        gid = str(pk.get('gid') or '').lower()
        if (pk['pack_id'].lower() == q or gid == q
                or pk['pack_id'].lower().endswith('/' + q)
                or pk['name'].lower() == q):
            return pk
    return None


def _symbolic_gate(spec: Dict[str, Any], pack_id: str, *,
                   frag_max_pieces: int = 16
                   ) -> Dict[str, Any]:
    """跑符号三项门禁，返回 checks + pass。"""
    want = {
        'bars': spec.get('bars'),
        'voices': spec.get('voices') or len(spec.get('parts') or []),
        'tempo': spec.get('tempo'),
        'meter': spec.get('time_signature'),
    }
    rk = evaluate.style_rank(spec, pack_id)
    aa = evaluate.attribute_adherence(spec, want)
    piece = evaluate.nearest_corpus_piece(spec, pack_id)
    frag = evaluate.nearest_corpus_fragment(
        spec, pack_id, max_corpus_pieces=frag_max_pieces, hop=4)
    checks = {
        'style_top3': {
            'ok': bool(rk.get('top3')),
            'detail': f"rank={rk.get('rank')}/{rk.get('total')}",
            'rank': rk,
        },
        'corpus_piece': {
            'ok': not bool(piece.get('is_near_duplicate')),
            'detail': (
                f"{piece.get('name')} sim={piece.get('similarity')}"
                if piece.get('name') else 'no-pool'),
            'result': piece,
        },
        'corpus_fragment': {
            'ok': not bool(frag.get('is_near_duplicate')),
            'detail': (
                f"{frag.get('corpus_id')} sim={frag.get('similarity')}"
                if frag.get('corpus_id') else 'no-pool'),
            'result': frag,
        },
        'attribute_adherence': {
            'ok': float(aa.get('score') or 0) >= 0.999,
            'detail': f"score={aa.get('score')}",
            'result': aa,
        },
    }
    # 属性遵循作软信息；硬符号门按计划三项
    hard_ok = all(checks[k]['ok'] for k in (
        'style_top3', 'corpus_piece', 'corpus_fragment'))
    return {
        'ok': hard_ok,
        'checks': checks,
        'style_overlap': evaluate.style_overlap(spec, pack_id),
        'pack_nn': evaluate.nearest_neighbor(spec, exclude=pack_id),
    }


def _listening_pass(scores: Dict[str, Any], pack_id: str,
                    rubric: Dict[str, Any]) -> Dict[str, Any]:
    """根据人工分数字典判定听审是否通过。"""
    common_ids = [d['id'] for d in (rubric.get('common_dimensions') or [])]
    hard_ids = [d['id'] for d in (rubric.get('pack_hard') or {}).get(pack_id, [])]
    pass_avg = float(rubric.get('pass_avg') or 4.0)
    hard_fail_max = int(rubric.get('hard_fail_max') or 2)
    common_vals = []
    missing = []
    for cid in common_ids:
        if cid not in scores or scores[cid] is None:
            missing.append(cid)
        else:
            common_vals.append(float(scores[cid]))
    hard_vals = []
    for hid in hard_ids:
        if hid not in scores or scores[hid] is None:
            missing.append(hid)
        else:
            hard_vals.append(float(scores[hid]))
    if missing:
        return {
            'ok': False, 'pending': True, 'missing': missing,
            'avg': None, 'hard_min': None,
        }
    avg = sum(common_vals) / len(common_vals) if common_vals else 0.0
    hard_min = min(hard_vals) if hard_vals else 5.0
    ok = avg >= pass_avg and hard_min > hard_fail_max
    return {
        'ok': ok, 'pending': False, 'missing': [],
        'avg': round(avg, 3), 'hard_min': hard_min,
    }


def _score_template_entry(pack_id: str, rubric: Dict[str, Any]
                          ) -> Dict[str, Any]:
    entry: Dict[str, Any] = {}
    for d in rubric.get('common_dimensions') or []:
        entry[d['id']] = None
    for d in (rubric.get('pack_hard') or {}).get(pack_id, []) or []:
        entry[d['id']] = None
    return entry


def generate_audit_piece(pk: Dict[str, Any], index: int, *,
                         bars: int, voices: int, seed_base: str,
                         out_dir: str, render: bool,
                         frag_max_pieces: int
                         ) -> Dict[str, Any]:
    pid = pk['pack_id']
    gid = pk['gid']
    dims = pk.get('dimensions') or {}
    fugue_mode = 'full' if str(dims.get('form') or '') == 'fugue' else 'off'
    seed = f'{seed_base}-{pid}-{index}'
    item: Dict[str, Any] = {
        'pack_id': pid, 'family': pk.get('family'), 'name': pk.get('name'),
        'index': index, 'seed': seed, 'ok_symbolic': False, 'issues': [],
    }
    try:
        calls = style_skeleton.build_skeleton_calls(
            gid, bars=bars, voices=voices, seed=seed, fugue=fugue_mode)
        spec = run_program(calls)
    except Exception as e:
        item['issues'].append(f'generate: {type(e).__name__}: {e}')
        return item

    if spec_to_music21 is None:
        item['issues'].append('spec_to_music21 unavailable')
        return item
    score = spec_to_music21(spec)
    base = f'{pid.replace("/", "_")}__{index}'
    base = ''.join(c if c.isalnum() or c in '-_' else '_' for c in base)
    os.makedirs(out_dir, exist_ok=True)
    xmlp = os.path.join(out_dir, base + '.musicxml')
    midp = os.path.join(out_dir, base + '.mid')
    score.write('musicxml', fp=xmlp)
    score.write('midi', fp=midp)
    item.update({
        'xml': xmlp, 'mid': midp, 'bars': spec.get('bars'),
        'tempo': spec.get('tempo'), 'meter': spec.get('time_signature'),
        'title': spec.get('title'), 'key_id': base,
    })
    if render:
        try:
            from render_enhance import render_to_wav, preset
            wavp = os.path.join(out_dir, base + '.wav')
            info = render_to_wav(midp, wavp, preset())
            if info.get('ok'):
                item['wav'] = wavp
            else:
                item['issues'].append(f'render: {info.get("error")}')
        except Exception as e:
            item['issues'].append(f'render: {e}')

    gate = _symbolic_gate(spec, pid, frag_max_pieces=frag_max_pieces)
    item['symbolic'] = {
        'ok': gate['ok'],
        'style_overlap': gate['style_overlap'],
        'checks': {k: {'ok': v['ok'], 'detail': v['detail']}
                   for k, v in gate['checks'].items()},
        'corpus_piece': gate['checks']['corpus_piece']['result'],
        'corpus_fragment': gate['checks']['corpus_fragment']['result'],
        'style_rank': gate['checks']['style_top3']['rank'],
        'pack_nn': gate['pack_nn'],
    }
    item['ok_symbolic'] = gate['ok']
    item['eval_md'] = evaluate.evaluate_report(
        spec, want={'bars': bars, 'voices': voices}, pack_id=pid)
    return item


def run_audit(packs: Optional[List[str]] = None, *, per_pack: int = 3,
              bars: int = 16, voices: int = 3, render: bool = False,
              out_dir: Optional[str] = None, seed_base: str = 'master',
              frag_max_pieces: int = 16
              ) -> Dict[str, Any]:
    rubric = _load_rubric()
    queries = packs or list(DEFAULT_PACKS)
    targets: List[Dict[str, Any]] = []
    for q in queries:
        meta = _resolve_pack_meta(q)
        if meta is None:
            raise SystemExit(f'未知风格包: {q}')
        targets.append(meta)

    out_dir = out_dir or DEFAULT_OUT
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    pieces: List[Dict[str, Any]] = []
    for pk in targets:
        for i in range(max(1, int(per_pack))):
            print(f'[audit] {pk["pack_id"]} #{i} …', flush=True)
            pieces.append(generate_audit_piece(
                pk, i, bars=bars, voices=voices, seed_base=seed_base,
                out_dir=out_dir, render=render,
                frag_max_pieces=frag_max_pieces))

    # 听审空白模板
    scores_path = os.path.join(out_dir, 'listening_scores.yaml')
    score_doc = {
        'rubric': os.path.relpath(RUBRIC_PATH, _ROOT),
        'note': '填 1–5 整数；保留 null 表示未听。填完后：'
                'python master_audit.py --apply-scores ' + scores_path,
        'scores': {},
    }
    for it in pieces:
        kid = it.get('key_id') or f"{it['pack_id']}__{it['index']}"
        score_doc['scores'][kid] = _score_template_entry(
            it['pack_id'], rubric)

    # 不覆盖已有人工分
    if os.path.isfile(scores_path):
        try:
            old = yaml.safe_load(open(scores_path, encoding='utf-8')) or {}
            old_scores = old.get('scores') or {}
            for kid, entry in score_doc['scores'].items():
                if kid in old_scores and isinstance(old_scores[kid], dict):
                    merged = dict(entry)
                    for k, v in old_scores[kid].items():
                        if v is not None:
                            merged[k] = v
                    score_doc['scores'][kid] = merged
        except Exception:
            pass
    with open(scores_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(score_doc, f, allow_unicode=True, sort_keys=False)

    report = build_audit_report(pieces, rubric, scores_path=scores_path,
                                stamp=stamp, render=render)
    md_path = os.path.join(_ROOT, 'runs', f'master_audit_{stamp}.md')
    os.makedirs(os.path.join(_ROOT, 'runs'), exist_ok=True)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(report)
    # 稳定别名
    latest = os.path.join(_ROOT, 'runs', 'master_audit_latest.md')
    with open(latest, 'w', encoding='utf-8') as f:
        f.write(report)

    csv_path = os.path.join(out_dir, 'symbolic_gate.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([
            'key_id', 'pack_id', 'seed', 'ok_symbolic', 'style_top3',
            'corpus_piece_ok', 'corpus_piece_sim', 'corpus_frag_ok',
            'corpus_frag_sim', 'overlap', 'xml', 'wav',
        ])
        for it in pieces:
            sy = it.get('symbolic') or {}
            ch = sy.get('checks') or {}
            cp = sy.get('corpus_piece') or {}
            cf = sy.get('corpus_fragment') or {}
            w.writerow([
                it.get('key_id'), it.get('pack_id'), it.get('seed'),
                it.get('ok_symbolic'),
                (ch.get('style_top3') or {}).get('ok'),
                (ch.get('corpus_piece') or {}).get('ok'),
                cp.get('similarity'),
                (ch.get('corpus_fragment') or {}).get('ok'),
                cf.get('similarity'),
                sy.get('style_overlap'),
                it.get('xml'), it.get('wav'),
            ])

    manifest = {
        'stamp': stamp, 'report': md_path, 'scores': scores_path,
        'csv': csv_path, 'out_dir': out_dir, 'pieces': [
            {k: it.get(k) for k in (
                'key_id', 'pack_id', 'seed', 'ok_symbolic', 'xml', 'mid',
                'wav', 'issues')}
            for it in pieces
        ],
    }
    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f'[audit] report → {md_path}')
    print(f'[audit] scores template → {scores_path}')
    return manifest


def build_audit_report(pieces: List[Dict[str, Any]], rubric: Dict[str, Any],
                       *, scores_path: str, stamp: str,
                       render: bool,
                       listening: Optional[Dict[str, Dict[str, Any]]] = None
                       ) -> str:
    listening = listening or {}
    lines: List[str] = []
    lines.append('# 大师级质量审计报告')
    lines.append('')
    lines.append(f'- 时间：{stamp}')
    lines.append(f'- 渲染 WAV：{"开" if render else "关"}')
    lines.append(f'- 听审量表：`docs/听审量表.md` / `runs/listening_rubric.yaml`')
    lines.append(f'- 填分文件：`{os.path.relpath(scores_path, _ROOT)}`')
    lines.append('')
    lines.append('## 门禁说明')
    lines.append('')
    lines.append('| 检查 | 通过条件 |')
    lines.append('|---|---|')
    lines.append('| 风格命中 | `style_rank` 目标包 ∈ top3 |')
    lines.append(f'| 非整曲抄袭 | 语料单曲 NN sim < {evaluate.CORPUS_PIECE_NEAR_DUP} |')
    lines.append(f'| 非片段抄袭 | 语料片段 NN sim < {evaluate.CORPUS_FRAG_NEAR_DUP} |')
    lines.append(f'| 听审 | 通用维平均 ≥ {rubric.get("pass_avg", 4)} '
                 f'且硬指标无 ≤ {rubric.get("hard_fail_max", 2)} |')
    lines.append('')

    by_pack: Dict[str, List[Dict[str, Any]]] = {}
    for it in pieces:
        by_pack.setdefault(it['pack_id'], []).append(it)

    n_sym_ok = sum(1 for it in pieces if it.get('ok_symbolic'))
    lines.append(f'## 汇总：符号门 {n_sym_ok}/{len(pieces)} 通过')
    lines.append('')

    for pid, items in by_pack.items():
        lines.append(f'### `{pid}`')
        lines.append('')
        ok_n = sum(1 for it in items if it.get('ok_symbolic'))
        lines.append(f'- 符号门：**{ok_n}/{len(items)}**')
        lines.append('')
        lines.append('| key | seed | 符号门 | top3 | 单曲NN | 片段NN | overlap | WAV | 听审 |')
        lines.append('|---|---|---|---|---|---|---|---|---|')
        for it in items:
            sy = it.get('symbolic') or {}
            ch = sy.get('checks') or {}
            kid = it.get('key_id') or ''
            listen = listening.get(kid)
            if listen is None:
                listen_s = '待填'
            elif listen.get('pending'):
                listen_s = '待填'
            else:
                listen_s = ('✅' if listen.get('ok') else '❌') + (
                    f" avg={listen.get('avg')}")
            wav = '✓' if it.get('wav') else '—'
            lines.append(
                f"| `{kid}` | `{it.get('seed')}` | "
                f"{'✅' if it.get('ok_symbolic') else '❌'} | "
                f"{'✅' if (ch.get('style_top3') or {}).get('ok') else '❌'} | "
                f"{'✅' if (ch.get('corpus_piece') or {}).get('ok') else '❌'} "
                f"{(sy.get('corpus_piece') or {}).get('similarity')} | "
                f"{'✅' if (ch.get('corpus_fragment') or {}).get('ok') else '❌'} "
                f"{(sy.get('corpus_fragment') or {}).get('similarity')} | "
                f"{sy.get('style_overlap')} | {wav} | {listen_s} |")
        lines.append('')
        for it in items:
            kid = it.get('key_id')
            lines.append(f'#### `{kid}`')
            lines.append('')
            if it.get('xml'):
                lines.append(f"- MusicXML: `{os.path.relpath(it['xml'], _ROOT)}`")
            if it.get('mid'):
                lines.append(f"- MIDI: `{os.path.relpath(it['mid'], _ROOT)}`")
            if it.get('wav'):
                lines.append(f"- WAV: `{os.path.relpath(it['wav'], _ROOT)}`")
            if it.get('issues'):
                lines.append('- 问题：' + '；'.join(it['issues']))
            sy = it.get('symbolic') or {}
            cp = sy.get('corpus_piece') or {}
            cf = sy.get('corpus_fragment') or {}
            if cp.get('name'):
                lines.append(
                    f"- 语料单曲：`{cp['name']}` sim={cp.get('similarity')}")
            if cf.get('corpus_id'):
                lines.append(
                    f"- 语料片段：`{cf['corpus_id']}` "
                    f"gen_bar={cf.get('gen_start_bar')} "
                    f"sim={cf.get('similarity')}")
            lines.append('')
            # 听审填分提示
            hard = (rubric.get('pack_hard') or {}).get(pid) or []
            common = rubric.get('common_dimensions') or []
            lines.append('听审填分（写入 listening_scores.yaml）：')
            lines.append('')
            for d in common:
                lines.append(f"  - `{d['id']}`（{d['label']}）：_ / 5")
            for d in hard:
                lines.append(f"  - `{d['id']}`（{d['label']}）：_ / 5")
            lines.append('')

    lines.append('---')
    lines.append('听审完成后执行：')
    lines.append(f'`python master_audit.py --apply-scores {os.path.relpath(scores_path, _ROOT)}`')
    return '\n'.join(lines)


def apply_scores(scores_path: str,
                 manifest_path: Optional[str] = None) -> str:
    """读入人工分，重写 latest 报告中的听审列。"""
    rubric = _load_rubric()
    doc = yaml.safe_load(open(scores_path, encoding='utf-8')) or {}
    raw_scores = doc.get('scores') or {}
    manifest_path = manifest_path or os.path.join(
        os.path.dirname(scores_path), 'manifest.json')
    manifest = json.load(open(manifest_path, encoding='utf-8'))
    # 重建 pieces 最小信息 + 从 csv/manifest 读符号结果
    csv_path = manifest.get('csv')
    pieces: List[Dict[str, Any]] = []
    if csv_path and os.path.isfile(csv_path):
        with open(csv_path, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                pid = row['pack_id']
                pieces.append({
                    'key_id': row['key_id'],
                    'pack_id': pid,
                    'seed': row['seed'],
                    'ok_symbolic': row['ok_symbolic'] in ('True', 'true', '1'),
                    'xml': row.get('xml'),
                    'wav': row.get('wav'),
                    'symbolic': {
                        'ok': row['ok_symbolic'] in ('True', 'true', '1'),
                        'style_overlap': float(row['overlap']) if row.get('overlap') else None,
                        'checks': {
                            'style_top3': {'ok': row['style_top3'] in ('True', 'true', '1')},
                            'corpus_piece': {'ok': row['corpus_piece_ok'] in ('True', 'true', '1')},
                            'corpus_fragment': {'ok': row['corpus_frag_ok'] in ('True', 'true', '1')},
                        },
                        'corpus_piece': {
                            'similarity': float(row['corpus_piece_sim'])
                            if row.get('corpus_piece_sim') else None,
                        },
                        'corpus_fragment': {
                            'similarity': float(row['corpus_frag_sim'])
                            if row.get('corpus_frag_sim') else None,
                            'corpus_id': None,
                        },
                    },
                    'issues': [],
                })
    else:
        for p in manifest.get('pieces') or []:
            pieces.append({**p, 'symbolic': {'ok': p.get('ok_symbolic'),
                                             'checks': {}, 'corpus_piece': {},
                                             'corpus_fragment': {}}})

    listening: Dict[str, Dict[str, Any]] = {}
    for it in pieces:
        kid = it['key_id']
        listening[kid] = _listening_pass(
            raw_scores.get(kid) or {}, it['pack_id'], rubric)

    stamp = manifest.get('stamp') or datetime.now().strftime('%Y%m%d_%H%M%S')
    report = build_audit_report(
        pieces, rubric, scores_path=scores_path, stamp=stamp + '+listen',
        render=any(it.get('wav') for it in pieces), listening=listening)
    out_md = os.path.join(_ROOT, 'runs', f'master_audit_{stamp}_scored.md')
    with open(out_md, 'w', encoding='utf-8') as f:
        f.write(report)
    with open(os.path.join(_ROOT, 'runs', 'master_audit_latest.md'),
              'w', encoding='utf-8') as f:
        f.write(report)
    print(f'[audit] scored report → {out_md}')
    return out_md


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='大师级质量审计')
    ap.add_argument('--packs', nargs='*', default=None,
                    help='包 id 或短名（默认 mazurka fugue swing-standard）')
    ap.add_argument('--per-pack', type=int, default=3)
    ap.add_argument('--bars', type=int, default=16)
    ap.add_argument('--voices', type=int, default=3)
    ap.add_argument('--render', action='store_true')
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--frag-pieces', type=int, default=16,
                    help='片段对照语料抽样曲数')
    ap.add_argument('--apply-scores', default=None,
                    help='听审填分 YAML 路径；只重算报告不重新生成')
    args = ap.parse_args(argv)
    if args.apply_scores:
        apply_scores(args.apply_scores)
        return 0
    run_audit(args.packs, per_pack=args.per_pack, bars=args.bars,
              voices=args.voices, render=args.render, out_dir=args.out_dir,
              frag_max_pieces=args.frag_pieces)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
