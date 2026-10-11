# -*- coding: utf-8 -*-
"""检查器专家裁定包：让乐理教师对 ``check_satb`` 的报错逐拍判真伪。

回应的问题
----------
故障注入（e6）只证明检查器实现了它被规定的规则；它没有回答「检查器报出的违例，
教师是否也认为是错」以及「检查器没报的地方，教师是否认为没错」。这里把检查器在
两个**非本系统生成**的来源上的输出交给专家盲判：

* 留出巴赫众赞歌（八分网格，论文发表口径：R05 关闭、tb_max=24）；
* music21 数字低音实现器在同一批低音与和弦标签上的输出（拍级网格，段间连接拍剔除，
  与 ``external_fb.run`` 同一协议、同一种子）。

抽样
----
* **报错拍**：按规则分层，每条规则（R05 在该口径下关闭）约 ``per_rule`` 拍，两个来源对半；
  同一拍报多条规则时只算一个条目，key 里记全部规则。
* **未报错拍**：两个来源各随机 ``n_clean // 2`` 个检查器完全没报的拍，用于估计漏报率。
* 条目整体打乱，编号 E001…；评审表不出现来源、作品号与规则号。

产物
----
``docs/expert_review/检查器专家裁定_谱例.pdf``          每条一个 2–4 拍谱例，* 号标出待判拍
``docs/expert_review/检查器专家裁定表_评审人A.xlsx``     两份相同的表，两位评审各填一份
``docs/expert_review/检查器专家裁定表_评审人B.xlsx``
``docs/expert_review/midi/E001.mid …``                  与谱例同内容的试听文件，只以编号命名
``docs/expert_review/musicxml/E001.musicxml …``         同一谱例的可编辑乐谱，只以编号命名
``docs/expert_review/检查器专家裁定_谱例.musicxml``       全部谱例的合订本（按编号顺序）
``runs/private/expert_review_key.json``               答案键（.gitignore 排除；同种子可重建）

用法::

    venv/bin/python -m score_learning.grammar.expert_pack
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from score_learning.grammar import script as gs  # noqa: E402
from score_learning.grammar.analyze import analyze_corpus  # noqa: E402
from score_learning.grammar.evaluate import heldout_pieces, piece_metrics  # noqa: E402
from score_learning.grammar.external_fb import (  # noqa: E402
    _beat_level, _with_voices, realize_piece,
)
from score_learning.grammar.theory import parse_chord_key, roman  # noqa: E402

CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')
OUT_DIR = os.path.join(ROOT, 'docs', 'expert_review')
KEY_PATH = os.path.join(ROOT, 'runs', 'private', 'expert_review_key.json')
PDF_NAME = '检查器专家裁定_谱例.pdf'
MIDI_DIR = 'midi'
XML_DIR = 'musicxml'
XML_NAME = '检查器专家裁定_谱例.musicxml'
MIDI_BPM = 60
VOICE_NAMES = ('Soprano', 'Alto', 'Tenor', 'Bass')
RATERS = ('A', 'B')

#: 发表口径下 R05 被关掉（音域放宽到 0–127），因此不进抽样
SAMPLED_RULES = ('R01', 'R02', 'R03', 'R03/R04', 'R06', 'R07', 'R08', 'R09')

VERDICT_OPTS = ['有错', '无错', '不确定']
#: 评审人可选的错误类型（教材用语，与规则号一一可映射，但表上不出现规则号）
ERROR_TYPES = ['平行五八度', '隐伏五八度', '声部交叉', '声部超越', '间距过大',
               '导音未解决', '七音未解决', '旋律音程不良', '导音重复', '和弦不完整',
               '其他']
TYPE_TO_RULE = {
    '平行五八度': 'R01', '隐伏五八度': 'R02', '声部交叉': 'R03/R04',
    '声部超越': 'R03', '间距过大': 'R03/R04', '导音未解决': 'R06',
    '七音未解决': 'R07', '旋律音程不良': 'R08', '导音重复': 'R09',
    '和弦不完整': 'R09',
}
LABEL_OPTS = ['正确', '有误', '不确定']


# ------------------------------------------------------------------ 收集
def _beat_index(piece: Dict[str, Any], t: float) -> int:
    ts = [b['t'] for b in piece['beats']]
    k = 0
    for i, x in enumerate(ts):
        if x <= t + 1e-9:
            k = i
        else:
            break
    return k


def _phrase_of(piece: Dict[str, Any], k: int):
    for ph in piece['phrases']:
        if ph['start'] <= k <= ph['end']:
            return ph
    return None


def collect(seed: int = 0, refined: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """返回 (候选拍列表, {来源:作品id → 送检谱面})。

    默认用专家评审前的原检查器（refined=False）：评审样本是按它分层抽取的，重建评审包须一致。

    每个候选拍：{'source', 'piece', 'k', 't', 'rules', 'msgs'}；
    rules 为空即未报错拍。
    """
    sc = gs.load()
    pieces = analyze_corpus(cache=CACHE, progress=False)
    held = heldout_pieces(pieces)
    cands: List[Dict[str, Any]] = []
    scored: Dict[str, Dict[str, Any]] = {}
    for i, p in enumerate(held):
        arms = [('bach', p, set())]
        try:
            v, chunks = realize_piece(p, seed + i)
            joins = {lo for lo, _ in chunks} - {chunks[0][0]}
            excl = {p['beats'][j]['t'] for j in joins}
            arms.append(('fb', _with_voices(p, v), excl))
        except Exception:  # noqa: BLE001
            pass
        for src, q, excl in arms:
            m = piece_metrics(q if src == 'bach' else _beat_level(q), sc,
                              exclude_t=excl or None, refined=refined)
            scored[f'{src}:{p["id"]}'] = q
            by_k: Dict[int, Dict[str, Any]] = {}
            for rule, t, msg in m['violation_detail']:
                k = _beat_index(q, t)
                e = by_k.setdefault(k, {'rules': set(), 'msgs': []})
                e['rules'].add(rule)
                e['msgs'].append(f'{rule} t={t} {msg}')
            excl_k = {_beat_index(q, t) for t in excl}
            for ph in q['phrases']:
                for k in range(ph['start'] + 1, ph['end'] + 1):
                    if k in excl_k:
                        continue
                    e = by_k.get(k, {'rules': set(), 'msgs': []})
                    cands.append({'source': src, 'piece': p['id'], 'k': k,
                                  't': q['beats'][k]['t'],
                                  'rules': sorted(e['rules']), 'msgs': e['msgs']})
    return cands, scored


def sample(cands: List[Dict[str, Any]], per_rule: int = 19, n_clean: int = 50,
           seed: int = 0) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    chosen: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for rule in SAMPLED_RULES:
        pools = {}
        for src in ('bach', 'fb'):
            pools[src] = [c for c in cands if c['source'] == src and rule in c['rules']
                          and (c['source'], c['piece'], c['k']) not in chosen]
            rng.shuffle(pools[src])
        # 两个来源轮流取，一方取尽后由另一方补足
        taken = 0
        while taken < per_rule and (pools['bach'] or pools['fb']):
            for src in ('bach', 'fb'):
                if pools[src] and taken < per_rule:
                    c = pools[src].pop()
                    chosen[(c['source'], c['piece'], c['k'])] = dict(c, stratum=rule)
                    taken += 1
    for src in ('bach', 'fb'):
        pool = [c for c in cands if c['source'] == src and not c['rules']]
        rng.shuffle(pool)
        for c in pool[:n_clean // 2]:
            chosen[(c['source'], c['piece'], c['k'])] = dict(c, stratum='clean')
    items = list(chosen.values())
    rng.shuffle(items)
    for j, it in enumerate(items, 1):
        it['item'] = f'E{j:03d}'
    return items


def population(cands: List[Dict[str, Any]]) -> Dict[str, Any]:
    """抽样框的总体规模（估计 recall 时要按总体加权）。"""
    out: Dict[str, Any] = {}
    for src in ('bach', 'fb'):
        cs = [c for c in cands if c['source'] == src]
        out[src] = {
            'beats': len(cs),
            'flagged': sum(1 for c in cs if c['rules']),
            'clean': sum(1 for c in cs if not c['rules']),
            'by_rule': {r: sum(1 for c in cs if r in c['rules']) for r in SAMPLED_RULES},
        }
    return out


# ------------------------------------------------------------------ 记谱
def _window(piece: Dict[str, Any], k: int) -> Tuple[int, int]:
    ph = _phrase_of(piece, k) or {'start': max(k - 2, 0), 'end': k + 1}
    return max(ph['start'], k - 2), min(ph['end'], k + 1)


def build_excerpt(piece: Dict[str, Any], k: int, eighth: bool):
    """待判拍前两拍到后一拍（不跨乐句）；首拍前补休止，使小节线与原谱对齐。"""
    from music21 import clef, duration, key, meter, note, stream, tie
    lo, hi = _window(piece, k)
    beats = piece['beats'][lo:hi + 1]
    pad = float(beats[0].get('pos') or 0.0)
    sc = stream.Score()
    for vi in range(4):
        part = stream.Part()
        part.insert(0, key.KeySignature(key.Key(
            piece['tonic'] if piece['mode'] == 'major' else piece['tonic'].lower(),
            piece['mode']).sharps))
        part.insert(0, clef.BassClef() if vi == 3 else
                    clef.Treble8vbClef() if vi == 2 else clef.TrebleClef())
        part.insert(0, meter.TimeSignature(piece['meter']))
        if pad:
            r = note.Rest()
            r.duration = duration.Duration(pad)
            part.insert(0, r)
        # 不跨拍合并（同音延续用连音线），否则后一拍的和弦标注与 * 标记会丢失
        prev = None
        for j, b in enumerate(beats):
            p0 = b['midi'][vi]
            p1 = b['eighth'][vi] if eighth else None
            segs = [(0.0, 0.5, p0), (0.5, 0.5, p1)] if p1 is not None and p1 != p0 \
                else [(0.0, 1.0, p0)]
            for s, (dt, ln, pitch) in enumerate(segs):
                n = note.Note(int(pitch))
                n.duration = duration.Duration(ln)
                if prev is not None and prev.pitch.midi == int(pitch):
                    prev.tie = tie.Tie('start' if prev.tie is None else 'continue')
                    n.tie = tie.Tie('stop')
                if vi == 3 and s == 0:
                    ch = b.get('chord')
                    rn = roman(parse_chord_key(ch), piece['mode']) if ch else '?'
                    n.addLyric(('*' if lo + j == k else '') + rn)
                part.insert(pad + j + dt, n)
                prev = n
        sc.insert(0, part)
    sc.makeNotation(inPlace=True)
    return sc


def write_files(items: List[Dict[str, Any]], scored: Dict[str, Dict[str, Any]],
                out_dir: str, bpm: int = MIDI_BPM) -> int:
    """每条谱例一个 ``<编号>.mid`` 与 ``<编号>.musicxml``，另出一份合订本 MusicXML。

    文件名、标题与声部名只含编号，不泄露来源。
    """
    from music21 import metadata, tempo
    midi_dir = os.path.join(out_dir, MIDI_DIR)
    xml_dir = os.path.join(out_dir, XML_DIR)
    os.makedirs(midi_dir, exist_ok=True)
    os.makedirs(xml_dir, exist_ok=True)
    combined = None
    for it in items:
        q = scored[f'{it["source"]}:{it["piece"]}']
        sc = build_excerpt(q, it['k'], eighth=it['source'] == 'bach')
        sc.parts[0].insert(0, tempo.MetronomeMark(number=bpm))
        sc.metadata = metadata.Metadata(title=it['item'], composer='')
        for vi, part in enumerate(sc.parts):
            part.partName = f'{it["item"]} {VOICE_NAMES[vi]}'
            part.id = f'{it["item"]}_{VOICE_NAMES[vi]}'
        sc.write('midi', fp=os.path.join(midi_dir, f'{it["item"]}.mid'))
        sc.write('musicxml', fp=os.path.join(xml_dir, f'{it["item"]}.musicxml'))
        if combined is None:
            combined = sc
            combined.metadata = metadata.Metadata(title='检查器专家裁定谱例', composer='')
        else:
            for part in sc.parts:
                combined.append(part)
    if combined is not None:
        combined.write('musicxml', fp=os.path.join(out_dir, XML_NAME))
    return len(items)


def render(items: List[Dict[str, Any]], scored: Dict[str, Dict[str, Any]], pdf_path: str):
    import subprocess

    import verovio

    from score_learning.grammar.cadence_score_export import CHROME, _printable_svg
    tk = verovio.toolkit()
    tk.setOptions({
        'adjustPageHeight': True, 'breaks': 'none', 'scale': 45,
        'pageWidth': 1800, 'pageMarginTop': 4, 'pageMarginBottom': 4,
        'pageMarginLeft': 8, 'pageMarginRight': 8,
        'header': 'none', 'footer': 'none', 'font': 'Bravura',
    })
    parts = ["""<!doctype html><meta charset="utf-8"><style>
@page { size: A4; margin: 10mm 12mm; }
body { font-family: "Songti SC","STSong",serif; }
.ex { break-inside: avoid; page-break-inside: avoid; margin-bottom: 6px; }
.hd { font-size: 11px; line-height: 1.4; border-left: 3px solid #444; padding-left: 6px; }
.hd b { font-size: 12px; }
svg { width: 42%; height: auto; display: block; margin: 0 auto; }
</style><body>"""]
    for it in items:
        q = scored[f'{it["source"]}:{it["piece"]}']
        sc = build_excerpt(q, it['k'], eighth=it['source'] == 'bach')
        tk.loadData(open(sc.write('musicxml'), encoding='utf-8').read())
        svg = tk.renderToSVG(1, False)
        zh = '大调' if q['mode'] == 'major' else '小调'
        parts.append(
            f'<div class="ex"><div class="hd"><b>{it["item"]}</b>　'
            f'{q["tonic"]}{zh} · {q["meter"]}　｜　* 号所在拍为待判拍</div>'
            f'{_printable_svg(svg)}</div>')
    parts.append('</body>')
    html_path = pdf_path.replace('.pdf', '.html')
    open(html_path, 'w', encoding='utf-8').write(''.join(parts))
    if os.path.exists(CHROME):
        subprocess.run([CHROME, '--headless', '--disable-gpu', '--no-sandbox',
                        '--no-pdf-header-footer',
                        '--run-all-compositor-stages-before-draw',
                        '--virtual-time-budget=30000',
                        f'--print-to-pdf={pdf_path}', html_path],
                       capture_output=True)
        os.remove(html_path)
    else:
        print('未找到 Chrome，保留 HTML：' + html_path)


# ------------------------------------------------------------------ 表格
def write_sheet(items: List[Dict[str, Any]], path: str, rater: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws0 = wb.active
    ws0.title = '填写说明'
    ws0.column_dimensions['A'].width = 22
    ws0.column_dimensions['B'].width = 96
    intro = [
        ('评审人', f'评审人 {rater}。请独立完成，不与另一位评审人讨论，也不要查看代码仓库。'),
        ('评审对象', f'{PDF_NAME} 中的 {len(items)} 个谱例（编号 E001 起）。'
                     '每个谱例 2–4 拍，低音下方是系统给出的和弦标注，带 * 号的那一拍是待判拍。'),
        ('要判断什么', '以四部和声教材（如 Aldwell & Schachter）的标准，判断「从前一拍进入 * 拍」'
                     '的声部进行，以及 * 拍本身的和弦配置，是否存在教材意义上的错误。'
                     '只判 * 拍；谱例里其他拍的问题不计。八分音符可能是经过音或辅助音。'),
        ('① 判定', '有错 / 无错 / 不确定。风格上可接受的巴赫式处理（如经过音造成的瞬间平行）'
                   '若你认为教材允许，请判「无错」，并在备注里说明。'),
        ('② 错误类型', '判「有错」时必填，选最主要的一项；有第二项可写在备注。'),
        ('③ 和弦标注', '* 拍下方的和弦标注是否正确：正确 / 有误 / 不确定。有误请在备注写出你的标注。'),
        ('试听', f'{MIDI_DIR}/ 文件夹里有与编号同名的 MIDI（如 E001.mid，♩={MIDI_BPM}），'
                 '内容与谱例相同，可用于试听；判定以谱面为准。'),
        ('MusicXML', f'{XML_DIR}/ 文件夹里有与编号同名的 MusicXML（如 E001.musicxml），'
                     f'可在 MuseScore 等软件中打开；{XML_NAME} 是全部谱例的合订本。'),
        ('时间', '每条约 1–2 分钟，全程约 4–5 小时，可分多次完成。'),
        ('来源', '谱例混合了多种来源，顺序已打乱；表中不提供来源信息。'),
    ]
    for r, (a, b) in enumerate(intro, 1):
        ws0.cell(row=r, column=1, value=a).font = Font(bold=True)
        c = ws0.cell(row=r, column=2, value=b)
        c.alignment = Alignment(wrap_text=True, vertical='top')
        ws0.row_dimensions[r].height = 44

    ws = wb.create_sheet('评审表')
    heads = [('编号', 8), ('调', 10), ('拍号', 7), ('① 判定', 10), ('② 错误类型', 14),
             ('③ 和弦标注', 11), ('备注', 50)]
    for j, (h, w) in enumerate(heads, 1):
        c = ws.cell(row=1, column=j, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1F3864')
        ws.column_dimensions[chr(64 + j)].width = w
    for r, it in enumerate(items, 2):
        ws.cell(row=r, column=1, value=it['item'])
        ws.cell(row=r, column=2, value=it['key_label'])
        ws.cell(row=r, column=3, value=it['meter'])
    last = len(items) + 1
    for col, opts in (('D', VERDICT_OPTS), ('E', ERROR_TYPES), ('F', LABEL_OPTS)):
        dv = DataValidation(type='list', formula1='"%s"' % ','.join(opts), allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f'{col}2:{col}{last}')
    ws.freeze_panes = 'B2'
    wb.save(path)


# ------------------------------------------------------------------ 主流程
def build(per_rule: int = 19, n_clean: int = 50, seed: int = 0,
          render_pdf: bool = True) -> Dict[str, Any]:
    cands, scored = collect(seed)
    items = sample(cands, per_rule=per_rule, n_clean=n_clean, seed=seed)
    for it in items:
        q = scored[f'{it["source"]}:{it["piece"]}']
        it['key_label'] = q['tonic'] + ('大调' if q['mode'] == 'major' else '小调')
        it['meter'] = q['meter']
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    key = {
        'seed': seed, 'per_rule': per_rule, 'n_clean': n_clean,
        'sampled_rules': list(SAMPLED_RULES),
        'population': population(cands),
        'items': [{k: it[k] for k in ('item', 'source', 'piece', 'k', 't', 'rules',
                                      'msgs', 'stratum')} for it in items],
    }
    with open(KEY_PATH, 'w', encoding='utf-8') as f:
        json.dump(key, f, ensure_ascii=False, indent=1)
    for r in RATERS:
        write_sheet(items, os.path.join(OUT_DIR, f'检查器专家裁定表_评审人{r}.xlsx'), r)
    write_files(items, scored, OUT_DIR)
    if render_pdf:
        render(items, scored, os.path.join(OUT_DIR, PDF_NAME))
    return key


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='检查器专家裁定包')
    ap.add_argument('--per-rule', type=int, default=19)
    ap.add_argument('--n-clean', type=int, default=50)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--no-pdf', action='store_true')
    a = ap.parse_args(argv)
    key = build(a.per_rule, a.n_clean, a.seed, not a.no_pdf)
    its = key['items']
    by = defaultdict(int)
    for it in its:
        by[(it['stratum'], it['source'])] += 1
    print('条目 %d（报错 %d / 未报错 %d）' % (
        len(its), sum(1 for i in its if i['rules']), sum(1 for i in its if not i['rules'])))
    for s in list(SAMPLED_RULES) + ['clean']:
        print('  %-8s bach %2d  fb %2d' % (s, by[(s, 'bach')], by[(s, 'fb')]))
    print('总体:', json.dumps(key['population'], ensure_ascii=False))
    print('→ ' + OUT_DIR)
    print('→ ' + KEY_PATH)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
