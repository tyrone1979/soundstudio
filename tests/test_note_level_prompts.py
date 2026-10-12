# -*- coding: utf-8 -*-
"""专业提示词的「全面命中测试」。

两套断言：
  A. 路由命中 —— 一批专业/音符级提示词经 resolve_skills(llm_available=True)
     全部落到 compose-full，且技术词（减七、半音、附点…）不被误判为风格包。
  B. 能力命中 —— 每条提示词对应「翻译器应产出的调用序列」（oracle），跑
     compose_kernel.run_program 后，断言谱面确实命中所要的音乐意图：
       第 N 小节为某和弦 / 旋律首音与走向 / 琶音 / 附点节奏 / 七和弦品质 /
       转调段音阶 / 减七解决 / 低声部半音经过音。

说明：测试环境无 LLM，故用「给定正确翻译」来验收「系统能否把提示词落到正确
音符级结果」——这是「专业提示词可以控制到音符级别」的端到端证明。
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import composition_skills as skill_lib          # noqa: E402
import compose_kernel as K                        # noqa: E402
from music21 import pitch as m21pitch            # noqa: E402


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _run(calls):
    return K.run_program({'calls': calls})


def _pc(name):
    try:
        return m21pitch.Pitch(str(name)).pitchClass
    except Exception:
        return None


def melody_notes(spec):
    return [e[0] for e in spec['parts'][0]['events'] if e[0] is not None]


def melody_durs(spec):
    return [e[1] for e in spec['parts'][0]['events'] if e[0] is not None]


def bass_notes(spec):
    bidx = len(spec['parts']) - 1
    return [e[0] for e in spec['parts'][bidx]['events'] if e[0] is not None]


def harmony_pcs(spec, bar):
    ch = spec['harmony'].get(bar, [])
    return {_pc(n) for n in ch}


# --------------------------------------------------------------------------
# A. 路由命中
# --------------------------------------------------------------------------

ROUTING_PROMPTS = [
    "写一首减七和弦解决的四声部圣咏",
    "弦乐四重奏风格的赋格，活泼的快板",
    "给我一段 ii-V-I 的爵士贝斯线条",
    "第3小节换成 Dm7 属和弦，旋律用 C-E-G 琶音",
    "第二声部用 C4 到 G4 的半音经过音",
    "附点八分音符节奏型的前奏",
    "B 段转到关系小调，用 i-iv-V7-i",
    "用七和弦写一段 ii-V-I 进行",
]


@pytest.mark.parametrize('prompt', ROUTING_PROMPTS)
def test_routing_hits_compose_full(prompt):
    ids, _ = skill_lib.resolve_skills(prompt, llm_available=True)
    assert ids == ['compose-full'], f'提示词「{prompt}」应只落 compose-full，实际 {ids}'


def test_technical_words_not_misrouted_when_llm_present():
    # 技术/作曲词在 LLM 可用时一律交 translator，不绑定风格包
    for p in ["减七和弦解决的四声部密集排列",
              "弦乐四重奏风格的赋格",
              "附点八分音符节奏型"]:
        ids, _ = skill_lib.resolve_skills(p, llm_available=True)
        assert ids == ['compose-full'], f'「{p}」不应误判风格，实际 {ids}'


def test_fallback_routing_still_works_without_llm():
    # 无 LLM 时回退 style_routing，至少不崩溃且主路径是 compose-full
    ids, _ = skill_lib.resolve_skills("给我一段 ii-V-I 的爵士贝斯线条",
                                      llm_available=False)
    assert 'compose-full' in ids


# --------------------------------------------------------------------------
# B. 能力命中（音符级）
# --------------------------------------------------------------------------

def test_bar_override_hits_exact_chord():
    """「第 3 小节用 Dm7」→ 第 3 小节和声为 D F A C。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {
            'section': 'A', 'progression': ['I', 'V7', 'I', 'V7', 'I', 'V7', 'I', 'V7'],
            'bar_overrides': {'3': 'Dm7'}}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'broken_arpeggio',
                                            'register': 'low', 'density': 1}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'mid',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    assert harmony_pcs(spec, 3) == {_pc('D'), _pc('F'), _pc('A'), _pc('C')}, \
        f"第3小节应为 Dm7，实际 {spec['harmony'][3]}"


def test_melody_absolute_pitch_stepwise():
    """「旋律从 C4 开始，级进上行到 G4」→ 首音 C4 且含 G4 上行片段。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'I', 'I', 'I', 'I', 'I', 'I', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'sustained',
                                            'register': 'low'}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'low', 'notes': [
            ['C4', 'quarter'], ['D4', 'quarter'], ['E4', 'quarter'], ['F4', 'quarter'],
            ['G4', 'quarter'], ['A4', 'quarter'], ['G4', 'quarter'], ['F4', 'quarter']]}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    notes = melody_notes(spec)
    assert notes[0] == 'C4', f"首音应为 C4，实际 {notes[0]}"
    assert 'G4' in notes, "应出现 G4（级进上行到 G4）"
    assert notes[:4] == ['C4', 'D4', 'E4', 'F4'], f"前4音应级进上行，实际 {notes[:4]}"


def test_melody_arpeggioCEG():
    """「用 C-E-G 的琶音作为旋律」→ 旋律前三个音为 C4 E4 G4。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'I', 'I', 'I', 'I', 'I', 'I', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'sustained',
                                            'register': 'low'}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'low', 'notes': [
            ['C4', 'eighth'], ['E4', 'eighth'], ['G4', 'eighth']]}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    notes = melody_notes(spec)
    assert notes[:3] == ['C4', 'E4', 'G4'], f"琶音头应为 C4 E4 G4，实际 {notes[:3]}"


def test_dotted_eighth_rhythm():
    """「附点八分音符节奏型」→ 旋律中出现 dotted_eighth 时值。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'I', 'I', 'I', 'I', 'I', 'I', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'sustained',
                                            'register': 'low'}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'mid', 'notes': [
            ['C4', 'dotted_eighth'], ['E4', 'eighth'], ['G4', 'quarter']]}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    assert 'dotted_eighth' in melody_durs(spec), \
        "旋律应含附点八分音符（dotted_eighth）"
    # 端到端：附点在**渲染层**也必须真正生效——渲染层曾不认 dotted_* 拼写，
    # 把它静默当成四分音符（时值与听感全错，但 spec 断言查不出来）。
    import llm_compose as _L
    _score = _L.spec_to_music21(spec)
    _qls = [n.duration.quarterLength
            for n in _score.parts[0].measure(1).notesAndRests]
    assert any(abs(q - 0.75) < 1e-9 for q in _qls), \
        f"附点八分音符未真正渲染（应为 0.75 拍）：{_qls}"


def test_ii_V_I_seventh_qualities():
    """「ii-V-I，用七和弦」→ ii7/V7/Imaj7 三和弦均带七音。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 110, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['ii7', 'V7', 'Imaj7', 'V7',
                                                           'ii7', 'V7', 'Imaj7', 'V7']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'block_chords',
                                            'register': 'low'}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'mid',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    assert harmony_pcs(spec, 1) == {_pc('D'), _pc('F'), _pc('A'), _pc('C')}, \
        f"ii7 应为 Dm7，实际 {spec['harmony'][1]}"
    assert harmony_pcs(spec, 2) == {_pc('G'), _pc('B'), _pc('D'), _pc('F')}, \
        f"V7 应为 G7，实际 {spec['harmony'][2]}"
    assert harmony_pcs(spec, 3) == {_pc('C'), _pc('E'), _pc('G'), _pc('B')}, \
        f"Imaj7 应为 Cmaj7，实际 {spec['harmony'][3]}"


def test_modulation_to_relative_minor():
    """「B 段转到关系小调」→ 第 9 小节起调号/调式为 c 小调，和声为 c 小三和弦。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 16}},
        {'script': 'set_sections', 'args': {'sections': [
            {'name': 'A', 'bars': 8},
            {'name': 'B', 'bars': 8, 'mode': 'minor'}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'I', 'I', 'I', 'I', 'I', 'I', 'I']}},
        {'script': 'set_harmony', 'args': {'section': 'B', 'key_sharps': -3,
                                           'mode': 'minor',
                                           'progression': ['i', 'iio', 'V7', 'i',
                                                           'vii°7', 'i', 'i', 'i']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'sustained',
                                            'register': 'low'}},
        {'script': 'accompaniment', 'args': {'section': 'B', 'pattern': 'broken_arpeggio',
                                            'register': 'low'}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'mid',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'melody', 'args': {'section': 'B', 'register': 'mid',
                                      'rhythm': 'fragmented', 'contour': 'wave'}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    # 第 9 小节应写出调号变化（关系小调 c 小调，3 降）
    assert [9, -3, 'minor'] in [list(k) for k in spec['key_changes']], \
        f"应在第9小节转 c 小调，实际 key_changes={spec['key_changes']}"
    # B 段首小节和声应为 c 小三和弦 {C, Eb, G}
    assert harmony_pcs(spec, 9) == {_pc('C'), _pc('Eb'), _pc('G')}, \
        f"B 段首小节应为 c 小三和弦，实际 {spec['harmony'][9]}"


def test_diminished_seventh_resolves_to_tonic():
    """「减七和弦解决到主和弦」→ 减七(4音)后接主和弦。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['vii°7', 'I', 'I', 'I',
                                                           'I', 'I', 'I', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'broken_arpeggio',
                                            'register': 'low'}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'mid',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    dim7 = harmony_pcs(spec, 1)
    assert len(dim7) == 4, f"vii°7 应为四音减七和弦，实际 {spec['harmony'][1]}"
    # 减七和弦音程为三个小三度堆叠：相邻音 pc 差 3（mod 12）
    s = sorted(dim7)
    steps = [(s[(i + 1) % 4] - s[i]) % 12 for i in range(4)]
    assert all(st == 3 for st in steps), f"减七应为三个小三度堆叠，实际 pc={s}"
    # 解决到主和弦 C 大三和弦
    assert harmony_pcs(spec, 2) == {_pc('C'), _pc('E'), _pc('G')}, \
        f"解决目标应为 C 大三和弦，实际 {spec['harmony'][2]}"


def test_bass_chromatic_passing_tones():
    """「第二声部用 C4 到 G4 的半音经过音」→ 低声部出现 C..G 的半音级进。"""
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'I', 'I', 'I', 'I', 'I', 'I', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'notes': [
            ['C4', 'quarter'], ['C#4', 'quarter'], ['D4', 'quarter'], ['D#4', 'quarter'],
            ['E4', 'quarter'], ['F4', 'quarter'], ['F#4', 'quarter'], ['G4', 'quarter']]}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'high',
                                      'rhythm': 'sparse', 'contour': 'arch'}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    pcs = [_pc(n) for n in bass_notes(spec)[:8]]
    assert pcs == [0, 1, 2, 3, 4, 5, 6, 7], \
        f"低声部应为 C..G 半音级进(pc 0..7)，实际 {pcs}"


def test_flat_prefixed_roman_not_misread_as_chord_symbol():
    """bII / #iv 等带前置临时记号的罗马数字必须判为罗马，而非和弦符号。

    回归点：_is_chord_symbol 早期实现会把 'b' 当成 B 音根音，误判成「B 大三和弦」
    并跳过风格契约。这里同时验证「分类函数」与「落谱结果」两层。
    """
    # 分类函数：bII 不是和弦符号；Dm7 是和弦符号
    assert K._is_chord_symbol('bII') is False, 'bII 应判为罗马数字'
    assert K._is_chord_symbol('#iv') is False, '#iv 应判为罗马数字'
    assert K._is_chord_symbol('Dm7') is True, 'Dm7 应判为和弦符号'
    assert K._is_chord_symbol('C#dim7') is True, 'C#dim7 应判为和弦符号'
    # 落谱：bII 在 C 大调应是 bII（降二级 = Db 大三和弦：Db/F/Ab），
    # 而非 B 大三和弦（B/D#/F#）。
    spec = _run([
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0,
         'mode': 'major', 'meter': '4/4', 'tempo': 100, 'bars': 8}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'bII', 'V7', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'block_chords'}},
        {'script': 'melody', 'args': {'section': 'A', 'register': 'high',
                                      'rhythm': 'sparse', 'contour': 'arch'}},
        {'script': 'narrative', 'args': {'surprises': 0}},
        {'script': 'finalize', 'args': {}},
    ])
    b2 = harmony_pcs(spec, 2)
    assert b2 == {_pc('Db'), _pc('F'), _pc('Ab')}, \
        f"bII 应译为 Db 大三和弦(pc {_pc('Db')},{_pc('F')},{_pc('Ab')})，实际 {b2}"


if __name__ == '__main__':
    pytest.main([__file__, '-q'])
