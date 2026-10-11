# -*- coding: utf-8 -*-
"""第一类二声部对位：识别、生成、校验、管线接线。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music_score.models import ClefType
from music_score.two_part_species import (
    detect_two_part_species,
    generate_two_part_species,
    validate_two_part_species,
    build_two_part_species_score,
    species_score_to_spec,
    _is_perfect_fourth,
    _is_third_or_sixth,
    _is_octave,
)
from llm_compose import (
    ComposeSession, compose_from_text, _fugue_requested,
    _wants_piano_grand, _apply_species_program_constraints,
    _plan_indicates_species, _try_compose_two_part_species,
)


PROMPT = (
    '写一首2声部的曲子，上方声部用高音谱表，下方声部用低音谱表。'
    '符合声部规则，要求低音线条以级进为主跳进为辅。1小节一个音符。'
    '除了开头和结尾，中间只能用3度和6度，结尾最后一个音符反向进行到8度。'
    '高音声部和低音声部最高的音要错开——在不同的小节上。不能用纯4度。'
)


def check(name, cond, detail=''):
    status = 'OK' if cond else 'FAIL'
    print(f'  [{status}] {name}' + (f' — {detail}' if detail and not cond else ''))
    if not cond:
        raise AssertionError(name + (f': {detail}' if detail else ''))


def test_detect():
    print('[1] detect_two_part_species')
    hit = detect_two_part_species(PROMPT)
    check('命中用户提示词', hit is not None)
    check('强制大谱表', hit['grand_staff'] is True)
    short = '写一首2声部1比1对位的曲子'
    check('命中短提示 1比1对位', detect_two_part_species(short) is not None)
    check('命中一比一（汉字）', detect_two_part_species(
        '写一首2声部一比一对位的曲子') is not None)
    check('命中第一类对位', detect_two_part_species(
        '写一首二声部第一类对位') is not None)
    check('不误伤普通二声部二重奏', detect_two_part_species(
        '写一个2声部小提琴二重奏') is None)
    check('不误伤赋格', detect_two_part_species('写一首二声部赋格') is None)


def test_interval_helpers():
    print('[2] 音程助手')
    check('纯四度', _is_perfect_fourth(65, 60))  # F4–C4
    check('非纯四', not _is_perfect_fourth(64, 60))  # E4–C4 = M3
    check('大六', _is_third_or_sixth(69, 60))  # A4–C4
    check('八度', _is_octave(72, 60))
    check('双八度不算结尾八度', not _is_octave(84, 60))


def test_validate_rules():
    print('[3] validate 规则正反例')
    u, l, meta = generate_two_part_species(8, seed=3)
    tonic = int(meta.get('tonic_pc', 0))
    issues = validate_two_part_species(u, l, tonic_pc=tonic)
    check('生成器结果合法', issues == [], str(issues))

    # 开头：低音主音、高音主三和弦
    check('开头低音主音', l[0] % 12 == tonic)
    check('开头高音主三和弦', u[0] % 12 in {tonic, (tonic + 4) % 12, (tonic + 7) % 12})

    # 终止：导音→主音 / 二级↓主音
    check('高音结尾主音', u[-1] % 12 == tonic)
    check('高音倒数第二导音', u[-2] % 12 == (tonic + 11) % 12)
    check('高音上行级进', u[-1] - u[-2] == 1)
    check('低音结尾主音', l[-1] % 12 == tonic)
    check('低音倒数第二二级', l[-2] % 12 == (tonic + 2) % 12)
    check('低音下行级进', l[-1] - l[-2] == -2)
    check('开头结尾低音同音域', l[0] == l[-1])
    check('开头结尾高音同八度', u[0] // 12 == u[-1] // 12)

    # 跳进后未反向级进应报
    bad_u = list(u)
    bad_l = list(l)
    # 强制高音：跳进后同向再跳（绕过开头/结尾）
    if len(bad_u) >= 5:
        bad_u[1] = bad_u[0] + 5  # leap up
        bad_u[2] = bad_u[1] + 2  # same direction step — should fail recovery
        iss = validate_two_part_species(bad_u, bad_l, tonic_pc=None)
        check('检出跳进未反向级进', any('跳进后' in x or '反向级进' in x for x in iss),
              str(iss))

    # 相邻同音应报
    bad_u = list(u)
    bad_l = list(l)
    bad_u[1] = bad_u[0]
    iss = validate_two_part_species(bad_u, bad_l, tonic_pc=tonic)
    check('检出相邻同音', any('同音高' in x for x in iss), str(iss))

    # 开头非主音低音应报
    bad_u = list(u)
    bad_l = list(l)
    bad_l[0] = bad_l[0] + 2
    iss = validate_two_part_species(bad_u, bad_l, tonic_pc=tonic)
    check('检出开头低音非主音', any('第 1 小节低音' in x for x in iss), str(iss))

    # 纯四度应报
    bad_u = list(u)
    bad_l = list(l)
    bad_u[2] = bad_l[2] + 5  # force P4 in middle
    iss = validate_two_part_species(bad_u, bad_l, tonic_pc=tonic)
    check('检出纯四度', any('纯四' in x for x in iss), str(iss))

    # 中间非法音程应报（大二度等）
    bad_u = list(u)
    bad_l = list(l)
    bad_u[2] = bad_l[2] + 2  # major second
    iss = validate_two_part_species(bad_u, bad_l, tonic_pc=tonic)
    check('检出非法中间音程', any('中间' in x or '3/6' in x for x in iss), str(iss))

    # 同时跳进应报
    bad_u = list(u)
    bad_l = list(l)
    if len(bad_u) >= 3:
        bad_u[1] = bad_u[0] + 5
        bad_l[1] = bad_l[0] + 5
        iss = validate_two_part_species(bad_u, bad_l, tonic_pc=None)
        check('检出同时跳进', any('同时跳进' in x for x in iss), str(iss))

    # 同向到八度应报
    bad_u = list(u)
    bad_l = list(l)
    bad_l[-1] = bad_u[-1] - 12
    if bad_u[-1] > bad_u[-2] and bad_l[-1] > bad_l[-2]:
        iss = validate_two_part_species(bad_u, bad_l, tonic_pc=None)
        check('检出同向结尾', any('反向' in x for x in iss), str(iss))
    else:
        bad_u[-2] = bad_u[-1] + 2
        bad_l[-2] = bad_l[-1] + 2
        iss = validate_two_part_species(bad_u, bad_l, tonic_pc=None)
        check('检出同向结尾', any('反向' in x for x in iss), str(iss))

    # 同小节双峰应报
    bad_u = list(u)
    bad_l = list(l)
    umax = max(bad_u)
    ui = bad_u.index(umax)
    peak_l = max(bad_l)
    for i in range(len(bad_l)):
        if i != ui and bad_l[i] == peak_l:
            bad_l[i] = peak_l - 1
    bad_l[ui] = peak_l
    iss = validate_two_part_species(bad_u, bad_l, tonic_pc=None)
    check('检出最高音同小节', any('最高音' in x or '错开' in x or '同一小节' in x
                              for x in iss), str(iss))


def test_build_score():
    print('[4] build_two_part_species_score')
    sc, report, meta = build_two_part_species_score(bars=8, seed=11)
    check('大谱表', len(sc.staves) == 2)
    check('RH 高音', sc.staves[0].clef == ClefType.TREBLE)
    check('LH 低音', sc.staves[1].clef == ClefType.BASS)
    check('花括号', bool(sc.staff_groups))
    check('每小节一音 RH', all(len([n for n in m.notes if not n.is_rest]) == 1
                              for m in sc.staves[0].measures))
    check('每小节一音 LH', all(len([n for n in m.notes if not n.is_rest]) == 1
                              for m in sc.staves[1].measures))
    check('无校验残留', meta['issues'] == [], str(meta['issues']))
    check('报告提及对位', '二声部' in report)
    lo = meta['lower_midis']
    steps = sum(1 for i in range(len(lo) - 1) if abs(lo[i + 1] - lo[i]) <= 2)
    check('低音级进为主', steps >= int(0.6 * (len(lo) - 1)),
          f'steps={steps}/{len(lo) - 1}')
    # 相邻不同音
    up = meta['upper_midis']
    check('高音无相邻同音', all(up[i] != up[i + 1] for i in range(len(up) - 1)))
    check('低音无相邻同音', all(lo[i] != lo[i + 1] for i in range(len(lo) - 1)))


def test_pipeline_e2e():
    print('[5] compose 端到端（Planner 字段 / 离线兜底）')
    # 直接用 Planner 字段驱动（有/无 API key 均可）
    session = ComposeSession()
    plan = {
        'title': '二声部对位', 'species': True, 'want_fugue': False,
        'texture': 'first-species', 'bars': 8, 'tempo': 88,
        'meter': '4/4', 'key_sharps': 0, 'mode': 'major',
        'user_brief': PROMPT,
    }
    out = _try_compose_two_part_species(session, PROMPT, plan=plan)
    check('返回', out is not None)
    score, spec, note = out
    check('返回谱', score is not None)
    check('species 标记', bool(spec.get('species')))
    check('两行 Piano', len(spec.get('parts') or []) == 2)
    names = [p.get('name') for p in spec['parts']]
    check('RH/LH 名', names == ['Piano RH', 'Piano LH'], str(names))
    check('staff_groups', bool(spec.get('staff_groups')))
    check('确定性管线', '确定性' in note or '二声部' in note)
    check('plan.species', bool((session.plan or {}).get('species')))
    check('无赋格', not bool(spec.get('fugue')))
    for p in spec['parts']:
        evs = [e for e in (p.get('events') or []) if e and e[0] is not None]
        check(f'{p["name"]} 8 音', len(evs) == 8, str(evs))
        check(f'{p["name"]} 全音符', all(e[1] == 'whole' for e in evs), str(evs))
    from music_score.two_part_species import extract_bar_midis_from_spec
    up, lo = extract_bar_midis_from_spec(spec)
    issues = validate_two_part_species(up, lo, tonic_pc=int(spec.get('tonic_pc', 0)))
    check('管线结果满足约束', issues == [], str(issues))


def test_fugue_not_triggered():
    print('[6] 对位 brief 不触发赋格')
    plan = {'user_brief': PROMPT, '_species_hint': {'kind': 'two_part_species'},
            'species': True, 'want_fugue': False}
    check('fugue_requested=False', not _fugue_requested(plan, 'auto'))
    check('wants piano grand', _wants_piano_grand(plan, None, PROMPT))
    short_plan = {'user_brief': '写一首2声部一比一对位的曲子',
                  'species': True, 'want_fugue': False,
                  'texture': 'first-species'}
    check('plan.species 指示对位', _plan_indicates_species(short_plan))
    check('短提示不触发赋格', not _fugue_requested(short_plan, 'auto'))
    check('want_fugue=False 优先', not _fugue_requested(
        {'user_brief': '写赋格', 'want_fugue': False}, 'auto'))
    check('want_fugue=True 才加', _fugue_requested(
        {'user_brief': '随便写', 'want_fugue': True}, 'auto'))
    check('明确赋格才加', _fugue_requested(
        {'user_brief': '写一首三声部赋格'}, 'auto'))
    check('仅对位不加赋格', not _fugue_requested(
        {'user_brief': '写一段优美的对位织体'}, 'auto'))


def test_short_prompt_e2e():
    print('[6b] 短提示 / Planner 字段走确定性对位')
    # 模拟 Planner 翻译结果（不依赖 keyword）
    session2 = ComposeSession()
    plan = {
        'title': '二声部对位', 'species': True, 'want_fugue': False,
        'texture': 'first-species', 'bars': 8, 'tempo': 100,
        'meter': '4/4', 'key_sharps': 0, 'mode': 'major',
        'instrumentation': ['piano', 'piano'],
        'user_brief': '写一首2声部一比一对位的曲子',
    }
    out = _try_compose_two_part_species(
        session2, plan['user_brief'], plan=plan)
    check('Planner 字段可生成', out is not None)
    _m21, spec2, note2 = out
    check('Planner 路径 species', bool(spec2.get('species')))
    check('Planner 路径无赋格', not bool(spec2.get('fugue')))
    check('用了 plan tempo', spec2.get('tempo') == 100, str(spec2.get('tempo')))
    check('Piano RH/LH', [p['name'] for p in spec2['parts']] == [
        'Piano RH', 'Piano LH'])
    # 离线 keyword 兜底仍可用（无 API key 时）
    from llm_compose import load_llm_config
    if not (load_llm_config() or {}).get('api_key'):
        session = ComposeSession()
        score, spec, note = compose_from_text(
            session, '写一首2声部一比一对位的曲子')
        check('离线兜底 species', bool(spec.get('species')))
        check('离线兜底无赋格', not bool(spec.get('fugue')))

def test_program_constraints():
    print('[7] program 硬约束')
    prog = {'calls': [
        {'script': 'new_score', 'args': {'voices': 3, 'instruments': ['violin', 'violin', 'cello']}},
        {'script': 'fugue', 'args': {}},
        {'script': 'melody', 'args': {'rhythm': 'flowing'}},
        {'script': 'finalize', 'args': {}},
    ]}
    plan = {'species': True, '_species_hint': {}}
    out, fixes = _apply_species_program_constraints(prog, plan)
    scripts = [c['script'] for c in out['calls']]
    check('去掉 fugue', 'fugue' not in scripts)
    ns = next(c for c in out['calls'] if c['script'] == 'new_score')
    check('piano×2', ns['args']['instruments'] == ['piano', 'piano'])
    mel = next(c for c in out['calls'] if c['script'] == 'melody')
    check('sparse', mel['args']['rhythm'] == 'sparse')
    check('有 fixes', bool(fixes))


def test_analyze_species():
    print('[8] 分析报告含对位项')
    from score_studio import analyze_composition
    sc, _rep, meta = build_two_part_species_score(bars=8, seed=5)
    spec = species_score_to_spec(sc, meta)
    checks = analyze_composition(sc, spec)
    species_ok = [c for c in checks if c[0] == '二声部对位' and c[1].startswith('✅')]
    check('分析通过二声部对位', bool(species_ok), str(
        [c for c in checks if c[0] == '二声部对位']))


def test_help_entry():
    print('[9] help_doc 条目')
    from help_doc import FEATURES, build_help
    from music_score.two_part_species import get_counterpoint_rules_doc
    ids = [f.get('id') for f in FEATURES]
    check('功能目录含二声部对位', '二声部对位（钢琴大谱表）' in ids)
    text = build_help('对位法')
    rules = get_counterpoint_rules_doc()
    check('help 含权威规则全文', '禁止纯 4 度' in text and '导音' in text)
    check('tutorial 与模块同源', rules[:40] in text)
    feat = next(f for f in FEATURES if f['id'] == '二声部对位（钢琴大谱表）')
    check('别名含对位法', '对位法' in feat.get('aliases', []))
    check('tutorial 已绑定', bool((feat.get('tutorial') or '').strip()))
    check('示例含根据当前乐谱', '根据当前' in (feat.get('example') or ''))


def test_from_current_score():
    print('[10] 根据当前乐谱写 1:1 对位')
    from music_score.two_part_species import (
        wants_species_from_current, detect_two_part_species,
        extract_cantus_from_spec, build_two_part_species_from_cantus,
        generate_two_part_species, species_score_to_spec,
        validate_two_part_species,
    )
    from llm_compose import (
        ComposeSession, _try_compose_species_from_current,
        _wants_species_from_current, _plan_indicates_species,
    )

    prompt = '根据当前打开的乐谱写一个1比1对位'
    check('识别 from_current', wants_species_from_current(prompt))
    check('detect 命中', detect_two_part_species(prompt) is not None)
    check('不误伤无当前语境', not wants_species_from_current('写一首圆舞曲'))

    # 先造一首合法一比一，再以其高音为「当前乐谱」
    u, l, meta0 = generate_two_part_species(8, seed=3)
    sc, rep, meta = build_two_part_species_from_cantus(
        u, cf_role='upper', seed=21, sharps=0, mode='major')
    check('基于 CF 生成有谱', len(sc.staves) == 2)
    check('报告提及当前', '当前' in rep or '定旋律' in rep)
    iss = validate_two_part_species(
        meta['upper_midis'], meta['lower_midis'],
        tonic_pc=int(meta.get('tonic_pc', 0)),
        fixed_cf='upper')
    check('对位校验通过', iss == [], str(iss))

    # 管线：session 已有 score_spec
    base_spec = species_score_to_spec(sc, meta)
    # 用原始单声部轮廓模拟「当前打开的单旋律」
    mono = {
        'title': '原曲', 'bars': 8, 'tempo': 96, 'time_signature': '4/4',
        'key_sharps': 0, 'mode': 'major',
        'parts': [{
            'name': 'Melody', 'clef': 'treble', 'program': 0,
            'events': [[f'C{4 + (m - 60) // 12}', 'whole']  # placeholder
                       for m in u],
        }],
    }
    # 正确写入音名
    from music_score.two_part_species import _midi_name
    mono['parts'][0]['events'] = [[_midi_name(m), 'whole'] for m in u]
    cf, role, msrc = extract_cantus_from_spec(mono)
    check('抽取得 CF 长度', len(cf) == 8, str(cf))
    check('CF 角色偏高音', role == 'upper', role)

    session = ComposeSession()
    session.score_spec = mono
    session.prefs = {'tempo': 96, 'meter': '4/4', 'key_sharps': 0,
                     'mode': 'major', 'fugue': 'off'}
    plan = {
        'species': True, 'species_from_current': True, 'want_fugue': False,
        'texture': 'first-species', 'bars': 8, 'tempo': 96,
        'key_sharps': 0, 'mode': 'major', 'title': '原曲对位',
        'user_brief': prompt,
    }
    check('plan 指示 species', _plan_indicates_species(plan))
    check('wants from current', _wants_species_from_current(prompt, plan))
    out = _try_compose_species_from_current(session, prompt, plan=plan)
    check('管线返回', out is not None)
    m21, spec, note = out
    check('结果 species', bool(spec.get('species')))
    check('结果 from_current', bool(spec.get('from_current')))
    check('两行钢琴', len(spec.get('parts') or []) == 2)
    check('说明含对位', '对位' in note)


if __name__ == '__main__':
    test_detect()
    test_interval_helpers()
    test_validate_rules()
    test_build_score()
    test_pipeline_e2e()
    test_fugue_not_triggered()
    test_short_prompt_e2e()
    test_program_constraints()
    test_analyze_species()
    test_help_entry()
    test_from_current_score()
    print('\n全部通过')