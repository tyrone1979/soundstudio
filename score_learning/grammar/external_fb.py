# -*- coding: utf-8 -*-
"""外部系统对照：music21 数字低音实现器的输出交给同一个检查器打分。

对应论文 §7 "Threats to validity" (ii)：此前所有对照组都与本系统共享语料、
折、词表，只差一个变量；从未用 ``check_satb`` 给一个**独立编写**的和声系统
打过分。这里补上。

外部系统
--------
``music21.figuredBass``（Cuthbert & Ariza 的 music21 自带模块）是一个按教科书
规则（禁平行五/八度、外声部隐伏五/八度、声部超越、上三声部间距、属七解决等）
实现数字低音的规则系统，代码与本项目无关。

协议
----
每首留出巴赫众赞歌：保留巴赫的**低音**与本分析器给出的**和弦标签**（根音、
性质、转位），把它们转写成数字低音交给实现器，由实现器自由写出上三声部；
随机实现用固定种子。和弦标签沿用巴赫，因此 ``check_satb`` 用到的和弦成员、
导音、七音信息与巴赫留出组完全相同，差别只在声部写法。

作为参照，同一批巴赫也在**拍级网格**（去掉八分经过音）上重新打分，因为实现器
只输出拍级和弦；这样两组的网格分辨率一致。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from score_learning.grammar import script as gs  # noqa: E402
from score_learning.grammar.analyze import analyze_corpus  # noqa: E402
from score_learning.grammar.evaluate import heldout_pieces, piece_metrics  # noqa: E402
from score_learning.grammar.satb_eval import RULES, _arm  # noqa: E402
from score_learning.grammar.theory import (  # noqa: E402
    LETTER_PC, LETTERS, QUALITIES, SEVENTHS, parse_chord_key, tonic_letter_pc,
)

OUT = os.path.join(ROOT, 'runs', 'experiments')
CACHE = os.path.join(ROOT, 'runs', 'grammar_cache', 'bach_chorales.json')

MAJOR_PCS = (0, 2, 4, 5, 7, 9, 11)
MINOR_PCS = (0, 2, 3, 5, 7, 8, 10)
#: 调外根音的拼写方向：+1 = 升高下方音级，-1 = 降低上方音级
CHROMATIC_ROOT = {
    'major': {1: -1, 3: -1, 6: +1, 8: -1, 10: -1},
    'minor': {1: -1, 4: +1, 6: +1, 9: +1, 11: +1},
}


def _alter(pc_needed: int, step_pc: int) -> int:
    d = (pc_needed - step_pc) % 12
    return d if d <= 6 else d - 12


def _root_step(root_rel: int, mode: str) -> int:
    """根音相对主音的音级序号（0..6）。"""
    pcs = MAJOR_PCS if mode == 'major' else MINOR_PCS
    if root_rel in pcs:
        return pcs.index(root_rel)
    direction = CHROMATIC_ROOT[mode].get(root_rel, +1)
    return pcs.index((root_rel - direction) % 12) if direction > 0 else \
        pcs.index((root_rel + 1) % 12)


def _key_signature_pc(letter: str, ks: Dict[str, int]) -> int:
    return (LETTER_PC[letter] + ks.get(letter, 0)) % 12


def figure_for(chord_key: str, tonic: str, mode: str, bass_midi: int):
    """和弦标签 → (低音 music21 Pitch, 数字低音字符串)。"""
    from music21 import key as m21key, pitch as m21pitch
    letter, tpc = tonic_letter_pc(tonic)
    k = m21key.Key(tonic.replace('b', '-') if len(tonic) > 1 else tonic,
                   mode)
    ks = {p.step: int(p.accidental.alter) for p in k.alteredPitches}
    root, q, inv = parse_chord_key(chord_key)
    ivs = QUALITIES[q]
    t_idx = LETTERS.index(letter)
    r_step = _root_step(root, mode)
    # 各成员的字母与绝对 pc（叠三度拼写）
    members = []
    for j, iv in enumerate(ivs):
        lt = LETTERS[(t_idx + r_step + 2 * j) % 7]
        members.append((lt, (tpc + root + iv) % 12))
    b_letter, b_pc = members[inv]
    b_alt = _alter(b_pc, LETTER_PC[b_letter])
    acc = {1: '#', -1: '-', 2: '##', -2: '--', 0: ''}[b_alt]
    bp = m21pitch.Pitch(b_letter + acc)
    # 八度：取与巴赫低音最近的那个
    bp.octave = 4
    while bp.midi - bass_midi > 6:
        bp.octave -= 1
    while bass_midi - bp.midi > 6:
        bp.octave += 1
    # 数字：每个非低音成员的音程号；与调号不符时加变音记号
    nums = []
    b_idx = LETTERS.index(b_letter)
    for lt, pc in members:
        if lt == b_letter:
            continue
        num = (LETTERS.index(lt) - b_idx) % 7 + 1
        # music21 的修饰符叠加在调号音高上；'n' 直接还原
        sig = _key_signature_pc(lt, ks)
        d = _alter(pc, sig)
        if d == 0:
            pref = ''
        elif pc == LETTER_PC[lt]:
            pref = 'n'
        else:
            pref = {1: '#', -1: 'b', 2: '##', -2: 'bb'}[d]
        nums.append((num, pref))
    nums.sort(key=lambda x: -x[0])
    return bp, ','.join(f'{p}{n}' for n, p in nums), k


def _realization(piece: Dict[str, Any], lo: int, hi: int):
    """拍 [lo, hi) 交给实现器；返回 Realization（可能 0 解）。"""
    from music21 import meter, note
    from music21.figuredBass import realizer, rules
    fb = None
    for b in piece['beats'][lo:hi]:
        bp, fig, k = figure_for(b['chord'], piece['tonic'], piece['mode'],
                                b['midi'][3])
        if fb is None:
            fb = realizer.FiguredBassLine(k, meter.TimeSignature(piece['meter']))
        fb.addElement(note.Note(bp, quarterLength=1.0), fig)
    return fb.realize(rules.Rules(), numParts=4, maxPitch='G5')


def _random_progression(real) -> list:
    """等价于 ``Realization.getRandomPossibilityProgression``；原实现在
    Python 3.11 下对 dict 视图调用 ``random.sample`` 会抛 TypeError。"""
    segs = real._segmentList
    if len(segs) == 1:
        return [random.choice(sorted(segs[0].correctA))]
    prev = random.choice(sorted(segs[0].movements.keys()))
    prog = [prev]
    for k in range(len(segs) - 1):
        prev = random.choice(sorted(segs[k].movements[prev]))
        prog.append(prev)
    return prog


def _solvable(piece: Dict[str, Any], lo: int, hi: int) -> bool:
    return _realization(piece, lo, hi).getNumSolutions() > 0


def _chunks(piece: Dict[str, Any]) -> List[Tuple[int, int]]:
    """按巴赫乐句切段；段内无解时在最长可解前缀处再切（前缀可解性单调）。"""
    out = []
    for ph in piece['phrases']:
        lo, end = ph['start'], ph['end'] + 1
        while lo < end:
            if _solvable(piece, lo, end):
                out.append((lo, end))
                break
            a, b = lo + 1, end - 1      # 最长可解前缀 [lo, a]，a 至少为 lo+1
            while a < b:
                m = (a + b + 1) // 2
                if _solvable(piece, lo, m):
                    a = m
                else:
                    b = m - 1
            out.append((lo, a))
            lo = a
    return out


def realize_piece(piece: Dict[str, Any], seed: int
                  ) -> Tuple[Dict[int, List[int]], List[Tuple[int, int]]]:
    """逐段实现上三声部，返回 ({拍号: SATB}, 段落)。"""
    random.seed(seed)
    voices: Dict[int, List[int]] = {}
    chunks = _chunks(piece)
    for lo, hi in chunks:
        real = _realization(piece, lo, hi)
        sc = real.generateRealizationFromPossibilityProgression(
            _random_progression(real))
        parts = list(sc.parts)
        # 实现器输出：上方 part 含三声部和弦，下方 part 为低音
        upper = list(parts[0].recurse().notes)
        lower = list(parts[1].recurse().notes)
        if len(upper) != hi - lo or len(lower) != hi - lo:
            raise RuntimeError('realizer output length mismatch')
        for j, (c, bn) in enumerate(zip(upper, lower)):
            ps = sorted((p.midi for p in c.pitches), reverse=True)
            voices[lo + j] = [ps[0], ps[1], ps[2], bn.pitch.midi]
    return voices, chunks


def _with_voices(piece: Dict[str, Any], voices: Dict[int, List[int]]) -> Dict[str, Any]:
    nb = [dict(b, midi=list(voices.get(i, b['midi'])), eighth=[None] * 4)
          for i, b in enumerate(piece['beats'])]
    return dict(piece, beats=nb)


def _beat_level(piece: Dict[str, Any]) -> Dict[str, Any]:
    return dict(piece, beats=[dict(b, eighth=[None] * 4) for b in piece['beats']])


def run(seed: int = 0, save: bool = True) -> Dict[str, Any]:
    sc = gs.load()
    pieces = analyze_corpus(cache=CACHE, progress=False)
    held = heldout_pieces(pieces)
    rows = {'bach_beat': {'major': [], 'minor': []},
            'fb_realizer': {'major': [], 'minor': []}}
    failed = []
    per_piece = []
    for i, p in enumerate(held):
        t0 = time.time()
        try:
            v, chunks = realize_piece(p, seed + i)
        except Exception as e:  # noqa: BLE001
            failed.append({'id': p['id'], 'error': repr(e)[:200]})
            continue
        dt = time.time() - t0
        # 段间连接实现器从未检查过，两组都不计该拍的违例（含乐句起点）
        joins = {lo for lo, _ in chunks} - {chunks[0][0]}
        n_split = len(chunks) - len(p['phrases'])
        excl = {p['beats'][j]['t'] for j in joins}
        mb = piece_metrics(_beat_level(p), sc, exclude_t=excl)
        mf = piece_metrics(_with_voices(p, v), sc, exclude_t=excl)
        rows['bach_beat'][p['mode']].append(mb)
        rows['fb_realizer'][p['mode']].append(mf)
        per_piece.append({'id': p['id'], 'mode': p['mode'], 'beats': mf['beats'],
                          'chunks': len(chunks), 'phrases': len(p['phrases']),
                          'joins_excluded': len(joins), 'extra_splits': n_split,
                          'bach_viol': mb['violations'], 'fb_viol': mf['violations'],
                          'seconds': round(dt, 2)})
    out: Dict[str, Any] = {
        'question': ('Strictness of the shared checker on an independently '
                     'written rule-based harmoniser (music21.figuredBass), '
                     'given Bach bass and chord labels.'),
        'setting': {
            'checker': 'grammar/check.py:check_satb, published evaluation '
                       'config (ranges 0-127 so R05 is off, tb_max=24)',
            'grid': 'beat level for both arms (no eighth-note passing tones)',
            'realizer': 'music21.figuredBass.realizer, default Rules(), '
                        'numParts=4, maxPitch=G5, generateRandomRealization',
            'seed': f'{seed}+piece index',
        },
        'arms': {}, 'failed': failed, 'per_piece': per_piece,
    }
    for arm, by_mode in rows.items():
        for mode, rs in by_mode.items():
            if rs:
                a = _arm(rs)
                a['drafts_clean'] = sum(1 for r in rs if r['violations'] == 0)
                out['arms'][f'{arm}_{mode}'] = a
    if save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, 'e6b_external_fb.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='外部系统（music21 数字低音实现器）对照')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--extra-seeds', type=int, nargs='*', default=[100, 200],
                    help='只记录违例率的附加种子（敏感性）')
    a = ap.parse_args(argv)
    sens = {}
    for s in a.extra_seeds:
        r = run(seed=s, save=False)
        sens[str(s)] = {k: v['violations_per_100'] for k, v in r['arms'].items()}
    o = run(seed=a.seed, save=False)
    sens[str(a.seed)] = {k: v['violations_per_100'] for k, v in o['arms'].items()}
    o['seed_sensitivity'] = sens
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'e6b_external_fb.json'), 'w', encoding='utf-8') as f:
        json.dump(o, f, ensure_ascii=False, indent=1)
    print('| 组 | 曲数 | 违例/100 拍 | 干净 | ' + ' | '.join(RULES) + ' |')
    for name, v in o['arms'].items():
        print('| %s | %d | %.2f | %d | %s |' % (
            name, v['pieces'], v['violations_per_100'], v['drafts_clean'],
            ' | '.join('%.2f' % v['by_rule_per_100'][r] for r in RULES)))
    print('失败:', len(o['failed']), o['failed'][:5])
    print('产物 → runs/experiments/e6b_external_fb.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
