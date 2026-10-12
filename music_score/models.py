"""
Core music model: notes, measures, clefs, key signatures, etc.
"""
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple


class ClefType(Enum):
    TREBLE = auto()       # G clef
    BASS = auto()         # F clef
    ALTO = auto()         # C clef（中音，C4 在第 3 线）
    TENOR = auto()        # C clef（次中音，C4 在第 4 线）
    TREBLE_8VB = auto()   # 高音谱号下方带 8（男高音声部，实际音高低八度）
    BASS_8VB = auto()     # 低音谱号下方带 8（低音提琴，实际音高低八度）


class NoteName(Enum):
    C = 0
    D = 1
    E = 2
    F = 3
    G = 4
    A = 5
    B = 6


class Accidental(Enum):
    NATURAL = 0
    SHARP = 1
    FLAT = -1
    DOUBLE_SHARP = 2
    DOUBLE_FLAT = -2


# 自然音名到音级（C=0）。不能用 NoteName 的枚举序号，否则 G 会被当成 4 而不是 7。
NAME_TO_PC = {
    NoteName.C: 0,
    NoteName.D: 2,
    NoteName.E: 4,
    NoteName.F: 5,
    NoteName.G: 7,
    NoteName.A: 9,
    NoteName.B: 11,
}
_PC_TO_NAME = {pc: name for name, pc in NAME_TO_PC.items()}

# 各谱号第 1 线（最底线）对应的音（音名, 八度）：谱表位置 ↔ 音高换算的唯一真相源。
# 8vb 谱号按实际音高记（低八度），谱面位置与不带 8 的谱号一致。
CLEF_BOTTOM_LINE = {
    ClefType.TREBLE: (NoteName.E, 4),
    ClefType.BASS: (NoteName.G, 2),
    ClefType.ALTO: (NoteName.F, 3),
    ClefType.TENOR: (NoteName.D, 3),
    ClefType.TREBLE_8VB: (NoteName.E, 3),
    ClefType.BASS_8VB: (NoteName.G, 1),
}

# 演奏法 / 装饰音 / 其它附在音符上的记号（Note.marks 取值）
ARTICULATION_MARKS = ('staccato', 'staccatissimo', 'accent', 'marcato',
                      'tenuto', 'fermata')
ORNAMENT_MARKS = ('trill', 'mordent', 'inverted_mordent', 'turn')
OTHER_MARKS = ('arpeggio', 'breath')
ALL_MARKS = ARTICULATION_MARKS + ORNAMENT_MARKS + OTHER_MARKS

# 小节线样式（Measure.barline_right / barline_left）
BARLINE_STYLES = ('regular', 'double', 'final', 'repeat_start', 'repeat_end', 'dashed')


class NoteDuration(Enum):
    WHOLE = 1.0
    HALF = 0.5
    QUARTER = 0.25
    EIGHTH = 0.125
    SIXTEENTH = 0.0625
    THIRTY_SECOND = 0.03125


@dataclass
class Pitch:
    """MIDI-like pitch representation."""
    name: NoteName
    octave: int
    accidental: Accidental = Accidental.NATURAL

    def to_midi(self) -> int:
        base = self.octave * 12 + 12 + NAME_TO_PC[self.name]
        return base + self.accidental.value

    @classmethod
    def from_midi(cls, midi: int, accidental: Accidental = Accidental.NATURAL) -> "Pitch":
        octave = (midi // 12) - 1
        pc = midi % 12
        if pc in _PC_TO_NAME and accidental == Accidental.NATURAL:
            return cls(_PC_TO_NAME[pc], octave, Accidental.NATURAL)
        name = min(NAME_TO_PC, key=lambda n: min((NAME_TO_PC[n] - pc) % 12, (pc - NAME_TO_PC[n]) % 12))
        delta = (pc - NAME_TO_PC[name] + 6) % 12 - 6
        acc = {
            0: Accidental.NATURAL,
            1: Accidental.SHARP,
            -1: Accidental.FLAT,
            2: Accidental.DOUBLE_SHARP,
            -2: Accidental.DOUBLE_FLAT,
        }.get(delta, accidental)
        return cls(name, octave, acc)


@dataclass
class Note:
    pitch: Pitch
    duration: NoteDuration
    is_rest: bool = False
    dots: int = 0
    tie_start: bool = False
    tie_end: bool = False
    chord_tones: List[Pitch] = field(default_factory=list)  # 非空时表示和弦，pitch 为最高声部
    marks: List[str] = field(default_factory=list)  # 演奏法/装饰音，取值见 ALL_MARKS
    slur_start: bool = False   # 连音线起点（本音符在弧线开始端）
    slur_end: bool = False     # 连音线终点
    # 连音（三连音等）：(实际音符数, 正常音符数)，如 (3, 2) = 三个占两个的时值
    tuplet: Optional[Tuple[int, int]] = None
    tuplet_start: bool = False  # 连音组首音（画括号/数字的起点）
    tuplet_end: bool = False
    grace: bool = False         # 倚音：不占小节时值，小号符头
    dynamic: Optional[str] = None        # 小节中途的力度记号（小节首的仍用 Measure.dynamic）
    hairpin_start: Optional[str] = None  # 'crescendo' / 'diminuendo'
    hairpin_end: bool = False
    lyric: Optional[str] = None
    chord_symbol: Optional[str] = None   # 和弦记号（如 Cmaj7、G/B）

    def duration_value(self) -> float:
        if self.grace:
            return 0.0
        base = self.duration.value
        extra = sum(base * (0.5 ** (i + 1)) for i in range(self.dots))
        val = base + extra
        if self.tuplet:
            actual, normal = self.tuplet
            if actual > 0 and normal > 0:
                val = val * normal / actual
        return val


@dataclass
class TimeSignature:
    numerator: int = 4
    denominator: int = 4
    symbol: Optional[str] = None   # 'common'（C，4/4）/ 'cut'（¢，2/2）；None = 数字

    def beats_per_measure(self) -> float:
        return self.numerator * (4 / self.denominator)


@dataclass
class KeySignature:
    sharps: int = 0   # positive = sharps, negative = flats

    def accidentals(self) -> List[tuple]:
        """Return list of (note_name, accidental) for key signature."""
        order_sharps = [NoteName.F, NoteName.C, NoteName.G, NoteName.D, NoteName.A, NoteName.E, NoteName.B]
        order_flats = list(reversed(order_sharps))
        result = []
        if self.sharps > 0:
            # 调号最多 7 个升降号，越界输入做钳制避免 IndexError
            for i in range(min(self.sharps, 7)):
                result.append((order_sharps[i], Accidental.SHARP))
        elif self.sharps < 0:
            for i in range(min(-self.sharps, 7)):
                result.append((order_flats[i], Accidental.FLAT))
        return result


@dataclass
class Measure:
    notes: List[Note] = field(default_factory=list)
    time_signature: Optional[TimeSignature] = None
    key_signature: Optional[KeySignature] = None
    clef: Optional[ClefType] = None
    number: int = 1
    dynamic: Optional[str] = None  # 小节起始力度记号（pp/p/mp/mf/f/ff…）
    tempo_mark: Optional[str] = None  # 小节起始速度标记（Andante / ♩=92 / Rit.…）
    tempo_bpm: Optional[int] = None   # 速度标记的数值（导出为 MetronomeMark，回放用）
    text: Optional[str] = None        # 文字指示（dolce / D.C. al Fine / 等），与速度分开
    rehearsal: Optional[str] = None   # 排练号（A / B / 1…），方框显示
    barline_left: Optional[str] = None   # 'repeat_start'
    barline_right: Optional[str] = None  # 'double' / 'final' / 'repeat_end' / 'dashed'
    volta: Optional[str] = None       # 反复房子号：'1' / '2' / '1,2'
    implicit: bool = False            # 弱起（不完整）小节：不按拍号补满、不算节奏错误

    def total_duration(self) -> float:
        return sum(n.duration_value() for n in self.notes)

    def is_full(self, ts: TimeSignature) -> bool:
        # total_duration 以全音符计；beats_per_measure 以四分音符计，需换算
        if self.implicit and self.notes:
            return True
        return self.total_duration() >= ts.beats_per_measure() / 4.0 - 1e-9

    def remaining_beats(self, ts: TimeSignature) -> float:
        # 返回值以全音符为单位（与 total_duration 一致）
        if self.implicit and self.notes:
            return 0.0
        return ts.beats_per_measure() / 4.0 - self.total_duration()


@dataclass
class Staff:
    clef: ClefType = ClefType.TREBLE
    measures: List[Measure] = field(default_factory=list)
    name: str = ""


@dataclass
class Score:
    title: str = "Untitled"
    composer: str = ""
    tempo: int = 120
    time_signature: TimeSignature = field(default_factory=TimeSignature)
    key_signature: KeySignature = field(default_factory=KeySignature)
    staves: List[Staff] = field(default_factory=list)
    # 大谱表分组：[{'parts':[0,1],'symbol':'brace','name':'Piano'}, ...]
    staff_groups: List[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.staves:
            self.staves.append(Staff())
