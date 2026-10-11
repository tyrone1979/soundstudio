# -*- coding: utf-8 -*-
"""叙事层（narrative）安全性回归测试。

背景 —— 三处真实缺陷（本次修复）：

  1. **调外音**：`finalize` 会自动补一层 `narrative`，它的「动机变形出场」用
     invert（半音倒影）/ ornament（上邻音 = +1 半音）等**半音级**算子重写高音
     声部若干小节。于是 C 大调里 G 的上邻音被写成 **Ab**、大三度倒影成
     **Db/Gb**、G 大调里写出 **Eb** —— 调外音直接进谱。
     修法：`_s_narrative` 把变形结果的**每个音**吸附到「该小节调内音 ∪ 和弦音」
     （原实现只吸附首音）。

  2. **终止式被覆盖**：该层排在 `cadence` 之后执行，却只跳过**最后一小节**
     （`if b > st.bars - 1`），于是倒数第二小节（终止式的属七）会被变形动机
     覆盖 —— 「末两小节主和弦长音」的终止硬保证失效，终止处还出现调外音。
     修法：`cadence` 用 `st.cadence_bars` 登记终止式小节，叙事层对其避让。

  3. **开关缺失**：用户明确说「不要赋格/复调模仿/卡农/动机变形」时无法让内核
     停手。新增 `narrative(appearances=false)`：旋律严格保持 `melody` 写下的形态。

运行：python -m pytest tests/test_narrative_key_safety.py -q
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import compose_kernel as ck                                   # noqa: E402
from compose_kernel import run_program                        # noqa: E402


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

_C_MAJOR = {'C', 'D', 'E', 'F', 'G', 'A', 'B'}

_DUR_QL = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5, '16th': 0.25,
           'dotted_quarter': 1.5, 'dotted_eighth': 0.75}


def _note_core(name):
    """音名 → 去八度的音级（'Ab5'/'A-5' → 'Ab'；'rest'/None → None）。"""
    if name is None:
        return None
    s = str(name).strip()
    if not s or s.lower() in ('rest', 'none'):
        return None
    s = re.sub(r'-?\d+$', '', s)          # 去八度
    s = re.sub(r'^([A-G])-$', r'\1b', s)  # music21 的降号拼写 'D-' → 'Db'
    if re.match(r'^[A-G]#?$', s):
        return s
    return s


def _split_bars(spec, part_idx=0):
    """把某声部的扁平 events 按小节切开 → {小节号(1-based): [音级,…]}。"""
    bar_ql = 4.0 * int(str(spec['time_signature']).split('/')[0]) \
        / int(str(spec['time_signature']).split('/')[1])
    out, cur, t, bar = {}, [], 0.0, 1
    for ev in (spec['parts'][part_idx].get('events') or []):
        if t >= bar_ql - 1e-9:
            out[bar] = cur
            cur, t, bar = [], 0.0, bar + 1
        pitch = ev[0]
        if isinstance(pitch, (list, tuple)):
            cur.extend(_note_core(p) for p in pitch)
        else:
            c = _note_core(pitch)
            if c:
                cur.append(c)
        t += _DUR_QL.get(str(ev[1]), 1.0)
    out[bar] = cur
    return out


def _prog(bars=24):
    """一份全 C 大调、无转调、无惊喜和弦的最小多段乐曲（3 声部）。"""
    half = bars // 2
    return {'calls': [
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': bars,
         'voices': 3, 'seed': 'narr-safe'}},
        {'script': 'set_sections', 'args': {'sections': [
            {'name': 'A', 'bars': half}, {'name': 'B', 'bars': bars - half}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'V', 'vi', 'IV']}},
        {'script': 'accompaniment', 'args': {'section': 'A',
         'pattern': 'block_chords', 'register': 'low', 'density': 1}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'mid',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'set_harmony', 'args': {'section': 'B',
                                           'progression': ['I', 'IV', 'V7', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'B',
         'pattern': 'broken_arpeggio', 'register': 'low', 'density': 1}},
        {'script': 'melody', 'args': {'section': 'B', 'register': 'mid',
                                      'rhythm': 'flowing', 'contour': 'wave'}},
        {'script': 'cadence', 'args': {'bars': 2, 'section': 'B'}},
        {'script': 'finalize', 'args': {}},
    ]}


def _narrative_off(st, args):
    st.narrative = {'skipped': 'TEST'}


# --------------------------------------------------------------------------
# 1) 吸附原语
# --------------------------------------------------------------------------

def test_snap_pc_pulls_chromatic_neighbor_back_into_key():
    """`_snap_pc` 把调外音吸附到最近的允许音级。"""
    c_major = {0, 2, 4, 5, 7, 9, 11}
    # Ab4 = 68 不在 C 大调 → 最近允许音级是 G4(67) 或 A4(69)
    assert ck._snap_pc(68, c_major) in (67, 69)
    # Db5 = 73 → C5(72) 或 D5(74)
    assert ck._snap_pc(73, c_major) in (72, 74)
    # 已在调内 → 原样
    for m in (60, 62, 64, 65, 67, 69, 71):
        assert ck._snap_pc(m, c_major) == m


def test_ornament_operator_inserts_semitone_neighbor():
    """`ornament` 的上邻音是 **+1 半音**（不是调内级进）—— 调外音的来源。

    用它钉住「为什么必须在叙事层做吸附」这个前提：算子本身按半音运算。
    """
    from narrative import Motif, transform_motif
    m = Motif([0, 4, 7], [2.0, 2.0, 2.0], 'm')
    o = transform_motif(m, 'ornament')
    assert 1 in o.intervals, f'加花应插入 +1 半音上邻音，实际 {o.intervals}'


# --------------------------------------------------------------------------
# 2) 动机变形后的旋律必须在调内
# --------------------------------------------------------------------------

def test_narrative_transformed_melody_stays_in_key():
    """叙事层重写高音声部后，全曲不得出现调外音。"""
    spec = run_program(_prog())
    n = spec.get('narrative') or {}
    assert any(a.get('applied') for a in (n.get('appearances') or [])), \
        '本用例要求至少一次「动机变形出场」被施加，否则是空转'
    for bar, toks in sorted(_split_bars(spec).items()):
        for t in toks:
            assert t in _C_MAJOR, f'第 {bar} 小节出现调外音 {t!r}（全曲 C 大调）'


# --------------------------------------------------------------------------
# 3) 终止式小节不被叙事层改写
# --------------------------------------------------------------------------

def test_narrative_never_rewrites_cadence_bars(monkeypatch):
    """`cadence(bars=2)` 写出的末两小节必须原封不动（含倒数第二小节的属七）。"""
    prog = _prog()
    with_narr = run_program(prog)
    monkeypatch.setattr(ck, '_s_narrative', _narrative_off)
    monkeypatch.setitem(ck.SCRIPT_IMPL, 'narrative', _narrative_off)
    without = run_program(prog)
    bw, bo = _split_bars(with_narr), _split_bars(without)
    last2 = sorted(bw)[-2:]
    assert last2 == [23, 24], f'用例前提：{last2}'
    for b in last2:
        assert bw[b] == bo[b], f'终止式小节 {b} 被叙事层改写了：{bw[b]} != {bo[b]}'


def test_cadence_bars_are_registered_on_state():
    """`cadence` 必须把终止式小节登记到 `st.cadence_bars`（叙事层据此避让）。"""
    seen = {}
    real = ck._s_narrative

    def spy(st, args):
        seen['cadence_bars'] = set(getattr(st, 'cadence_bars', set()) or set())
        return real(st, args)

    ck._s_narrative = spy
    ck.SCRIPT_IMPL['narrative'] = spy
    try:
        run_program(_prog(bars=24))
    finally:
        ck._s_narrative = real
        ck.SCRIPT_IMPL['narrative'] = real
    assert seen.get('cadence_bars') == {23, 24}, seen


# --------------------------------------------------------------------------
# 4) appearances 开关
# --------------------------------------------------------------------------

def test_narrative_appearances_false_disables_motif_rewrite():
    """`narrative(appearances=false)` → 不做动机变形出场（旋律保持原样）。"""
    prog = _prog()
    prog['calls'].insert(-1, {'script': 'narrative',
                              'args': {'appearances': False}})
    spec = run_program(prog)
    assert (spec.get('narrative') or {}).get('appearances') == [], \
        'appearances=false 时不应有计划任何变形出场'
    # 对照：默认（不显式调用）会自动补一层并计划变形出场
    default_apps = (run_program(_prog()).get('narrative') or {}).get('appearances')
    assert default_apps, '默认路径应当有计划变形出场（否则开关无意义）'


def test_narrative_appearances_false_keeps_melody_intact(monkeypatch):
    """关掉 appearances 后，高音声部应等于「完全不跑叙事层」的那个版本。"""
    base = _prog()
    off = _prog()
    off['calls'].insert(-1, {'script': 'narrative',
                             'args': {'appearances': False, 'surprises': 0,
                                      'silence': False}})
    with_off = run_program(off)
    monkeypatch.setattr(ck, '_s_narrative', _narrative_off)
    monkeypatch.setitem(ck.SCRIPT_IMPL, 'narrative', _narrative_off)
    no_narr = run_program(base)

    def _notes(spec):
        # 只比音高与时值：演奏法记号由叙事层另行施加（与 appearances 无关）
        return [(e[0], str(e[1])) for e in (spec['parts'][0].get('events') or [])]

    assert _notes(with_off) == _notes(no_narr), \
        'appearances=false + 无惊喜/无留白时，旋律应与「不跑叙事层」逐音相同'
