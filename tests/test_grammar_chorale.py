# -*- coding: utf-8 -*-
"""可解释众赞歌语法：分析 → 学习 → 规则脚本 → 确定性作曲 → 硬规则 0 违例 → 内核/命令接入。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond, detail=''):
    print(('  ✓ ' if cond else '  ✗ ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def test_check_rules():
    print('[1] 硬规则检查器')
    from score_learning.grammar.check import (bad_melodic, hidden_perfect_outer,
                                              parallel_perfect, spacing_ok)
    check('平行五度', parallel_perfect(67, 60, 69, 62))
    check('反向八度也算', parallel_perfect(67, 55, 72, 48))
    check('斜向（一声部不动）不算平行', not parallel_perfect(69, 50, 69, 62))
    check('同度允许', spacing_ok([67, 67, 60, 48]))
    check('交叉不允许', not spacing_ok([60, 64, 55, 48]))
    check('三全音跳进', bad_melodic(60, 66))
    check('隐伏八度（女高音跳进）', hidden_perfect_outer(64, 48, 72, 60))
    check('女高音级进进入八度可以', not hidden_perfect_outer(71, 55, 72, 60))


def test_theory():
    print('[2] 乐理工具')
    from score_learning.grammar.theory import chord_tone_names, recognize_chord, roman
    ch, _ = recognize_chord({7: 2.0, 11: 1.0, 2: 1.0, 5: 1.0}, 7, 'major')
    check('识别 V7', ch == (7, '7', 0), str(ch))
    check('罗马数字 V7', roman((7, '7', 0), 'major') == 'V7')
    check('副属 V/V', roman((2, 'M', 0), 'major') == 'V/V')
    names = chord_tone_names((2, 'M', 0), 'G', 'major')
    check('G 大调 V/V 拼写含 C#', 'C#' in names.values(), str(names))


def _script():
    from score_learning.grammar import script as gs
    return gs.load()


def test_script():
    print('[3] 规则脚本')
    from score_learning.grammar import script as gs
    check('grammar.yaml 存在', os.path.isfile(gs.DEFAULT_YAML))
    check('grammar.md 存在', os.path.isfile(gs.DEFAULT_MD))
    sc = _script()
    for k in ('vocab', 'harmony', 'cadences', 'form', 'melody', 'voicing', 'bass', 'ornament',
              'hard_rules', 'evaluation'):
        check(f'含 {k}', k in sc)
    ev = sc['evaluation']['major']
    check('位置条件降低困惑度', ev['perplexity_script'] < ev['perplexity_no_position'],
          f"{ev['perplexity_script']} vs {ev['perplexity_no_position']}")
    ids = [r['id'] for lv in sc['harmony']['major'].values() for r in lv]
    check('规则编号唯一', len(ids) == len(set(ids)))
    md = open(gs.DEFAULT_MD, encoding='utf-8').read()
    check('说明文档含终止公式章节', '终止公式' in md)


def test_compose():
    print('[4] 确定性作曲')
    from score_learning.grammar.compose import compose
    sc = _script()
    for mode, key in (('major', 1), ('minor', -2)):
        r1 = compose(sc, key, mode, None, 'test')
        r2 = compose(sc, key, mode, None, 'test')
        check(f'{mode} 零违例', not r1['violations'], str(r1['violations'][:2]))
        check(f'{mode} 同种子逐音符一致', r1['spec']['parts'] == r2['spec']['parts'])
        last = r1['beats'][-1]
        check(f'{mode} 末拍为主和弦', last['chord'].startswith('0:'), last['chord'])
        check(f'{mode} 每拍有规则编号', all(b['rule'] for b in r1['beats']))
        check(f'{mode} 句终规划可追溯', all(p['plan_rule'] for p in r1['phrases']))
    r = compose(sc, 0, 'major', None, 'bars', total_bars=13)
    check('total_bars=13 恰好填满', r['spec']['bars'] == 13, str(r['spec']['bars']))
    from llm_compose import spec_to_music21
    r = compose(sc, -2, 'minor', 6, '1')
    want = {p[0] for part in r['spec']['parts'] for p in part['events'] if p[0]}
    got = {n.nameWithOctave for p in spec_to_music21(r['spec']).parts for n in p.flatten().notes}
    check('降号小调里副属的升号拼写不被调号改写（C# 不变 D-）', want == got,
          str(sorted(got - want))[:80])


def test_kernel_and_command():
    print('[5] 内核脚本 + 斜杠命令')
    from compose_kernel import run_program, SCRIPT_IMPL, kernel_docs
    check('chorale_grammar 已注册', 'chorale_grammar' in SCRIPT_IMPL)
    check('文档含 chorale_grammar', 'chorale_grammar' in kernel_docs())
    prog = {'calls': [
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 2, 'mode': 'major',
                                         'meter': '4/4', 'tempo': 66, 'bars': 12, 'voices': 4,
                                         'instruments': ['soprano', 'alto', 'tenor', 'bass'],
                                         'seed': 'k'}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 12}]}},
        {'script': 'chorale_grammar', 'args': {}},
        {'script': 'finalize', 'args': {}}]}
    spec = run_program(prog)
    check('12 小节 4 声部', spec['bars'] == 12 and len(spec['parts']) == 4)
    check('次中音用高音八度谱号', spec['parts'][2]['clef'] == 'treble8vb')
    check('spec 带语法追溯', bool(spec.get('grammar', {}).get('phrases')))
    check('有延长记号', any(len(e) > 2 and e[2] == 'fermata' for e in spec['parts'][0]['events']))
    from llm_compose import ComposeSession, compose_from_text, _strip_unrequested_grammar
    session = ComposeSession()
    score, spec2, note = compose_from_text(session, '/compose-grammar minor 10 seed=x')
    check('/compose-grammar 出谱', score is not None and spec2['mode'] == 'minor')
    check('/compose-grammar 说明含乐句', '第1句' in note, note[:80])
    p2, msg = _strip_unrequested_grammar(prog, {'user_brief': '雨夜钢琴'})
    check('brief 未提众赞歌时摘除', not any(c['script'] == 'chorale_grammar' for c in p2['calls']))
    p3, _ = _strip_unrequested_grammar(prog, {'user_brief': '一首巴赫风格的众赞歌'})
    check('brief 提到众赞歌时保留', any(c['script'] == 'chorale_grammar' for c in p3['calls']))


def test_pickup():
    print('[6] 弱起乐句')
    import tempfile
    from music21 import converter, stream
    from score_learning.grammar.compose import compose
    from score_learning.grammar.analyze import analyze_chorale
    from llm_compose import spec_to_music21, m21_to_score_spec
    sc = _script()
    lay = sc['form'].get('layout') or {}
    check('学到弱起比例且弱起占多数', lay.get('pickup', 0) > 0.5, str(lay))
    check('学到弱起乐句长度', bool(sc['form'].get('pickup_phrase_slots')))
    ql = {'quarter': 1.0, 'half': 2.0, 'eighth': 0.5}
    for mode, key in (('major', 1), ('minor', -2)):
        r1 = compose(sc, key, mode, None, 'pk', total_bars=10, layout='pickup')
        r2 = compose(sc, key, mode, None, 'pk', total_bars=10, layout='pickup')
        check(f'{mode} 弱起零违例', not r1['violations'], str(r1['violations'][:2]))
        check(f'{mode} 弱起同种子一致', r1['spec']['parts'] == r2['spec']['parts'])
        check(f'{mode} 计划记录弱起', r1['plan']['layout'] == 'pickup')
        sp = r1['spec']
        check(f'{mode} spec 带 pickup_ql=1', sp.get('pickup_ql') == 1.0)
        check(f'{mode} 总时值 = 1 + 4×小节数',
              all(sum(ql[e[1]] for e in p['events']) == 41.0 for p in sp['parts']))
        b0 = r1['beats'][0]
        check(f'{mode} 首拍是第 0 小节第 4 拍', (b0['bar'], b0['beat']) == (0, 4))
        cads = [i for i, b in enumerate(r1['beats']) if b['cadence']]
        check(f'{mode} 句终都落第 3 拍', all(r1['beats'][i]['beat'] == 3 for i in cads))
        check(f'{mode} 中间终止后紧跟下一句第 4 拍弱起',
              all(r1['beats'][i + 1]['beat'] == 4 and r1['beats'][i + 1]['phrase_start']
                  for i in cads[:-1]))
        sop = sp['parts'][0]['events']
        ferm = [e for e in sop if len(e) > 2 and e[2] == 'fermata']
        check(f'{mode} 中间终止四分音符、末终止二分音符',
              all(e[1] == 'quarter' for e in ferm[:-1]) and ferm[-1][1] == 'half')
    r = compose(sc, 1, 'major', None, 'pk', total_bars=10, layout='pickup')
    path = os.path.join(tempfile.mkdtemp(), 'pk.musicxml')
    spec_to_music21(r['spec']).write('musicxml', fp=path)
    ms = list(converter.parse(path).parts[0].getElementsByClass(stream.Measure))
    check('MusicXML 第一小节 paddingLeft=3、编号 0',
          ms[0].paddingLeft == 3.0 and ms[0].number == 0, f'{ms[0].paddingLeft} {ms[0].number}')
    check('MusicXML 共 1 + 10 小节，第 1 小节完整',
          len(ms) == 11 and ms[1].duration.quarterLength == 4.0)
    check('MusicXML 弱起小节 implicit="yes"', 'implicit="yes"' in open(path, encoding='utf-8').read())
    a = analyze_chorale(path)
    check('分析器读回首拍位置 3.0', a['beats'][0]['pos'] == 3.0, str(a['beats'][0]['pos']))
    check('分析器读回乐句数与计划一致', len(a['phrases']) == r['plan']['n'],
          f"{len(a['phrases'])} vs {r['plan']['n']}")
    back = m21_to_score_spec(converter.parse(path))
    check('MusicXML → spec 识别弱起', back.get('pickup_ql') == 1.0 and back['bars'] == 10,
          f"{back.get('pickup_ql')} {back['bars']}")
    rd = compose(sc, 1, 'major', None, 'pk', total_bars=10, layout='downbeat')
    check('强拍起不带 pickup_ql', 'pickup_ql' not in rd['spec'] and not rd['violations'])
    md = list(spec_to_music21(rd['spec']).parts[0].getElementsByClass(stream.Measure))
    check('旧 spec（无 pickup_ql）第一小节完整、编号 1',
          md[0].number == 1 and md[0].paddingLeft == 0 and len(md) == 10)

    from compose_kernel import run_program
    prog = {'calls': [
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 2, 'mode': 'major',
                                         'meter': '4/4', 'tempo': 66, 'bars': 8, 'voices': 4,
                                         'instruments': ['soprano', 'alto', 'tenor', 'bass'],
                                         'seed': 'k'}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'chorale_grammar', 'args': {'layout': 'pickup'}},
        {'script': 'finalize', 'args': {}}]}
    spec = run_program(prog)
    check('内核弱起 spec 带 pickup_ql=1', spec.get('pickup_ql') == 1.0)
    check('内核弱起追溯记录 layout', spec['grammar'].get('layout') == 'pickup')
    check('内核弱起总时值 33 拍',
          all(sum(ql[e[1]] for e in p['events']) == 33.0 for p in spec['parts']))
    km = list(spec_to_music21(spec).parts[0].getElementsByClass(stream.Measure))
    check('内核弱起导出第 0 小节', km[0].number == 0 and km[0].paddingLeft == 3.0)
    from llm_compose import ComposeSession, compose_from_text
    _s, sp2, note = compose_from_text(ComposeSession(), '/compose-grammar 8 弱起 seed=p')
    check('/compose-grammar 弱起', sp2.get('pickup_ql') == 1.0 and '弱起' in note)
    _s, sp3, _n = compose_from_text(ComposeSession(), '/compose-grammar 8 强拍起 seed=p')
    check('/compose-grammar 强拍起', 'pickup_ql' not in sp3)


def test_pickup_report():
    print('[7] 弱起曲的分析报告与结构校验')
    from llm_compose import (ComposeSession, compose_from_text, spec_to_music21,
                             validate_score_spec)
    from score_studio import _auto_problems, analyze_composition, m21_to_model
    _s, spec, _n = compose_from_text(ComposeSession(), '/compose-grammar minor 12 弱起 seed=7')
    check('未设速度时用众赞歌默认 66', spec['tempo'] == 66, str(spec['tempo']))
    check('未设调号时小调默认 1 个降号', spec['key_sharps'] == -1, str(spec['key_sharps']))
    crit = [i for i in validate_score_spec(spec) if i['level'] == 'CRITICAL']
    check('结构校验不把弱起算成时值错误', not crit, str(crit[:1]))
    model = m21_to_model(spec_to_music21(spec), '')
    checks = analyze_composition(model, spec)
    bad = [c for c in checks if c[0] == '节奏完整性' and c[1].startswith('❌')]
    check('节奏完整性无 ❌', not bad, str(bad[:1]))
    key = next(c for c in checks if c[0] == '调号一致性')
    check('调号提示用 spec 的小调名', 'D 小调' in key[2], key[2])
    check('小调导音不算变化音', 'C#' not in key[2], key[2])
    rows = _auto_problems({}, spec, model)
    bars_row = next(r for r in rows if r[0] == '计划小节↔实际小节')
    check('计划小节与实际小节一致（不计弱起小节）', bars_row[1].startswith('🟢'), bars_row[2])


if __name__ == '__main__':
    test_check_rules()
    test_theory()
    test_script()
    test_compose()
    test_kernel_and_command()
    test_pickup()
    test_pickup_report()
    print(f'\n{"全部通过" if not FAILS else f"失败 {len(FAILS)} 项：" + "，".join(FAILS)}')
    sys.exit(1 if FAILS else 0)
