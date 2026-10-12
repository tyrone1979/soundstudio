"""谱面分析项的回归测试（调号一致性等）。

背景：analyze_composition 的「无转调」分支漏了按调内允许集合过滤，把用到的
所有自然音都当成「变化音」报出来——纯 C 大调的曲子也会报「C 大调下出现变化音
C, D, E, F, G, A, B」，且 ✅ 分支永远不可达。用例 #1 正是钉住这一点。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import score_studio as SS           # noqa: E402
from music21 import key, meter, note, stream  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f'  ✅ {name}')
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {detail}')


def _model_of(note_names, title='t'):
    """用一组音名构造一个单声部 4/4 谱面模型。"""
    s = stream.Score()
    p = stream.Part()
    p.append(meter.TimeSignature('4/4'))
    p.append(key.KeySignature(0))
    for nm in note_names:
        p.append(note.Note(nm))
    s.append(p)
    return SS.m21_to_model(s, title)


def _key_signature_check(score):
    for cat, st, desc in SS.analyze_composition(score, None):
        if cat == '调号一致性':
            return st, desc
    return None, ''


def test_diatonic_score_passes():
    """纯自然音的 C 大调谱不应报「变化音」。"""
    sc = _model_of(['C4', 'D4', 'E4', 'F4', 'G4', 'A4', 'B4', 'C5'])
    st, desc = _key_signature_check(sc)
    check('纯自然音不报变化音', st == '✅ 通过', f'得到 {st} | {desc}')


def test_only_real_chromatic_reported():
    """有变化音时只列真正调外的音，不能把自然音混进来。"""
    sc = _model_of(['C4', 'E4', 'F#4', 'G4', 'B4', 'A#4'])
    st, desc = _key_signature_check(sc)
    listed = desc.split('出现变化音')[-1].split('（')[0].strip()
    check('报告了变化音', st == '⚠️ 注意', f'{st} | {desc}')
    check('只含调外音 F#/A#', set(listed.split(', ')) == {'F#', 'A#'},
          f'实际列出: {listed}')
    check('未混入自然音', not any(x in listed.split(', ')
                                for x in ('C', 'D', 'E', 'G', 'A', 'B')),
          f'实际列出: {listed}')


def test_report_checks_all():
    print('\n=== 谱面分析项（analyze_composition）===')
    test_diatonic_score_passes()
    test_only_real_chromatic_reported()
    print(f'\n总计：✅ {_PASSED} / ❌ {_FAILED}')
    assert _FAILED == 0, f'{_FAILED} 项失败'
