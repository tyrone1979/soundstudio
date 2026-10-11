# -*- coding: utf-8 -*-
"""「在乐谱上选中音符/小节 → 用提示词重写」的验收测试。

覆盖三层：
  A. 选中小节重写（确定性路径，无 LLM Key）——提示词 → 只改选中小节的和声/旋律，
     其余小节逐音符不变、每小节时值之和恒等于拍号容量（不乱位移小节线）。
  B. 选中符号重写（确定性路径）——提示词 → 只改选中音符的音高/八度/半音/时值/休止。
  C. 路由——有 Key 走 LLM 选区重写、无 Key（本地/测试）走确定性重写，
     且「同时有 measures 与 symbols」时符号优先。
  D. 提示词词法——和弦符号 / 罗马数字（含品质后缀）/ 音名 / 级数 / 小节引用 / 拍号
     的抽取互不串味（这是「选中小节改和弦却顺手改了旋律」那类 bug 的回归）。
  E. 附点时值基础设施——dotted_* 在渲染层与校验层都被认得，且不污染自动生成路径。

运行：python -m pytest tests/test_selection_rewrite.py -q
"""

import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import llm_compose as L                                    # noqa: E402
from music21 import pitch as m21pitch                      # noqa: E402


# --------------------------------------------------------------------------
# 夹具与工具
# --------------------------------------------------------------------------

def _base_spec():
    """一份最小的可编辑乐谱（复用 /demo 的确定性小曲）。"""
    return L.demo_spec(L.ComposeSession())


_BASE = _base_spec()
_BARS = int(_BASE['bars'])
_TS = str(_BASE['time_signature'])
_BAR_QL = L._ql_per_bar_full(_TS)
_MEL, _BASS = L._detect_voices(_BASE)


def _spec():
    return copy.deepcopy(_BASE)


def _bars(part):
    return L._split_events_to_bars(part['events'], _BARS, _TS)


def _part_ql(part):
    return round(sum(L._dur_ql(e[1]) for e in part['events']), 6)


def _bar_totals(part):
    return [round(sum(L._dur_ql(e[1]) for e in b), 6) for b in _bars(part)]


def _pcs(names):
    return sorted({m21pitch.Pitch(str(n)).pitchClass for n in names})


def _assert_invariants(spec):
    """整曲不变式：声部总时值不变 + 每小节时值之和恒等于拍号容量。"""
    for i, p in enumerate(spec['parts']):
        assert _part_ql(p) == _part_ql(_BASE['parts'][i]), \
            f'声部{i} 总时值被改变：{_part_ql(p)} != {_part_ql(_BASE["parts"][i])}'
        assert _bar_totals(p) == [_BAR_QL] * _BARS, \
            f'声部{i} 小节时值之和偏离拍号容量：{_bar_totals(p)}'


def _assert_other_bars_intact(spec, keep):
    """除 keep（0-based 小节集合）外，所有小节的音符逐条一致。"""
    for i, p in enumerate(_BASE['parts']):
        base_bars = _bars(p)
        now_bars = _bars(spec['parts'][i])
        for m in range(_BARS):
            if m in keep:
                continue
            assert now_bars[m] == base_bars[m], \
                f'声部{i} 第{m + 1}小节被意外改动：{now_bars[m]} != {base_bars[m]}'


def _edit_measures(prompt, measures):
    s = L.ComposeSession()
    s.score_spec = _spec()
    return L._deterministic_edit_measures(
        s, prompt, {'measures': list(measures), 'symbols': []})


def _edit_symbol(prompt, symbols):
    s = L.ComposeSession()
    s.score_spec = _spec()
    return L._deterministic_edit_symbols(
        s, prompt, {'measures': [], 'symbols': list(symbols)})


def _sym(si, mi, ni, pitch=None, duration=None):
    return {'staff_idx': si, 'measure_idx': mi, 'note_idx': ni,
            'pitch': pitch, 'duration': duration}


# ==========================================================================
# D. 提示词词法抽取（串味回归）
# ==========================================================================

def test_extract_chord_symbol_not_note_name():
    """'F#5'/'C4' 是音名，不能被当成和弦 F / C。"""
    assert L._extract_chord_symbols('改成 F#5') == []
    assert L._extract_chord_symbols('旋律写成 C4 E4 G4') == []
    assert L._extract_chord_symbols('改成 Dm7 和弦') == ['Dm7']
    assert L._extract_chord_symbols('第3小节改成 G7') == ['G7']


def test_extract_chord_symbol_not_english_word():
    """英文词首字母（Andante 的 A）不算和弦。"""
    assert L._extract_chord_symbols('用 Andante 的速度') == []


def test_extract_roman_keeps_quality_suffix():
    """罗马数字的品质后缀必须保留：ii7 掉了 7 就退化成三和弦。"""
    assert L._extract_roman('和声用 ii7 V7 Imaj7') == ['ii7', 'V7', 'Imaj7']
    assert L._extract_roman('用 bII 那不勒斯和弦') == ['bII']
    assert L._extract_roman('vii°7 解决到 I') == ['vii°7', 'I']


def test_extract_melody_tokens_ignores_chord_and_bar_refs():
    """和弦符号里的 7、小节引用里的 3 都不是音阶级数。"""
    assert L._extract_melody_tokens('改成 Dm7 和弦') == []
    assert L._extract_melody_tokens('第3小节改成 G7') == []
    assert L._extract_melody_tokens('拍号 4/4 不动') == []
    assert L._extract_melody_tokens('旋律写成 C4 E4 G4') == \
        [('abs', 'C4'), ('abs', 'E4'), ('abs', 'G4')]
    assert L._extract_melody_tokens('用和弦音 ^1 ^5 ^7') == \
        [('chord', 1), ('chord', 5), ('chord', 7)]
    assert L._extract_melody_tokens('旋律用级数 5 5 1 3') == \
        [('deg', 5), ('deg', 5), ('deg', 1), ('deg', 3)]


def test_duration_word_longest_match_wins():
    """「附点八分音符」不能被「八分音符」抢先命中而丢掉附点。"""
    assert L._first_duration_word('改成附点八分音符节奏') == '附点八分音符'
    assert L._parse_duration_word('附点八分音符') == 'dotted_eighth'
    assert L._parse_duration_word('附点四分音符') == 'dotted_quarter'
    assert L._parse_duration_word('二分音符') == 'half'
    assert L._parse_duration_word('没有时值词') is None


# ==========================================================================
# A. 选中小节重写
# ==========================================================================

def test_measure_chord_symbol_rewrite():
    """「把这个小节改成 Dm7 和弦」→ 该小节和声为 Dm7 四音；其余小节不变。"""
    _, spec, note = _edit_measures('把这个小节改成 Dm7 和弦', [2])
    assert _pcs(spec['harmony'][2]) == _pcs(['D', 'F', 'A', 'C'])
    assert '和声' in note
    _assert_other_bars_intact(spec, keep={2})
    _assert_invariants(spec)


def test_measure_chord_rewrite_does_not_touch_melody():
    """纯和声指令不得顺手改旋律（和弦符号里的 7 曾被误当成音阶级数）。"""
    _, spec, note = _edit_measures('把这个小节改成 Dm7 和弦', [2])
    assert '旋律' not in note
    assert _bars(spec['parts'][_MEL])[2] == _bars(_BASE['parts'][_MEL])[2]


def test_measure_roman_progression_sevenths():
    """罗马数字进行（含 7 后缀）逐小节铺开，且七和弦是四音而非三和弦。"""
    bars = [1, 2, 3]
    _, spec, note = _edit_measures('和声用 ii7 V7 Imaj7', bars)
    assert '和声' in note
    for m in bars:
        assert len(spec['harmony'][m]) == 4, \
            f'第{m + 1}小节应为七和弦（4 音），实际 {spec["harmony"][m]}'
    _assert_invariants(spec)


def test_measure_melody_scale_run_up():
    """「旋律上行音阶」→ 旋律首音到末音按调内级数上行。"""
    _, spec, _ = _edit_measures('旋律上行音阶', [0])
    notes = [e[0] for e in _bars(spec['parts'][_MEL])[0] if e[0]]
    assert len(notes) >= 4
    midis = [m21pitch.Pitch(n).midi for n in notes[:4]]
    assert midis == sorted(midis) and len(set(midis)) == 4, f'首四音未级进上行：{notes}'
    _assert_other_bars_intact(spec, keep={0})
    _assert_invariants(spec)


def test_measure_melody_arpeggio_of_chord():
    """「用 C 和弦琶音」→ 旋律前三音 = C 和弦音（C-E-G）。"""
    _, spec, _ = _edit_measures('用 C 和弦琶音', [4])
    notes = [e[0] for e in _bars(spec['parts'][_MEL])[4] if e[0]]
    assert _pcs(notes[:3]) == _pcs(['C', 'E', 'G']), f'琶音不对：{notes[:3]}'
    _assert_invariants(spec)


def test_measure_melody_absolute_pitches():
    """「旋律写成 C4 E4 G4」→ 原样落谱（绝对音名不被抬八度）。"""
    _, spec, _ = _edit_measures('旋律写成 C4 E4 G4', [3])
    notes = [e[0] for e in _bars(spec['parts'][_MEL])[3] if e[0]]
    assert notes == ['C4', 'E4', 'G4'], f'实际 {notes}'
    _assert_other_bars_intact(spec, keep={3})
    _assert_invariants(spec)


def test_measure_rhythm_only_prompt():
    """「改成附点八分音符节奏」→ 旋律使用 dotted_eighth 且小节仍填满。"""
    _, spec, note = _edit_measures('改成附点八分音符节奏', [5])
    assert '旋律' in note
    durs = [e[1] for e in _bars(spec['parts'][_MEL])[5]]
    assert 'dotted_eighth' in durs, f'实际时值 {durs}'
    _assert_invariants(spec)


def test_measure_bass_voice_follows_new_harmony():
    """低声部随新和声改写，且只占一小节（曾错写成两小节的量导致全曲位移）。"""
    _, spec, _ = _edit_measures('把这个小节改成 Dm7 和弦', [1])
    bass_bar = _bars(spec['parts'][_BASS])[1]
    total = sum(L._dur_ql(e[1]) for e in bass_bar)
    assert abs(total - _BAR_QL) < 1e-6, f'低声部小节容量 {total} != {_BAR_QL}'
    root_pcs = {m21pitch.Pitch(str(e[0])).pitchClass for e in bass_bar if e[0]}
    assert m21pitch.Pitch('D').pitchClass in root_pcs, f'低声部未用 D 根音：{bass_bar}'


def test_measure_edit_renders_to_8_bars():
    """改完仍能渲染成 8 小节的乐谱（MusicXML 结构不被破坏）。"""
    _, spec, _ = _edit_measures('把这个小节改成 Dm7 和弦', [2])
    score = L.spec_to_music21(spec)
    assert len(score.parts[0].getElementsByClass('Measure')) == _BARS
    total = sum(n.duration.quarterLength for n in score.parts[0].recurse().notesAndRests)
    assert abs(total - _BARS * _BAR_QL) < 1e-6


def test_measure_empty_selection_raises():
    s = L.ComposeSession()
    s.score_spec = _spec()
    with pytest.raises(ValueError):
        L._deterministic_edit_measures(s, '改成 Dm7 和弦',
                                       {'measures': [], 'symbols': []})


def test_measure_out_of_range_raises():
    s = L.ComposeSession()
    s.score_spec = _spec()
    with pytest.raises(ValueError):
        L._deterministic_edit_measures(s, '改成 Dm7 和弦',
                                       {'measures': [99], 'symbols': []})


def test_measure_unrecognized_prompt_is_noop():
    _, spec, note = _edit_measures('你好', [1])
    assert '未能' in note
    _assert_other_bars_intact(spec, keep=set())
    _assert_invariants(spec)


# ==========================================================================
# B. 选中符号重写
# ==========================================================================

def test_symbol_set_absolute_pitch():
    """「改成 C5」→ 该音符音高变为 C5，其余音符不变。"""
    _, spec, note = _edit_symbol('改成 C5', [_sym(_MEL, 0, 0)])
    assert '更新 1 个符号' in note
    bar = _bars(spec['parts'][_MEL])[0]
    assert bar[0][0] == 'C5'
    assert bar[1:] == _bars(_BASE['parts'][_MEL])[0][1:]
    _assert_other_bars_intact(spec, keep={0})
    _assert_invariants(spec)


def test_symbol_transpose_octave_up():
    _, spec, _ = _edit_symbol('升高八度', [_sym(_MEL, 0, 1)])
    base = _bars(_BASE['parts'][_MEL])[0][1][0]
    now = _bars(spec['parts'][_MEL])[0][1][0]
    assert m21pitch.Pitch(now).midi - m21pitch.Pitch(base).midi == 12
    _assert_invariants(spec)


def test_symbol_transpose_octave_down():
    _, spec, _ = _edit_symbol('降低八度', [_sym(_MEL, 0, 1)])
    base = _bars(_BASE['parts'][_MEL])[0][1][0]
    now = _bars(spec['parts'][_MEL])[0][1][0]
    assert m21pitch.Pitch(now).midi - m21pitch.Pitch(base).midi == -12
    _assert_invariants(spec)


def test_symbol_transpose_semitone_up():
    _, spec, _ = _edit_symbol('升高半音', [_sym(_MEL, 0, 1)])
    base = _bars(_BASE['parts'][_MEL])[0][1][0]
    now = _bars(spec['parts'][_MEL])[0][1][0]
    delta = (m21pitch.Pitch(now).pitchClass - m21pitch.Pitch(base).pitchClass) % 12
    assert delta == 1, f'{base} -> {now}'
    _assert_invariants(spec)


def test_symbol_set_duration():
    _, spec, _ = _edit_symbol('改成二分音符', [_sym(_MEL, 0, 1)])
    assert _bars(spec['parts'][_MEL])[0][1][1] == 'half'
    _assert_invariants(spec)


def test_symbol_set_dotted_duration():
    """附点时值：谱面时值为 1.5，且小节仍恰好填满（裁尾/补休止不留缺口）。"""
    _, spec, _ = _edit_symbol('改成附点四分音符', [_sym(_MEL, 0, 1)])
    assert _bars(spec['parts'][_MEL])[0][1][1] == 'dotted_quarter'
    score = L.spec_to_music21(spec)
    qls = [n.duration.quarterLength for n in score.parts[_MEL].measure(1).notesAndRests]
    assert any(abs(q - 1.5) < 1e-9 for q in qls), f'未渲染出附点四分音符：{qls}'
    _assert_invariants(spec)


def test_symbol_to_rest():
    _, spec, _ = _edit_symbol('改成休止符', [_sym(_MEL, 0, 1)])
    assert _bars(spec['parts'][_MEL])[0][1][0] is None
    _assert_invariants(spec)


def test_symbol_duration_growth_does_not_shift_later_bars():
    """把首音拉长到全音符会撑爆小节：必须裁尾保持容量，不能让后面全部位移。"""
    base0 = _bars(_BASE['parts'][_MEL])[0][0][0]
    _, spec, _ = _edit_symbol('改成全音符', [_sym(_MEL, 0, 0)])
    bar0 = _bars(spec['parts'][_MEL])[0]
    assert bar0[0] == [base0, 'whole'], bar0
    assert abs(sum(L._dur_ql(e[1]) for e in bar0) - _BAR_QL) < 1e-6
    _assert_other_bars_intact(spec, keep={0})
    _assert_invariants(spec)


def test_symbol_edit_preserves_marks_elsewhere():
    """改一个音符不能把别处的演奏法记号（连音线/重音）抹掉。"""
    s = L.ComposeSession()
    spec0 = _spec()
    bl = L._split_events_to_bars(spec0['parts'][_MEL]['events'], _BARS, _TS)
    bl[5][0] = [bl[5][0][0], bl[5][0][1], 'accent']
    spec0['parts'][_MEL]['events'] = L._flatten_bars(bl)
    s.score_spec = spec0
    _, spec, _ = L._deterministic_edit_symbols(
        s, '改成 C5', {'measures': [], 'symbols': [_sym(_MEL, 0, 0)]})
    bar5 = _bars(spec['parts'][_MEL])[5]
    assert bar5[0][2:] == ['accent'], f'记号丢失：{bar5[0]}'


def test_symbol_out_of_range_is_noop():
    _, spec, note = _edit_symbol('改成 C5', [_sym(_MEL, 0, 99)])
    assert '未能' in note
    _assert_invariants(spec)


def test_symbol_unrecognized_prompt_is_noop():
    _, spec, note = _edit_symbol('你好', [_sym(_MEL, 0, 1)])
    assert '未能' in note
    _assert_invariants(spec)


# ==========================================================================
# C. 路由（compose_pipeline）
# ==========================================================================

_MEASURE_SEL = {'measures': [2], 'symbols': []}
_SYMBOL_SEL = {'measures': [], 'symbols': [_sym(0, 0, 0)]}


def test_routing_without_llm_uses_deterministic_measures(monkeypatch):
    monkeypatch.setattr(L, 'load_llm_config', lambda *a, **k: {})
    s = L.ComposeSession()
    s.score_spec = _spec()
    _score, _spec_out, note = L.compose_pipeline(
        s, '把这个小节改成 Dm7 和弦', selection=_MEASURE_SEL)
    assert '（确定性）' in note, note


def test_routing_without_llm_uses_deterministic_symbols(monkeypatch):
    monkeypatch.setattr(L, 'load_llm_config', lambda *a, **k: {})
    s = L.ComposeSession()
    s.score_spec = _spec()
    _score, _spec_out, note = L.compose_pipeline(
        s, '升高八度', selection=_SYMBOL_SEL)
    assert '（确定性）' in note, note


def test_routing_with_llm_dispatches_to_llm_fn(monkeypatch):
    """有 Key 时应走 LLM 选区重写（而不是确定性路径）。"""
    called = {}

    def _fake(session, user_text, selection, m21_score=None):
        called['llm'] = True
        return None, {}, 'LLM-PATH'

    monkeypatch.setattr(L, 'load_llm_config', lambda *a, **k: {'api_key': 'x'})
    monkeypatch.setattr(L, '_compose_selection_measures', _fake)
    s = L.ComposeSession()
    s.score_spec = _spec()
    _score, _spec_out, note = L.compose_pipeline(
        s, '把这个小节改成 Dm7 和弦', selection=_MEASURE_SEL)
    assert called.get('llm') and note == 'LLM-PATH'


def test_routing_symbols_take_priority_over_measures(monkeypatch):
    """同时有 measures 与 symbols 时，按符号（更精确的选区）处理。"""
    monkeypatch.setattr(L, 'load_llm_config', lambda *a, **k: {})
    s = L.ComposeSession()
    s.score_spec = _spec()
    both = {'measures': [0], 'symbols': [_sym(_MEL, 0, 0)]}
    _score, _spec_out, note = L.compose_pipeline(s, '改成 C5', selection=both)
    assert '符号' in note, note


def test_routing_full_score_critique_ignores_selection(monkeypatch):
    """整曲批评（结构化 brief / 整曲重写）必须忽略残留选区，走整曲路径。"""
    monkeypatch.setattr(L, 'load_llm_config', lambda *a, **k: {})
    assert L.skill_lib.is_full_score_revision_request('整曲重写，按建议逐条落实') is True
    long_body = (
        '全曲第1–32小节存在结构性问题，请按下列高优先级条目逐条修改：\n'
        + '\n'.join(
            f'{i}. 修改：第{i * 2}小节旋律过于平铺缺少跳进，需要加入跳进并反向级进解决；'
            f'位置：第{i * 2}小节' for i in range(1, 6)))
    assert len(long_body) >= 180
    assert L.skill_lib.is_full_score_revision_request(long_body) is True
    # 短促的选区指令不应被误判为整曲重写
    assert L.skill_lib.is_full_score_revision_request('把这个小节改成 Dm7 和弦') is False


# ==========================================================================
# D'. 选区描述（UI 提示）
# ==========================================================================

def test_describe_selection_measures_and_symbols():
    desc = L.describe_selection(
        {'measures': [2], 'symbols': [_sym(0, 0, 0, 'G4', 'quarter')]})
    assert '小节 3' in desc and '第1小节' in desc and 'G4' in desc
    assert L.describe_selection(None) == ''
    assert L.describe_selection({'measures': [], 'symbols': []}) == ''


# ==========================================================================
# E. 附点时值基础设施（渲染 + 校验），且不污染自动生成
# ==========================================================================

def test_dotted_duration_recognized_by_render_and_validator():
    spec = _spec()
    bl = L._split_events_to_bars(spec['parts'][_MEL]['events'], _BARS, _TS)
    bl[0][0] = [bl[0][0][0], 'dotted_quarter']
    bl[0] = L._fit_bar(bl[0], _TS)
    spec['parts'][_MEL]['events'] = L._flatten_bars(bl)

    score = L.spec_to_music21(spec)
    qls = [n.duration.quarterLength for n in score.parts[_MEL].measure(1).notesAndRests]
    assert any(abs(q - 1.5) < 1e-9 for q in qls), f'附点未被渲染：{qls}'

    bad = [i for i in L.validate_score_spec(spec) if '未知时值' in i['message']]
    assert not bad, f'附点时值被校验器判为未知：{bad}'


def test_dotted_durations_do_not_leak_into_auto_generation():
    """自动填充路径只能取基础时值——否则既有风格指纹会漂移。"""
    assert L._dur_ql('dotted_quarter') == 1.5
    assert L._fit_duration_name(1.5) == 'quarter'
    assert L._fit_duration_name(3.0) == 'half'
    assert L._fit_duration_name(0.75) == 'eighth'
    assert 'dotted_quarter' not in L.DUR_QL


def test_fit_bar_keeps_total_exact_for_odd_remainders():
    """补休止必须精确收口（含附点），不能留下小数缺口。"""
    for ts in ('4/4', '3/4', '2/4'):
        cap = L._ql_per_bar_full(ts)
        bar = [[None, 'quarter']]
        out = L._fit_bar(bar, ts)
        assert abs(sum(L._dur_ql(e[1]) for e in out) - cap) < 1e-9, (ts, out)


if __name__ == '__main__':
    pytest.main([__file__, '-q'])
