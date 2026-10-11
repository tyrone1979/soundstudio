# -*- coding: utf-8 -*-
"""提示词 → 乐谱 → 分析报告 一致性测试。

对每个测试用例执行同一条链路（与 UI 完全一致的代码路径）：

    中文提示词 → 确定性脚本序列（离线用；与 translator 同规则）
              → compose_kernel.run_program → spec
              → llm_compose.spec_to_music21 → music21 乐谱
              → score_studio.m21_to_model → 内部乐谱模型
              → score_studio.export_analysis_report → 分析报告 Markdown

然后三方对照断言「正确性」：
    A. 提示词要求   ↔ spec（速度/拍号/小节/调性/转调/赋格/大谱表）
    B. spec        ↔ 分析报告文本（基本信息 / 转调 / 第 3 节无 ❌ / 第 4 节透明化）
    C. spec        ↔ MusicXML（调号位置 / staves / 往返解析）

离线确定性生成不调 LLM API；设环境变量 COMPOSE_E2E_LLM=1 时，额外运行
真实 LLM 管线（planner → translator → 校验修复），并用同一套断言检查
「提示词要求是否被管线如实落到谱面与报告」。

运行：python -m pytest tests/test_prompt_to_report.py -q
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compose_kernel as K          # noqa: E402
import llm_compose as L             # noqa: E402
import score_studio as SS           # noqa: E402

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


# ============================================================
# 测试用例：提示词 + 「与 translator 同规则」的确定性翻译
# ============================================================

# genre 速度基调（离线模拟 genre pack 的 tempo 约束）
_NOCTURNE_PROFILE = {'tempo': {'min': 60, 'max': 80, 'default': 72}}
_WALTZ_PROFILE = {'tempo': {'min': 120, 'max': 160, 'default': 132}}

CASES = [
    # 用例 1：显式速度直通 + B 段转调 + 不许自加赋格 + 钢琴大谱表
    dict(
        name='夜曲·显式速度·转调·无赋格',
        prompt=(
            '【要求】\n'
            '1. C 大调起步，钢琴曲，4/4，共 16 小节。\n'
            '2. 速度固定 ♩=100（显式指定，即使超出夜曲常规 60–80 也不许改）。\n'
            '3. A 段 8 小节安静：柱式长音，和声 I–V7–vi–IV，力度 p。\n'
            '4. B 段 8 小节转 A 小调（不换调号），和声 i–iv–V7–i，力度 mf。\n'
            '5. 不要赋格，不要复调。'
        ),
        # 翻译（与 translator 同规则）：显式 ♩=100 直通 new_score；
        # B 段转调用 set_harmony 的 mode='minor'；brief 未提赋格 → 不调 fugue
        calls=[
            {'script': 'new_score', 'args': {'title': '雾中回声', 'key_sharps': 0,
                                             'mode': 'major', 'meter': '4/4',
                                             'tempo': 100, 'bars': 16}},
            {'script': 'set_sections', 'args': {'sections': [
                {'name': 'A', 'bars': 8, 'dynamic': 'p', 'tempo_text': 'Moderato'},
                {'name': 'B', 'bars': 8, 'dynamic': 'mf'}]}},
            {'script': 'set_harmony', 'args': {'section': 'A',
                                               'progression': ['I', 'V7', 'vi', 'IV']}},
            {'script': 'set_harmony', 'args': {'section': 'B',
                                               'progression': ['i', 'iv', 'V7', 'i'],
                                               'mode': 'minor'}},
            {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'sustained'}},
            {'script': 'melody', 'args': {'section': 'A', 'rhythm': 'flowing'}},
            {'script': 'accompaniment', 'args': {'section': 'B', 'pattern': 'block_chords'}},
            {'script': 'melody', 'args': {'section': 'B', 'rhythm': 'driving'}},
            {'script': 'cadence', 'args': {'bars': 2}},
            {'script': 'finalize', 'args': {}},
        ],
        genre_profile=_NOCTURNE_PROFILE, explicit_tempo=True,
        expect=dict(
            tempo=100, meter='4/4', bars=16, sharps=0, mode='major',
            parts=2, fugue=False, grand_staff=True,
            key_changes=[[9, 0, 'minor']],
            report_key='C 大调',
            report_modulation='第 9 小节 → A 小调',
            xml_fifths=['0', '0'], xml_minor_mode=True, xml_score_parts=1,
        ),
    ),
    # 用例 2：未给速度 → genre 只做提示（基调钳制生效）；圆舞曲 3/4；不提赋格 → 无赋格
    dict(
        name='圆舞曲·genre 速度提示·无赋格',
        prompt='写一首轻快的圆舞曲，3/4 拍，16 小节，G 大调，A 段安静些、B 段热闹些。不要赋格。',
        # 翻译：用户没给速度 → translator 按基调区间取值；这里故意取基线 100，
        # 让 genre 钳制（120–160，默认 132）把它纠正回规范 → 最终 ♩=132
        calls=[
            {'script': 'new_score', 'args': {'title': '林中圆舞', 'key_sharps': 1,
                                             'mode': 'major', 'meter': '3/4',
                                             'tempo': 100, 'bars': 16}},
            {'script': 'set_sections', 'args': {'sections': [
                {'name': 'A', 'bars': 8, 'dynamic': 'p'},
                {'name': 'B', 'bars': 8, 'dynamic': 'f'}]}},
            {'script': 'set_harmony', 'args': {'section': 'A',
                                               'progression': ['I', 'V7', 'I', 'IV']}},
            {'script': 'set_harmony', 'args': {'section': 'B',
                                               'progression': ['I', 'ii', 'V7', 'I']}},
            {'script': 'accompaniment', 'args': {'section': 'A', 'pattern': 'waltz'}},
            {'script': 'melody', 'args': {'section': 'A', 'rhythm': 'flowing'}},
            {'script': 'accompaniment', 'args': {'section': 'B', 'pattern': 'waltz'}},
            {'script': 'melody', 'args': {'section': 'B', 'rhythm': 'flowing'}},
            {'script': 'cadence', 'args': {'bars': 2}},
            {'script': 'finalize', 'args': {}},
        ],
        genre_profile=_WALTZ_PROFILE, explicit_tempo=False,
        expect=dict(
            tempo=132, meter='3/4', bars=16, sharps=1, mode='major',
            parts=2, fugue=False, grand_staff=True,
            key_changes=[],
            report_key='G 大调',
            report_modulation=None,
            xml_fifths=['1'], xml_minor_mode=False, xml_score_parts=1,
        ),
    ),
    # 用例 3：brief 明确要求赋格 → 允许 fugue；三声部；小调显示关系小调名
    dict(
        name='赋格·用户明确要求',
        prompt='请写一首 a 小调三声部赋格，4/4，16 小节，速度 96。',
        calls=[
            {'script': 'new_score', 'args': {'title': '小调赋格习作', 'key_sharps': 0,
                                             'mode': 'minor', 'meter': '4/4',
                                             'tempo': 96, 'bars': 16, 'voices': 3}},
            {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 16}]}},
            {'script': 'fugue', 'args': {}},
            {'script': 'finalize', 'args': {}},
        ],
        genre_profile=None, explicit_tempo=True,
        expect=dict(
            tempo=96, meter='4/4', bars=16, sharps=0, mode='minor',
            parts=3, fugue=True, grand_staff=False,
            key_changes=[],
            report_key='A 小调',
            report_modulation=None,
            xml_fifths=['0', '0', '0'], xml_minor_mode=False, xml_score_parts=3,
        ),
    ),
]


# ============================================================
# 生成 + 报告（与 UI 相同代码路径）
# ============================================================

def generate_and_report(case, out_dir, spec_hook=None):
    """跑完 提示词 → spec → music21 → 报告 全链路，返回 (spec, m21, report_text, trace)。"""
    kernel_trace = []
    trace = {
        'user_text': case['prompt'],
        'pipeline': 'translate',
        'skill_ids': ['compose-full'],
        'llm_calls': [],
        'emotion': None,
        'emotion_applied': False,
        'constraint_fixes': [],
        'kernel_calls': kernel_trace,
        'repair': None,
        'result': {},
    }
    spec = K.run_program({'calls': case['calls']}, trace=kernel_trace)
    # genre 速度基调（与 _run_translator 相同的调用方式）
    fixes = L._clamp_tempo_to_genre(spec, case['genre_profile'],
                                    explicit_tempo=case['explicit_tempo'])
    trace['constraint_fixes'] = fixes
    if spec_hook:
        spec_hook(spec, case)
    m21 = L.spec_to_music21(spec)
    try:
        not_log = L.last_notation_log()
    except Exception:
        not_log = None
    model = SS.m21_to_model(m21, title=str(spec.get('title') or ''),
                            composer=str(spec.get('composer') or ''))
    path = os.path.join(out_dir, f"{case['name']}_report.md")
    report, _counts = SS.export_analysis_report(model, path, spec=spec,
                                                notation_log=not_log,
                                                compose_trace=trace)
    dev_path = os.path.splitext(path)[0] + '_dev.md'
    with open(dev_path, encoding='utf-8') as f:
        dev = f.read()
    return spec, m21, report, trace, dev


# ============================================================
# 断言：提示词 ↔ spec ↔ 报告 ↔ MusicXML
# ============================================================

def _field(text, label):
    m = re.search(rf'^- {label}:\s*(.+)$', text, re.M)
    return m.group(1).strip() if m else None


def assert_case(case, spec, m21, report, xml_path, dev=''):
    exp = case['expect']
    tag = case['name']

    # ---- A. 提示词 ↔ spec ----
    check(f'[{tag}] spec.tempo == 提示词要求', spec.get('tempo') == exp['tempo'],
          f"spec={spec.get('tempo')} expect={exp['tempo']}")
    check(f'[{tag}] spec.meter == 提示词要求', spec.get('time_signature') == exp['meter'],
          f"spec={spec.get('time_signature')}")
    check(f'[{tag}] spec.bars == 提示词要求', int(spec.get('bars') or 0) == exp['bars'],
          f"spec={spec.get('bars')}")
    check(f'[{tag}] spec.key_sharps/mode', (spec.get('key_sharps'), spec.get('mode'))
          == (exp['sharps'], exp['mode']), f"spec=({spec.get('key_sharps')}, {spec.get('mode')})")
    check(f'[{tag}] spec.fugue（brief 提到才有）', bool(spec.get('fugue')) == exp['fugue'],
          f"spec={spec.get('fugue')}")
    has_group = bool(spec.get('staff_groups'))
    check(f'[{tag}] 大谱表标记', has_group == exp['grand_staff'], f'staff_groups={spec.get("staff_groups")}')
    kc = [list(k) for k in (spec.get('key_changes') or [])]
    check(f'[{tag}] spec.key_changes', kc == exp['key_changes'], f'spec={kc}')

    # ---- B. spec ↔ 报告 ----
    check(f'[{tag}] 报告标题', f"作曲分析报告：{spec.get('title')}" in report, report[:80])
    key_line = _field(report, '调性') or ''
    # 报告调性行带括号说明（如「C 大调（0 个升号）」），前缀匹配即可
    check(f'[{tag}] 报告·调性', key_line.startswith(exp['report_key']),
          f"报告={key_line!r} expect 前缀={exp['report_key']!r}")
    if exp['report_modulation']:
        check(f'[{tag}] 报告·转调', _field(report, '转调') == exp['report_modulation'],
              f"报告={_field(report, '转调')!r} expect={exp['report_modulation']!r}")
    else:
        check(f'[{tag}] 报告·无转调行', _field(report, '转调') is None,
              f"报告={_field(report, '转调')!r}")
    check(f'[{tag}] 报告·拍号', _field(report, '拍号') == exp['meter'],
          f"报告={_field(report, '拍号')!r}")
    tempo_line = _field(report, '速度') or ''
    m = re.search(r'四分音符\s*=\s*(\d+)\s*BPM', tempo_line)
    check(f'[{tag}] 报告·速度', bool(m) and int(m.group(1)) == exp['tempo'],
          f'报告={tempo_line!r} expect={exp["tempo"]}')
    n_staves = len(re.findall(r'^- 声部 \d+:', report, re.M))
    check(f'[{tag}] 报告·声部数', n_staves == exp['parts'], f'报告={n_staves}')
    # 第 3 节：汇总行「❌ N」中的 N 必须为 0（❌ 字符出现在图例属正常）
    m_fail = re.search(r'❌\s*(\d+)', report)
    check(f'[{tag}] 报告·第 3 节 ❌ 计数为 0', bool(m_fail) and int(m_fail.group(1)) == 0,
          f'汇总={m_fail.group(0) if m_fail else "未找到"}')
    # 4.5 表格行（以 | 开头）中不得出现 🔴（图例行里的 🔴 属正常）——4.5 在程序员版
    red_rows = [ln for ln in dev.splitlines()
                if ln.lstrip().startswith('|') and '🔴' in ln]
    check(f'[{tag}] 报告·4.5 无 🔴 表行', not red_rows, f'{red_rows[:1]}')
    # 程序员版拆分检查：透明化只出现在 _dev，编辑者版不含
    check(f'[{tag}] 报告·拆分（透明化仅在 _dev）',
          '## 4. 作曲过程透明化' in dev and '作曲过程透明化' not in report)
    check(f'[{tag}] 报告·拆分（基本信息在两版）',
          '## 2. 基本信息' in report and '## 2. 谱面事实' in dev)
    kc4 = 'fugue' in (re.search(r'### 4\.3.*?(?=### 4\.4)', dev, re.S).group(0)
                      if re.search(r'### 4\.3.*?(?=### 4\.4)', dev, re.S) else '')
    check(f'[{tag}] 报告·4.3 脚本表 fugue 与 spec 一致', kc4 == exp['fugue'],
          f'4.3 含 fugue={kc4} spec.fugue={spec.get("fugue")}')
    if exp['grand_staff']:
        check(f'[{tag}] 报告·含 Piano 与 Piano LH 两声部',
              'Piano，' in report and 'Piano LH' in report, '缺少 Piano / Piano LH')
    # 终止式必须按末段调性判定：终止式行只在 ⚠️/❌ 时进第 3 节表格，
    # ✅ 时只体现在汇总计数——所以这里断言「没有非 ✅ 的终止式行」
    bad_cadence = [ln for ln in report.splitlines()
                   if ln.lstrip().startswith('| 终止式') and '✅' not in ln]
    check(f'[{tag}] 报告·终止式判定通过', not bad_cadence, bad_cadence[:1])

    # ---- C. spec ↔ MusicXML ----
    txt = open(xml_path, encoding='utf-8').read()
    fifths = re.findall(r'<fifths>(-?\d+)</fifths>', txt)
    check(f'[{tag}] XML·调号序列', fifths == exp['xml_fifths'], f'XML={fifths}')
    n_parts = txt.count('<score-part id=')
    check(f'[{tag}] XML·score-part 数', n_parts == exp['xml_score_parts'], f'XML={n_parts}')
    staves = re.findall(r'<staves>(\d)</staves>', txt)
    check(f'[{tag}] XML·大谱表 staves=2', (staves == ['2']) == exp['grand_staff'],
          f'staves={staves}')
    if exp['grand_staff']:
        check(f'[{tag}] XML·staff 1/2 都有音符',
              sorted(set(re.findall(r'<staff>(\d)</staff>', txt))) == ['1', '2'],
              '缺少 staff 1 或 2')
    if exp['key_changes'] and exp['xml_minor_mode']:
        check(f'[{tag}] XML·转调小节带 <mode>minor</mode>', '<mode>minor</mode>' in txt,
              '缺少 mode=minor')
    return txt


# ============================================================
# 离线确定性测试（默认运行）
# ============================================================

def test_prompt_to_report_offline():
    print('[离线] 提示词 → 乐谱 → 分析报告 三方对照')
    out_dir = tempfile.mkdtemp(prefix='p2r_')
    for case in CASES:
        spec, m21, report, _trace, dev = generate_and_report(case, out_dir)
        xml_path = os.path.join(out_dir, f"{case['name']}.musicxml")
        m21.write('musicxml', fp=xml_path)
        assert_case(case, spec, m21, report, xml_path, dev)
    print(f'  离线汇总：✅ {_PASSED} / ❌ {_FAILED}')


# ============================================================
# 真实 LLM 管线测试（COMPOSE_E2E_LLM=1 时运行，需已配置 API）
# ============================================================

def test_prompt_to_report_llm():
    if not os.environ.get('COMPOSE_E2E_LLM'):
        print('[LLM] 跳过（设 COMPOSE_E2E_LLM=1 启用真实管线测试）')
        return
    import llm_compose as L2
    if not (L2.llm_config().get('api_key') if hasattr(L2, 'llm_config') else True):
        print('[LLM] 未配置 API，跳过')
        return
    print('[LLM] 提示词 → planner/translator 真实管线 → 报告对照')
    out_dir = tempfile.mkdtemp(prefix='p2r_llm_')
    # LLM 模式只跑前两个非赋格用例（验证不越权 + 显式要求落谱）
    for case in [c for c in CASES if not c['expect']['fugue']]:
        session = L2.ComposeSession()
        L2._init_compose_trace(session, case['prompt'])
        _m21, spec, _msg = L2.compose_pipeline(session, case['prompt'])
        m21 = L2.spec_to_music21(spec)
        report_path = os.path.join(out_dir, f"{case['name']}_report.md")
        model = SS.m21_to_model(m21, title=str(spec.get('title') or ''),
                                composer=str(spec.get('composer') or ''))
        report, _c = SS.export_analysis_report(model, report_path, spec=spec,
                                               notation_log=L2.last_notation_log(),
                                               compose_trace=session.compose_trace)
        exp = case['expect']
        # 提示词显式要求的值必须如实落谱（越权约束的回归断言）
        check(f'[LLM·{case["name"]}] spec.tempo', spec.get('tempo') == exp['tempo'],
              f"spec={spec.get('tempo')}")
        check(f'[LLM·{case["name"]}] spec.meter', spec.get('time_signature') == exp['meter'],
              f"spec={spec.get('time_signature')}")
        check(f'[LLM·{case["name"]}] spec.bars', int(spec.get('bars') or 0) == exp['bars'],
              f"spec={spec.get('bars')}")
        check(f'[LLM·{case["name"]}] spec.fugue 未越权', bool(spec.get('fugue')) is False,
              f"spec.fugue={spec.get('fugue')}")
        check(f'[LLM·{case["name"]}] 报告·速度与谱面一致',
              str(spec.get('tempo')) in report, '报告未出现谱面速度')
        check(f'[LLM·{case["name"]}] 报告·第 3 节无 ❌', '❌' not in report, '')
        if exp['key_changes']:
            check(f'[LLM·{case["name"]}] spec.key_changes 转调落谱',
                  bool(spec.get('key_changes')), f"spec={spec.get('key_changes')}")


if __name__ == '__main__':
    test_prompt_to_report_offline()
    test_prompt_to_report_llm()
    print(f'\n总计：✅ {_PASSED} / ❌ {_FAILED}')
    sys.exit(1 if _FAILED else 0)
