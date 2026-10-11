# -*- coding: utf-8 -*-
"""带和弦标注语料的可解释语法（民谣 / 爵士 / 流行 / 牧歌），以及和声由分析器自己识别的
多声部语料（古典钢琴奏鸣曲 / 弦乐四重奏 / 肖邦）：规则脚本 → 确定性作曲 →
R01–R10 硬规则 0 违例 → 内核 / 斜杠命令 → 工作室报告无 ❌。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []

STYLES = {'folk': ('jig', 2), 'jazz': ('bebop', -1), 'pop': ('pop', 0), 'madrigal': ('madrigal', -1),
          'sonata': ('allegro', 0), 'strings': ('minuet', 1), 'symphony': ('minuet', 2),
          'chopin': ('mazurka', -2)}
# 织体 → (声部数, 钢琴大谱表分组)
TEXTURE = {'melody_piano': (3, [[1, 2]]), 'piano': (2, [[0, 1]]), 'quartet': (4, []),
           'orchestra': (9, [])}
ORCHESTRA = ['Flute', 'Oboe', 'Horn', 'Bassoon', 'Timpani',
             'Violin I', 'Violin II', 'Viola', 'Violoncello']


def check(name, cond, detail=''):
    print(('  ✓ ' if cond else '  ✗ ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def test_check_helpers():
    print('[1] R08 平行检测（与工作室报告同口径）')
    from score_learning.leadsheet.check import parallel_motion, timeline_parallels
    check('同向进入纯八度', parallel_motion([65], [67], [53], [55]) == '八')
    check('同向进入纯五度', parallel_motion([67], [69], [60], [62]) == '五')
    check('一声部不动不算', parallel_motion([67], [67], [60], [62]) is None)
    check('反向不算', parallel_motion([67], [69], [60], [58]) is None)
    check('和弦按就近配对：F→G 中 F4→G4 与低音 F→G 平行八度',
          parallel_motion([60, 65, 69], [58, 62, 67], [53], [55]) == '八')
    li = [[0.0, 1.0, [65]], [1.0, 1.0, [67]]]
    lj = [[0.0, 1.0, [53]], [1.0, 1.0, [55]]]
    check('时间线上找到平行位置', timeline_parallels(li, lj) == [1.0])
    from score_learning.leadsheet.check import split_streams, stream_parallels
    lh = [[0.0, 1.0, [48]], [1.0, 1.0, [55, 64]], [2.0, 1.0, [57, 65]]]
    st = split_streams(lh)
    check('钢琴谱表拆成高低两条流', len(st) == 2 and [e[2] for e in st[0]] == [[48], [64], [65]]
          and [e[2] for e in st[1]] == [[55], [57]], str(st))
    a = [[0.0, 1.0, [60]], [1.0, 1.0, [62]]]
    b = [[0.0, 1.0, [48]], [1.0, 1.0, [50]]]
    check('跨谱表查平行八度', stream_parallels([a, b], [0, 1]) == [(1.0, 0, 1)])
    check('同谱表八度加强不算', stream_parallels([a, b], [0, 0]) == [])
    from score_learning.leadsheet.learn import functional_chord
    check('功能和弦：副属 V/V、那不勒斯、借用 iv 保留',
          all(functional_chord(t, 'major') for t in ('2:M', '2:7', '1:M', '5:m', '6:d')))
    check('功能和弦：大调导音小三和弦、小调 #vii 小三和弦剔除',
          not functional_chord('11:m', 'major') and not functional_chord('11:m', 'minor'))


def test_scripts():
    print('[2] 规则脚本')
    from score_learning.leadsheet import script as S
    for style in STYLES:
        yml, md = S.paths(style)
        check(f'{style} grammar.yaml / grammar.md 存在', os.path.isfile(yml) and os.path.isfile(md))
        sc = S.load(style)
        for sub, d in sc['subs'].items():
            ids = {r['id'] for r in d['hard_rules']}
            # R04（罕见跳进）只在语料确有罕见跳进时生成
            need = {f'R{i:02d}' for i in range(1, 11)} - {'R04'}
            check(f'{style}/{sub} 硬规则齐全', need <= ids, str(sorted(ids)))
            check(f'{style}/{sub} 学到节奏模式', d['rhythm']['mode'] in ('bar', 'cell'))
            for mode, ev in (d.get('evaluation') or {}).items():
                if ev.get('heldout_events', 0) >= 300:
                    check(f'{style}/{sub}/{mode} 脚本困惑度低于一元模型',
                          ev['perplexity_script'] < ev['perplexity_unigram'],
                          f"{ev['perplexity_script']} vs {ev['perplexity_unigram']}")
        text = open(md, encoding='utf-8').read()
        check(f'{style} 说明文档含曲式与终止公式', '曲式' in text and '终止' in text)


def test_compose():
    print('[3] 确定性作曲')
    from score_learning.leadsheet import script as S
    from score_learning.leadsheet.compose import compose
    for style, (sub, key) in STYLES.items():
        sc = S.load(style)
        bars = 24 if style == 'madrigal' else 32
        for mode in sc['subs'][sub]['vocab']:
            r1 = compose(sc, sub, mode, key, bars, seed='t')
            r2 = compose(sc, sub, mode, key, bars, seed='t')
            tag = f'{style}/{sub}/{mode}'
            check(f'{tag} 零违例', not r1['violations'], str(r1['violations'][:2]))
            check(f'{tag} 同种子逐音符一致', r1['spec']['parts'] == r2['spec']['parts'])
            check(f'{tag} 小节数', r1['spec']['bars'] == bars, str(r1['spec']['bars']))
            groups = [sg.get('parts') for sg in r1['spec'].get('staff_groups') or []]
            texture = sc['subs'][sub].get('texture', 'melody_piano')
            n_parts, want_groups = TEXTURE[texture]
            check(f'{tag} 织体 {texture}：{n_parts} 个声部',
                  len(r1['spec']['parts']) == n_parts and groups == want_groups,
                  f"{len(r1['spec']['parts'])} {groups}")
            check(f'{tag} 每个和弦有规则编号', all(s['rule'] for s in r1['slots']))
            check(f'{tag} 末和弦是主和弦', r1['slots'][-1]['tok'].startswith('0:'),
                  r1['slots'][-1]['tok'])
            letters = [s['label'] for s in r1['plan']['sections']]
            check(f'{tag} 段数与曲式一致', len(letters) == len(r1['units']) == bars // 8,
                  f'{len(letters)} {len(r1["units"])}')
            for i, L in enumerate(letters):
                j = letters.index(L)
                if j < i:
                    check(f'{tag} 再现段 {L} 和弦一致',
                          r1['units'][i]['chords'] == r1['units'][j]['chords'])
                    break
            parts = r1['spec']['parts']
            # 管弦乐：弦乐组全程演奏，木管 / 圆号 / 定音鼓只在全奏段进入
            accs = parts[6:] if texture == 'orchestra' else parts[1:]
            for part in accs:
                sounding = [e for e in part['events'] if e[0] is not None]
                check(f'{tag} 伴奏声部 {part["name"]} 有音', len(sounding) >= bars // 2,
                      str(len(sounding)))
            if texture == 'orchestra':
                check(f'{tag} 管弦乐总谱顺序', [p['name'] for p in parts] == ORCHESTRA,
                      str([p['name'] for p in parts]))
                for part in parts[:5]:
                    sounding = [e for e in part['events'] if e[0] is not None]
                    check(f'{tag} 全奏声部 {part["name"]} 有音', len(sounding) >= 2,
                          str(len(sounding)))
            if texture == 'piano':
                lh = [e for e in r1['spec']['parts'][1]['events'] if e[0] is not None]
                check(f'{tag} 左手是音型（音符数多于小节数）', len(lh) > bars, str(len(lh)))
    sc = S.load('folk')
    r = compose(sc, 'reel', 'major', 2, 32, seed='p', layout='pickup')
    check('民谣弱起 spec 带 pickup_ql', (r['spec'].get('pickup_ql') or 0) > 0 and not r['violations'])
    r = compose(sc, 'reel', 'major', 2, 32, seed='p', layout='downbeat')
    check('民谣强拍起不带 pickup_ql', not r['spec'].get('pickup_ql') and not r['violations'])


def test_kernel_and_command():
    print('[4] 内核脚本 + 斜杠命令 + 工作室报告')
    from compose_kernel import SCRIPT_IMPL, kernel_docs
    from llm_compose import (ComposeSession, CommandOnly, compose_from_text, spec_to_music21,
                             validate_score_spec)
    from score_studio import _auto_problems, analyze_composition, m21_to_model
    check('leadsheet_grammar 已注册', 'leadsheet_grammar' in SCRIPT_IMPL)
    check('文档含 leadsheet_grammar', 'leadsheet_grammar' in kernel_docs())
    cases = [('/compose-grammar 民谣 jig seed=1', '6/8', 32),
             ('/compose-grammar 爵士 bebop 16 seed=1', '4/4', 16),
             ('/compose-grammar 流行 minor seed=1', '4/4', 32),
             ('/compose-grammar 牧歌 seed=1', '4/4', 24),
             ('/compose-grammar waltz 小号 seed=2', '3/4', 32),
             ('/compose-grammar 奏鸣曲 seed=1', '4/4', 32),
             ('/compose-grammar sonata compound 16 seed=4', '6/8', 16),
             ('/compose-grammar 小步舞曲 四重奏 seed=2', '3/4', 32),
             ('/compose-grammar strings duple minor 16 seed=3', '2/4', 16),
             ('/compose-grammar 交响曲 小步舞曲 seed=2', '3/4', 32),
             ('/compose-grammar symphony allegro minor 16 seed=4', '4/4', 16),
             ('/compose-grammar 肖邦 玛祖卡 minor seed=2', '3/4', 32)]
    for cmd, meter, bars in cases:
        _s, spec, note = compose_from_text(ComposeSession(), cmd)
        ts = spec.get('time_signature') or spec.get('time')
        check(f'{cmd}：拍号 {meter}、{bars} 小节', ts == meter and spec['bars'] == bars,
              f"{ts} {spec['bars']}")
        check(f'{cmd}：回复列出曲式与段落', '曲式' in note and '第1段' in note, note[:80])
        check(f'{cmd}：spec 带语法追溯', bool((spec.get('grammar') or {}).get('units')))
        crit = [i for i in validate_score_spec(spec) if i['level'] == 'CRITICAL']
        check(f'{cmd}：结构校验无 CRITICAL', not crit, str(crit[:1]))
        model = m21_to_model(spec_to_music21(spec), '')
        bad = [c for c in analyze_composition(model, spec) if c[1].startswith('❌')]
        check(f'{cmd}：工作室报告无 ❌（含平行五八度）', not bad, str(bad[:1]))
        red = [r for r in _auto_problems({}, spec, model) if r[1].startswith('🔴')]
        check(f'{cmd}：自动问题无 🔴', not red, str(red[:1]))
    _s, spec, _n = compose_from_text(ComposeSession(), '/compose-grammar waltz 小号 seed=2')
    check('乐器关键词生效', spec['parts'][0].get('name') == 'Trumpet', str(spec['parts'][0].get('name')))
    try:
        compose_from_text(ComposeSession(), '/compose-grammar waltz minor')
        check('语料没有的调式给出说明', False)
    except CommandOnly as e:
        check('语料没有的调式给出说明', '可用' in str(e), str(e))
    _s, spec, _n = compose_from_text(ComposeSession(), '/compose-grammar 四重奏 16 seed=1')
    check('四重奏声部名', [p['name'] for p in spec['parts']] ==
          ['Violin I', 'Violin II', 'Viola', 'Violoncello'], str([p['name'] for p in spec['parts']]))
    _s, spec, _n = compose_from_text(ComposeSession(), '/compose-grammar 奏鸣曲 16 seed=1')
    check('奏鸣曲是钢琴大谱表', [p['name'] for p in spec['parts']] == ['Piano RH', 'Piano LH']
          and spec.get('staff_groups'), str([p['name'] for p in spec['parts']]))
    try:
        compose_from_text(ComposeSession(), '/compose-grammar 肖邦 前奏曲')
        check('没学到的子风格给出说明', False)
    except CommandOnly as e:
        check('没学到的子风格给出说明', '可用' in str(e), str(e))
    _s, spec, note = compose_from_text(ComposeSession(), '/compose-grammar minor 8 seed=x')
    check('不带风格词仍是众赞歌', len(spec['parts']) == 4 and '第1句' in note, note[:60])
    _s, spec, _n = compose_from_text(ComposeSession(), '/compose-grammar 海顿 16 seed=1')
    check('交响曲声部名（总谱顺序）', [p['name'] for p in spec['parts']] == ORCHESTRA,
          str([p['name'] for p in spec['parts']]))


def test_llm_routing():
    print('[5] 自然语言 → 已学语法（Planner learned_grammar / 离线关键词）')
    import json
    import llm_compose as L
    D = L._detect_learned_grammar
    check('小步舞曲默认奏鸣曲', D('作一曲小步舞曲') == {'style': 'sonata', 'sub': 'minuet', 'mode': None})
    check('「做一首交响」→ symphony（短别名）',
          D('做一首交响') == {'style': 'symphony', 'sub': None, 'mode': None})
    check('交响 + 小步舞曲 → symphony', (D('写一首海顿交响曲小步舞曲') or {}).get('style') == 'symphony')
    check('舒伯特具名不走短句语法', D('写一首舒伯特早期交响曲快板') is None)
    check('最长风格词优先：海顿弦乐四重奏 → strings',
          (D('海顿弦乐四重奏') or {}).get('style') == 'strings')
    check('情绪词推调式', (D('来一首忧伤的玛祖卡') or {}).get('mode') == 'minor')
    check('众赞歌', (D('写一首巴赫众赞歌') or {}).get('style') == 'chorale')
    check('对位 / 赋格不走语法', D('写一首二声部一比一对位') is None and D('写一首赋格') is None)
    check('无风格词不识别', D('写一首欢快的曲子') is None)
    check('语料没有的调式让路给常规流程', L._try_compose_learned_grammar(
        L.ComposeSession(), '', {'style': 'symphony', 'sub': 'minuet', 'mode': 'minor'}) is None)
    check('超过 32 小节让路给常规流程', L._try_compose_learned_grammar(
        L.ComposeSession(), '写一首 64 小节的小步舞曲', {'style': 'sonata', 'sub': 'minuet'}) is None)
    prog, fix = L._strip_unrequested_grammar(
        {'calls': [{'script': 'new_score'}, {'script': 'leadsheet_grammar'}]}, {})
    check('translator 自加的 leadsheet_grammar 被摘除',
          [c['script'] for c in prog['calls']] == ['new_score'] and fix, str(prog))

    old_cfg, old_chat = L.load_llm_config, L.chat_completion
    try:
        L.load_llm_config = lambda: {}
        s = L.ComposeSession()
        _m, spec, note = L.compose_from_text(s, '写一首弦乐四重奏小步舞曲 16小节')
        check('离线：四重奏小步舞曲 16 小节', len(spec['parts']) == 4 and spec['bars'] == 16
              and spec['time_signature'] == '3/4', f"{len(spec['parts'])} {spec['bars']}")
        check('离线：回复说明识别结果', '识别为已学风格' in note, note[:80])

        # 有 LLM Key 时，「做一首交响」也应直接走语法，不调 Planner
        L.load_llm_config = lambda: {'api_key': 'test'}
        def boom(*a, **k):
            raise RuntimeError('LLM should not be called for short symphony prompt')
        L.chat_completion = boom
        s = L.ComposeSession()
        _m, spec, note = L.compose_from_text(s, '做一首交响')
        check('有 LLM：「做一首交响」→ 9 声部交响语法', len(spec['parts']) == 9
              and '/compose-grammar symphony' in note, note[:100])

        # 带调号 / 小节数的短句：关键词路由（不调 LLM），调号从文案解析
        s = L.ComposeSession()
        _m, spec, note = L.compose_from_text(s, '帮我作一曲 A 大调交响乐小步舞曲，16 小节')
        check('短句路由：管弦乐 9 声部', [p['name'] for p in spec['parts']] == ORCHESTRA,
              str([p['name'] for p in spec['parts']]))
        check('短句路由：A 大调 / 16 小节 / 3/4', spec['key_sharps'] == 3 and spec['bars'] == 16
              and spec['time_signature'] == '3/4',
              f"{spec['key_sharps']} {spec['bars']} {spec['time_signature']}")
        check('短句路由：不调 LLM', '识别为已学风格' in note and 'symphony' in note, note[:100])

        # 文案无已学风格词 → 新曲不再进 Planner，只提示 /compose-grammar
        L.load_llm_config = lambda: {'api_key': 'test'}
        L.chat_completion = boom
        s = L.ComposeSession()
        try:
            L.compose_from_text(s, '帮我写一首 16 小节的新曲子，G 大调，轻松一点')
            check('无风格词新曲应拒绝 Planner', False, 'unexpected success')
        except L.CommandOnly as e:
            msg = str(e)
            check('无风格词新曲 → 只提示 /compose-grammar',
                  '/compose-grammar' in msg and 'Planner' in msg, msg[:120])
    finally:
        L.load_llm_config, L.chat_completion = old_cfg, old_chat


def test_learn_report_and_library():
    print('[6] /learn-grammar 参数 + 规则库')
    from llm_compose import _LEADSHEET_ALIASES
    check('中英文风格别名', all(k in _LEADSHEET_ALIASES for k in (
        '民谣', 'jazz', '流行', '牧歌', 'bebop', '奏鸣曲', '四重奏', '肖邦', '玛祖卡', '小步舞曲',
        '交响曲', '海顿')))
    import rule_library as R
    rules = {r['id']: r for r in R._grammar_rules()}
    for pack in ('british-folk-tune', 'swing-standard', 'pop-standard', 'madrigal',
                 'piano-sonata', 'string-quartet', 'symphony', 'chopin-piano'):
        r = rules.get(f'learned_grammar_{pack}')
        check(f'规则库含 {pack}', r is not None)
        if r:
            check(f'{pack} 来源不是巴赫语料', '巴赫' not in r['sources'][0]['title'],
                  r['sources'][0]['title'])
            check(f'{pack} 关联 leadsheet 代码', all('leadsheet' in c['ref'] for c in r['code']))
    import help_doc
    text = help_doc.build_help() if hasattr(help_doc, 'build_help') else ''
    check('帮助里写明风格参数', '牧歌' in text and 'bebop' in text)
    check('帮助里写明古典风格', '奏鸣曲' in text and '四重奏' in text and '肖邦' in text
          and '交响曲' in text)
    check('帮助里写明自然语言路由', '小步舞曲' in text and 'learned_grammar' in text)


if __name__ == '__main__':
    test_check_helpers()
    test_scripts()
    test_compose()
    test_kernel_and_command()
    test_llm_routing()
    test_learn_report_and_library()
    print(f'\n{"全部通过" if not FAILS else f"失败 {len(FAILS)} 项：" + "，".join(FAILS)}')
    sys.exit(1 if FAILS else 0)
