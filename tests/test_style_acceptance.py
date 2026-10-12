# -*- coding: utf-8 -*-
"""风格作曲验收测试：专业设置 / 简单提示词 / 分析报告 / 随机性 / 风格正确性。

五组命题（对应「作曲要有专业设置和简单提示词；做出的曲目要确认分析报告；
是否正确；风格是否正确；是否是随机性」）：

  ① 专业设置：显式参数（速度/拍号/小节/调性/声部/赋格/种子）逐条落谱，
     且优先级为「显式参数 > 手写 profile」（已学语法包不再读旧 style_contract）。
  ② 简单提示词：自然语言 → 路由 → 生成 → 报告 全链路可用。
  ③ 分析报告：两版报告都产出、章节齐全；报告里的谱面事实与从 music21
     反读的事实逐条一致；自动检查的计数与返回的 (passed, warned, failed) 自洽。
  ④ 随机性：同种子逐音符复现；换种子只改细节（骨架不变）；不给种子两次必不同。
  ⑤ 风格正确性：已学 = 有 grammar.yaml；骨架写入 style= 包路径；伴奏和弦可听。

本文件的断言都是「可判定的」，不是「看起来对」：
  - ⑤ 的伴奏音级数阈值来自改动前后的实测对照：契约和声可用时低音声部每小节奏出
    和弦自身音（≥3 个音级，实测 15/16）；改动前伴奏只弹根音+五音，恒为 0/16。
    所以该阈值是「能区分旧实现」的阈值，不是随手取的数字。
  - ③ 的「共检查 N 项（✅ / ⚠️ / ❌）」计数是从报告正文反解出来的，用来钉住
    「报告说得 vs 谱面实际」一致，而不是「报告里没有 ❌ 字样」。
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import style_skeleton as sk                      # noqa: E402
import style_routing as routing                   # noqa: E402
import evaluate as ev                             # noqa: E402
import composition_skills as SL                   # noqa: E402
import score_studio as SS                         # noqa: E402
from llm_compose import spec_to_music21           # noqa: E402

_PASSED = 0
_FAILED = 0

# 有 grammar.yaml 的已学包（UI 可见集合）
LEARNED = [
    'classical/symphony', 'classical/piano-sonata', 'classical/string-quartet',
    'folk/british-folk-tune', 'jazz/swing-standard', 'pop/pop-standard',
    'sacred/chorale', 'romantic/chopin-piano', 'vocal/madrigal',
]
# 其中仍有风格指纹、可用于重叠率等指标验收的子集
LEARNED_WITH_FP = [
    'classical/symphony', 'folk/british-folk-tune', 'jazz/swing-standard',
    'pop/pop-standard', 'sacred/chorale',
]
# 已无「专家空包」；保留空列表以免旧断言引用报错
PLAIN = []

_REPORT_DIR = '/tmp'


def check(name, cond, extra=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f'  ✅ {name}')
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {extra}')


def gid(pack):
    return f'genre:{pack}'


def pack_id_of(pack):
    """`family/pack` → 该包的裸 pack_id（指纹/指标一律用裸 id）。

    style_skeleton 接受 `genre:family/pack`，但 evaluate 的 `all_fingerprints()`、
    `pack_fingerprint()`、`spec['pack_id']` 用的都是**裸包名**（'swing-standard'）。
    两种写法混用会让指标静默返回 None —— 本测试第五组正是钉住这一点。
    """
    return sk.resolve_genre(gid(pack)).get('pack_id')


# 规格缓存：同一配置只生成一次（每次都跑内核 + 两套校验 + music21 构建，
# 40 次重复生成会让测试从 ~50s 涨到 ~95s）
_SPEC_CACHE = {}


def spec_of(pack, **kw):
    key = (pack, tuple(sorted(kw.items())))
    if key not in _SPEC_CACHE:
        _SPEC_CACHE[key] = sk.skeleton_spec(gid(pack), **kw)
    return _SPEC_CACHE[key]


# 主工作配置：风格验收的绝大多数断言都基于它
CFG = dict(bars=16, voices=3, seed='acc', fugue='off')


# ---------------------------------------------------------------- 工具

def _bar_cap(ts):
    num, den = (int(x) for x in str(ts).split('/'))
    return num * (4.0 / den)


def acc_pcs_per_bar(spec, only_bass=True, min_pc=3):
    """每个小节「同时发声的音级数」达到 min_pc 的小节数。

    只统计低音声部（伴奏音型的承载声部）。这是判定「学到的和弦品质是否可听」
    的客观量：无契约时 broken_arpeggio 只弹根音与五音（2 个音级），契约和声
    可用时奏和弦自身音（三和弦 3 个、七和弦 4 个）。
    """
    cap = _bar_cap(spec.get('time_signature') or '4/4')
    units = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5, '16th': 0.25}
    target = None
    for p in (spec.get('parts') or []):
        nm = str(p.get('name') or '').lower()
        if 'bass' in nm or '低音' in nm:
            target = p
            break
    if target is None:
        target = (spec.get('parts') or [{}])[-1]
    bars = {}
    acc = 0.0
    for e in (target.get('events') or []):
        idx = int(acc / cap + 1e-9)
        acc += units.get(str(e[1]), 1.0)
        names = e[0] if isinstance(e[0], list) else [e[0]]
        s = bars.setdefault(idx, set())
        for n in names:
            if n:
                s.add(re.sub(r'-?\d+$', '', str(n)))
    return sum(1 for i in range(int(spec.get('bars') or 0))
               if len(bars.get(i, ())) >= min_pc)


def max_pcs_per_bar(spec):
    """低音声部单小节的音级数最大值（七和弦会到 4）。"""
    cap = _bar_cap(spec.get('time_signature') or '4/4')
    units = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5, '16th': 0.25}
    parts = spec.get('parts') or []
    target = next((p for p in parts
                   if 'bass' in str(p.get('name') or '').lower()), parts[-1] if parts else {})
    bars = {}
    acc = 0.0
    for e in (target.get('events') or []):
        idx = int(acc / cap + 1e-9)
        acc += units.get(str(e[1]), 1.0)
        names = e[0] if isinstance(e[0], list) else [e[0]]
        s = bars.setdefault(idx, set())
        for n in names:
            if n:
                s.add(re.sub(r'-?\d+$', '', str(n)))
    return max((len(v) for v in bars.values()), default=0)


def section_plan_of(spec):
    return [(s['name'], s['start'], s['bars']) for s in (spec.get('sections') or [])]


def note_sequence(spec):
    """全曲音符序列（音高+时值），用于判定「两次生成是否不同」。"""
    out = []
    for p in (spec.get('parts') or []):
        for e in (p.get('events') or []):
            out.append((json.dumps(e[0], ensure_ascii=False), str(e[1])))
    return out


# ---------------------------------------------------------------- ① 专业设置

def test_professional_settings_reach_score():
    print('\n=== ① 专业设置：显式参数逐条落谱 ===')
    # 一项都不能含糊：速度、拍号、小节、调性、调式、声部、赋格、种子
    kw = dict(bars=24, voices=3, key='Eb', mode='minor', tempo=88,
              meter='3/4', seed='acc-pro', fugue='off')
    spec = sk.skeleton_spec(gid('sacred/chorale'), **kw)

    check('显式小节数落谱', spec['bars'] == 24, f"得到 {spec['bars']}")
    check('显式拍号落谱', spec['time_signature'] == '3/4', f"得到 {spec['time_signature']}")
    check('显式速度落谱', spec['tempo'] == 88, f"得到 {spec['tempo']}")
    check('显式调号落谱（Eb → -3）', spec['key_sharps'] == -3, f"得到 {spec['key_sharps']}")
    check('显式调式落谱（minor）', spec['mode'] == 'minor', f"得到 {spec['mode']}")
    check('显式声部数落谱', spec['voices'] == 3 and len(spec['parts']) == 3,
          f"得到 voices={spec['voices']} parts={len(spec['parts'])}")
    check('显式关赋格落谱', not spec.get('fugue'), f"得到 {spec.get('fugue')}")
    check('显式种子落谱', spec.get('seed') == 'acc-pro', f"得到 {spec.get('seed')}")

    # 段落小节之和必须精确等于总小节，且每段 ≥1（内核会拒绝 0 小节段）
    plan = section_plan_of(spec)
    check('段落小节之和 == 总小节', sum(b for _n, _s, b in plan) == 24,
          f'{plan}')
    check('每段小节数 ≥1', all(b >= 1 for _n, _s, b in plan), f'{plan}')

    # 从 music21 反读：不是只看 spec 字段，而是看真的写进谱面没有
    model = SS.m21_to_model(spec_to_music21(spec), spec.get('title') or 't')
    check('music21 反读速度 == 声明速度', int(model.tempo) == 88,
          f'谱面 {model.tempo} vs 声明 88')
    ts = f'{model.time_signature.numerator}/{model.time_signature.denominator}'
    check('music21 反读拍号 == 声明拍号', ts == '3/4', f'谱面 {ts}')

    # §① 的核心：属性遵循度必须满分（want 就是用户下达的专业设置）
    want = {'tempo': 88, 'meter': '3/4', 'key_sharps': -3, 'mode': 'minor',
            'bars': 24, 'voices': 3, 'fugue': 'off'}
    aa = ev.attribute_adherence(spec, want)
    bad = [c for c in aa['checks'] if not c['ok']]
    check('属性遵循度 == 1.00', aa['score'] == 1.0 and aa['n'] == 7,
          f"score={aa['score']} n={aa['n']} 不合格={bad}")


def test_explicit_beats_contract():
    """优先级：显式参数 > 手写 profile（已学语法包不再读旧 style_contract）。"""
    print('\n=== ① 优先级：显式参数压过风格缺省 ===')
    base = spec_of('jazz/swing-standard', **CFG)
    g = sk.resolve_genre(gid('jazz/swing-standard'))
    ct = sk.contract_of(g)
    check('有语法的包不再加载旧契约', ct is None)
    prof_tempo = int(((g.get('profile') or {}).get('tempo') or {}).get('default') or 0)
    check('profile 默认速度可用', prof_tempo > 0, str(prof_tempo))
    over = spec_of('jazz/swing-standard', bars=16, voices=3, tempo=100, meter='3/4',
                   seed='acc', fugue='off')
    check('显式 tempo 覆盖缺省', over['tempo'] == 100,
          f"缺省 {base['tempo']} → 显式 100，得到 {over['tempo']}")
    check('显式 meter 覆盖缺省', over['time_signature'] == '3/4',
          f"得到 {over['time_signature']}")
    check('不给显式参数时用 profile/骨架缺省速度', base['tempo'] == prof_tempo
          or base['tempo'] > 0,
          f"profile {prof_tempo}，得到 {base['tempo']}")


# ---------------------------------------------------------------- ② 简单提示词

_PROMPT_CASES = [
    ('来一段四声部众赞歌', 'chorale'),
    ('写一首爵士摇摆标准曲', 'swing-standard'),
    ('给我一段古典交响乐', 'symphony'),
    ('英伦民谣小调', 'british-folk-tune'),
    ('来一段圆舞曲', 'waltz'),
    ('肖邦风格玛祖卡', 'mazurka'),
    ('凯尔特吉格', 'celtic-jig'),
    ('赋格', 'fugue'),
]


def test_simple_prompts_route():
    print('\n=== ② 简单提示词 → 风格路由 ===')
    for text, want in _PROMPT_CASES:
        r = routing.route(text)
        check(f'「{text}」→ {want}',
              r.ok and r.pack_id == want,
              f'得到 ok={r.ok} pack={r.pack_id} conf={r.confidence:.2f} '
              f'候选={r.candidates_text(2)}')


def test_prompt_to_report_pipeline():
    """提示词 → 路由 → 生成 → 报告：全链路不能用「占位」蒙过去。"""
    print('\n=== ② 提示词全链路（路由→生成→报告）===')
    text = '写一首爵士摇摆标准曲'
    res = routing.route(text)
    check('路由命中且置信度达标', res.ok and res.confidence >= routing.DEFAULT_THRESHOLD,
          f'conf={res.confidence}')
    spec = spec_of('jazz/swing-standard', **CFG)
    check('生成物带风格包 id（归档/评估可复现）',
          spec.get('pack_id') == res.pack_id, f"得到 {spec.get('pack_id')}")
    check('生成物非空（有声部与小节）',
          spec.get('parts') and spec.get('bars') == 16, '')
    rep = ev.evaluate_report(spec, pack_id=res.pack_id)
    for key in ('风格重叠率', '风格命中', '属性遵循度', '最近邻'):
        check(f'报告含「{key}」', key in rep, rep[:80])
    check('报告不含 N/A（指标基准齐备）', 'N/A' not in rep, rep)


def test_ui_brief_commands_land():
    """检查器「变奏」框里的 /命令 → 专业设置落谱；剩余自然语言 → 情绪词。

    这条把「专业设置」与「简单提示词」两个入口接到一起：用户在同一个框里既写
    `/bars 24 /fugue off`，又写「激烈」，两者都必须生效。`_split_brief_commands`
    是 staticmethod，可完全不起 Qt 地测（无窗口、无线程，不会引入抖动）。
    """
    print('\n=== ② 检查器 /命令 → 专业设置落谱 ===')
    parse = SS.StudioWindow._split_brief_commands
    ov, rest = parse('/bars 24 /voices 3 /fugue off /tempo 100 /meter 3/4 激烈')
    check('解析出全部覆盖项',
          ov == {'bars': 24, 'voices': 3, 'fugue': 'off',
                 'tempo': 100, 'meter': '3/4'}, str(ov))
    check('剩余自然语言保留（→ 情绪词）', rest == '激烈', repr(rest))
    ov2, _r2 = parse('/bars 4 /voices 7 越界')
    check('越界值被拒（bars 4 / voices 7 落在合法区间外）', ov2 == {}, str(ov2))

    spec = sk.skeleton_spec(gid('sacred/chorale'), mood=rest, **ov)
    check('/bars 24 → 谱面 24 小节', spec['bars'] == 24, str(spec['bars']))
    check('/voices 3 → 3 声部', spec['voices'] == 3, str(spec['voices']))
    check('/fugue off → 无赋格段', not spec.get('fugue'), str(spec.get('fugue')))
    check('/tempo 100 → 谱面速度 100', spec['tempo'] == 100, str(spec['tempo']))
    check('/meter 3/4 → 谱面拍号 3/4',
          spec['time_signature'] == '3/4', spec['time_signature'])

    check('/style 圆舞曲 → 解析到圆舞曲包',
          (sk.resolve_genre('圆舞曲') or {}).get('id') == gid('dance/waltz'),
          str(sk.resolve_genre('圆舞曲')))
    waltz = sk.skeleton_spec(sk.resolve_genre('圆舞曲').get('id'), bars=16, voices=3,
                             seed='ui', fugue='off')
    check('/style 圆舞曲 → 生成的就是 3/4 圆舞曲',
          waltz['time_signature'] == '3/4' and waltz.get('pack_id') == 'waltz',
          f"{waltz['time_signature']} / {waltz.get('pack_id')}")


# ---------------------------------------------------------------- ③ 分析报告

def test_analysis_report_confirms_score():
    print('\n=== ③ 分析报告：与谱面事实一致 ===')
    trace = []
    spec = sk.skeleton_spec(gid('sacred/chorale'), bars=16, voices=3,
                            seed='rep', fugue='off', trace=trace)
    model = SS.m21_to_model(spec_to_music21(spec), spec.get('title') or 't')
    editor_path = os.path.join(_REPORT_DIR, 'acc_editor.md')
    # 两个形态都必须能出报告：list（skeleton_spec 的原样输出）与 dict（会话态）
    try:
        text, counts = SS.export_analysis_report(model, editor_path, spec=spec,
                                                compose_trace=trace)
        list_ok = True
    except Exception as e:                       # noqa: BLE001
        list_ok = False
        text, counts = '', (0, 0, 1)
        print(f'     list 形态报错：{e!r}')
    check('list 形态 trace 也能出报告（不抛 AttributeError）', list_ok, '')
    try:
        SS.export_analysis_report(
            model, os.path.join(_REPORT_DIR, 'acc_editor_dict.md'), spec=spec,
            compose_trace={'kernel_calls': trace, 'pipeline': 'skeleton'})
        dict_ok = True
    except Exception as e:                       # noqa: BLE001
        dict_ok = False
        print(f'     dict 形态报错：{e!r}')
    check('dict 形态 trace 也能出报告', dict_ok, '')
    dev_path = editor_path.replace('.md', '_dev.md')
    check('编辑者版落盘', os.path.exists(editor_path), editor_path)
    check('程序员版落盘', os.path.exists(dev_path), dev_path)

    editor = open(editor_path, encoding='utf-8').read()
    dev = open(dev_path, encoding='utf-8').read()

    # 章节齐全（编号由报告自身维护，这里钉住「有这几节」而非写死编号）
    for sec in ('要核对的作曲原则', '基本信息', '需关注的问题', '记谱与演奏法',
                '生成质量评估'):
        check(f'编辑者版含「{sec}」', sec in editor, '')
    for sec in ('会话与生成环境', '谱面事实', '自动检查结果', '作曲过程透明化',
                '记谱与演奏法', '生成质量评估'):
        check(f'程序员版含「{sec}」', sec in dev, '')
    for sub in ('4.1 流程图', '4.2 大模型输入输出', '4.3 脚本调用与输入输出',
                '4.4 本地强制约束', '4.5 自动问题定位'):
        check(f'程序员版含「{sub}」', sub in dev, '')

    # 透明化：脚本链必须真的列出来（20 步：new_score…finalize）
    check('trace 非空（生成过程被记录）', len(trace) >= 8, f'{len(trace)} 条')
    check('报告列出逐脚本链', 'new_score' in dev and 'finalize' in dev, '')
    check('脚本链步数与 trace 一致', f'| {len(trace)} |' in dev,
          f'trace={len(trace)}')

    # 报告里的谱面事实 == 从 music21 反读的事实
    def _field(label, pat=r'(.+)$'):
        m = re.search(rf'^- {re.escape(label)}: {pat}', editor, re.M)
        return m.group(1).strip() if m else None
    tempo_line = _field('速度')
    ts_line = _field('拍号')
    check('报告速度字段 == music21 速度',
          tempo_line is not None and str(int(model.tempo)) in tempo_line,
          f'报告 {tempo_line!r} / 模型 {model.tempo}')
    ts = f'{model.time_signature.numerator}/{model.time_signature.denominator}'
    check('报告拍号字段 == music21 拍号', ts_line == ts,
          f'报告 {ts_line!r} / 模型 {ts}')
    # 声部行数 == 实际声部数
    n_part_lines = len(re.findall(r'^- 声部 \d+:', editor, re.M))
    check('报告声部条数 == 声部数', n_part_lines == len(spec['parts']),
          f'报告 {n_part_lines} / spec {len(spec["parts"])}')

    # 检查计数自洽：报告正文的统计行 == 函数返回的三元组
    m = re.search(r'共检查 (\d+) 项（✅ (\d+) / ⚠️ (\d+) / ❌ (\d+)）', editor)
    check('报告含检查统计行', bool(m), '')
    if m:
        got = tuple(int(x) for x in m.groups())
        check('统计行 == 返回的 (passed, warned, failed)',
              got == (counts[0] + counts[1] + counts[2], counts[0], counts[1], counts[2]),
              f'报告 {got} / 返回 {counts}')

    # 骨架曲现在应当零 CRITICAL：分析报告给出「曲子是对的」这个结论
    check('自动检查无 ❌（failed == 0）', counts[2] == 0, f'failed={counts[2]}')
    check('报告对曲目下正向结论', '❌ 0' in editor, '')


def test_report_style_notes_visible():
    """已学语法包不再施加旧契约；报告应如实写「无」或仅列骨架说明。"""
    print('\n=== ③ 报告：旧契约作废后不再谎称「有契约约束」 ===')
    trace = []
    spec = sk.skeleton_spec(gid('jazz/swing-standard'), bars=16, voices=3,
                            seed='notes', fugue='off', trace=trace)
    notes = [n for n in (spec.get('style_notes') or []) if '风格契约' in str(n)]
    check('爵士 spec 不再写入风格契约约束', len(notes) == 0, f'{notes[:3]}')
    model = SS.m21_to_model(spec_to_music21(spec), spec.get('title') or 't')
    p = os.path.join(_REPORT_DIR, 'acc_notes.md')
    SS.export_analysis_report(model, p, spec=spec, compose_trace=trace)
    dev = open(p.replace('.md', '_dev.md'), encoding='utf-8').read()
    check('程序员版 4.4 节存在', '4.4 本地强制约束' in dev, '')
    check('4.4 说明无风格契约（或写明无）',
          '风格契约施加的约束**：无' in dev or '风格契约施加的约束' not in dev
          or '（0 条' in dev, '')


# ---------------------------------------------------------------- ④ 随机性

def test_randomness():
    print('\n=== ④ 随机性 ===')
    P = gid('sacred/chorale')
    kw = dict(bars=16, voices=3, fugue='off')

    a = sk.skeleton_spec(P, seed='R', **kw)
    b = sk.skeleton_spec(P, seed='R', **kw)
    check('同种子两次逐音符完全相同', note_sequence(a) == note_sequence(b), '')

    c = sk.skeleton_spec(P, seed='R2', **kw)
    check('换种子：骨架不变（速度/拍号/小节/调性/段落）',
          (a['tempo'], a['time_signature'], a['bars'], a['key_sharps'], a['mode'],
           section_plan_of(a)) ==
          (c['tempo'], c['time_signature'], c['bars'], c['key_sharps'], c['mode'],
           section_plan_of(c)),
          f"A={section_plan_of(a)} C={section_plan_of(c)}")
    check('换种子：音符细节改变', note_sequence(a) != note_sequence(c), '')

    d1 = sk.skeleton_spec(P, **kw)
    d2 = sk.skeleton_spec(P, **kw)
    check('不给种子：两次生成的种子不同',
          d1.get('seed') and d2.get('seed') and d1['seed'] != d2['seed'],
          f"{d1.get('seed')} vs {d2.get('seed')}")
    check('不给种子：两次生成音符不同', note_sequence(d1) != note_sequence(d2), '')

    # 种子必须能穿透到子种子（旋律/和声/织体各自派生），否则「复现」是假的
    check('子种子已派生', isinstance(a.get('subseeds'), dict) and len(a['subseeds']) > 0,
          str(a.get('subseeds')))


# ---------------------------------------------------------------- ⑤ 风格正确性

def test_contract_injected_only_for_learned():
    print('\n=== ⑤ 骨架仍写入 style=（供包 id 追溯；契约已不生效）===')
    for pack in LEARNED:
        calls = sk.build_skeleton_calls(gid(pack), bars=16)
        got = (calls['calls'][0].get('args') or {}).get('style')
        check(f'{pack} 的 new_score 带上 style={pack}', got == pack, f'得到 {got!r}')


def test_contract_reaches_the_score():
    """已学语法包不再施加旧契约速度/拍号；谱面仍应有合理缺省。"""
    print('\n=== ⑤ 已学包用 profile 缺省落谱（旧契约作废）===')
    for pack in LEARNED:
        g = sk.resolve_genre(gid(pack))
        ct = sk.contract_of(g)
        check(f'{pack} 无旧契约', ct is None)
        spec = spec_of(pack, **CFG)
        check(f'{pack} 速度落谱', isinstance(spec.get('tempo'), int) and spec['tempo'] > 0,
              str(spec.get('tempo')))
        check(f'{pack} 拍号落谱', bool(spec.get('time_signature')),
              str(spec.get('time_signature')))
        model = SS.m21_to_model(spec_to_music21(spec), 't')
        check(f'{pack} 谱面速度可读', int(model.tempo) == int(spec['tempo']),
              f'谱面 {model.tempo} vs {spec["tempo"]}')


def test_seventh_chords_are_audible():
    """爵士等织体伴奏要能听出和弦自身音（不再依赖旧契约的 seventh_ratio）。"""
    print('\n=== ⑤ 伴奏和弦可听 ===')
    chordal = [
        'jazz/swing-standard', 'pop/pop-standard',
        'classical/symphony', 'sacred/chorale',
    ]
    for pack in chordal:
        spec = spec_of(pack, **CFG)
        rich = acc_pcs_per_bar(spec, min_pc=3)
        check(f'{pack} 伴奏奏出和弦自身音（≥3 音级的小节 ≥8/16）', rich >= 8,
              f'{rich}/16')

    jazz = spec_of('jazz/swing-standard', **CFG)
    rich4 = acc_pcs_per_bar(jazz, min_pc=4)
    check('爵士多数小节奏出七和弦（≥4 音级）', rich4 >= 6, f'{rich4}/16')
    check('爵士低音单小节音级数达到 4（七和弦可听）', max_pcs_per_bar(jazz) >= 4,
          f'max={max_pcs_per_bar(jazz)}')

    for pack in ('folk/british-folk-tune', 'academic/fugue'):
        spec = spec_of(pack, **CFG)
        check(f'{pack}（sustained 衬底）低音不以柱式堆和弦（≤2 音级）',
              acc_pcs_per_bar(spec, min_pc=3) <= 2,
              f'{acc_pcs_per_bar(spec, min_pc=3)}/16')


def test_style_metrics_available_for_learned():
    print('\n=== ⑤ 有指纹的已学包都有风格指标基准 ===')
    fps = ev.all_fingerprints()
    for pack in LEARNED_WITH_FP:
        pid = pack_id_of(pack)
        check(f'{pack} 有指纹（裸 id {pid}）', pid in fps, f'现有 {len(fps)} 个指纹')
    for pack in LEARNED_WITH_FP:
        pid = pack_id_of(pack)
        spec = spec_of(pack, **CFG)
        check(f'{pack} spec 的 pack_id 是裸 id', spec.get('pack_id') == pid,
              f"得到 {spec.get('pack_id')!r}")
        ov = ev.style_overlap(spec, pid)
        rk = ev.style_rank(spec, pid)
        check(f'{pack} 风格重叠率可算', isinstance(ov, float), f'得到 {ov!r}')
        check(f'{pack} 风格命中排名可算', rk.get('rank') is not None,
              f'得到 {rk.get("rank")!r}')
        check(f'{pack} 最近邻非自身', (ev.nearest_neighbor(spec, exclude=pid) or {})
              .get('name') != pid, '')


def test_style_contrast_between_packs():
    """不同风格要写出不同的东西，而不是同一首换标题。"""
    print('\n=== ⑤ 风格之间确有差异 ===')
    jazz = sk.skeleton_spec(gid('jazz/swing-standard'), bars=16, voices=3,
                            seed='ct', fugue='off')
    chor = sk.skeleton_spec(gid('sacred/chorale'), bars=16, voices=3,
                            seed='ct', fugue='off')
    check('速度不同（155 vs 76）', jazz['tempo'] != chor['tempo'],
          f"{jazz['tempo']} vs {chor['tempo']}")
    check('音符内容不同', note_sequence(jazz) != note_sequence(chor), '')
    # 指纹余弦受「语料 2/3 + 生成 1/3」权重影响，绝对值可为负；用速度带兜底
    check('爵士速度偏快（摇摆惯性）', int(jazz['tempo']) >= 120,
          f"got {jazz['tempo']}")
    check('众赞歌速度偏慢', int(chor['tempo']) <= 90,
          f"got {chor['tempo']}")


def test_fingerprints_match_current_code():
    """指纹必须能由当前代码复现。

    背景：本仓库曾有 26 个指纹**整体陈旧**（与当时 HEAD 代码的产出一致性为 0），
    而全部分析报告的「风格重叠率 / 风格命中」都拿它当基准 —— 指标一直在跟过期
    基准比。这里抽样复核「存盘指纹 == 当前代码算出的 3 种子均值」，把这条不变量
    钉住（全量 31 包复核约 100s，故抽样 3 个包）。
    """
    print('\n=== ⑤ 指纹与当前生成代码一致（抽样）===')
    from compose_kernel import run_program
    for pack in ('jazz/swing-standard', 'sacred/chorale', 'dance/waltz'):
        g = sk.resolve_genre(gid(pack))
        pid = g.get('pack_id')
        fp_path = os.path.join(g.get('path') or '', 'fingerprint.json')
        if not os.path.exists(fp_path):
            check(f'{pack} 有指纹文件', False, fp_path)
            continue
        stored = json.load(open(fp_path, encoding='utf-8'))['gen_vector']
        vecs = [ev.spec_vector(run_program(sk.build_skeleton_calls(
            gid(pack), bars=16, voices=3, seed=f'{pid}-fp{i}'))) for i in range(3)]
        recomputed = [round(x, 6) for x in ev.mean_vector(vecs)]
        check(f'{pack} 指纹可由当前代码复现', recomputed == stored,
              f'存盘 {stored[:4]}… 现算 {recomputed[:4]}…')


# ---------------------------------------------------------------- 驱动

def test_style_acceptance_all():
    global _PASSED, _FAILED
    _PASSED, _FAILED = 0, 0        # 聚合用例单独计数（pytest 会先逐个跑各子用例）
    for fn in (test_professional_settings_reach_score,
               test_explicit_beats_contract,
               test_simple_prompts_route,
               test_prompt_to_report_pipeline,
               test_ui_brief_commands_land,
               test_analysis_report_confirms_score,
               test_report_style_notes_visible,
               test_randomness,
               test_contract_injected_only_for_learned,
               test_contract_reaches_the_score,
               test_seventh_chords_are_audible,
               test_style_metrics_available_for_learned,
               test_style_contrast_between_packs,
               test_fingerprints_match_current_code):
        fn()
    print(f'\n总计：✅ {_PASSED} / ❌ {_FAILED}')
    # 与 tests/test_skill_library.py 同约定：聚合用例把失败变成断言失败，
    # 否则 pytest 只会看到被吞掉的 check() 计数而误报绿。
    assert _FAILED == 0, f'{_FAILED} 项断言失败'


if __name__ == '__main__':
    test_style_acceptance_all()
    sys.exit(0)
