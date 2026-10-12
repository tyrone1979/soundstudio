# -*- coding: utf-8 -*-
"""把 35 条待裁定终止式还原成**真实乐谱**。

做法：读 ``runs/grammar_cache/bach_chorales.json``（与 ``learn.py`` 训练分支同源），
按 ``learn.py:162-193`` 完全相同的抽取条件复算出每个
``(target, 三和弦, 女高音)`` 组合的真实出现位置，与 ``grammar.yaml`` 中 35 条
C 规则逐条对齐，再用 music21 生成四声部谱例、verovio 渲染 SVG、Chrome 合成 PDF。

产物
----
``docs/cadence_review/终止式评审乐谱.pdf``
    85 个谱例（每条 C 规则的全部 n 次出现），每段标题给出 C 编号、作品、调、
    第几句、起止小节、和声骨架。
``docs/cadence_review/终止式评审乐谱.musicxml``
    同一批谱例的可编辑单行本。
``docs/cadence_review/midi/<C编号>_<第几次>.mid``
    每个谱例一个试听文件（♩=60），与 PDF 标题中的编号对应。
``runs/experiments/cadence_occurrences.json``
    逐次出现的定位表，供评审 Excel 引用。

用法
----
::

    venv/bin/python -m score_learning.grammar.cadence_score_export
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import yaml  # noqa: E402

CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')
YAML = os.path.join(ROOT, 'skills', 'composition', 'genre', 'packs',
                    'sacred', 'chorale', 'grammar.yaml')
AUDIT = os.path.join(ROOT, 'runs', 'experiments', 'cadence_audit.json')
OUT_DIR = os.path.join(ROOT, 'docs', 'cadence_review')
OUT_JSON = os.path.join(ROOT, 'runs', 'experiments', 'cadence_occurrences.json')

MIN_VOCAB = 8
MIDI_BPM = 60
VOICE_NAMES = ('Soprano', 'Alto', 'Tenor', 'Bass')
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


# ------------------------------------------------------------------ 抽取
def _forms_from_yaml():
    """(mode, target, chords, soprano) → C 编号；编号 → form 信息。"""
    d = yaml.safe_load(open(YAML, encoding='utf-8'))
    unknown = {r['id']
               for r in json.load(open(AUDIT, encoding='utf-8'))['still_unknown']}
    key2id, id2info = {}, {}
    for mode, targets in (d.get('cadences') or {}).items():
        for target, blk in targets.items():
            for f in (blk.get('forms') or []):
                if f['id'] not in unknown:
                    continue
                chs, sop = tuple(f['chords']), tuple(f['soprano'])
                key2id[(mode, target, chs, sop)] = f['id']
                id2info[f['id']] = {'id': f['id'], 'mode': mode, 'target': target,
                                    'chords': list(chs), 'soprano': list(sop),
                                    'n': f['n'], 'p': f.get('p')}
    return key2id, id2info


def _measure_map(piece):
    """每拍 → 小节号。弱起拍归第 0 小节，第一条小节线之后为第 1 小节。"""
    out = []
    mnum = 1 if piece['beats'][0]['downbeat'] else 0
    for i, b in enumerate(piece['beats']):
        if b['downbeat'] and i > 0:
            mnum += 1
        out.append(mnum)
    return out


def collect_occurrences():
    from score_learning.learn_core import is_heldout
    pieces = json.load(open(CACHE, encoding='utf-8'))['pieces']
    key2id, id2info = _forms_from_yaml()
    hits = {}
    for mode in ('major', 'minor'):
        tr = [p for p in pieces if p['mode'] == mode and not is_heldout(p['id'])]
        cc = Counter(b['chord'] for p in tr for b in p['beats'] if b['chord'])
        vset = {c for c, k in cc.items() if k >= MIN_VOCAB}
        for p in tr:
            beats = p['beats']
            mmap = _measure_map(p)
            tot = len(p['phrases'])
            for kth, ph in enumerate(p['phrases']):
                e = ph['end']
                if e - 2 < ph['start']:
                    continue
                chs = tuple(beats[i]['chord'] for i in (e - 2, e - 1, e))
                if not all(c in vset for c in chs):
                    continue
                tpc = p['tonic_pc']
                sop = tuple((beats[i]['midi'][0] - tpc) % 12 for i in (e - 1, e))
                tgt = ':'.join(chs[-1].split(':')[:2])
                cid = key2id.get((mode, tgt, chs, sop))
                if cid is None:
                    continue
                hits.setdefault(cid, []).append({
                    'piece': p['id'], 'tonic': p['tonic'], 'mode': p['mode'],
                    'meter': p['meter'], 'phrase_kth': kth + 1, 'phrase_total': tot,
                    'start': ph['start'], 'end': e,
                    'm_start': mmap[ph['start']], 'm_end': mmap[e],
                })
    return hits, id2info, pieces


# ------------------------------------------------------------------ 记谱
def _halfbeat_grid(beats):
    """beats → 4 声部半拍网格音高；半拍无新音时用拍首音填补（＝延留）。"""
    grid = [[] for _ in range(4)]
    for b in beats:
        for vi in range(4):
            grid[vi].append(b['midi'][vi])
        for vi in range(4):
            grid[vi].append(b['eighth'][vi])
    for vi in range(4):
        row = grid[vi]
        for j in range(1, len(row), 2):
            if row[j] is None:
                row[j] = row[j - 1]
    return grid


def _merge(row):
    out = []
    for j, pitch in enumerate(row):
        if pitch is None:
            continue
        if out and out[-1][2] == pitch and out[-1][0] + out[-1][1] == j:
            out[-1] = (out[-1][0], out[-1][1] + 1, pitch)
        else:
            out.append((j, 1, pitch))
    return out


def build_excerpt(piece, occ, roman_win=None):
    """一个乐句 → 一份对齐好小节线的四声部 Score。"""
    from music21 import (bar, clef, duration, expressions, key, meter, note,
                         stream)
    beats = piece['beats'][occ['start']:occ['end'] + 1]
    grid = _halfbeat_grid(beats)
    win = occ['end'] - occ['start']

    sc = stream.Score()
    for vi in range(4):
        part = stream.Part()
        part.id = VOICE_NAMES[vi]
        part.partName = VOICE_NAMES[vi]
        part.insert(0, key.KeySignature(
            key.Key(piece['tonic'] if occ['mode'] == 'major'
                    else piece['tonic'].lower(), occ['mode']).sharps))
        # 女高/女中/男高/男低：男高用低八度 treble 谱号，避免大量下加线
        part.insert(0, (clef.BassClef() if vi == 3
                        else clef.Treble8vbClef() if vi == 2
                        else clef.TrebleClef()))
        part.insert(0, meter.TimeSignature(piece['meter']))
        for slot, ln, pitch in _merge(grid[vi]):
            n = note.Note(pitch)
            n.duration = duration.Duration(ln * 0.5)
            part.insert(slot * 0.5, n)
        sc.insert(0, part)
    sc.makeNotation(inPlace=True)

    # 终止拍（延长记号处）加 fermata
    target_off = win * 1.0
    for part in sc.parts:
        for el in part.recurse().notes:
            if abs(el.getOffsetInHierarchy(sc) - target_off) < 1e-6:
                el.expressions.append(expressions.Fermata())
                break

    # 终止窗口的三个和弦在男低谱下标注罗马数字
    if roman_win:
        roms = roman_win.split(' – ')
        bass = sc.parts[-1]
        for k, rn in enumerate(roms):
            off = win - 2 + k
            holder = None
            for el in bass.recurse().notes:
                o = el.getOffsetInHierarchy(bass)
                if o - 1e-6 <= off < o + el.duration.quarterLength - 1e-6:
                    holder = el
                    break
            if holder is not None:
                holder.addLyric(rn)

    try:
        sc.parts[-1][-1].rightBarline = bar.Barline('double')
    except Exception:
        pass
    return sc


def roman_window(form):
    from score_learning.grammar.theory import parse_chord_key, roman
    return ' – '.join(roman(parse_chord_key(c), form['mode'])
                      for c in form['chords'])


def desc(form, occ, i):
    zh = '大调' if occ['mode'] == 'major' else '小调'
    mr = (f"第{occ['m_start']}小节" if occ['m_start'] == occ['m_end']
          else f"第{occ['m_start']}–{occ['m_end']}小节")
    return {
        'label': f"【{form['id']} · 第{i}/{len(form['_occs'])}次】",
        'where': f"{occ['piece']} · {occ['tonic']}{zh} · {occ['meter']} · "
                 f"第{occ['phrase_kth']}/{occ['phrase_total']}句 · {mr}",
        'roman': roman_window(form),
        'sop': f"{form['soprano'][0]} → {form['soprano'][1]}",
    }


# ------------------------------------------------------------------ 渲染
def render_all(hits, forms, pieces, midi_dir=None):
    import verovio
    from music21 import tempo
    if midi_dir:
        os.makedirs(midi_dir, exist_ok=True)
    blocks = []
    combined = None
    tk = verovio.toolkit()
    # 注意：本版本 verovio 的 setOptions 接收 **dict**，传 JSON 字符串会被忽略
    tk.setOptions({
        'adjustPageHeight': True, 'breaks': 'none', 'scale': 45,
        'pageWidth': 2400, 'pageMarginTop': 4, 'pageMarginBottom': 4,
        'pageMarginLeft': 8, 'pageMarginRight': 8,
        'header': 'none', 'footer': 'none', 'font': 'Bravura',
    })
    pmap = {p['id']: p for p in pieces}
    for cid in sorted(forms, key=lambda x: int(x[1:])):
        form = dict(forms[cid])
        occs = hits.get(cid, [])
        form['_occs'] = occs
        for i, occ in enumerate(occs, 1):
            sc = build_excerpt(pmap[occ['piece']], occ, roman_window(form))
            tmp = sc.write('musicxml')          # 返回临时文件路径
            xml = open(tmp, encoding='utf-8').read()
            tk.loadData(xml)
            svg = tk.renderToSVG(1, False)
            blocks.append((cid, i, desc(form, occ, i), svg))
            # 必须在并入 combined 之前写：第一个谱例对象本身会被用作 combined
            if midi_dir:
                sc.parts[0].insert(0, tempo.MetronomeMark(number=MIDI_BPM))
                sc.write('midi', fp=os.path.join(midi_dir, f'{cid}_{i}.mid'))
            if combined is None:
                combined = sc
            else:
                for pr in sc.parts:
                    pr.id = f'{cid}_{i}_{pr.id}'
                    combined.append(pr)
    return blocks, combined


def _printable_svg(svg):
    """verovio 的 CSS（stroke:currentColor）在 Chrome 打印管线里不生效，
    显式补上描边/填充并去掉 <style>，否则整页空白。"""
    import re
    svg = re.sub(r'<style.*?</style>', '', svg, flags=re.S)
    svg = re.sub(r'<path (d="[^"]+" stroke-width="\d+" />)',
                 r'<path fill="none" stroke="black" \1', svg)
    svg = re.sub(r'(<g id="E[0-9A-F]+-[^"]*">\s*)<path ',
                 r'\1<path fill="black" ', svg)
    return svg


def write_pdf(blocks, pdf_path):
    parts = ["""<!doctype html><meta charset="utf-8"><style>
@page { size: A4; margin: 10mm 12mm; }
body { font-family: "Songti SC","STSong",serif; }
.ex { break-inside: avoid; page-break-inside: avoid; margin-bottom: 4px; }
.hd { font-size: 11px; line-height: 1.4; border-left: 3px solid #444;
      padding-left: 6px; margin-bottom: 1px; }
.hd b { font-size: 12px; }
.hd span { color: #555; }
svg { width: 72%; height: auto; display: block; margin: 0 auto; }
</style><body>"""]
    for cid, i, d, svg in blocks:
        parts.append(
            f'<div class="ex"><div class="hd"><b>{d["label"]} {d["roman"]}</b><br>'
            f'<span>{d["where"]}　｜　女高音终止音 {d["sop"]}</span></div>'
            f'{_printable_svg(svg)}</div>')
    parts.append('</body>')
    html = ''.join(parts)
    html_path = pdf_path.replace('.pdf', '.html')
    open(html_path, 'w', encoding='utf-8').write(html)
    if os.path.exists(CHROME):
        subprocess.run([CHROME, '--headless', '--disable-gpu', '--no-sandbox',
                        '--no-pdf-header-footer',
                        '--run-all-compositor-stages-before-draw',
                        '--virtual-time-budget=20000',
                        f'--print-to-pdf={pdf_path}', html_path],
                       capture_output=True)
    else:
        print('未找到 Chrome，跳过 PDF 合成')
    return html_path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    hits, forms, pieces = collect_occurrences()
    missing = [c for c in forms if c not in hits]
    print('待裁定规则 %d 条；还原出现 %d 次；未匹配 %s'
          % (len(forms), sum(len(v) for v in hits.values()), missing or '无'))

    payload = {c: {'form': forms[c], 'occurrences': hits.get(c, [])}
               for c in sorted(forms, key=lambda x: int(x[1:]))}
    json.dump(payload, open(OUT_JSON, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('→ ' + OUT_JSON)

    midi_dir = os.path.join(OUT_DIR, 'midi')
    blocks, combined = render_all(hits, forms, pieces, midi_dir)
    print('已渲染 %d 个谱例；MIDI → %s' % (len(blocks), midi_dir))

    xml_path = os.path.join(OUT_DIR, '终止式评审乐谱.musicxml')
    combined.write('musicxml', fp=xml_path)
    print('→ ' + xml_path)

    pdf_path = os.path.join(OUT_DIR, '终止式评审乐谱.pdf')
    write_pdf(blocks, pdf_path)
    if os.path.exists(pdf_path):
        print('→ %s (%.1f KB)' % (pdf_path, os.path.getsize(pdf_path) / 1024))
    return blocks


if __name__ == '__main__':
    main()
