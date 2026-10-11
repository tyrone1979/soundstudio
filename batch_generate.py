"""风格包批量生成流水线 —— 确定性、可复现、可审计（#7 竞品差距）。

复用既有「确定性内核」资产，不调 LLM：
- 枚举风格包：composition_skills.list_genres()（与 evaluate.build_all_fingerprints 同源）
- 每包用风格插值内核 build_skeleton_calls → compose_kernel.run_program → spec
- 风格保真度：evaluate.style_overlap(spec, pack_id)（0–1，越大越像该风格）
- 产出：每包 N 首确定性骨架乐谱（MusicXML + MIDI），可选渲染 WAV
- 报告：runs/batch_report.md（汇总 + 每包明细）+ runs/batch_report.csv（审计）

确定性保证：每首 seed = "{pack_id}-batch-{i}"，风格插值内核无随机性来源；
混响/渲染（若开启）同样 seed 固定。整条流水线可重跑复现同一批产物。

用法：
    python batch_generate.py                 # 全包，每包 1 首
    python batch_generate.py --packs fugue chorale --per-pack 3
    python batch_generate.py --render        # 额外渲染 WAV（更慢）
或在对话里：/batch          /batch fugue chorale       /batch cinematic
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

import composition_skills as skill_lib          # == skill_lib
import style_skeleton
from compose_kernel import run_program
import evaluate

try:
    from llm_compose import spec_to_music21
except Exception:  # pragma: no cover
    spec_to_music21 = None


# ---------------------------------------------------------------------------
# 枚举 / 参数
# ---------------------------------------------------------------------------
@dataclass
class BatchConfig:
    packs: Optional[List[str]] = None   # None = 全部可用包
    per_pack: int = 1
    bars: int = 16
    voices: int = 3
    seed_base: str = 'batch'
    out_dir: str = os.path.join(_ROOT, 'scores', 'batch')
    render: bool = False
    force_fp: bool = False              # 强制重建指纹
    sr: int = 44100


def enumerate_packs() -> List[Dict[str, Any]]:
    """返回全部启用风格包（与 evaluate.build_all_fingerprints 同源）。"""
    out: List[Dict[str, Any]] = []
    for g in skill_lib.list_genres():
        pid = str(g.get('pack_id') or '')
        if not pid or not g.get('enabled', True):
            continue
        out.append({
            'pack_id': pid,
            'gid': g.get('id'),
            'family': g.get('family') or (pid.split('/')[0] if '/' in pid else ''),
            'name': g.get('name') or pid,
            'path': g.get('path'),
            'dimensions': g.get('dimensions') or {},
        })
    return out


def _resolve_targets(cfg: BatchConfig) -> List[Dict[str, Any]]:
    all_pk = enumerate_packs()
    if not cfg.packs:
        return all_pk
    q = [p.lower() for p in cfg.packs]
    out: List[Dict[str, Any]] = []
    for pk in all_pk:
        # gid 可能为 None（genre 缺 id 键）：统一转字符串避免 AttributeError
        gid = str(pk.get('gid') or '').lower()
        if (pk['pack_id'].lower() in q or gid in q
                or pk['family'].lower() in q or pk['name'].lower() in q):
            out.append(pk)
    return out


# ---------------------------------------------------------------------------
# 单包生成
# ---------------------------------------------------------------------------
def generate_pack(pk: Dict[str, Any], cfg: BatchConfig) -> Dict[str, Any]:
    pid = pk['pack_id']
    gid = pk['gid']
    rec: Dict[str, Any] = {
        'pack_id': pid, 'family': pk['family'], 'name': pk['name'],
        'generated': 0, 'overlaps': [], 'outputs': [], 'issues': [],
        'fp_sources': None, 'fugue': None,
    }
    # 指纹（目标）
    try:
        fp = evaluate.pack_fingerprint(pid, seeds=3, force=cfg.force_fp,
                                       bars=cfg.bars, voices=cfg.voices)
        rec['fp_sources'] = fp.get('sources')
    except Exception as e:
        rec['issues'].append(f'指纹生成失败: {type(e).__name__}: {e}')
        fp = None

    dims = pk['dimensions']
    fugue_mode = 'full' if str(dims.get('form') or '') == 'fugue' else 'auto'
    rec['fugue'] = fugue_mode

    os.makedirs(cfg.out_dir, exist_ok=True)
    for i in range(max(1, int(cfg.per_pack))):
        seed = f'{cfg.seed_base}-{pid}-{i}'
        try:
            kw = {'bars': cfg.bars, 'voices': cfg.voices, 'seed': seed,
                  'fugue': fugue_mode}
            calls = style_skeleton.build_skeleton_calls(gid, **kw)
            spec = run_program(calls)
            overlap = evaluate.style_overlap(spec, pid)
            if overlap is not None:
                rec['overlaps'].append(round(float(overlap), 4))
            # 写出 MusicXML + MIDI
            if spec_to_music21 is None:
                raise RuntimeError('spec_to_music21 不可用')
            score = spec_to_music21(spec)
            base = f'{pk["family"]}__{pid.replace("/","_")}__{i}'
            base = ''.join(c if c.isalnum() or c in '-_' else '_' for c in base)
            xmlp = os.path.join(cfg.out_dir, base + '.musicxml')
            midp = os.path.join(cfg.out_dir, base + '.mid')
            score.write('musicxml', fp=xmlp)
            score.write('midi', fp=midp)
            item: Dict[str, Any] = {
                'seed': seed, 'xml': xmlp, 'mid': midp,
                'overlap': overlap, 'bars': spec.get('bars'),
                'voices': spec.get('voices') or len(spec.get('parts') or []),
                'title': spec.get('title'),
            }
            # 可选渲染 WAV
            if cfg.render:
                try:
                    from render_enhance import render_to_wav, preset
                    wavp = os.path.join(cfg.out_dir, base + '.wav')
                    info = render_to_wav(midp, wavp, preset())
                    if info['ok']:
                        item['wav'] = wavp
                    else:
                        rec['issues'].append(f'{seed} 渲染失败: {info.get("error")}')
                except Exception as e:
                    rec['issues'].append(f'{seed} 渲染异常: {e}')
            rec['outputs'].append(item)
            rec['generated'] += 1
        except Exception as e:
            rec['issues'].append(f'seed={seed}: {type(e).__name__}: {e}')
    rec['avg_overlap'] = round(sum(rec['overlaps']) / len(rec['overlaps']), 4) \
        if rec['overlaps'] else None
    rec['min_overlap'] = round(min(rec['overlaps']), 4) if rec['overlaps'] else None
    rec['max_overlap'] = round(max(rec['overlaps']), 4) if rec['overlaps'] else None
    return rec


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_report(records: List[Dict[str, Any]], cfg: BatchConfig) -> str:
    total_gen = sum(r['generated'] for r in records)
    n_pack = len(records)
    avg_all = [r['avg_overlap'] for r in records if r['avg_overlap'] is not None]
    lines: List[str] = []
    lines.append('# 风格包批量生成流水线 · 报告')
    lines.append('')
    lines.append(f'- 生成时间（本地）：{_now()}')
    lines.append(f'- 风格包数：**{n_pack}**　生成曲目总数：**{total_gen}**'
                 f'　每包 {cfg.per_pack} 首')
    lines.append(f'- 内核：确定性风格插值骨架（不调 LLM）　保真度指标：'
                 f'`evaluate.style_overlap`（0–1，越大越像该风格）')
    lines.append(f'- 渲染 WAV：{"开" if cfg.render else "关"}'
                 f'　输出目录：`{os.path.relpath(cfg.out_dir, _ROOT)}/`')
    if avg_all:
        lines.append(f'- 全局平均风格保真度：**{round(sum(avg_all)/len(avg_all),4)}**'
                     f'（覆盖 {len(avg_all)}/{n_pack} 个有指纹的包）')
    lines.append('')
    lines.append('## 汇总表')
    lines.append('')
    lines.append('| 风格包 | 家族 | 生成 | 平均保真度 | 最低 | 指纹来源 | 问题 |')
    lines.append('|---|---|---|---|---|---|---|')
    for r in records:
        issues = ('；'.join(r['issues'][:2]) or '—') if r['issues'] else '—'
        src = r['fp_sources'] or '—'
        lines.append(f"| `{r['pack_id']}` | {r['family']} | {r['generated']} | "
                     f"{r['avg_overlap'] if r['avg_overlap'] is not None else '—'} | "
                     f"{r['min_overlap'] if r['min_overlap'] is not None else '—'} | "
                     f"{src} | {issues} |")
    lines.append('')
    lines.append('## 每包明细')
    lines.append('')
    for r in records:
        lines.append(f"### `{r['pack_id']}`（{r['family']} · 赋格={r['fugue']}）")
        lines.append('')
        if r['overlaps']:
            lines.append(f"平均保真度 **{r['avg_overlap']}**　"
                         f"范围 [{r['min_overlap']}, {r['max_overlap']}]")
        else:
            lines.append('（无保真度数据——指纹不可用或生成失败）')
        lines.append('')
        for it in r['outputs']:
            ov = it['overlap'] if it['overlap'] is not None else '—'
            wav = f"　WAV: `{os.path.relpath(it['wav'], _ROOT)}`" if it.get('wav') else ''
            lines.append(f"- seed `{it['seed']}`　保真度 {ov}　"
                         f"小节 {it.get('bars')}　声部 {it.get('voices')}　"
                         f"标题《{it.get('title')}》")
            lines.append(f"  - MusicXML: `{os.path.relpath(it['xml'], _ROOT)}`")
            lines.append(f"  - MIDI: `{os.path.relpath(it['mid'], _ROOT)}`{wav}")
        if r['issues']:
            lines.append('')
            lines.append('问题：' + '；'.join(r['issues']))
        lines.append('')
    lines.append('---')
    lines.append('确定性 / 可审计说明：每首 seed 固定为 `{seed_base}-{pack_id}-{i}`，'
                 '风格插值内核无随机性；混响（若开启）seed 固定。'
                 '整条流水线可重跑复现同一批产物与保真度数值。')
    return '\n'.join(lines)


def write_csv(records: List[Dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['pack_id', 'family', 'generated', 'avg_overlap',
                    'min_overlap', 'max_overlap', 'fp_sources', 'issues',
                    'outputs_dir'])
        for r in records:
            outs = ';'.join(os.path.relpath(it['mid'], _ROOT) for it in r['outputs'])
            w.writerow([r['pack_id'], r['family'], r['generated'],
                        r['avg_overlap'], r['min_overlap'], r['max_overlap'],
                        json.dumps(r['fp_sources'], ensure_ascii=False),
                        ';'.join(r['issues']), outs])


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def run_batch(packs: Optional[List[str]] = None, *, per_pack: int = 1,
              render: bool = False, out_dir: Optional[str] = None,
              force_fp: bool = False) -> str:
    """运行批量生成，返回 markdown 报告（同时落盘 runs/batch_report.md + .csv）。"""
    cfg = BatchConfig(packs=packs, per_pack=per_pack, render=render,
                      force_fp=force_fp)
    if out_dir:
        cfg.out_dir = out_dir
    targets = _resolve_targets(cfg)
    records: List[Dict[str, Any]] = []
    for pk in targets:
        records.append(generate_pack(pk, cfg))
    report = build_report(records, cfg)
    # 落盘（目录与写入路径统一锚定仓库根，不依赖 cwd；失败不再静默吞掉）
    try:
        runs_dir = os.path.join(_ROOT, 'runs')
        os.makedirs(runs_dir, exist_ok=True)
        with open(os.path.join(runs_dir, 'batch_report.md'), 'w',
                  encoding='utf-8') as f:
            f.write(report)
        write_csv(records, os.path.join(runs_dir, 'batch_report.csv'))
    except Exception as e:
        print(f'[batch_generate] 报告落盘失败：{e}', file=sys.stderr)
    return report


def _selftest() -> int:
    fail = 0
    def check(name, cond, extra=''):
        nonlocal fail
        if cond:
            print(f'  ✓ {name}')
        else:
            fail += 1
            print(f'  ✗ {name} {extra}')
    print('[batch_generate] 自检（小子集）')
    pk = enumerate_packs()
    check('枚举到风格包', len(pk) > 0, str(len(pk)))
    # 取前 2 个包，每包 1 首，不渲染，快
    subset = [p['pack_id'] for p in pk[:2]]
    rep = run_batch(subset, per_pack=1, render=False)
    check('报告含标题', '# 风格包批量生成流水线' in rep)
    for pid in subset:
        check(f'报告含 {pid}', pid in rep)
    # CSV 落盘
    csvp = os.path.join(_ROOT, 'runs', 'batch_report.csv')
    check('CSV 落盘', os.path.exists(csvp))
    # 输出文件存在
    import glob
    mids = glob.glob(os.path.join(_ROOT, 'scores', 'batch', '*.mid'))
    check('生成了 MIDI 文件', len(mids) >= 2, str(len(mids)))
    xmls = glob.glob(os.path.join(_ROOT, 'scores', 'batch', '*.musicxml'))
    check('生成了 MusicXML 文件', len(xmls) >= 2, str(len(xmls)))
    # 报告里保真度数值在 [-1, 1] 区间（若生成成功）
    import re as _re
    ovs = [float(x) for x in _re.findall(r'保真度 (\d+\.\d+)', rep)]
    check('保真度数值有效(>=0)', all(0.0 <= v <= 1.0 for v in ovs) if ovs else True,
          str(ovs))
    return fail


def main(argv=None):
    ap = argparse.ArgumentParser(description='风格包批量生成流水线')
    ap.add_argument('--packs', nargs='*', default=None,
                    help='限定风格包 id / 家族 / 名称（默认全部）')
    ap.add_argument('--per-pack', type=int, default=1, help='每包生成首数')
    ap.add_argument('--render', action='store_true', help='额外渲染 WAV（更慢）')
    ap.add_argument('--out-dir', default=None, help='输出目录')
    ap.add_argument('--force-fp', action='store_true', help='强制重建指纹')
    ap.add_argument('--selftest', action='store_true', help='运行自检')
    args = ap.parse_args(argv)
    if args.selftest:
        sys.exit(1 if _selftest() else 0)
    report = run_batch(args.packs, per_pack=args.per_pack,
                       render=args.render, out_dir=args.out_dir,
                       force_fp=args.force_fp)
    print(report)


if __name__ == '__main__':
    main()
