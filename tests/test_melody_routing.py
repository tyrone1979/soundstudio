# -*- coding: utf-8 -*-
"""旋律路由（brief 指定某乐器独奏）——意图识别 → 参数注入 → 编制重排 → 音区收敛。

链路四层，逐层可验：

  1) ``_detect_solo_instrument``：brief 文本 → 独奏乐器。判定乐器名与「独奏」标记
     邻近共现，因此「大提琴独奏」命中、「用小提琴写旋律」不命中（非提到即独奏）。
  2) ``_apply_solo_routing``：确定性注入 new_score 的 ``solo_instrument``
     （不依赖 LLM 自觉传参，与 ``_ensure_instruments`` 同一思路）。
  3) ``new_score``：把该乐器提到声部 0（旋律声部），其余声部保持高→低相对次序。
  4) ``_s_melody``：把默认音区窗口收进该乐器的可奏音域——否则把主题交给大提琴/圆号
     这类中低音乐器时会写出超音域的音（mid 窗口 62..81 越出大提琴上限 72）。

运行：python -m pytest tests/test_melody_routing.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compose_kernel as K       # noqa: E402
import llm_compose as L          # noqa: E402
from notation import part_range  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {detail}')


# ------------------------------------------------------------------
# 测试用例数据
# ------------------------------------------------------------------

#: (brief 文本, 期望的独奏乐器或 None)
SOLO_TEXT_CASES = [
    ('大提琴独奏，弦乐伴奏', 'cello'),
    ('由长笛主奏呈示部', 'flute'),
    ('flute solo accompanied by strings', 'flute'),
    ('开头让圆号独奏一段', 'horn'),
    ('用小提琴写一段抒情旋律', None),      # 只提到乐器，无独奏意图
    ('写一首明亮的管弦乐曲', None),
    ('', None),
]

_INSTRUMENTS = ['violin', 'cello', 'contrabass']


def _program(solo=None, instruments=None):
    """构造一份确定性脚本序列（与 translator 规则一致）。"""
    insts = list(instruments or _INSTRUMENTS)
    ns = {'title': '夜曲', 'key_sharps': 0, 'mode': 'major', 'meter': '4/4',
          'tempo': 88, 'bars': 16, 'voices': len(insts),
          'instruments': insts, 'seed': 'routing-test'}
    if solo:
        ns['solo_instrument'] = solo
    return {'calls': [
        {'script': 'new_score', 'args': ns},
        {'script': 'set_sections', 'args': {'sections': [
            {'name': 'A', 'bars': 8, 'dynamic': 'p'},
            {'name': 'B', 'bars': 8, 'dynamic': 'mf'}]}},
        {'script': 'set_harmony', 'args': {'section': 'A',
                                           'progression': ['I', 'V7', 'vi', 'IV']}},
        {'script': 'set_harmony', 'args': {'section': 'B',
                                           'progression': ['I', 'ii', 'V7', 'I']}},
        {'script': 'accompaniment', 'args': {'section': 'A',
                                             'pattern': 'sustained'}},
        {'script': 'melody', 'args': {'section': 'A', 'rhythm': 'flowing'}},
        {'script': 'accompaniment', 'args': {'section': 'B',
                                             'pattern': 'block_chords'}},
        {'script': 'melody', 'args': {'section': 'B', 'rhythm': 'flowing'}},
        {'script': 'cadence', 'args': {'bars': 2}},
        {'script': 'finalize', 'args': {}},
    ]}


def _melody_midis(spec):
    """取声部 0（旋律声部）全部发音音高的 MIDI（跳过休止与不可解析项）。

    注意事件首元素可能是「和弦列表」（如八度加厚写成 ['C5','C6']），必须逐个取值——
    否则会把超音域的加厚音漏掉，造成测试假绿。
    """
    out = []
    for ev in (spec['parts'][0]['events'] or []):
        if not ev:
            continue
        nm = ev[0] if isinstance(ev, (list, tuple)) else ev
        if nm is None:
            continue
        names = nm if isinstance(nm, (list, tuple)) else [nm]
        for n in names:
            if not n or str(n).strip().lower().startswith('r'):
                continue
            try:
                out.append(K._midi(n))
            except Exception:
                pass
    return out


# ------------------------------------------------------------------
# 1) 意图识别
# ------------------------------------------------------------------

def test_detect_solo_intent():
    for text, want in SOLO_TEXT_CASES:
        got = L._detect_solo_instrument(text)
        check(f'意图识别 {text!r} → {want}', got == want, f'got={got!r}')


# ------------------------------------------------------------------
# 2) 参数注入
# ------------------------------------------------------------------

def test_apply_solo_routing():
    def _ns(instruments):
        return {'calls': [{'script': 'new_score',
                           'args': {'voices': len(instruments),
                                    'instruments': list(instruments)}}]}

    # 命中独奏意图且乐器在编制内 → 注入 solo_instrument
    prog, note = L._apply_solo_routing(
        _ns(_INSTRUMENTS), {'user_brief': '大提琴独奏，弦乐伴奏'})
    args = prog['calls'][0]['args']
    check('注入·命中时写入 solo_instrument',
          args.get('solo_instrument') == 'cello',
          f"args={args.get('solo_instrument')!r}")
    check('注入·给出说明文字', '旋律路由' in note and 'cello' in note, f'note={note!r}')

    # 无独奏意图 → 不动 program
    prog2, note2 = L._apply_solo_routing(
        _ns(_INSTRUMENTS), {'user_brief': '写一首明亮的管弦乐曲'})
    check('注入·无独奏意图时不改編制',
          'solo_instrument' not in prog2['calls'][0]['args'] and not note2,
          f"args={prog2['calls'][0]['args']}")

    # 独奏乐器不在编制内 → 不擅自改写编制
    prog3, note3 = L._apply_solo_routing(
        _ns(_INSTRUMENTS), {'user_brief': '小号独奏开场'})
    check('注入·乐器不在编制内时不注入',
          'solo_instrument' not in prog3['calls'][0]['args'] and not note3,
          f"args={prog3['calls'][0]['args']}")

    # 无 new_score 调用 → 安全返回
    check('注入·无 new_score 时安全返回',
          L._apply_solo_routing({'calls': []}, None)[0] == {'calls': []}, '')


# ------------------------------------------------------------------
# 3) 编制重排 + 参数校验
# ------------------------------------------------------------------

def test_routing_reorders_score():
    base = K.run_program(_program())
    rout = K.run_program(_program(solo='cello'))
    base_names = [p['name'] for p in base['parts']]
    rout_names = [p['name'] for p in rout['parts']]
    check('重排·未指定路由时维持原高→低次序',
          base_names == ['Violin', 'Cello', 'Contrabass'], f'{base_names}')
    check('重排·独奏乐器提到声部 0',
          rout_names[0] == 'Cello', f'{rout_names}')
    check('重排·其余声部保持相对次序',
          rout_names == ['Cello', 'Violin', 'Contrabass'], f'{rout_names}')


def test_routing_rejects_bad_arg():
    # 独奏乐器不在编制名单内
    try:
        K.run_program(_program(solo='trumpet'))
        check('校验·独奏乐器不在编制内应报错', False, '未抛 KernelError')
    except K.KernelError as e:
        check('校验·独奏乐器不在编制内应报错', '不在 instruments 名单内' in str(e),
              str(e))
    # 给了 solo_instrument 却没给 instruments
    prog = _program()
    prog['calls'][0]['args'].pop('instruments')
    prog['calls'][0]['args']['solo_instrument'] = 'cello'
    try:
        K.run_program(prog)
        check('校验·缺少 instruments 时应报错', False, '未抛 KernelError')
    except K.KernelError as e:
        check('校验·缺少 instruments 时应报错', 'instruments 同时给出' in str(e),
              str(e))


# ------------------------------------------------------------------
# 4) 音区收敛（核心：低音乐器承载旋律也不超音域）
# ------------------------------------------------------------------

def test_fit_register_window():
    # mid 窗口 62..81 遇上大提琴 (36,72)：整体下移贴上界，宽度不变
    lo, hi = K._fit_register_window(62, 81, 36, 72)
    check('窗口·大提琴：平移后仍在音域内', lo >= 36 and hi <= 72, f'{lo},{hi}')
    check('窗口·大提琴：保持原宽度', hi - lo == 19, f'{lo},{hi}')
    # 短笛 (64,103)：窗口下沿低于乐器下限 → 整体上移贴下界
    lo, hi = K._fit_register_window(62, 81, 64, 103)
    check('窗口·短笛：贴下界', lo == 64 and hi <= 103, f'{lo},{hi}')
    # 钢琴 (21,108) 音域远宽于窗口 → 不动
    lo, hi = K._fit_register_window(62, 81, 21, 108)
    check('窗口·宽音域乐器不动', (lo, hi) == (62, 81), f'{lo},{hi}')
    # 定音鼓 (36,60) 音域仍宽于窗口 → 整体下移贴上界并保宽度
    lo, hi = K._fit_register_window(53, 72, 36, 60)
    check('窗口·窄于 (36,60) 时贴下界', (lo, hi) == (41, 60), f'{lo},{hi}')
    # 音域比窗口还窄 → 用满整个音域
    lo, hi = K._fit_register_window(62, 81, 36, 50)
    check('窗口·窄于 (36,50) 时用满音域', (lo, hi) == (36, 50), f'{lo},{hi}')


def test_melody_within_solo_instrument_range():
    """把旋律路由给大提琴后，旋律每个音都必须在大提琴可奏音域内。

    这是「旋律路由」的核心价值：不收敛音区的直接后果就是超音域的音
    （大提琴上限 72，而 mid 窗口上沿 81）。
    """
    spec = K.run_program(_program(solo='cello'))
    rlo, rhi = part_range('cello', 'bass')
    check('音区·大提琴旋律声部匹配',
          spec['parts'][0]['name'].lower() == 'cello',
          spec['parts'][0]['name'])
    midis = _melody_midis(spec)
    check('音区·旋律非空', len(midis) > 0, f'n={len(midis)}')
    bad = [m for m in midis if not (rlo <= m <= rhi)]
    check(f'音区·旋律全部落在大提琴 [{rlo},{rhi}] 内',
          not bad, f'越界音 {sorted(set(bad))[:12]}')


def test_thicken_respects_solo_range():
    """显式 thicken（高潮八度加厚）在路由后不得把旋律推出乐器音域。

    加厚是 `+12` 半音，最容易越过低音乐器上限；守卫的做法是「能加厚才加厚，
    加不了就维持单音」，而不是报错或硬写超音域的音。
    """
    def _with_thicken(solo=None):
        calls = _program(solo=solo)['calls']
        calls.insert(-1, {'script': 'thicken', 'args': {'section': 'A'}})
        return K.run_program({'calls': calls})

    spec = _with_thicken(solo='cello')
    rlo, rhi = part_range('cello', 'bass')
    bad = [m for m in _melody_midis(spec) if not (rlo <= m <= rhi)]
    check('音区·thicken 加厚后仍在大提琴音域内', not bad,
          f'越界音 {sorted(set(bad))[:12]}')
    # 守卫「按需」生效：真正加厚的音仍应存在（不是全部跳过、测试不空过）
    doubled = [e for e in spec['parts'][0]['events']
               if e and isinstance(e[0], (list, tuple))]
    check('音区·thicken 仍对音域内的音生效', len(doubled) > 0, f'doubled={len(doubled)}')
    # 未路由时音域宽得多 → 加厚不受限（数量应明显更多，证明守卫只在路由后介入）
    free = _with_thicken()
    free_d = [e for e in free['parts'][0]['events']
              if e and isinstance(e[0], (list, tuple))]
    check('音区·未路由时加厚不受限', len(free_d) > len(doubled),
          f'free={len(free_d)} vs routed={len(doubled)}')


def test_motif_respects_solo_range():
    """带 motif 的旋律也要落在独奏乐器音域内。

    motif 分支的音阶表原本按 [lo-2, hi+2] 构造，绕过了 _fit_register_window
    建立的窄化窗口：小号（上限 88）会写出 89、低音提琴（上限 67）会写出 69。
    两个数字取自用户的真实 session（d0afa6ac1c / a42be432f6）。
    """
    _orch = ['flute', 'oboe', 'clarinet', 'bassoon', 'horn', 'trumpet',
             'trombone', 'tuba', 'timpani', 'violin', 'violin', 'viola',
             'cello', 'contrabass']
    _small = ['flute', 'clarinet', 'violin', 'viola', 'cello', 'horn',
              'contrabass', 'timpani']
    cases = [
        ('trumpet', 'high', [[1, 'quarter'], [3, 'eighth'], [5, 'eighth'],
                             [1, 'half']], (55, 88), _orch),
        ('contrabass', 'low', [[5, 'quarter'], [1, 'eighth'], [7, 'eighth'],
                               [1, 'half']], (28, 67), _small),
    ]
    for solo, register, motif, (rlo, rhi), insts in cases:
        prog = _program(solo=solo, instruments=insts)
        for c in prog['calls']:
            if c['script'] == 'melody':
                c['args'] = dict(c['args'], register=register, motif=motif)
        spec = K.run_program(prog)
        ms = _melody_midis(spec)
        bad = [m for m in ms if not (rlo <= m <= rhi)]
        check(f'带 motif 的 {solo} 旋律全部在音域内',
              not bad and ms,
              f'[{min(ms)},{max(ms)}] vs [{rlo},{rhi}] 越界={sorted(set(bad))[:5]}')


def test_bass_respects_instrument_range():
    """低音声部也要兜进该乐器音域——不再直接拼 `{根音}{oct_}`。

    低音提琴被路由为独奏后，定音鼓成了最后一个声部（即低音声部），
    broken_arpeggio 的 `{五音}{oct_+1}` 会写到 G4=67，超出定音鼓 36–60。
    """
    insts = ['flute', 'clarinet', 'violin', 'viola', 'cello', 'horn',
             'contrabass', 'timpani']
    prog = _program(solo='contrabass', instruments=insts)
    for c in prog['calls']:
        if c['script'] == 'accompaniment':
            c['args'] = dict(c['args'], pattern='broken_arpeggio',
                             register='mid', density=1)
    spec = K.run_program(prog)
    # 逐个声部核对，不只是旋律声部
    for p in spec['parts']:
        evs = p['events'] or []
        ms = []
        for ev in evs:
            if not ev:
                continue
            n = ev[0]
            for nm in (n if isinstance(n, (list, tuple)) else [n]):
                if nm and not str(nm).strip().lower().startswith('r'):
                    try:
                        ms.append(K._midi(nm))
                    except Exception:
                        pass
        if not ms:
            continue
        lo, hi = part_range(p['name'], p.get('clef', 'treble'))
        bad = [m for m in ms if not (lo <= m <= hi)]
        check(f'声部 {p["name"]} 在音域内', not bad,
              f'[{min(ms)},{max(ms)}] vs [{lo},{hi}] 越界={sorted(set(bad))[:5]}')


def test_routing_is_deterministic():
    """同 seed + 同路由 → 逐事件一致（确定性内核的基本属性）。"""
    a = K.run_program(_program(solo='cello'))
    b = K.run_program(_program(solo='cello'))
    check('确定性·两次生成逐事件一致',
          a['parts'][0]['events'] == b['parts'][0]['events'], '')


def test_melody_routing_all():
    test_detect_solo_intent()
    test_apply_solo_routing()
    test_routing_reorders_score()
    test_routing_rejects_bad_arg()
    test_fit_register_window()
    test_melody_within_solo_instrument_range()
    test_motif_respects_solo_range()
    test_bass_respects_instrument_range()
    test_thicken_respects_solo_range()
    test_routing_is_deterministic()
    print(f'\n总计：✅ {_PASSED} / ❌ {_FAILED}')
    assert _FAILED == 0, f'{_FAILED} 项失败'
