"""
乐谱编辑内核：规则引擎（实时校验/禁止）+ 智能提示引擎 + 编辑控制器。

纯逻辑层，不依赖 Qt（score_studio 的 UI 层订阅控制器的回调）。
三级判定与 AI 代码编辑器类比：
  - error  → 编译错误：直接禁止落笔（小节溢出、超出记谱范围）
  - strict → lint error：严格校验开启时禁止（平行五八度、超八度跳进）
  - warn   → lint warning：允许放置，谱面黄色高亮（调外变化音、边缘音域）
"""
import copy
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .models import (
    Score, Staff, Measure, Note, Pitch, ClefType, NoteName, Accidental,
    NoteDuration, KeySignature,
)
from .renderer import pitch_to_staff_position


# ---------------------------------------------------------------- 音阶工具

_ORDER_SHARPS = [NoteName.F, NoteName.C, NoteName.G, NoteName.D,
                 NoteName.A, NoteName.E, NoteName.B]
_NATURAL_PC = {NoteName.C: 0, NoteName.D: 2, NoteName.E: 4, NoteName.F: 5,
               NoteName.G: 7, NoteName.A: 9, NoteName.B: 11}
_ACC_SYMBOL = {Accidental.NATURAL: '', Accidental.SHARP: '#',
               Accidental.FLAT: 'b', Accidental.DOUBLE_SHARP: '##',
               Accidental.DOUBLE_FLAT: 'bb'}

DUR_ORDER = [NoteDuration.WHOLE, NoteDuration.HALF, NoteDuration.QUARTER,
             NoteDuration.EIGHTH, NoteDuration.SIXTEENTH,
             NoteDuration.THIRTY_SECOND]


def scale_pcs(sharps: int) -> set:
    """调号对应自然音阶的 pitch-class 集合。"""
    sharp_set = set(_ORDER_SHARPS[:max(sharps, 0)])
    flat_set = set(list(reversed(_ORDER_SHARPS))[:max(-sharps, 0)])
    result = set()
    for name, pc in _NATURAL_PC.items():
        if name in sharp_set:
            pc = (pc + 1) % 12
        elif name in flat_set:
            pc = (pc - 1) % 12
        result.add(pc)
    return result


def scale_pitch_names(sharps: int) -> List[Tuple[NoteName, Accidental]]:
    """调号内七个 (音名, 变音记号)，按 C→B 顺序。"""
    sharp_map = {n: Accidental.SHARP for n in _ORDER_SHARPS[:max(sharps, 0)]}
    flat_map = {n: Accidental.FLAT
                for n in list(reversed(_ORDER_SHARPS))[:max(-sharps, 0)]}
    out = []
    for name in NoteName:
        out.append((name, sharp_map.get(name, flat_map.get(name,
                                                           Accidental.NATURAL))))
    return out


def key_tonic(sharps: int) -> Tuple[NoteName, Accidental]:
    """调号主音（大调惯例）。"""
    major_tonic = {0: NoteName.C, 1: NoteName.G, 2: NoteName.D,
                   3: NoteName.A, 4: NoteName.E, 5: NoteName.B, 6: NoteName.F,
                   -1: NoteName.F, -2: NoteName.B, -3: NoteName.E,
                   -4: NoteName.A, -5: NoteName.D, -6: NoteName.G,
                   -7: NoteName.C}
    tonic = major_tonic.get(sharps, NoteName.C)
    for nm, acc in scale_pitch_names(sharps):
        if nm == tonic:
            return nm, acc
    return tonic, Accidental.NATURAL


def pitch_midi(p: Pitch) -> int:
    return p.to_midi()


def pitch_label(p: Pitch) -> str:
    return f"{p.name.name}{_ACC_SYMBOL.get(p.accidental, '')}{p.octave}"


def transposed_pitch(p: Pitch, semitones: int) -> Pitch:
    """半音移调（保持可读拼写）。"""
    return Pitch.from_midi(pitch_midi(p) + semitones)


def _scale_step_offset(p: Pitch, steps: int, sharps: int) -> Optional[Pitch]:
    """按调内音级移动 steps 步（±1 = 上/下一级）。失败返回 None。"""
    if steps == 0:
        return p
    names = scale_pitch_names(sharps)
    abs_idx = p.octave * 7 + list(NoteName).index(p.name)
    cur = abs_idx + steps
    nm, acc = names[cur % 7]
    return Pitch(nm, cur // 7, acc)


# ---------------------------------------------------------------- 规则引擎

@dataclass
class Verdict:
    level: str      # 'ok' | 'warn' | 'strict' | 'error'
    code: str
    msg: str

    @property
    def blocked(self) -> bool:
        return self.level in ('error', 'strict')


@dataclass
class Violation:
    staff_idx: int
    measure_idx: int
    note_idx: int
    level: str      # 'error' | 'warn'
    code: str
    msg: str


# 记谱范围（谱表位置）：0..4 为五线，±1 为第一条加线……
POS_HARD_MIN, POS_HARD_MAX = -5.0, 9.0     # 硬禁止（约上下三条加线之外）
POS_WARN_MIN, POS_WARN_MAX = -3.0, 7.0     # 警告（超出常用音域）


class RuleEngine:
    """对「将要放置的音」和「已有乐谱」做规则校验。"""

    def __init__(self, strict: bool = True):
        self.strict = strict

    # ---------- 即将放置的音 ----------

    def check_placement(self, score: Score, staff_idx: int, measure_idx: int,
                        pitch: Pitch, duration, insert_index: Optional[int] = None) -> Verdict:
        """新音符放置校验（含小节容量）。duration 接受 NoteDuration 或浮点（全音符）。"""
        staves = score.staves
        if not (0 <= staff_idx < len(staves)):
            return Verdict('error', 'staff', '声部不存在')
        staff = staves[staff_idx]
        if not (0 <= measure_idx < len(staff.measures)):
            return Verdict('error', 'measure', '小节不存在')
        measure = staff.measures[measure_idx]
        ts = measure.time_signature or score.time_signature

        if not pitch:
            return Verdict('ok', '', '')

        # 1. 硬规则：小节时值溢出
        remaining = measure.remaining_beats(ts)
        dur = duration.value if isinstance(duration, NoteDuration) else float(duration)
        if dur > remaining + 1e-9:
            return Verdict('error', 'overflow',
                           f'小节只剩 {self._dur_label(remaining)}，'
                           f'{self._dur_label(dur)} 放不下')

        v = self._check_note_rules(score, staff_idx, measure_idx, pitch,
                                   duration, insert_index)
        if v is not None:
            return v
        return Verdict('ok', '', '')

    def check_pitch_change(self, score: Score, staff_idx: int, measure_idx: int,
                           pitch: Pitch, note_idx: int) -> Verdict:
        """改已有音符的音高：除小节容量外全部规则照查。"""
        if not (0 <= staff_idx < len(score.staves)):
            return Verdict('error', 'staff', '声部不存在')
        if not (0 <= measure_idx < len(score.staves[staff_idx].measures)):
            return Verdict('error', 'measure', '小节不存在')
        v = self._check_note_rules(score, staff_idx, measure_idx, pitch,
                                   None, note_idx)
        if v is not None:
            return v
        return Verdict('ok', '', '')

    def check_duration_change(self, score: Score, staff_idx: int, measure_idx: int,
                              note_idx: int, new_duration) -> Verdict:
        """改已有音符的时值（含附点模拟）：对比全小节总量而非剩余空间。"""
        staff = score.staves[staff_idx]
        measure = staff.measures[measure_idx]
        ts = measure.time_signature or score.time_signature
        note = measure.notes[note_idx]
        new = new_duration.value if isinstance(new_duration, NoteDuration) \
            else float(new_duration)
        old = note.duration_value()
        capacity = ts.beats_per_measure() / 4.0
        new_total = measure.total_duration() - old + new
        if new_total > capacity + 1e-9:
            return Verdict('error', 'overflow',
                           f'小节装不下：改后总时值 {self._dur_label(new_total)}'
                           f' 超过 {self._dur_label(capacity)}')
        return Verdict('ok', '', '')

    def _check_note_rules(self, score: Score, staff_idx: int, measure_idx: int,
                          pitch: Pitch, duration, insert_index: Optional[int]) -> Optional[Verdict]:
        """容量之外的规则：范围 / 平行 / 跳进 / 变化音 / 边缘音域。"""
        staff = score.staves[staff_idx]
        measure = staff.measures[measure_idx]

        # 2. 硬规则：超出记谱范围
        pos = pitch_to_staff_position(pitch, staff.clef)
        if pos < POS_HARD_MIN or pos > POS_HARD_MAX:
            return Verdict('error', 'range',
                           f'{pitch_label(pitch)} 超出可记谱范围')

        # 3. 严格规则：平行五八度（与其他声部，onset 对齐）
        if duration is not None:
            new_note = Note(pitch, duration if isinstance(duration, NoteDuration)
                            else NoteDuration.QUARTER)
        else:
            new_note = Note(pitch, NoteDuration.QUARTER)
        for oi in range(len(score.staves)):
            if oi == staff_idx or measure_idx >= len(score.staves[oi].measures):
                continue
            om = score.staves[oi].measures[measure_idx]
            if not om.notes:
                continue
            # 织体启发式：八分及更短的快速跑动（分解和弦/经过句）不做平行
            # 判定，避免对夜曲伴奏等合法织体误报
            if any(n.duration_value() <= NoteDuration.EIGHTH.value
                   for n in list(measure.notes) + list(om.notes)):
                continue
            par = self._parallel_between(measure, om, new_note=new_note,
                                         insert_index=insert_index)
            if par:
                if self.strict:
                    return Verdict('strict', 'parallel',
                                   f'平行{par}——被严格校验禁止')
                return Verdict('warn', 'parallel', f'平行{par}（允许，黄色高亮）')

        # 4. 严格规则：超八度旋律跳进（与前一音）
        prev = self._prev_note(score, staff_idx, measure_idx, insert_index)
        if prev is not None and not prev.is_rest:
            iv = abs(pitch_midi(pitch) - pitch_midi(prev.pitch))
            if iv > 12:
                msg = f'跳进 {iv} 个半音，超过八度'
                if self.strict:
                    return Verdict('strict', 'leap', msg + '——被严格校验禁止')
                return Verdict('warn', 'leap', msg)

        # 5. 警告：调外变化音
        sharps = (measure.key_signature or score.key_signature).sharps
        if pitch_midi(pitch) % 12 not in scale_pcs(sharps):
            return Verdict('warn', 'chromatic',
                           f'{pitch_label(pitch)} 为调外变化音（允许，注意倾向）')

        # 6. 警告：边缘音域
        if pos < POS_WARN_MIN or pos > POS_WARN_MAX:
            return Verdict('warn', 'extreme',
                           f'{pitch_label(pitch)} 位于边缘音域（加线多，慎用）')
        return None

    # ---------- 已有乐谱 ----------

    def lint_score(self, score: Score) -> List[Violation]:
        out: List[Violation] = []
        for si, staff in enumerate(score.staves):
            for mi, measure in enumerate(staff.measures):
                ts = measure.time_signature or score.time_signature
                # 小节溢出（整小节一条）
                over = measure.total_duration() - ts.beats_per_measure() / 4.0
                if over > 1e-9:
                    out.append(Violation(si, mi, 0, 'error', 'overflow',
                                         f'小节时值溢出 {self._dur_label(over)}'))
                # 逐音：变化音 / 平行 / 跳进 / 范围（容量检查跳过）。
                # strict 项（平行/跳进）在存量乐谱上降为黄色提示——硬禁止只
                # 作用于「正在放置/修改的那个音」（check_placement/check_pitch_change）
                for ni, note in enumerate(measure.notes):
                    if note.is_rest:
                        continue
                    v = self.check_pitch_change(score, si, mi, note.pitch, ni)
                    if v.level == 'ok':
                        continue
                    if v.level == 'error':
                        out.append(Violation(si, mi, ni, 'error', v.code, v.msg))
                    else:
                        out.append(Violation(si, mi, ni, 'warn', v.code, v.msg))
        # 平行五八度（跨声部，onset 对齐）
        out.extend(self._lint_parallel(score))
        return out

    # ---------- 内部 ----------

    @staticmethod
    def _dur_label(whole: float) -> str:
        labels = [('全音符', 1.0), ('二分音符', 0.5), ('附点四分', 0.375),
                  ('四分音符', 0.25), ('附点八分', 0.1875), ('八分音符', 0.125),
                  ('十六分音符', 0.0625), ('三十二分音符', 0.03125)]
        for name, val in labels:
            if abs(whole - val) < 1e-6:
                return name
        return f'{whole * 4:g} 拍'

    @staticmethod
    def _onset_map(measure: Measure) -> List[Tuple[float, Note]]:
        """小节内 [(onset(全音符单位), note)]。"""
        out = []
        t = 0.0
        for n in measure.notes:
            out.append((t, n))
            t += n.duration_value()
        return out

    @staticmethod
    def _note_midis(note: Note) -> List[int]:
        """音符全部音高（含 chord_tones），供四部和声平行检测。"""
        if note is None or note.is_rest:
            return []
        out = [pitch_midi(note.pitch)]
        for ct in (note.chord_tones or []):
            try:
                out.append(pitch_midi(ct))
            except Exception:
                pass
        return out

    def _parallel_between(self, own_measure: Measure, other_measure: Measure,
                          new_note: Optional[Note] = None,
                          insert_index: Optional[int] = None) -> str:
        """两个声部同小节的平行五/八度检测（按 onset 对齐）。

        含 chord_tones（钢琴大谱表 SA/TB 叠写）时，展开为多声部两两检测。
        new_note/insert_index：模拟在 insert_index 处插入 new_note。
        返回 '五度' / '八度' / ''。
        """
        own_pairs = self._onset_map(own_measure)
        if new_note is not None:
            onset_new = sum(n.duration_value()
                            for n in own_measure.notes[:insert_index or 0])
            own_pairs = list(own_pairs[:insert_index or 0]) + \
                [(onset_new, new_note)]
        other_at = dict(self._onset_map(other_measure))
        own_at = dict(own_pairs)
        common = sorted(set(own_at) & set(other_at))
        # 每个声部对单独跟踪 prev：(iv, midi_a, midi_b)
        prev_by_pair: Dict[Tuple[int, int], Tuple[int, int, int]] = {}
        for t in common:
            om, tm = own_at[t], other_at[t]
            own_ms = self._note_midis(om)
            other_ms = self._note_midis(tm)
            if not own_ms or not other_ms:
                prev_by_pair.clear()
                continue
            for i, a in enumerate(own_ms):
                for j, b in enumerate(other_ms):
                    iv = abs(a - b) % 12
                    key = (i, j)
                    prev = prev_by_pair.get(key)
                    if prev is not None and iv == prev[0] and iv in (0, 7):
                        if a != prev[1] or b != prev[2]:
                            # 同向才算平行
                            if (a - prev[1]) * (b - prev[2]) > 0:
                                return '五度' if iv == 7 else '八度'
                    prev_by_pair[key] = (iv, a, b)
        return ''

    def _lint_parallel(self, score: Score) -> List[Violation]:
        out: List[Violation] = []
        n_staves = len(score.staves)
        for a in range(n_staves):
            for b in range(a + 1, n_staves):
                sa, sb = score.staves[a], score.staves[b]
                for mi in range(min(len(sa.measures), len(sb.measures))):
                    ma, mb = sa.measures[mi], sb.measures[mi]
                    if not ma.notes or not mb.notes:
                        continue
                    # 织体启发式：任一声部含八分或更短的快速跑动
                    # （分解和弦/经过句）时跳过——onset 对齐的平行判定
                    # 只对真正的持续声部（圣咏/四声部）有意义
                    if any(n.duration_value() <= NoteDuration.EIGHTH.value
                           for n in list(ma.notes) + list(mb.notes)):
                        continue
                    par = self._parallel_between(ma, mb)
                    if par:
                        # 定位到第二个共同 onset 的音（近似：取第 2 个非休止音）
                        ni = 0
                        for idx, n in enumerate(ma.notes):
                            if not n.is_rest:
                                ni = idx
                                if idx >= 1:
                                    break
                        out.append(Violation(
                            a, mi, ni, 'warn', 'parallel',
                            f'与声部 {b + 1} 形成平行{par}（ lint 提示，允许保留）'))
        return out

    @staticmethod
    def _prev_note(score: Score, staff_idx: int, measure_idx: int,
                   insert_index: Optional[int]) -> Optional[Note]:
        staff = score.staves[staff_idx]
        measure = staff.measures[measure_idx] if measure_idx < len(staff.measures) else None
        if measure is not None:
            idx = len(measure.notes) if insert_index is None else insert_index
            for i in range(min(idx, len(measure.notes)) - 1, -1, -1):
                return measure.notes[i]
        for mi in range(measure_idx - 1, -1, -1):
            m = staff.measures[mi]
            if m.notes:
                return m.notes[-1]
        return None


# ---------------------------------------------------------------- 智能提示

@dataclass
class Candidate:
    pitch: Pitch
    duration: NoteDuration
    reason: str
    rank: float = 0.0
    kind: str = 'note'          # 'note' | 'phrase'
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0
    # 短分支：[(pitch, duration), …]；单音时通常 = [(pitch, duration)]
    preview_events: List[Tuple[Pitch, NoteDuration]] = field(default_factory=list)

    @property
    def label(self) -> str:
        evs = [normalize_assist_event(e) for e in (self.preview_events or [])]
        if self.kind == 'phrase' and evs:
            n_notes = sum(1 for e in evs if e['kind'] == 'note')
            n_rest = sum(1 for e in evs if e['kind'] == 'rest')
            bits = []
            for e in evs[:6]:
                if e['kind'] == 'rest':
                    bits.append('休止')
                elif e['kind'] == 'dynamic':
                    bits.append(str(e.get('dynamic') or ''))
                elif e.get('pitch'):
                    bits.append(pitch_label(e['pitch']))
            more = '…' if len(evs) > 6 else ''
            extra = f'（{n_notes}音' + (f'+{n_rest}休' if n_rest else '') + '）'
            return ' '.join(b for b in bits if b) + more + extra
        if evs and evs[0]['kind'] == 'rest':
            return '休止'
        return f'{pitch_label(self.pitch)}'

    def __post_init__(self):
        if not self.reasons:
            self.reasons = [self.reason] if self.reason else []
        if not self.preview_events:
            self.preview_events = [(self.pitch, self.duration)]
        if self.score == 0.0 and self.rank:
            self.score = self.rank


def normalize_assist_event(ev) -> dict:
    """统一候选事件：note / rest / dynamic。"""
    if isinstance(ev, dict):
        kind = ev.get('kind') or (
            'rest' if ev.get('is_rest') else 'dynamic' if ev.get('dynamic') and not ev.get('duration')
            else 'note')
        return {
            'kind': kind,
            'pitch': ev.get('pitch'),
            'duration': ev.get('duration'),
            'marks': list(ev.get('marks') or []),
            'dynamic': ev.get('dynamic'),
            'bar_offset': int(ev.get('bar_offset') or 0),
            'is_rest': kind == 'rest' or bool(ev.get('is_rest')),
        }
    if isinstance(ev, (tuple, list)) and len(ev) >= 2:
        return {
            'kind': 'note', 'pitch': ev[0], 'duration': ev[1],
            'marks': [], 'dynamic': None, 'bar_offset': 0, 'is_rest': False,
        }
    return {'kind': 'note', 'pitch': None, 'duration': None, 'marks': [],
            'dynamic': None, 'bar_offset': 0, 'is_rest': False}


class SuggestionEngine:
    """「下一个符号」智能提示：导音解决 > 级进 > 和弦音 > 重复。"""

    def __init__(self, rule_engine: Optional[RuleEngine] = None):
        self.rules = rule_engine or RuleEngine()

    def suggest(self, score: Score, staff_idx: int, measure_idx: int,
                insert_index: Optional[int], duration: NoteDuration,
                limit: int = 5) -> List[Candidate]:
        staves = score.staves
        if not (0 <= staff_idx < len(staves)):
            return []
        staff = staves[staff_idx]
        if not (0 <= measure_idx < len(staff.measures)):
            return []
        measure = staff.measures[measure_idx]
        ts = measure.time_signature or score.time_signature
        sharps = (measure.key_signature or score.key_signature).sharps

        # 时值自适应：放不下就取最大可放的标准时值
        remaining = measure.remaining_beats(ts)
        dur = duration
        while dur.value > remaining + 1e-9 and dur != NoteDuration.THIRTY_SECOND:
            idx = DUR_ORDER.index(dur)
            if idx == len(DUR_ORDER) - 1:
                break
            dur = DUR_ORDER[idx + 1]
        if remaining <= 1e-9:
            return []

        prev = RuleEngine._prev_note(score, staff_idx, measure_idx, insert_index)
        tonic_name, tonic_acc = key_tonic(sharps)
        tonic_pc = (_NATURAL_PC[tonic_name] +
                    (1 if tonic_acc == Accidental.SHARP else
                     -1 if tonic_acc == Accidental.FLAT else 0)) % 12

        # 候选音高池：调内三个八度（围绕前一音或 C4–C6）
        names = scale_pitch_names(sharps)
        center_midi = pitch_midi(prev.pitch) if prev is not None else 64
        pool: List[Pitch] = []
        for octv in range(2, 7):
            for nm, acc in names:
                p = Pitch(nm, octv, acc)
                if abs(pitch_midi(p) - center_midi) <= 14:
                    pool.append(p)
        pool.sort(key=pitch_midi)

        cands: List[Candidate] = []

        def add(p: Pitch, reason: str, rank: float):
            for c in cands:
                if pitch_midi(c.pitch) == pitch_midi(p):
                    if rank > c.rank:
                        c.reason, c.rank = reason, rank
                    return
            # 规则预检：error 类直接不推荐
            v = self.rules.check_placement(score, staff_idx, measure_idx, p,
                                           dur, insert_index)
            if v.level == 'error':
                return
            cands.append(Candidate(p, dur, reason, rank))

        if prev is not None and not prev.is_rest:
            pm = pitch_midi(prev.pitch)
            # 导音解决（主音上方小二度 → 主音）
            for p in pool:
                if pitch_midi(p) % 12 == tonic_pc and (pitch_midi(p) - pm) == 1:
                    add(p, '导音解决到主音', 100)
            # 级进
            for p in pool:
                d = pitch_midi(p) - pm
                if d in (1, 2):
                    add(p, '级进上行，衔接流畅', 60)
                elif d in (-1, -2):
                    add(p, '级进下行，衔接流畅', 58)
            # 主/属和弦琶音
            for p in pool:
                pc = pitch_midi(p) % 12
                iv = (pc - tonic_pc) % 12
                if iv in (0, 4, 7) and 0 < abs(pitch_midi(p) - pm) <= 9:
                    add(p, '主和弦琶音，稳定支撑', 50)
                elif iv in (7, 11, 2) and 0 < abs(pitch_midi(p) - pm) <= 9:
                    add(p, '属功能和弦音，推动进行', 45)
            # 重复
            same = Pitch(prev.pitch.name, prev.pitch.octave, prev.pitch.accidental)
            add(same, '重复前音，巩固语汇', 30)
        else:
            # 无前音：从主音/五音/三音起步
            for p in pool:
                pc = pitch_midi(p) % 12
                iv = (pc - tonic_pc) % 12
                if iv == 0:
                    add(p, '从主音起步', 80)
                elif iv == 7:
                    add(p, '从属音起步', 60)
                elif iv == 4:
                    add(p, '从中音起步（大调色彩）', 40)

        cands.sort(key=lambda c: -c.rank)
        return cands[:limit]


# ---------------------------------------------------------------- 编辑控制器

class EditController:
    """编辑操作 + 撤销/重做 + 校验接线。UI 层设置回调后接收通知。

    score 属性由宿主窗口在换谱时同步（controller.score = self.score）。
    """

    def __init__(self):
        self.score: Optional[Score] = None
        self.rules = RuleEngine(strict=True)
        self.suggestions = SuggestionEngine(self.rules)
        # 辅助作曲引擎（风格/和声感知）；失败时回退 SuggestionEngine
        try:
            from .assist_engine import AssistEngine
            self.assist = AssistEngine(self.rules)
        except Exception:
            self.assist = None
        # 编辑状态（调色板当前选择）：工具/插入时值/变音记号
        self.tool = 'select'            # 'select' | 'note' | 'rest' | 'erase'
        self.duration = NoteDuration.QUARTER
        self.accidental = Accidental.NATURAL
        # 回调
        self.on_change: Optional[Callable[[], None]] = None
        self.on_status: Optional[Callable[[str], None]] = None
        self.on_verdict: Optional[Callable[[Verdict], None]] = None
        self.on_violations: Optional[Callable[[List[Violation]], None]] = None
        self._undo_stack: List[list] = []
        self._redo_stack: List[list] = []
        self._batch = False

    # ---------- 状态 ----------

    def set_strict(self, on: bool):
        self.rules.strict = bool(on)
        self._after_change('严格校验：' + ('开（平行五八度/大跳将禁止）' if on else '关（仅硬错误禁止）'))

    @property
    def strict(self) -> bool:
        return self.rules.strict

    # ---------- 撤销/重做 ----------

    def _snapshot(self):
        if self.score is None:
            return
        self._undo_stack.append(copy.deepcopy(self.score.staves))
        if len(self._undo_stack) > 60:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self) -> bool:
        if not self._undo_stack or self.score is None:
            return False
        self._redo_stack.append(copy.deepcopy(self.score.staves))
        self.score.staves = self._undo_stack.pop()
        self._after_change('已撤销')
        return True

    def redo(self) -> bool:
        if not self._redo_stack or self.score is None:
            return False
        self._undo_stack.append(copy.deepcopy(self.score.staves))
        self.score.staves = self._redo_stack.pop()
        self._after_change('已重做')
        return True

    # ---------- 操作 ----------

    def try_insert_note(self, staff_idx: int, measure_idx: int,
                        insert_index: Optional[int], pitch: Pitch,
                        duration: NoteDuration, is_rest: bool = False) -> bool:
        if self.score is None:
            return False
        staff = self.score.staves[staff_idx]
        measure = staff.measures[measure_idx]
        idx = len(measure.notes) if insert_index is None else max(0, min(
            insert_index, len(measure.notes)))
        target_pitch = pitch
        if is_rest:
            # 休止符固定放中线上，音高无意义
            clef = staff.clef
            base = {ClefType.TREBLE: Pitch(NoteName.B, 4),
                    ClefType.BASS: Pitch(NoteName.D, 3),
                    ClefType.ALTO: Pitch(NoteName.C, 4)}.get(clef,
                                                             Pitch(NoteName.B, 4))
            target_pitch = base
        if not is_rest:
            v = self.rules.check_placement(self.score, staff_idx, measure_idx,
                                           pitch, duration, idx)
            if self.on_verdict:
                self.on_verdict(v)
            if v.blocked:
                if self.on_status:
                    self.on_status(f'✋ 已禁止：{v.msg}')
                return False
        if not self._batch:
            self._snapshot()
        note = Note(target_pitch, duration, is_rest=is_rest)
        measure.notes.insert(idx, note)
        if not self._batch:
            self._after_change(
                f'已插入 {"休止符" if is_rest else pitch_label(pitch)}'
                f'（第 {measure_idx + 1} 小节）')
        return True

    def insert_piano_step(self, rh: int, lh: int, measure_idx: int,
                          midis: List[int], split: int = 60,
                          pad_rests: bool = True) -> Optional[dict]:
        """MIDI 键盘一笔（单音/和弦，可跨左右手）写入大谱表。

        不做放置拦截（实时演奏丢音比违规更糟）；写入后照常 lint 标出违规。
        """
        if self.score is None:
            return None
        from .piano_step import write_step
        self._snapshot()
        try:
            info = write_step(self.score, rh, lh, measure_idx, midis,
                              self.duration, split=split, pad_rests=pad_rests)
        except Exception as e:
            if self._undo_stack:
                self._undo_stack.pop()
            if self.on_status:
                self.on_status(f'MIDI 写入失败：{e}')
            return None
        mi = info['measure_idx']
        parts = []
        for si, hand, tag in ((rh, info['rh'], '右'), (lh, info['lh'], '左')):
            if not hand:
                continue
            note = self.score.staves[si].measures[mi].notes[-1]
            labels = [pitch_label(p) for p in [note.pitch] + list(note.chord_tones)]
            parts.append(f'{tag}手 {"+".join(labels)}')
        self._after_change(f'MIDI → {"　".join(parts)}（第 {mi + 1} 小节）')
        return info

    def try_set_pitch(self, staff_idx: int, measure_idx: int, note_idx: int,
                      pitch: Pitch) -> bool:
        if self.score is None:
            return False
        measure = self.score.staves[staff_idx].measures[measure_idx]
        note = measure.notes[note_idx]
        if note.is_rest:
            return False
        v = self.rules.check_pitch_change(self.score, staff_idx, measure_idx,
                                          pitch, note_idx)
        if self.on_verdict:
            self.on_verdict(v)
        if v.blocked:
            if self.on_status:
                self.on_status(f'✋ 已禁止：{v.msg}')
            return False
        self._snapshot()
        old = pitch_label(note.pitch)
        note.pitch = pitch
        note.chord_tones = []   # 改音高后原和弦叠置不再成立
        self._after_change(f'{old} → {pitch_label(pitch)}')
        return True

    def transpose_selected(self, keys: List[Tuple[int, int, int]],
                           semitones: int) -> int:
        """对选中音符半音移调，返回成功数量。"""
        done = 0
        for (si, mi, ni) in keys:
            note = self._note_at(si, mi, ni)
            if note is None or note.is_rest:
                continue
            if self.try_set_pitch(si, mi, ni,
                                  transposed_pitch(note.pitch, semitones)):
                done += 1
        return done

    def transpose_steps_selected(self, keys: List[Tuple[int, int, int]],
                                 steps: int) -> int:
        """调内音级移位（↑/↓ 一级）。"""
        done = 0
        if self.score is None:
            return 0
        for (si, mi, ni) in keys:
            note = self._note_at(si, mi, ni)
            if note is None or note.is_rest:
                continue
            sharps = (self.score.staves[si].measures[mi].key_signature
                      or self.score.key_signature).sharps
            np_ = _scale_step_offset(note.pitch, steps, sharps)
            if np_ is not None and self.try_set_pitch(si, mi, ni, np_):
                done += 1
        return done

    def delete_notes(self, keys: List[Tuple[int, int, int]]) -> int:
        if self.score is None:
            return 0
        by_measure: dict = {}
        for (si, mi, ni) in keys:
            by_measure.setdefault((si, mi), []).append(ni)
        total = 0
        groups = []
        for (si, mi), idxs in by_measure.items():
            idxs = sorted(set(idxs), reverse=True)
            measure = self.score.staves[si].measures[mi] if mi < len(self.score.staves[si].measures) else None
            if measure is None:
                continue
            for ni in idxs:
                if 0 <= ni < len(measure.notes):
                    groups.append((si, mi, ni, measure.notes[ni]))
        if not groups:
            return 0
        self._snapshot()
        for (si, mi, ni, _n) in groups:
            measure = self.score.staves[si].measures[mi]
            if 0 <= ni < len(measure.notes):
                del measure.notes[ni]
                total += 1
        self._after_change(f'已删除 {total} 个符号')
        return total

    def set_duration(self, keys: List[Tuple[int, int, int]],
                     duration: NoteDuration) -> int:
        done = 0
        if self.score is None:
            return 0
        # 先校验再统一应用（改时值对比全小节总量；任一超容 → 拒绝该音）
        pending = []
        for (si, mi, ni) in keys:
            note = self._note_at(si, mi, ni)
            if note is None or note.duration == duration:
                continue
            v = self.rules.check_duration_change(self.score, si, mi, ni,
                                                 duration)
            if self.on_verdict:
                self.on_verdict(v)
            if v.blocked:
                if self.on_status:
                    self.on_status(f'✋ 第 {mi + 1} 小节：{v.msg}')
                continue
            pending.append((si, mi, ni, duration))
        if not pending:
            return 0
        self._snapshot()
        for (si, mi, ni, d) in pending:
            self._note_at(si, mi, ni).duration = d
            done += 1
        self._after_change(f'已改时值 ×{done}')
        return done

    def toggle_rest(self, keys: List[Tuple[int, int, int]]) -> int:
        done = 0
        if self.score is None:
            return 0
        self._snapshot()
        for (si, mi, ni) in keys:
            note = self._note_at(si, mi, ni)
            if note is None:
                continue
            note.is_rest = not note.is_rest
            done += 1
        self._after_change(f'休止切换 ×{done}')
        return done

    def toggle_dots(self, keys: List[Tuple[int, int, int]]) -> int:
        if self.score is None:
            return 0
        # 附点会加大时值：先校验溢出（同一音符去重）
        pending = []
        seen = set()
        for (si, mi, ni) in keys:
            if (si, mi, ni) in seen:
                continue
            seen.add((si, mi, ni))
            note = self._note_at(si, mi, ni)
            if note is None or note.dots >= 2:
                continue
            # 模拟 +1 附点后的时值（对比全小节总量，直接传浮点）
            base = note.duration.value
            new_val = base + sum(base * (0.5 ** (i + 1))
                                 for i in range(note.dots + 1))
            if note.grace:
                new_val = 0.0
            elif note.tuplet and note.tuplet[0] > 0:
                new_val = new_val * note.tuplet[1] / note.tuplet[0]
            v = self.rules.check_duration_change(self.score, si, mi, ni,
                                                 new_val)
            if v.blocked:
                if self.on_status:
                    self.on_status(f'✋ 第 {mi + 1} 小节：{v.msg}')
                continue
            pending.append((si, mi, ni))
        if not pending:
            return 0
        self._snapshot()
        for (si, mi, ni) in pending:
            self._note_at(si, mi, ni).dots += 1
        self._after_change(f'加附点 ×{len(pending)}')
        return len(pending)

    def set_accidental(self, keys: List[Tuple[int, int, int]],
                       accidental: Accidental) -> int:
        if self.score is None:
            return 0
        self._snapshot()
        n = 0
        for (si, mi, ni) in keys:
            note = self._note_at(si, mi, ni)
            if note is None or note.is_rest:
                continue
            note.pitch.accidental = accidental
            n += 1
        self._after_change(f'变音记号 ×{n}' if n else '无可改音符')
        return n

    def toggle_mark(self, keys: List[Tuple[int, int, int]], mark: str) -> int:
        if self.score is None:
            return 0
        self._snapshot()
        n = 0
        for (si, mi, ni) in keys:
            note = self._note_at(si, mi, ni)
            if note is None or note.is_rest:
                continue
            if mark in note.marks:
                note.marks.remove(mark)
            else:
                note.marks.append(mark)
            n += 1
        self._after_change(f'演奏法记号 ×{n}' if n else '无可标音符')
        return n

    def apply_line_action(self, keys: List[Tuple[int, int, int]], action: str) -> int:
        """延音/连音/渐强弱/倚音/三连音等。keys 为选中音符。"""
        if self.score is None or not keys:
            return 0
        action = (action or '').strip().lower()
        self._snapshot()
        n = 0
        if action == 'tie':
            # 单音：切换起点；两音及以上：第一个 start、最后一个 end
            ordered = sorted(keys)
            if len(ordered) == 1:
                note = self._note_at(*ordered[0])
                if note and not note.is_rest:
                    note.tie_start = not note.tie_start
                    n = 1
            else:
                first = self._note_at(*ordered[0])
                last = self._note_at(*ordered[-1])
                if first and not first.is_rest:
                    first.tie_start = True
                    n += 1
                if last and not last.is_rest:
                    last.tie_end = True
                    n += 1
            self._after_change(f'延音线 ×{n}' if n else '无可标音符')
            return n
        if action == 'slur':
            ordered = sorted(keys)
            if len(ordered) == 1:
                note = self._note_at(*ordered[0])
                if note and not note.is_rest:
                    note.slur_start = not note.slur_start
                    n = 1
            else:
                first = self._note_at(*ordered[0])
                last = self._note_at(*ordered[-1])
                if first and not first.is_rest:
                    first.slur_start = True
                    n += 1
                if last and not last.is_rest:
                    last.slur_end = True
                    n += 1
            self._after_change(f'连音线 ×{n}' if n else '无可标音符')
            return n
        if action in ('crescendo', 'diminuendo'):
            ordered = sorted(keys)
            first = self._note_at(*ordered[0])
            last = self._note_at(*ordered[-1]) if len(ordered) > 1 else None
            if first and not first.is_rest:
                if first.hairpin_start == action:
                    first.hairpin_start = None
                else:
                    first.hairpin_start = action
                n += 1
            if last and last is not first and not last.is_rest:
                last.hairpin_end = True
                n += 1
            self._after_change(f'{action} ×{n}' if n else '无可标音符')
            return n
        if action == 'grace':
            for key in keys:
                note = self._note_at(*key)
                if note is None or note.is_rest:
                    continue
                note.grace = not note.grace
                n += 1
            self._after_change(f'倚音 ×{n}' if n else '无可标音符')
            return n
        tuplet_m = None
        if action.startswith('tuplet') and len(action) > 6 and action[6:].isdigit():
            tuplet_m = int(action[6:])
        elif action == 'tuplet3':
            tuplet_m = 3
        if tuplet_m is not None and tuplet_m >= 2:
            spec = (tuplet_m, 2 if tuplet_m == 3 else max(2, tuplet_m - 1))
            ordered = sorted(keys)
            for i, key in enumerate(ordered):
                note = self._note_at(*key)
                if note is None or note.is_rest:
                    continue
                if note.tuplet == spec:
                    note.tuplet = None
                    note.tuplet_start = False
                    note.tuplet_end = False
                else:
                    note.tuplet = spec
                    note.tuplet_start = (i == 0)
                    note.tuplet_end = (i == len(ordered) - 1)
                n += 1
            self._after_change(f'{tuplet_m}连音 ×{n}' if n else '无可标音符')
            return n
        if self.on_status:
            self.on_status(f'未知符号动作：{action}')
        return 0

    def set_barline(self, measure_indices: List[int], style: str) -> int:
        """设置选中小节的右侧小节线样式。"""
        if self.score is None:
            return 0
        from .models import BARLINE_STYLES
        style = (style or 'regular').strip().lower()
        if style not in BARLINE_STYLES:
            if self.on_status:
                self.on_status(f'未知小节线：{style}')
            return 0
        self._snapshot()
        n = 0
        for staff in self.score.staves:
            for mi in measure_indices:
                if 0 <= mi < len(staff.measures):
                    m = staff.measures[mi]
                    if style == 'regular':
                        m.barline_right = None
                        m.barline_left = None
                    elif style == 'repeat_start':
                        m.barline_left = 'repeat_start'
                    elif style == 'repeat_end':
                        m.barline_right = 'repeat_end'
                    else:
                        m.barline_right = style
                    n += 1
        self._after_change(f'小节线 {style} → {len(measure_indices)} 小节')
        return n

    def set_measure_key(self, measure_indices: List[int], sharps: int) -> int:
        if self.score is None or not measure_indices:
            return 0
        from .models import KeySignature
        self._snapshot()
        ks = KeySignature(sharps=int(sharps))
        for staff in self.score.staves:
            for mi in measure_indices:
                if 0 <= mi < len(staff.measures):
                    staff.measures[mi].key_signature = ks
        if 0 in measure_indices:
            self.score.key_signature = ks
        self._after_change(f'调号 {sharps:+d} → {len(measure_indices)} 小节')
        return len(measure_indices)

    def set_measure_time(self, measure_indices: List[int], *,
                         numerator: int, denominator: int,
                         symbol: Optional[str] = None) -> int:
        if self.score is None or not measure_indices:
            return 0
        from .models import TimeSignature
        self._snapshot()
        ts = TimeSignature(int(numerator), int(denominator), symbol)
        for staff in self.score.staves:
            for mi in measure_indices:
                if 0 <= mi < len(staff.measures):
                    staff.measures[mi].time_signature = ts
        if 0 in measure_indices:
            self.score.time_signature = ts
        label = symbol or f'{numerator}/{denominator}'
        self._after_change(f'拍号 {label} → {len(measure_indices)} 小节')
        return len(measure_indices)

    def set_measure_clef(self, staff_indices: List[int], measure_indices: List[int],
                         clef) -> int:
        if self.score is None or not measure_indices:
            return 0
        from .models import ClefType
        if not isinstance(clef, ClefType):
            try:
                clef = ClefType[clef]
            except (KeyError, TypeError):
                if self.on_status:
                    self.on_status(f'未知谱号：{clef}')
                return 0
        self._snapshot()
        n = 0
        staves = self.score.staves
        for si in staff_indices:
            if not (0 <= si < len(staves)):
                continue
            for mi in measure_indices:
                if 0 <= mi < len(staves[si].measures):
                    staves[si].measures[mi].clef = clef
                    n += 1
            if 0 in measure_indices:
                staves[si].clef = clef
        self._after_change(f'谱号 {clef.name} → {n} 处')
        return n

    def set_measure_tempo(self, measure_indices: List[int], *,
                          mark: Optional[str] = None,
                          bpm: Optional[int] = None) -> int:
        if self.score is None or not measure_indices:
            return 0
        self._snapshot()
        for staff in self.score.staves:
            for mi in measure_indices:
                if 0 <= mi < len(staff.measures):
                    m = staff.measures[mi]
                    if mark is not None:
                        m.tempo_mark = mark or None
                    if bpm is not None:
                        m.tempo_bpm = int(bpm) if bpm else None
        if bpm is not None and 0 in measure_indices:
            self.score.tempo = int(bpm)
        self._after_change(f'速度标记 → {len(measure_indices)} 小节')
        return len(measure_indices)

    def set_measure_text(self, measure_indices: List[int], text: str) -> int:
        if self.score is None or not measure_indices:
            return 0
        self._snapshot()
        text = (text or '').strip() or None
        for staff in self.score.staves:
            for mi in measure_indices:
                if 0 <= mi < len(staff.measures):
                    staff.measures[mi].text = text
        self._after_change(f'文字指示 → {len(measure_indices)} 小节')
        return len(measure_indices)

    def set_rehearsal_mark(self, measure_indices: List[int], label: str) -> int:
        if self.score is None or not measure_indices:
            return 0
        self._snapshot()
        label = (label or '').strip() or None
        for staff in self.score.staves:
            for mi in measure_indices:
                if 0 <= mi < len(staff.measures):
                    staff.measures[mi].rehearsal = label
        self._after_change(f'排练号 → {len(measure_indices)} 小节')
        return len(measure_indices)

    def set_dynamics(self, measure_indices: List[int], dyn: str) -> int:
        if self.score is None:
            return 0
        self._snapshot()
        n = 0
        for staff in self.score.staves:
            for mi in measure_indices:
                if 0 <= mi < len(staff.measures):
                    staff.measures[mi].dynamic = dyn
                    n += 1
        self._after_change(f'力度 {dyn} → {len(measure_indices)} 小节')
        return n

    def add_measures(self, count: int = 1) -> int:
        """在谱末追加空小节（所有声部同步）。返回新的总小节数；失败返回 0。"""
        if self.score is None:
            return 0
        try:
            from .assist_engine import append_empty_measures
            self._snapshot()
            total = append_empty_measures(self.score, count)
        except Exception as e:
            if self.on_status:
                self.on_status(f'增加小节失败：{e}')
            return 0
        self._after_change(f'已增加 {max(1, int(count))} 小节，共 {total} 小节')
        return total

    def add_part(self, token: str = 'bass') -> Tuple[int, str]:
        """追加空声部。返回 (索引, 说明)；失败 (-1, 错误信息)。"""
        if self.score is None:
            return -1, '暂无乐谱'
        try:
            from .part_accomp import add_part_from_token
            self._snapshot()
            idx, msg = add_part_from_token(self.score, token)
        except Exception as e:
            if self.on_status:
                self.on_status(f'添加声部失败：{e}')
            return -1, str(e)
        self._after_change(msg)
        return idx, msg

    def fill_accompaniment(self, pattern: str = 'bass',
                           target_staff: Optional[int] = None,
                           mode: str = 'major') -> Tuple[bool, str]:
        """为配乐声部填入伴奏。返回 (成功, 说明)。"""
        if self.score is None:
            return False, '暂无乐谱'
        try:
            from .part_accomp import fill_accompaniment
            self._snapshot()
            meta = fill_accompaniment(
                self.score, pattern=pattern, target_staff=target_staff,
                mode=mode)
        except Exception as e:
            if self.on_status:
                self.on_status(f'配乐失败：{e}')
            return False, str(e)
        msg = (f'已写入配乐（{meta.get("pattern")}）→ 声部 '
               f'{meta.get("target_name")}，共 {meta.get("bars_filled")} 小节')
        self._after_change(msg)
        return True, msg

    # ---------- 查询 ----------

    def check(self, staff_idx, measure_idx, pitch, duration,
              insert_index=None) -> Verdict:
        if self.score is None:
            return Verdict('error', 'no_score', '暂无乐谱')
        return self.rules.check_placement(self.score, staff_idx, measure_idx,
                                          pitch, duration, insert_index)

    def suggest(self, staff_idx, measure_idx, insert_index,
                duration) -> List[Candidate]:
        if self.score is None:
            return []
        if self.assist is not None and getattr(self.assist, 'enabled', True):
            try:
                return self.assist.suggest(
                    self.score, staff_idx, measure_idx, insert_index, duration)
            except Exception:
                pass
        return self.suggestions.suggest(self.score, staff_idx, measure_idx,
                                        insert_index, duration)

    def suggest_phrases(self) -> List[Candidate]:
        """最近一次 suggest 产生的本小节铺满（无则空）。"""
        if self.assist is None:
            return []
        return list(self.assist.last_bar_fills())

    def suggest_full_fills(self) -> List[Candidate]:
        """最近一次 suggest 产生的全曲铺满。"""
        if self.assist is None:
            return []
        return list(self.assist.last_full_fills())

    def try_insert_phrase(self, staff_idx: int, measure_idx: int,
                          insert_index: Optional[int],
                          events) -> int:
        """写入一整段候选（可含休止/力度，可跨小节）。返回成功事件数。"""
        return self.try_insert_events(staff_idx, measure_idx, insert_index, events)

    def try_insert_events(self, staff_idx: int, measure_idx: int,
                          insert_index: Optional[int],
                          events) -> int:
        if self.score is None or not events:
            return 0
        self._snapshot()
        n = 0
        idx = insert_index
        mi = measure_idx
        suppress = self.on_change
        self.on_change = None
        self._batch = True
        try:
            for raw in events:
                ev = normalize_assist_event(raw)
                staff = self.score.staves[staff_idx]
                if mi >= len(staff.measures):
                    break
                measure = staff.measures[mi]
                ts = measure.time_signature or self.score.time_signature
                if idx is None:
                    idx = len(measure.notes)
                if ev['kind'] == 'dynamic' and ev.get('dynamic'):
                    measure.dynamic = ev['dynamic']
                    n += 1
                    continue
                dur = ev.get('duration')
                if dur is None:
                    continue
                while measure.remaining_beats(ts) <= 1e-9:
                    mi += 1
                    idx = 0
                    if mi >= len(staff.measures):
                        break
                    measure = staff.measures[mi]
                    ts = measure.time_signature or self.score.time_signature
                if mi >= len(staff.measures):
                    break
                if ev.get('dynamic'):
                    measure.dynamic = ev['dynamic']
                is_rest = ev['kind'] == 'rest' or ev.get('is_rest')
                pitch = ev.get('pitch')
                ok = self.try_insert_note(
                    staff_idx, mi, idx, pitch, dur, is_rest=is_rest)
                if not ok:
                    break
                if not is_rest and ev.get('marks'):
                    note = measure.notes[idx if idx is not None else 0]
                    for mk in ev['marks']:
                        if mk not in note.marks:
                            note.marks.append(mk)
                n += 1
                idx = (idx or 0) + 1
                if measure.remaining_beats(ts) <= 1e-9:
                    mi += 1
                    idx = 0
        finally:
            self._batch = False
            self.on_change = suppress
        self._after_change(f'已采纳候选 {n} 个符号')
        return n

    def lint(self) -> List[Violation]:
        if self.score is None:
            return []
        return self.rules.lint_score(self.score)

    # ---------- 内部 ----------

    def _note_at(self, si, mi, ni) -> Optional[Note]:
        if self.score is None or si >= len(self.score.staves):
            return None
        measures = self.score.staves[si].measures
        if mi >= len(measures) or ni >= len(measures[mi].notes):
            return None
        return measures[mi].notes[ni]

    def _after_change(self, msg: str = ''):
        if self.on_violations:
            self.on_violations(self.lint())
        if self.on_change:
            self.on_change()
        if msg and self.on_status:
            self.on_status(msg)
