"""第一类二声部对位（species / bicinium）：钢琴大谱表 · 1 小节 1 音。

权威规则文本见 ``COUNTERPOINT_RULES_DOC`` / ``get_counterpoint_rules_doc()``；
凡涉及对位（species / 一比一 / 对位法）均须遵守，并与 ``validate_two_part_species`` 一致。

确定性生成 + 校验；不调 LLM。
"""
from __future__ import annotations

import random
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .assist_engine import blank_score
from .models import Note, NoteDuration, Pitch, Score

# 简单音程（半音）：小三/大三/小六/大六
_THIRD_SIXTH = frozenset({3, 4, 8, 9})
# 中间允许：三六 + 纯五/八（反向到达时优先）
_MIDDLE_OK = _THIRD_SIXTH | frozenset({7, 12})
# 开头可用的协和：同度/纯五/八度 + 三六度
_OPENING_OK = _THIRD_SIXTH | frozenset({0, 7, 12})
# 纯四度（含复音程归约）
_PERFECT_FOURTH_PC = 5
# 跳进阈值（半音）：≥ 小三度算跳进；1–2 半音为级进
_LEAP_MIN = 3
# 连续平行 3/6 度最多允许几步（再多损独立性）
_MAX_PARALLEL_36_RUN = 2
# 反向进行占比下限
_MIN_CONTRARY_RATIO = 0.45

# ---------------------------------------------------------------------------
# 对位法规则（单一事实来源：帮助文档 / Planner / Translator / 校验共用）
# ---------------------------------------------------------------------------
COUNTERPOINT_RULES_DOC = """## 对位法规则（第一类二声部 · 权威）

凡用户请求涉及**对位 / 一比一 / 第一类 / species / note-against-note**（且未明确要求赋格）时，
一律按本规则执行。对位 ≠ 赋格：未点名赋格/卡农/创意曲时不要走赋格。

### 谱面与织体
1. **钢琴大谱表**：上方高音谱表、下方低音谱表（RH/LH，`piano`×2）。
2. **一比一（第一类）**：每小节每声部**恰好一个全音符**；禁止一小节多音或空小节。
3. 默认 8–16 小节；调性清晰，开头与结尾同调。

### 纵向音程
4. **开头**：低音为主音；高音落在主三和弦；可用同度/三/六/五/八度。
5. **中间小节**：以 **3 度、6 度** 为主；允许经**反向**到达的 **5 度、8 度**；**禁止纯 4 度**（含复音程归约后的纯四）。
6. **结尾**：两声部成**八度**；须**反向**到达（两声部都运动，禁止同向或一静一动）。

### 终止式（硬）
7. 倒数第二→末小节：
   - 高音：**导音 → 主音**（上行半音/级进 +1）
   - 低音：**二级 → 主音**（下行级进 −2）

### 声部独立性与进行
8. **禁止两声部同时跳进**（一侧跳进时另一侧必须级进）。
9. **跳进后须反向级进**（跳进后的下一音须朝反方向级进收回）。
10. **相邻小节同声部不得同音高**（禁止原地重复）。
11. **限制连续平行 3/6 度**（连续同行同度级平行 ≤ 2 步）；**反向进行占比 ≥ 45%**。
12. **高低声部最高音须错开小节**（不可同时到达各自峰值）。
13. **低音以级进为主**（级进比 ≥ 60%）。
14. **开头与结尾音域一致**：低音首尾同音高；高音首尾同一八度音域。

### 流程约定
15. **确定性写谱**：意图由 Planner 识别（`species: true`）后由内核生成并校验；不把音符交给 LLM。
16. **每次作曲重新随机 seed**（可复现调试时可显式固定 seed）。
17. **禁止**在仅「对位」请求下自动插入赋格段。
18. **根据当前乐谱**：提示「根据/基于当前打开的乐谱写 1:1 对位」时，从当前谱每小节抽一音作定旋律，另一声部按本规则生成（定旋律侧旋律约束可微调）。

> 实现与校验：`music_score/two_part_species.py`（`validate_two_part_species`）。
> 聊天内查阅：`/help 对位法` 或 `/help 二声部对位`。
"""


def get_counterpoint_rules_doc() -> str:
    """返回对位法权威规则（帮助文档与 LLM 硬约束共用）。"""
    return COUNTERPOINT_RULES_DOC.strip()


def counterpoint_rules_compact() -> str:
    """给 Planner / 短提示用的压缩版硬约束清单。"""
    return (
        '对位硬约束：钢琴大谱表；1音/小节；中间3/6为主、可反向至5/8；禁纯4；'
        '禁同时跳进；跳进后反向级进；限平行3/6；反向≥45%；高低峰值错开；'
        '低音级进为主；首尾同音域；结尾反向八度且高音导→主、低音Ⅱ↓Ⅰ；'
        '对位≠赋格；每次 seed 随机。'
    )


def _pc(midi: int) -> int:
    return int(midi) % 12


def _leading_tone_pc(tonic_pc: int) -> int:
    return (int(tonic_pc) + 11) % 12


def _degree2_pc(tonic_pc: int) -> int:
    return (int(tonic_pc) + 2) % 12


def _tonic_triad_pcs(tonic_pc: int, mode: str = 'major') -> frozenset:
    """主三和弦音级集合（大调 1-3-5；小调 1-♭3-5）。"""
    t = int(tonic_pc) % 12
    third = 3 if str(mode).lower().startswith('min') else 4
    return frozenset({t, (t + third) % 12, (t + 7) % 12})


def _is_leap(a: int, b: int) -> bool:
    return abs(int(b) - int(a)) >= _LEAP_MIN


def _is_step_motion(a: int, b: int) -> bool:
    d = abs(int(b) - int(a))
    return 1 <= d <= 2


def _leap_recovery_ok(prev: int, mid: int, nxt: int) -> bool:
    """若 prev→mid 为跳进，则 mid→nxt 必须反向级进。"""
    if not _is_leap(prev, mid):
        return True
    if not _is_step_motion(mid, nxt):
        return False
    return (int(mid) - int(prev)) * (int(nxt) - int(mid)) < 0


def wants_species_from_current(text: str) -> bool:
    """是否「根据当前打开的乐谱写 1:1 对位」。"""
    t = str(text or '').strip()
    if not t:
        return False
    tl = t.lower()
    if re.search(r'赋格|fugue|fugal|卡农|canon|创意曲|invention', tl, re.I):
        return False
    from_cur = bool(re.search(
        r'根据当前|基于当前|按当前|当前(打开的)?乐谱|现有乐谱|本曲|这首(曲子|乐谱)?|'
        r'from\s+(the\s+)?current|based\s+on\s+(the\s+)?(current|open)|'
        r'against\s+(the\s+)?(current|open)|using\s+(the\s+)?(current|open)',
        t, re.I))
    if not from_cur:
        return False
    one_to_one = bool(re.search(
        r'1\s*[:：比]\s*1|1比1|一对一|一比一|壹比壹|第一类|species\s*1|'
        r'first\s*species|note[-\s]?against[-\s]?note|一小节一|每小节一',
        t, re.I))
    species_word = bool(re.search(
        r'对位|species|counterpoint|bicinium', t, re.I))
    return one_to_one or species_word


def detect_two_part_species(text: str) -> Optional[Dict[str, Any]]:
    """识别「二声部 · 钢琴大谱表 · 第一类对位」类提示词。

    明确「二声部 + 1:1/第一类对位」即命中；含「赋格」则排除（走赋格路径）。
    「根据当前乐谱写 1:1 对位」也命中（不必再写「二声部」）。
    """
    t = str(text or '').strip()
    if not t:
        return None
    tl = t.lower()

    # 点名赋格 → 不是第一类对位
    if re.search(r'赋格|fugue|fugal|卡农|canon|创意曲|invention', tl, re.I):
        return None

    from_current = wants_species_from_current(t)

    two_voice = bool(re.search(
        r'2\s*声部|两声部|二声部|双声部|two[-\s]?part|2[-\s]?voice|bicinium',
        t, re.I))
    # 根据当前乐谱写 1:1 对位：隐含二声部
    if not two_voice and not from_current:
        return None

    grand = bool(re.search(
        r'高音谱表|低音谱表|大谱表|钢琴|treble|bass\s*clef|grand\s*staff|piano',
        tl, re.I))
    one_to_one = bool(re.search(
        r'1\s*小节\s*一|一小节一|每小节一|一节一音|one\s*note\s*per|'
        r'1\s*[:：比]\s*1|1比1|一对一|一比一|壹比壹|'
        r'全音符|第一类|species\s*1|first\s*species|note[-\s]?against[-\s]?note',
        t, re.I))
    thirds_sixths = bool(re.search(
        r'3\s*度.*6\s*度|6\s*度.*3\s*度|三度.*六度|六度.*三度|'
        r'thirds?\s*and\s*sixths?|sixths?\s*and\s*thirds?',
        t, re.I))
    no_p4 = bool(re.search(
        r'不能用?\s*纯\s*4|禁止\s*纯\s*4|不用\s*纯\s*4|纯四度|perfect\s*4',
        t, re.I))
    contrary_oct = bool(re.search(
        r'反向.{0,8}[8八]\s*度|contrary.{0,12}octave|[8八]\s*度.{0,8}反向',
        t, re.I))
    stagger = bool(re.search(
        r'最高.{0,12}错开|错开.{0,12}最高|不同的小节|stagger',
        t, re.I))
    bass_step = bool(re.search(
        r'级进为主|以级进|stepwise|跳进为辅',
        t, re.I))
    species_word = bool(re.search(
        r'对位|species|counterpoint|声部规则|bicinium',
        t, re.I))

    hard_layout = grand or one_to_one
    interval_rules = thirds_sixths or no_p4 or contrary_oct or stagger
    rule_count = sum(
        [thirds_sixths, no_p4, contrary_oct, stagger, bass_step])
    # 接受：
    #   A) 二声部 + 1:1/第一类 + 对位词  → 短提示「2声部1比1对位」
    #   B) 二声部 + (谱表/1:1) + 任一条音程/错峰规则
    #   C) 二声部 + 大谱表 + 1:1 + 对位词
    #   D) 二声部 + ≥2 条音程规则
    #   E) 根据当前乐谱 + 1:1/对位（隐含二声部）
    ok = (
        from_current
        or (one_to_one and species_word)
        or (hard_layout and interval_rules)
        or (grand and one_to_one and species_word)
        or (interval_rules and rule_count >= 2 and species_word)
        or (rule_count >= 3)
    )
    if not ok:
        return None

    return {
        'kind': 'two_part_species',
        'grand_staff': True,
        'one_note_per_bar': True,
        'middle_thirds_sixths': True,
        'no_perfect_fourth': True,
        'contrary_octave_end': True,
        'stagger_peaks': True,
        'bass_stepwise': True,
        'from_current': from_current,
        'matched': {
            'two_voice': two_voice or from_current,
            'grand': grand,
            'one_per_bar': one_to_one,
            'thirds_sixths': thirds_sixths,
            'no_p4': no_p4,
            'contrary_oct': contrary_oct,
            'stagger': stagger,
            'bass_step': bass_step,
            'species_word': species_word,
            'from_current': from_current,
        },
    }


def _diatonic_midis(lo: int, hi: int, tonic_pc: int = 0,
                    mode: str = 'major') -> List[int]:
    if mode == 'minor':
        steps = {0, 2, 3, 5, 7, 8, 10}  # 自然小调
    else:
        steps = {0, 2, 4, 5, 7, 9, 11}
    out = []
    for m in range(lo, hi + 1):
        if (m - tonic_pc) % 12 in steps:
            out.append(m)
    return out


def _iv_semitones(upper: int, lower: int) -> int:
    return int(upper) - int(lower)


def _is_perfect_fourth(upper: int, lower: int) -> bool:
    d = _iv_semitones(upper, lower)
    if d <= 0:
        return False
    return (d % 12) == _PERFECT_FOURTH_PC


def _is_third_or_sixth(upper: int, lower: int) -> bool:
    d = _iv_semitones(upper, lower)
    return d in _THIRD_SIXTH


def _is_middle_ok_interval(upper: int, lower: int) -> bool:
    d = _iv_semitones(upper, lower)
    return d in _MIDDLE_OK


def _is_octave(upper: int, lower: int) -> bool:
    """结尾要求单八度（12 半音），不含同度/双八度。"""
    return _iv_semitones(upper, lower) == 12


def _motion_signs(pu: int, pb: int, u: int, b: int) -> Tuple[int, int]:
    du = int(u) - int(pu)
    db = int(b) - int(pb)
    su = 0 if du == 0 else (1 if du > 0 else -1)
    sb = 0 if db == 0 else (1 if db > 0 else -1)
    return su, sb


def _is_contrary(pu: int, pb: int, u: int, b: int) -> bool:
    su, sb = _motion_signs(pu, pb, u, b)
    return su != 0 and sb != 0 and su * sb < 0


def _is_similar_parallel_36(pu: int, pb: int, u: int, b: int) -> bool:
    """同向且音程同为 3 或 6 度（平行三六）。"""
    prev = _iv_semitones(pu, pb)
    cur = _iv_semitones(u, b)
    if prev not in _THIRD_SIXTH or cur not in _THIRD_SIXTH:
        return False
    if prev != cur:
        return False
    su, sb = _motion_signs(pu, pb, u, b)
    return su != 0 and sb != 0 and su == sb


def _midi_name(midi: int) -> str:
    try:
        from music21 import pitch as m21pitch
        return m21pitch.Pitch(midi=int(midi)).nameWithOctave
    except Exception:
        p = Pitch.from_midi(int(midi))
        acc = {0: '', 1: '#', -1: 'b', 2: '##', -2: 'bb'}.get(
            p.accidental.value, '')
        return f'{p.name.name}{acc}{p.octave}'


def validate_two_part_species(
    upper: Sequence[int],
    lower: Sequence[int],
    *,
    allow_opening_free: bool = True,
    bass_step_min_ratio: float = 0.6,
    tonic_pc: Optional[int] = 0,
    mode: str = 'major',
    fixed_cf: Optional[str] = None,
) -> List[str]:
    """返回违规说明列表（空 = 全部通过）。

    tonic_pc：主音音级（0=C…）。为 None 时跳过导音/二级/开头主和弦校验。
    fixed_cf：'upper'|'lower'|None — 定旋律固定时放宽该声部自身旋律硬约束。
    """
    issues: List[str] = []
    n = min(len(upper), len(lower))
    if n < 2:
        return ['至少需要 2 小节']
    if len(upper) != len(lower):
        issues.append(f'声部小节数不一致：上={len(upper)} 下={len(lower)}')
    fx = (fixed_cf or '').lower().strip()
    if fx not in ('upper', 'lower'):
        fx = ''

    for i in range(n):
        u, b = int(upper[i]), int(lower[i])
        if u <= b:
            issues.append(f'第 {i + 1} 小节声部交叉或同度叠置异常：上={u} 下={b}')
        if _is_perfect_fourth(u, b):
            issues.append(f'第 {i + 1} 小节出现纯四度（禁止）')
        d = _iv_semitones(u, b)
        if i == 0 and allow_opening_free:
            if d not in _OPENING_OK:
                issues.append(f'第 1 小节音程不在允许集合（同度/三六/五/八）：{d} 半音')
        elif i == n - 1:
            if not _is_octave(u, b):
                issues.append(f'结尾须为八度，实际 {d} 半音')
        else:
            if not _is_middle_ok_interval(u, b):
                issues.append(
                    f'第 {i + 1} 小节中间仅允许 3/6/5/8 度，实际 {d} 半音')

    # 相邻小节同声部不得同音高（定旋律侧放宽）
    for i in range(n - 1):
        if int(upper[i]) == int(upper[i + 1]) and fx != 'upper':
            issues.append(f'高音声部第 {i + 1}–{i + 2} 小节同音高（禁止）')
        if int(lower[i]) == int(lower[i + 1]) and fx != 'lower':
            issues.append(f'低音声部第 {i + 1}–{i + 2} 小节同音高（禁止）')

    # 禁止两声部同时跳进；跳进后须反向级进
    for i in range(n - 1):
        u_leap = _is_leap(upper[i], upper[i + 1])
        b_leap = _is_leap(lower[i], lower[i + 1])
        if u_leap and b_leap:
            issues.append(
                f'第 {i + 1}–{i + 2} 小节两声部同时跳进（须一侧级进）')
    for voice_name, line, skip in (
        ('高音', upper, fx == 'upper'),
        ('低音', lower, fx == 'lower'),
    ):
        if skip:
            continue
        for i in range(n - 2):
            a, b, c = int(line[i]), int(line[i + 1]), int(line[i + 2])
            if _is_leap(a, b) and not _leap_recovery_ok(a, b, c):
                issues.append(
                    f'{voice_name}第 {i + 1}–{i + 3} 小节：跳进后须反向级进'
                    f'（{a}→{b}→{c}）')

    # 限制连续平行 3/6；统计反向占比
    if n >= 2:
        run = 0
        contrary = 0
        movable = 0
        for i in range(n - 1):
            pu, pb = int(upper[i]), int(lower[i])
            u, b = int(upper[i + 1]), int(lower[i + 1])
            su, sb = _motion_signs(pu, pb, u, b)
            if su != 0 and sb != 0:
                movable += 1
                if su * sb < 0:
                    contrary += 1
            if _is_similar_parallel_36(pu, pb, u, b):
                run += 1
                if run > _MAX_PARALLEL_36_RUN:
                    issues.append(
                        f'第 {i + 2 - _MAX_PARALLEL_36_RUN}–{i + 2} 小节连续平行'
                        f'3/6 度过多（≤{_MAX_PARALLEL_36_RUN}）')
            else:
                run = 0
        min_ratio = 0.30 if fx else _MIN_CONTRARY_RATIO
        if movable > 0 and contrary / movable < min_ratio:
            issues.append(
                f'反向进行过少：{contrary}/{movable}='
                f'{contrary / movable:.0%}（要求 ≥ {min_ratio:.0%}）')

    # 结尾反向到八度
    u0, u1 = int(upper[n - 2]), int(upper[n - 1])
    b0, b1 = int(lower[n - 2]), int(lower[n - 1])
    du, db = u1 - u0, b1 - b0
    if du == 0 or db == 0:
        issues.append('结尾到八度须两声部都运动（反向），不能有静止声部')
    elif du * db > 0:
        issues.append('结尾到八度须反向进行，实际同向')

    # 开头与结尾音域一致（定旋律侧放宽）
    if fx != 'lower' and int(lower[0]) != int(lower[n - 1]):
        issues.append(
            f'开头与结尾低音音域须一致（同音高），'
            f'开头 {lower[0]} / 结尾 {lower[n - 1]}')
    if fx != 'upper' and (int(upper[0]) // 12) != (int(upper[n - 1]) // 12):
        issues.append(
            f'开头与结尾高音须在同一八度音域，'
            f'开头 {upper[0]} / 结尾 {upper[n - 1]}')

    # 开头 + 终止（需主音）
    if tonic_pc is not None:
        tp = int(tonic_pc) % 12
        triad = _tonic_triad_pcs(tp, mode)
        if fx != 'upper' and _pc(upper[0]) not in triad:
            issues.append(
                f'第 1 小节高音须落在主三和弦上（pcs={sorted(triad)}），'
                f'实际 {_pc(upper[0])}')
        if fx != 'lower' and _pc(lower[0]) != tp:
            issues.append(
                f'第 1 小节低音须为主音（pc={tp}），实际 {_pc(lower[0])}')

        lt = _leading_tone_pc(tp)
        d2 = _degree2_pc(tp)
        cf_has_upper_cad = (_pc(u0) == lt and _pc(u1) == tp and du == 1)
        cf_has_lower_cad = (_pc(b0) == d2 and _pc(b1) == tp and db == -2)

        if fx != 'upper' or cf_has_upper_cad:
            if _pc(u1) != tp:
                issues.append(f'结尾高音须为主音（pc={tp}），实际 {_pc(u1)}')
            if _pc(u0) != lt:
                issues.append(f'倒数第二小节高音须为导音（pc={lt}），实际 {_pc(u0)}')
            if du != 1:
                issues.append(f'高音须导音上行级进到主音（+1），实际 {du:+d}')
        if fx != 'lower' or cf_has_lower_cad:
            if _pc(b1) != tp:
                issues.append(f'结尾低音须为主音（pc={tp}），实际 {_pc(b1)}')
            if _pc(b0) != d2:
                issues.append(f'倒数第二小节低音须为二级音（pc={d2}），实际 {_pc(b0)}')
            if db != -2:
                issues.append(f'低音须从二级下行级进到主音（-2），实际 {db:+d}')

    # 错开最高音
    umax, bmax = max(upper[:n]), max(lower[:n])
    u_peaks = {i for i in range(n) if upper[i] == umax}
    b_peaks = {i for i in range(n) if lower[i] == bmax}
    if u_peaks & b_peaks:
        issues.append(
            f'高低声部最高音落在同一小节 {[i + 1 for i in sorted(u_peaks & b_peaks)]}')

    # 低音级进比（定旋律在低音时放宽）
    if n >= 2 and fx != 'lower':
        leaps = 0
        steps = 0
        for i in range(n - 1):
            d = abs(int(lower[i + 1]) - int(lower[i]))
            if d <= 2:
                steps += 1
            elif d >= 3:
                leaps += 1
        total = steps + leaps
        ratio = steps / max(total, 1)
        if ratio < bass_step_min_ratio:
            issues.append(
                f'低音级进比过低：{ratio:.0%}（要求 ≥ {bass_step_min_ratio:.0%}）')

    # 一音一小节：由调用方保证长度；此处不检查时值
    return issues


def generate_two_part_species(
    bars: int = 8,
    *,
    sharps: int = 0,
    mode: str = 'major',
    seed: int = 42,
    max_tries: int = 20000,
) -> Tuple[List[int], List[int], Dict[str, Any]]:
    """搜索一条满足全部硬约束的 1:1 二声部线。"""
    bars = max(4, min(32, int(bars)))
    # 调号 → 主音 PC（大调圈）
    major_pc = {
        0: 0, 1: 7, 2: 2, 3: 9, 4: 4, 5: 11, 6: 6, 7: 1,
        -1: 5, -2: 10, -3: 3, -4: 8, -5: 1, -6: 6, -7: 11,
    }
    tonic = int(major_pc.get(int(sharps), 0))
    if mode == 'minor':
        tonic = (tonic - 3) % 12  # 关系小调主音近似：调号对应关系大调
    lt_pc = _leading_tone_pc(tonic)
    d2_pc = _degree2_pc(tonic)
    triad_pcs = _tonic_triad_pcs(tonic, mode)

    upper_pool = _diatonic_midis(60, 81, tonic, mode)  # C4–A5
    lower_pool = _diatonic_midis(48, 64, tonic, mode)  # C3–E4
    # 导音在自然小调池里可能缺失；终止需要导音，补入上声部池
    for m in range(60, 82):
        if _pc(m) == lt_pc and m not in upper_pool:
            upper_pool.append(m)
    upper_pool.sort()
    rng = random.Random(int(seed))

    def opening_ok(u, b):
        """低音主音 + 高音主三和弦 + 允许开头音程。"""
        if u <= b or _is_perfect_fourth(u, b):
            return False
        if _pc(b) != tonic:
            return False
        if _pc(u) not in triad_pcs:
            return False
        d = _iv_semitones(u, b)
        return d in _OPENING_OK

    def middle_ok(u, b, pu=None, pb=None):
        if u <= b or _is_perfect_fourth(u, b):
            return False
        d = _iv_semitones(u, b)
        if d not in _MIDDLE_OK:
            return False
        # 5/8 度须反向进入（独立性）
        if d in (7, 12) and pu is not None and pb is not None:
            if not _is_contrary(pu, pb, u, b):
                return False
        return True

    def parallel_bad(pu, pb, u, b):
        """禁止平行纯五/纯八。"""
        prev = _iv_semitones(pu, pb)
        cur = _iv_semitones(u, b)
        if prev == cur and prev in (7, 12, 0, 19, 24):
            du, db = u - pu, b - pb
            if du * db > 0:  # 同向
                return True
        return False

    def parallel_36_run_ok(upper_so_far, lower_so_far, u, b):
        """加入 (u,b) 后连续平行 3/6 不超过上限。"""
        if not upper_so_far:
            return True
        run = 0
        seq_u = list(upper_so_far) + [u]
        seq_l = list(lower_so_far) + [b]
        for i in range(len(seq_u) - 1):
            if _is_similar_parallel_36(seq_u[i], seq_l[i],
                                       seq_u[i + 1], seq_l[i + 1]):
                run += 1
                if run > _MAX_PARALLEL_36_RUN:
                    return False
            else:
                run = 0
        return True

    for _attempt in range(max_tries):
        upper: List[int] = []
        lower: List[int] = []
        ok = True
        for i in range(bars):
            cands = []
            for b in lower_pool:
                for u in upper_pool:
                    if i == 0:
                        if not opening_ok(u, b):
                            continue
                    elif i == bars - 2:
                        if _pc(u) != lt_pc or _pc(b) != d2_pc:
                            continue
                        pu = upper[-1] if upper else None
                        pb = lower[-1] if lower else None
                        if not middle_ok(u, b, pu, pb):
                            # 导音/二级对常为 6 度，允许不强制反向进入
                            if not (u > b and _is_third_or_sixth(u, b)
                                    and not _is_perfect_fourth(u, b)):
                                continue
                    elif i == bars - 1:
                        if _pc(u) != tonic or _pc(b) != tonic:
                            continue
                        if not _is_octave(u, b):
                            continue
                        if u <= b:
                            continue
                        # 与开头同音域
                        if upper and (u // 12) != (upper[0] // 12):
                            continue
                        if lower and b != lower[0]:
                            continue
                    else:
                        pu = upper[-1] if upper else None
                        pb = lower[-1] if lower else None
                        if not middle_ok(u, b, pu, pb):
                            continue
                    if upper:
                        pu, pb = upper[-1], lower[-1]
                        if u == pu or b == pb:
                            continue
                        if parallel_bad(pu, pb, u, b):
                            continue
                        u_leap = _is_leap(pu, u)
                        b_leap = _is_leap(pb, b)
                        if u_leap and b_leap:
                            continue  # 禁止同时跳进
                        if abs(u - pu) > 12:
                            continue
                        if len(upper) >= 2:
                            if not _leap_recovery_ok(upper[-2], pu, u):
                                continue
                            if not _leap_recovery_ok(lower[-2], pb, b):
                                continue
                        if not parallel_36_run_ok(upper, lower, u, b):
                            continue
                        if i == bars - 1:
                            du, db = u - pu, b - pb
                            if du != 1 or db != -2:
                                continue
                            if du * db > 0:
                                continue
                        score = 0.0
                        if _is_contrary(pu, pb, u, b):
                            score += 4.0
                        elif _motion_signs(pu, pb, u, b)[0] * _motion_signs(
                                pu, pb, u, b)[1] > 0:
                            score -= 1.5
                        if _is_third_or_sixth(u, b):
                            score += 0.5
                        if abs(b - pb) <= 2:
                            score += 2.5
                        elif abs(b - pb) <= 5:
                            score += 0.8
                        else:
                            score -= 1.5
                        if abs(u - pu) <= 4:
                            score += 1.0
                        if u_leap or b_leap:
                            score -= 0.3
                        mid = (bars - 1) / 2.0
                        score -= 0.05 * abs(i - mid) * abs(u - 72)
                        # 轻微随机扰动，增加多样性
                        score += rng.uniform(-0.4, 0.4)
                        cands.append((score, u, b))
                    else:
                        cands.append((rng.uniform(0.5, 1.5), u, b))
            if not cands:
                ok = False
                break
            cands.sort(key=lambda x: -x[0])
            # 放宽 top-k，增强随机性
            top = cands[: max(12, len(cands) // 3)]
            _, u, b = rng.choice(top)
            upper.append(u)
            lower.append(b)
        if not ok:
            continue
        issues = validate_two_part_species(
            upper, lower, tonic_pc=tonic, mode=mode)
        if not issues:
            meta = {
                'bars': bars, 'key_sharps': sharps, 'mode': mode, 'seed': seed,
                'tonic_pc': tonic, 'upper': list(upper), 'lower': list(lower),
            }
            return upper, lower, meta

    raise RuntimeError(
        f'未能在 {max_tries} 次尝试内生成满足约束的二声部对位'
        f'（bars={bars}, sharps={sharps}, seed={seed}）')


def _tonic_from_sharps(sharps: int, mode: str = 'major') -> int:
    major_pc = {
        0: 0, 1: 7, 2: 2, 3: 9, 4: 4, 5: 11, 6: 6, 7: 1,
        -1: 5, -2: 10, -3: 3, -4: 8, -5: 1, -6: 6, -7: 11,
    }
    tonic = int(major_pc.get(int(sharps), 0))
    if str(mode).lower().startswith('min'):
        tonic = (tonic - 3) % 12
    return tonic


def _pitch_token_to_midi(token: Any) -> Optional[int]:
    if token is None:
        return None
    if isinstance(token, (int, float)):
        return int(token)
    try:
        from music21 import pitch as m21pitch
        if isinstance(token, (list, tuple)):
            return max(int(m21pitch.Pitch(str(x)).midi) for x in token)
        return int(m21pitch.Pitch(str(token)).midi)
    except Exception:
        try:
            # 简易解析 C4 / F#5
            s = str(token).strip()
            m = re.match(r'^([A-Ga-g])([#b]*)(-?\d+)$', s)
            if not m:
                return None
            name = m.group(1).upper()
            acc = m.group(2).count('#') - m.group(2).count('b')
            octv = int(m.group(3))
            base = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[name]
            return 12 * (octv + 1) + base + acc
        except Exception:
            return None


def _reduce_staff_to_bar_midis(notes_by_bar: Sequence[Sequence[Tuple[int, float]]]
                               ) -> List[Optional[int]]:
    """每小节取时值最长的音（并列取音高最高），无音则 None。"""
    out: List[Optional[int]] = []
    for bar in notes_by_bar:
        if not bar:
            out.append(None)
            continue
        best = max(bar, key=lambda x: (float(x[1]), int(x[0])))
        out.append(int(best[0]))
    return out


def extract_cantus_from_score_model(score: Score
                                    ) -> Tuple[List[int], str, Dict[str, Any]]:
    """从内部 Score 模型抽定旋律（每小节一音）。

    返回 (midis, cf_role, meta)。cf_role 为 'upper'|'lower'（定旋律所在谱表角色）。
    """
    staves = list(getattr(score, 'staves', None) or [])
    if not staves:
        raise ValueError('当前乐谱没有声部')
    lines: List[List[Optional[int]]] = []
    means: List[float] = []
    for staff in staves:
        per: List[List[Tuple[int, float]]] = []
        for m in staff.measures:
            bar_notes: List[Tuple[int, float]] = []
            for n in m.notes:
                if getattr(n, 'is_rest', False):
                    continue
                try:
                    midi = int(n.pitch.to_midi())
                except Exception:
                    continue
                ql = float(getattr(n, 'quarter_length', None) or 4.0)
                bar_notes.append((midi, ql))
            per.append(bar_notes)
        midis = _reduce_staff_to_bar_midis(per)
        lines.append(midis)
        vals = [x for x in midis if x is not None]
        means.append(sum(vals) / len(vals) if vals else -1.0)

    # 选有音最多、平均音高偏高的声部作定旋律（旋律优先）
    best_i = 0
    best_score = (-1, -1.0)
    for i, line in enumerate(lines):
        filled = sum(1 for x in line if x is not None)
        sc = (filled, means[i])
        if sc > best_score:
            best_score = sc
            best_i = i
    raw = lines[best_i]
    # 填补空小节：前向/后向填充
    filled_line: List[int] = []
    last = None
    for x in raw:
        if x is not None:
            last = int(x)
            filled_line.append(last)
        elif last is not None:
            filled_line.append(last)
        else:
            filled_line.append(-1)
    if any(x < 0 for x in filled_line):
        first = next((x for x in filled_line if x >= 0), None)
        if first is None:
            raise ValueError('当前乐谱没有可抽取的音符')
        filled_line = [first if x < 0 else x for x in filled_line]
    # 去掉尾部纯重复填充导致过短：至少 4 小节
    if len(filled_line) < 4:
        raise ValueError(f'定旋律至少需要 4 小节，当前 {len(filled_line)}')
    avg = sum(filled_line) / len(filled_line)
    # 用户未指定时：偏高 → 作高音定旋律；偏低 → 作低音定旋律
    cf_role = 'upper' if avg >= 60 else 'lower'
    if best_i == 0 and len(staves) >= 1:
        # 第一谱表通常是高音
        if means[best_i] >= 55:
            cf_role = 'upper'
    if best_i >= 1 and means[best_i] < 60:
        cf_role = 'lower'
    meta = {
        'source_staff_index': best_i,
        'bars': len(filled_line),
        'cf_role': cf_role,
        'mean_midi': avg,
        'key_sharps': int(getattr(getattr(score, 'key_signature', None), 'sharps', 0) or 0),
        'tempo': int(getattr(score, 'tempo', 88) or 88),
        'meter': (
            f"{int(getattr(getattr(score, 'time_signature', None), 'numerator', 4) or 4)}/"
            f"{int(getattr(getattr(score, 'time_signature', None), 'denominator', 4) or 4)}"
        ),
        'title': getattr(score, 'title', '') or '',
    }
    return filled_line, cf_role, meta


def prepare_cf_for_species(
    cf: Sequence[int],
    *,
    cf_role: str = 'upper',
    sharps: int = 0,
    mode: str = 'major',
) -> List[int]:
    """把任意旋律轮廓收成更易写第一类对位的定旋律（微调，保轮廓）。"""
    role = 'upper' if str(cf_role).lower().startswith('up') else 'lower'
    tonic = _tonic_from_sharps(sharps, mode)
    lt_pc = _leading_tone_pc(tonic)
    d2_pc = _degree2_pc(tonic)
    triad = _tonic_triad_pcs(tonic, mode)
    lo, hi = (60, 81) if role == 'upper' else (40, 64)
    pool = _diatonic_midis(lo, hi, tonic, mode)
    if role == 'upper':
        for m in range(lo, hi + 1):
            if _pc(m) == lt_pc and m not in pool:
                pool.append(m)
        pool.sort()
    else:
        for m in range(lo, hi + 1):
            if _pc(m) == d2_pc and m not in pool:
                pool.append(m)
        pool.sort()

    def nearest(target: int, allowed_pcs: Optional[set] = None) -> int:
        cands = pool
        if allowed_pcs is not None:
            cands = [m for m in pool if _pc(m) in allowed_pcs] or pool
        return min(cands, key=lambda m: (abs(m - int(target)), abs(m - (lo + hi) // 2)))

    out = [nearest(x) for x in cf]
    n = len(out)
    if n < 4:
        return out
    # 开头：落在主三和弦（高）或主音（低）
    if role == 'upper':
        out[0] = nearest(out[0], set(triad))
    else:
        out[0] = nearest(out[0], {tonic})
    # 结尾两小节：尽量导→主 / Ⅱ↓Ⅰ
    if role == 'upper':
        out[-1] = nearest(out[-1], {tonic})
        # 导音：优先 out[-1]-1
        prefer = out[-1] - 1
        out[-2] = min(
            [m for m in pool if _pc(m) == lt_pc] or [prefer],
            key=lambda m: abs(m - prefer))
    else:
        out[-1] = nearest(out[-1], {tonic})
        prefer = out[-1] + 2
        out[-2] = min(
            [m for m in pool if _pc(m) == d2_pc] or [prefer],
            key=lambda m: abs(m - prefer))
    # 打破相邻同音
    for i in range(1, n):
        if out[i] == out[i - 1]:
            # 优先级进邻音
            alts = [m for m in pool if abs(m - out[i]) <= 2 and m != out[i]]
            if not alts:
                alts = [m for m in pool if m != out[i]]
            if alts:
                out[i] = min(alts, key=lambda m: abs(m - out[i - 1]))
    return out


def extract_cantus_from_spec(spec: Dict[str, Any]
                             ) -> Tuple[List[int], str, Dict[str, Any]]:
    """从 score_spec 抽定旋律。"""
    parts = list(spec.get('parts') or [])
    if not parts:
        raise ValueError('当前乐谱 spec 没有声部')
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    try:
        num, den = ts.split('/')
        bar_ql = 4.0 * int(num) / int(den)
    except Exception:
        bar_ql = 4.0

    def events_to_bars(events) -> List[Optional[int]]:
        # 每小节收集 (midi, ql)
        buckets: Dict[int, List[Tuple[int, float]]] = {}
        t = 0.0
        for ev in events or []:
            if not isinstance(ev, (list, tuple)) or len(ev) < 2:
                continue
            pitch, dur = ev[0], ev[1]
            ql_map = {
                'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5,
                '16th': 0.25, 'dotted_half': 3.0, 'dotted_quarter': 1.5,
            }
            ql = float(ql_map.get(str(dur), 1.0))
            midi = _pitch_token_to_midi(pitch)
            bi = int(t // bar_ql + 1e-9)
            if midi is not None:
                buckets.setdefault(bi, []).append((midi, ql))
            t += ql
        n = bars or (max(buckets.keys()) + 1 if buckets else 0)
        per = [buckets.get(i, []) for i in range(n)]
        return _reduce_staff_to_bar_midis(per)

    lines = [events_to_bars(p.get('events')) for p in parts]
    means = []
    for line in lines:
        vals = [x for x in line if x is not None]
        means.append(sum(vals) / len(vals) if vals else -1.0)
    best_i = 0
    best_score = (-1, -1.0)
    for i, line in enumerate(lines):
        filled = sum(1 for x in line if x is not None)
        sc = (filled, means[i])
        if sc > best_score:
            best_score = sc
            best_i = i
    raw = lines[best_i]
    last = None
    filled_line: List[int] = []
    for x in raw:
        if x is not None:
            last = int(x)
            filled_line.append(last)
        elif last is not None:
            filled_line.append(last)
        else:
            filled_line.append(-1)
    if any(x < 0 for x in filled_line):
        first = next((x for x in filled_line if x >= 0), None)
        if first is None:
            raise ValueError('当前乐谱没有可抽取的音符')
        filled_line = [first if x < 0 else x for x in filled_line]
    if len(filled_line) < 4:
        raise ValueError(f'定旋律至少需要 4 小节，当前 {len(filled_line)}')
    avg = sum(filled_line) / len(filled_line)
    clef = str(parts[best_i].get('clef') or '').lower()
    if 'bass' in clef:
        cf_role = 'lower'
    elif 'treble' in clef:
        cf_role = 'upper'
    else:
        cf_role = 'upper' if avg >= 60 else 'lower'
    meta = {
        'source_staff_index': best_i,
        'bars': len(filled_line),
        'cf_role': cf_role,
        'mean_midi': avg,
        'key_sharps': int(spec.get('key_sharps') or 0),
        'mode': str(spec.get('mode') or 'major'),
        'tempo': int(spec.get('tempo') or 88),
        'meter': ts,
        'title': str(spec.get('title') or ''),
    }
    return filled_line, cf_role, meta


def extract_cantus_from_m21(m21_score
                            ) -> Tuple[List[int], str, Dict[str, Any]]:
    """从 music21 Score 抽定旋律。"""
    parts = list(m21_score.parts) if hasattr(m21_score, 'parts') and len(m21_score.parts) else [m21_score]
    lines: List[List[Optional[int]]] = []
    means: List[float] = []
    for part in parts:
        measures = list(part.getElementsByClass('Measure')) if hasattr(part, 'getElementsByClass') else []
        if not measures:
            # flatten into synthetic bars by offset
            flat = part.flatten().notes
            # fallback: treat whole part as one stream of notes, group by measureNumber
            by_bar: Dict[int, List[Tuple[int, float]]] = {}
            for n in flat:
                try:
                    mn = int(getattr(n, 'measureNumber', 1) or 1)
                    midi = int(n.pitch.midi) if hasattr(n, 'pitch') else int(max(p.midi for p in n.pitches))
                    ql = float(n.quarterLength)
                except Exception:
                    continue
                by_bar.setdefault(mn, []).append((midi, ql))
            if not by_bar:
                lines.append([])
                means.append(-1.0)
                continue
            mx = max(by_bar)
            per = [by_bar.get(i, []) for i in range(1, mx + 1)]
            midis = _reduce_staff_to_bar_midis(per)
        else:
            per = []
            for m in measures:
                bar_notes: List[Tuple[int, float]] = []
                for n in m.notes:
                    try:
                        if hasattr(n, 'pitch'):
                            midi = int(n.pitch.midi)
                        else:
                            midi = int(max(p.midi for p in n.pitches))
                        ql = float(n.quarterLength)
                    except Exception:
                        continue
                    bar_notes.append((midi, ql))
                per.append(bar_notes)
            midis = _reduce_staff_to_bar_midis(per)
        lines.append(midis)
        vals = [x for x in midis if x is not None]
        means.append(sum(vals) / len(vals) if vals else -1.0)

    best_i = 0
    best_score = (-1, -1.0)
    for i, line in enumerate(lines):
        filled = sum(1 for x in line if x is not None)
        sc = (filled, means[i])
        if sc > best_score:
            best_score = sc
            best_i = i
    raw = lines[best_i]
    last = None
    filled_line: List[int] = []
    for x in raw:
        if x is not None:
            last = int(x)
            filled_line.append(last)
        elif last is not None:
            filled_line.append(last)
        else:
            filled_line.append(-1)
    if any(x < 0 for x in filled_line):
        first = next((x for x in filled_line if x >= 0), None)
        if first is None:
            raise ValueError('当前乐谱没有可抽取的音符')
        filled_line = [first if x < 0 else x for x in filled_line]
    if len(filled_line) < 4:
        raise ValueError(f'定旋律至少需要 4 小节，当前 {len(filled_line)}')
    avg = sum(filled_line) / len(filled_line)
    cf_role = 'upper' if avg >= 60 else 'lower'
    # key / tempo
    flat = m21_score.flatten()
    sharps = 0
    tempo = 88
    meter = '4/4'
    try:
        from music21 import key as m21key, tempo as m21tempo, meter as m21meter
        ks = flat.getElementsByClass(m21key.KeySignature)
        if len(ks):
            sharps = int(getattr(ks[0], 'sharps', 0) or 0)
        mm = flat.getElementsByClass(m21tempo.MetronomeMark)
        if len(mm) and getattr(mm[0], 'number', None) is not None:
            tempo = int(round(float(mm[0].number)))
        ts = flat.getElementsByClass(m21meter.TimeSignature)
        if len(ts):
            meter = f'{ts[0].numerator}/{ts[0].denominator}'
    except Exception:
        pass
    meta = {
        'source_staff_index': best_i,
        'bars': len(filled_line),
        'cf_role': cf_role,
        'mean_midi': avg,
        'key_sharps': sharps,
        'tempo': tempo,
        'meter': meter,
        'title': str(getattr(m21_score, 'metadata', None) and getattr(m21_score.metadata, 'title', '') or ''),
    }
    return filled_line, cf_role, meta


def infer_cf_role_from_text(text: str, default: str = 'upper') -> str:
    """提示词指定对位写在上方/下方时覆盖默认。"""
    t = str(text or '')
    if re.search(r'上方(写|加)?对位|高音(写|加)?对位|对位(写|加)?在(上|高)|upper\s*counter', t, re.I):
        return 'lower'  # 定旋律在低，自由声在高
    if re.search(r'下方(写|加)?对位|低音(写|加)?对位|对位(写|加)?在(下|低)|bass\s*counter|lower\s*counter', t, re.I):
        return 'upper'  # 定旋律在高，自由声在低
    return default if default in ('upper', 'lower') else 'upper'


def generate_species_against_cf(
    cf: Sequence[int],
    *,
    cf_role: str = 'upper',
    sharps: int = 0,
    mode: str = 'major',
    seed: int = 42,
    max_tries: int = 40000,
) -> Tuple[List[int], List[int], Dict[str, Any]]:
    """固定定旋律，搜索另一声部，满足对位法硬约束（定旋律侧放宽）。"""
    role = 'upper' if str(cf_role).lower().startswith('up') else 'lower'
    cf_list = [int(x) for x in cf]
    bars = len(cf_list)
    if bars < 4:
        raise ValueError('定旋律至少 4 小节')
    tonic = _tonic_from_sharps(sharps, mode)
    lt_pc = _leading_tone_pc(tonic)
    d2_pc = _degree2_pc(tonic)
    triad_pcs = _tonic_triad_pcs(tonic, mode)

    upper_pool = _diatonic_midis(60, 81, tonic, mode)
    lower_pool = _diatonic_midis(40, 64, tonic, mode)
    for m in range(60, 82):
        if _pc(m) == lt_pc and m not in upper_pool:
            upper_pool.append(m)
    upper_pool.sort()
    for m in range(40, 65):
        if _pc(m) == d2_pc and m not in lower_pool:
            lower_pool.append(m)
    lower_pool.sort()
    free_pool = lower_pool if role == 'upper' else upper_pool
    rng = random.Random(int(seed))

    cf_upper_cad = (
        role == 'upper'
        and _pc(cf_list[-2]) == lt_pc
        and _pc(cf_list[-1]) == tonic
        and (cf_list[-1] - cf_list[-2]) == 1
    )
    cf_lower_cad = (
        role == 'lower'
        and _pc(cf_list[-2]) == d2_pc
        and _pc(cf_list[-1]) == tonic
        and (cf_list[-1] - cf_list[-2]) == -2
    )

    def pair_at(i: int, free: int) -> Tuple[int, int]:
        if role == 'upper':
            return int(cf_list[i]), int(free)
        return int(free), int(cf_list[i])

    def opening_ok(u, b):
        if u <= b or _is_perfect_fourth(u, b):
            return False
        d = _iv_semitones(u, b)
        if d not in _OPENING_OK:
            return False
        if role != 'lower' and _pc(b) != tonic:
            return False
        if role != 'upper' and _pc(u) not in triad_pcs:
            return False
        return True

    def middle_ok(u, b, pu=None, pb=None):
        if u <= b or _is_perfect_fourth(u, b):
            return False
        d = _iv_semitones(u, b)
        if d not in _MIDDLE_OK:
            return False
        # 对定旋律：5/8 反向优选但不强制（定旋律走向不可改）
        return True

    def parallel_bad(pu, pb, u, b):
        prev = _iv_semitones(pu, pb)
        cur = _iv_semitones(u, b)
        if prev == cur and prev in (7, 12, 0, 19, 24):
            du, db = u - pu, b - pb
            if du * db > 0:
                return True
        return False

    def parallel_36_run_ok(upper_so_far, lower_so_far, u, b):
        if not upper_so_far:
            return True
        run = 0
        seq_u = list(upper_so_far) + [u]
        seq_l = list(lower_so_far) + [b]
        for i in range(len(seq_u) - 1):
            if _is_similar_parallel_36(seq_u[i], seq_l[i],
                                       seq_u[i + 1], seq_l[i + 1]):
                run += 1
                if run > _MAX_PARALLEL_36_RUN:
                    return False
            else:
                run = 0
        return True

    for _attempt in range(max_tries):
        upper: List[int] = []
        lower: List[int] = []
        ok = True
        for i in range(bars):
            cands = []
            for free in free_pool:
                u, b = pair_at(i, free)
                if i == 0:
                    if not opening_ok(u, b):
                        continue
                elif i == bars - 1:
                    if not _is_octave(u, b) or u <= b:
                        continue
                    # 自由声部收束：尽量主音；定旋律侧不强求
                    if role == 'upper':
                        if _pc(b) != tonic:
                            continue
                        if lower and b != lower[0]:
                            continue
                    else:
                        if _pc(u) != tonic:
                            continue
                        if upper and (u // 12) != (upper[0] // 12):
                            continue
                elif i == bars - 2:
                    # 终止前一小节
                    if role == 'upper':
                        # 低音尽量二级；若定旋律已是导→主公式则强制
                        if cf_upper_cad and _pc(b) != d2_pc:
                            continue
                        if not (u > b and not _is_perfect_fourth(u, b)
                                and (_is_third_or_sixth(u, b) or _is_middle_ok_interval(u, b))):
                            continue
                    else:
                        if cf_lower_cad and _pc(u) != lt_pc:
                            continue
                        if not (u > b and not _is_perfect_fourth(u, b)
                                and (_is_third_or_sixth(u, b) or _is_middle_ok_interval(u, b))):
                            continue
                else:
                    pu = upper[-1] if upper else None
                    pb = lower[-1] if lower else None
                    if not middle_ok(u, b, pu, pb):
                        continue
                if upper:
                    pu, pb = upper[-1], lower[-1]
                    if (role != 'upper' and u == pu) or (role != 'lower' and b == pb):
                        continue
                    if parallel_bad(pu, pb, u, b):
                        continue
                    u_leap = _is_leap(pu, u)
                    b_leap = _is_leap(pb, b)
                    if u_leap and b_leap:
                        continue
                    free_leap = b_leap if role == 'upper' else u_leap
                    if free_leap and abs((b if role == 'upper' else u) - (pb if role == 'upper' else pu)) > 12:
                        continue
                    if len(upper) >= 2:
                        if role != 'upper' and not _leap_recovery_ok(upper[-2], pu, u):
                            continue
                        if role != 'lower' and not _leap_recovery_ok(lower[-2], pb, b):
                            continue
                    if not parallel_36_run_ok(upper, lower, u, b):
                        continue
                    if i == bars - 1:
                        du, db = u - pu, b - pb
                        if du == 0 or db == 0 or du * db > 0:
                            continue
                        if cf_upper_cad and role == 'upper' and db != -2:
                            continue
                        if cf_lower_cad and role == 'lower' and du != 1:
                            continue
                    score = 0.0
                    if _is_contrary(pu, pb, u, b):
                        score += 4.0
                    if _is_third_or_sixth(u, b):
                        score += 0.8
                    free_prev = pb if role == 'upper' else pu
                    free_cur = b if role == 'upper' else u
                    if abs(free_cur - free_prev) <= 2:
                        score += 2.5
                    elif abs(free_cur - free_prev) <= 5:
                        score += 0.8
                    else:
                        score -= 1.2
                    score += rng.uniform(-0.4, 0.4)
                    cands.append((score, free))
                else:
                    cands.append((rng.uniform(0.5, 1.5), free))
            if not cands:
                ok = False
                break
            cands.sort(key=lambda x: -x[0])
            top = cands[: max(12, len(cands) // 3)]
            _, free = rng.choice(top)
            u, b = pair_at(i, free)
            upper.append(u)
            lower.append(b)
        if not ok:
            continue
        issues = validate_two_part_species(
            upper, lower, tonic_pc=tonic, mode=mode, fixed_cf=role)
        if not issues:
            meta = {
                'bars': bars, 'key_sharps': sharps, 'mode': mode, 'seed': seed,
                'tonic_pc': tonic, 'upper': list(upper), 'lower': list(lower),
                'fixed_cf': role, 'from_current': True,
            }
            return upper, lower, meta

    raise RuntimeError(
        f'未能在 {max_tries} 次内为定旋律写出合法对位'
        f'（bars={bars}, cf_role={role}, sharps={sharps}, seed={seed}）')


def build_two_part_species_from_cantus(
    cf: Sequence[int],
    *,
    cf_role: str = 'upper',
    title: str = '二声部对位（基于当前乐谱）',
    tempo: int = 88,
    meter: str = '4/4',
    sharps: int = 0,
    mode: str = 'major',
    seed: int = 42,
) -> Tuple[Score, str, Dict[str, Any]]:
    """定旋律 + 对位声 → 钢琴大谱表。"""
    role = 'upper' if str(cf_role).lower().startswith('up') else 'lower'
    raw_cf = [int(x) for x in cf]
    prepared = prepare_cf_for_species(
        raw_cf, cf_role=role, sharps=sharps, mode=mode)
    last_err: Optional[Exception] = None
    upper = lower = gen = None
    for attempt_cf, label in (
        (raw_cf, 'raw'),
        (prepared, 'prepared'),
    ):
        for s_off in (0, 17, 41, 99):
            try:
                upper, lower, gen = generate_species_against_cf(
                    attempt_cf, cf_role=role, sharps=sharps, mode=mode,
                    seed=int(seed) + s_off, max_tries=25000)
                gen = dict(gen)
                gen['cf_prepare'] = label
                break
            except Exception as e:
                last_err = e
                upper = None
        if upper is not None:
            break
    if upper is None or lower is None or gen is None:
        raise RuntimeError(
            f'无法为当前乐谱写出合法 1:1 对位：{last_err}')
    score = blank_score(
        title=title, bars=len(upper), tempo=int(tempo), sharps=int(sharps),
        meter=meter, composer='二声部对位', layout='piano')
    apply_species_to_score(score, upper, lower)
    issues = validate_two_part_species(
        upper, lower, tonic_pc=int(gen.get('tonic_pc', 0)), mode=mode,
        fixed_cf=str(gen.get('fixed_cf') or role))
    names_u = [_midi_name(m) for m in upper]
    names_l = [_midi_name(m) for m in lower]
    ivs = []
    for u, b in zip(upper, lower):
        d = _iv_semitones(u, b)
        label = {0: 'P1', 3: 'm3', 4: 'M3', 7: 'P5', 8: 'm6', 9: 'M6',
                 12: 'P8'}.get(d, f'{d}st')
        ivs.append(label)
    role = str(gen.get('fixed_cf') or role)
    prep_note = ''
    if gen.get('cf_prepare') == 'prepared':
        prep_note = '（定旋律已微调开头/终止与相邻同音，以符合第一类对位）'
    report = (
        f'✅ **基于当前乐谱的第一类对位**（定旋律={role}）{prep_note}：'
        f'{len(upper)} 小节 · RH/LH · {meter} · ♩={tempo} · seed={seed}\n'
        f'- 保留定旋律轮廓，另一声部按对位法规则生成\n'
        f'- 上：{" ".join(names_u)}\n'
        f'- 下：{" ".join(names_l)}\n'
        f'- 音程：{" · ".join(ivs)}\n'
    )
    if issues:
        report += '- ⚠️ 校验残留：' + '；'.join(issues) + '\n'
    meta = {
        'title': title,
        'bars': len(upper),
        'key_sharps': int(sharps),
        'mode': mode,
        'time_signature': meter,
        'tempo': int(tempo),
        'staff_groups': list(score.staff_groups),
        'upper_midis': list(upper),
        'lower_midis': list(lower),
        'intervals': ivs,
        'species': True,
        'from_current': True,
        'fixed_cf': role,
        'seed': seed,
        'issues': issues,
        **gen,
    }
    return score, report, meta


def _bar_duration(beats: float) -> Tuple[NoteDuration, int]:
    """返回 (基础时值, 附点数)。"""
    if abs(beats - 4.0) < 1e-6:
        return NoteDuration.WHOLE, 0
    if abs(beats - 3.0) < 1e-6:
        return NoteDuration.HALF, 1
    if abs(beats - 2.0) < 1e-6:
        return NoteDuration.HALF, 0
    return NoteDuration.WHOLE, 0


def apply_species_to_score(score: Score, upper: Sequence[int],
                           lower: Sequence[int]) -> int:
    """把 MIDI 线写入钢琴大谱表。返回写入小节数。"""
    if len(score.staves) < 2:
        raise ValueError('需要钢琴大谱表（RH+LH）')
    rh, lh = score.staves[0], score.staves[1]
    beats = score.time_signature.beats_per_measure()
    dur, dots = _bar_duration(beats)
    n = min(len(upper), len(lower), len(rh.measures), len(lh.measures))
    for i in range(n):
        rh.measures[i].notes = [
            Note(Pitch.from_midi(int(upper[i])), dur, dots=dots)]
        lh.measures[i].notes = [
            Note(Pitch.from_midi(int(lower[i])), dur, dots=dots)]
    return n


def build_two_part_species_score(
    *,
    title: str = '二声部对位',
    bars: int = 8,
    tempo: int = 88,
    meter: str = '4/4',
    sharps: int = 0,
    mode: str = 'major',
    seed: int = 42,
) -> Tuple[Score, str, Dict[str, Any]]:
    """生成满足硬约束的钢琴大谱表二声部对位 + 说明。"""
    upper, lower, gen = generate_two_part_species(
        bars, sharps=sharps, mode=mode, seed=seed)
    score = blank_score(
        title=title, bars=len(upper), tempo=int(tempo), sharps=int(sharps),
        meter=meter, composer='二声部对位', layout='piano')
    apply_species_to_score(score, upper, lower)
    issues = validate_two_part_species(
        upper, lower, tonic_pc=int(gen.get('tonic_pc', 0)), mode=mode)
    names_u = [_midi_name(m) for m in upper]
    names_l = [_midi_name(m) for m in lower]
    ivs = []
    for u, b in zip(upper, lower):
        d = _iv_semitones(u, b)
        label = {0: 'P1', 3: 'm3', 4: 'M3', 7: 'P5', 8: 'm6', 9: 'M6',
                 12: 'P8'}.get(d, f'{d}st')
        ivs.append(label)
    report = (
        f'✅ **二声部第一类对位**（确定性）：{len(upper)} 小节 · '
        f'RH 高音谱表 / LH 低音谱表 · {meter} · ♩={tempo} · seed={seed}\n'
        f'- 开头Ⅰ；中间 3/6（可反向至 5/8）；禁同时跳进与过多平行 3/6；'
        f'多用反向；开头结尾同音域；结尾导→主 / Ⅱ↓Ⅰ\n'
        f'- 上：{" ".join(names_u)}\n'
        f'- 下：{" ".join(names_l)}\n'
        f'- 音程：{" · ".join(ivs)}\n'
    )
    if issues:
        report += '- ⚠️ 校验残留：' + '；'.join(issues) + '\n'
    meta = {
        'title': title,
        'bars': len(upper),
        'key_sharps': int(sharps),
        'mode': mode,
        'time_signature': meter,
        'tempo': int(tempo),
        'staff_groups': list(score.staff_groups),
        'upper_midis': list(upper),
        'lower_midis': list(lower),
        'intervals': ivs,
        'species': True,
        'seed': seed,
        'issues': issues,
        **gen,
    }
    return score, report, meta


def species_score_to_spec(score: Score, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Score 模型 → compose 管线用的 score_spec（含 events）。"""
    upper = meta.get('upper_midis') or []
    lower = meta.get('lower_midis') or []
    beats = score.time_signature.beats_per_measure()
    if abs(beats - 3.0) < 1e-6:
        dur = 'dotted_half'
    elif abs(beats - 2.0) < 1e-6:
        dur = 'half'
    else:
        dur = 'whole'
    return {
        'title': meta.get('title') or score.title,
        'composer': '二声部对位',
        'tempo': meta.get('tempo') or score.tempo,
        'time_signature': meta.get('time_signature') or '4/4',
        'key_sharps': meta.get('key_sharps', 0),
        'mode': meta.get('mode') or 'major',
        'bars': meta.get('bars') or len(upper),
        'staff_groups': meta.get('staff_groups') or list(score.staff_groups),
        'species': True,
        'tonic_pc': meta.get('tonic_pc', 0),
        'parts': [
            {
                'name': 'Piano RH', 'clef': 'treble', 'program': 0,
                'events': [[_midi_name(m), dur] for m in upper],
            },
            {
                'name': 'Piano LH', 'clef': 'bass', 'program': 0,
                'events': [[_midi_name(m), dur] for m in lower],
            },
        ],
    }


def extract_bar_midis_from_spec(spec: Dict[str, Any]
                                ) -> Tuple[List[Optional[int]], List[Optional[int]]]:
    """从 2 声部 spec 抽每小节首音 MIDI（用于校验）。"""
    parts = list(spec.get('parts') or [])
    if len(parts) < 2:
        return [], []
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    try:
        num, den = ts.split('/')
        bar_ql = 4.0 * int(num) / int(den)
    except Exception:
        bar_ql = 4.0

    def per_bar(events):
        out: List[Optional[int]] = []
        t = 0.0
        cur_bar = -1
        for ev in events or []:
            if not isinstance(ev, (list, tuple)) or len(ev) < 2:
                continue
            pitch, dur = ev[0], ev[1]
            # 简化时值
            ql_map = {
                'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5,
                '16th': 0.25, 'dotted_half': 3.0, 'dotted_quarter': 1.5,
            }
            ql = float(ql_map.get(str(dur), 4.0))
            bi = int(t // bar_ql + 1e-9)
            if bi != cur_bar and bi < max(bars, 64):
                while len(out) < bi:
                    out.append(None)
                midi = None
                if pitch is not None:
                    try:
                        from music21 import pitch as m21pitch
                        if isinstance(pitch, (list, tuple)):
                            midi = max(int(m21pitch.Pitch(str(x)).midi)
                                       for x in pitch)
                        else:
                            midi = int(m21pitch.Pitch(str(pitch)).midi)
                    except Exception:
                        midi = None
                out.append(midi)
                cur_bar = bi
            t += ql
        while bars and len(out) < bars:
            out.append(None)
        return out[:bars] if bars else out

    return per_bar(parts[0].get('events')), per_bar(parts[1].get('events'))
