"""编辑：添加声部 + 简易配乐（确定性，不调 LLM）。

典型场景：单声部旋律 → 加低音声部 / 钢琴左手 → 按型和声填配乐。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import (
    Accidental, ClefType, Measure, Note, NoteDuration, NoteName, Pitch, Score,
    Staff,
)
from .piano_step import spell_midi

# 声部预设：key → (显示名, 谱号, GM program 提示)
PART_PRESETS: Dict[str, Tuple[str, ClefType, int]] = {
    'bass': ('Bass', ClefType.BASS, 32),
    'lh': ('LH', ClefType.BASS, 0),
    '左手': ('LH', ClefType.BASS, 0),
    '低音': ('Bass', ClefType.BASS, 32),
    'cello': ('Cello', ClefType.BASS, 42),
    '大提琴': ('Cello', ClefType.BASS, 42),
    'contrabass': ('Contrabass', ClefType.BASS, 43),
    'piano': ('LH', ClefType.BASS, 0),  # 升为钢琴大谱表时用
    '钢琴': ('LH', ClefType.BASS, 0),
    'treble': ('Voice', ClefType.TREBLE, 0),
    '高音': ('Voice', ClefType.TREBLE, 0),
    'violin': ('Violin', ClefType.TREBLE, 40),
    '小提琴': ('Violin', ClefType.TREBLE, 40),
    'flute': ('Flute', ClefType.TREBLE, 73),
    '长笛': ('Flute', ClefType.TREBLE, 73),
    'viola': ('Viola', ClefType.ALTO, 41),
    '中提琴': ('Viola', ClefType.ALTO, 41),
    'alto': ('Alto', ClefType.ALTO, 41),
}

ACCOMP_PATTERNS = ('bass', 'block', 'broken', 'alberti')

_MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
_MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)


def _tonic_pc(sharps: int, mode: str = 'major') -> int:
    major = {
        0: 0, 1: 7, 2: 2, 3: 9, 4: 4, 5: 11, 6: 6, 7: 1,
        -1: 5, -2: 10, -3: 3, -4: 8, -5: 1, -6: 6, -7: 11,
    }
    t = int(major.get(int(sharps), 0))
    if str(mode).lower().startswith('min'):
        t = (t - 3) % 12
    return t


def resolve_part_preset(token: str) -> Tuple[str, ClefType, int]:
    """解析声部名 → (name, clef, program)。未知则按低音处理。"""
    raw = str(token or '').strip()
    key = raw.lower().replace(' ', '')
    if key in PART_PRESETS:
        return PART_PRESETS[key]
    # 中文别名直接命中
    if raw in PART_PRESETS:
        return PART_PRESETS[raw]
    # 默认：含「低/bass」→ 低音谱表，否则高音
    if re_search_bass(raw):
        return (raw or 'Bass', ClefType.BASS, 32)
    return (raw or 'Voice', ClefType.TREBLE, 0)


def re_search_bass(text: str) -> bool:
    t = str(text or '').lower()
    return any(k in t for k in (
        'bass', '低音', '左手', 'lh', 'cello', '大提琴', 'contrabass',
        '贝斯', '脚踏',
    ))


def add_staff(
    score: Score,
    *,
    name: str = 'Bass',
    clef: ClefType = ClefType.BASS,
    make_piano_brace: bool = False,
) -> int:
    """在乐谱末尾追加空声部（小节数与最长声部对齐）。返回新声部索引。"""
    if score is None:
        raise ValueError('没有乐谱')
    if not score.staves:
        raise ValueError('乐谱没有谱表')
    n_bars = max(len(s.measures) for s in score.staves)
    n_bars = max(1, n_bars)
    staff = Staff(
        clef=clef,
        name=str(name or 'Voice'),
        measures=[Measure(number=i + 1) for i in range(n_bars)],
    )
    score.staves.append(staff)
    idx = len(score.staves) - 1
    if make_piano_brace and len(score.staves) >= 2:
        # 把前两行收成钢琴花括号（常见：旋律 + LH）
        score.staff_groups = [{
            'parts': [0, 1], 'symbol': 'brace', 'name': 'Piano',
        }]
        if score.staves[0].name in ('', 'Staff', 'Voice', 'Melody'):
            score.staves[0].name = 'RH'
        if score.staves[1].name in ('', 'Bass', 'Voice'):
            score.staves[1].name = 'LH'
            score.staves[1].clef = ClefType.BASS
    return idx


def add_part_from_token(score: Score, token: str = 'bass') -> Tuple[int, str]:
    """按预设名添加声部。返回 (index, 说明)。"""
    name, clef, _prog = resolve_part_preset(token)
    tok = str(token or '').strip().lower()
    piano = tok in ('piano', '钢琴', 'grand', '大谱表', 'lh', '左手')
    # 单声部升钢琴大谱表
    if piano and len(score.staves) == 1:
        idx = add_staff(score, name='LH', clef=ClefType.BASS,
                        make_piano_brace=True)
        return idx, f'已升为钢琴大谱表（RH + LH），新声部索引 {idx}'
    idx = add_staff(score, name=name, clef=clef, make_piano_brace=False)
    return idx, f'已添加声部「{name}」（索引 {idx}）'


def _bar_midis(staff: Staff, mi: int) -> List[int]:
    if mi < 0 or mi >= len(staff.measures):
        return []
    out: List[int] = []
    for n in staff.measures[mi].notes:
        if getattr(n, 'is_rest', False):
            continue
        try:
            out.append(int(n.pitch.to_midi()))
            for ct in getattr(n, 'chord_tones', None) or []:
                out.append(int(ct.to_midi()))
        except Exception:
            continue
    return out


def _infer_triad_pcs(midis: Sequence[int], tonic: int, mode: str) -> List[int]:
    """从小节音高推断三和弦音级（根/三/五）。"""
    steps = _MINOR_STEPS if str(mode).lower().startswith('min') else _MAJOR_STEPS
    scale = {(tonic + s) % 12 for s in steps}
    pcs = [m % 12 for m in midis if (m % 12) in scale] or [m % 12 for m in midis]
    if not pcs:
        return [tonic, (tonic + 4) % 12, (tonic + 7) % 12]
    # 选出现最多且像和弦根的音级
    from collections import Counter
    cnt = Counter(pcs)
    root = cnt.most_common(1)[0][0]
    # 优先调内大/小三
    maj3, min3 = (root + 4) % 12, (root + 3) % 12
    fifth = (root + 7) % 12
    third = maj3 if maj3 in scale or cnt[maj3] >= cnt[min3] else min3
    if str(mode).lower().startswith('min') and root == tonic:
        third = min3
    return [root, third, fifth]


def _midi_in_octave(pc: int, octave: int) -> int:
    """音级 + 八度 → MIDI（C4=60 口径：octave 4 → 60）。"""
    return 12 * (int(octave) + 1) + (int(pc) % 12)


def _fill_beats(beats: float, notes: List[Note]) -> List[Note]:
    """用休止补满小节拍数（NoteDuration.value 以全音符为单位）。"""
    used = sum(n.duration_value() * (1.0 + 0.5 * getattr(n, 'dots', 0))
               for n in notes)
    # duration_value 已含附点逻辑时优先用它
    used = 0.0
    for n in notes:
        used += float(n.duration_value())
    target = float(beats) / 4.0  # 全音符单位
    rem = target - used
    out = list(notes)
    # 用四分休止补
    q = NoteDuration.QUARTER.value
    while rem > q - 1e-9:
        out.append(Note(Pitch(NoteName.C, 4), NoteDuration.QUARTER, is_rest=True))
        rem -= q
    if rem > 1e-6:
        # 剩余用最接近的短休止近似
        for dur in (NoteDuration.EIGHTH, NoteDuration.SIXTEENTH,
                    NoteDuration.THIRTY_SECOND):
            if rem >= dur.value - 1e-9:
                out.append(Note(Pitch(NoteName.C, 4), dur, is_rest=True))
                rem -= dur.value
                if rem <= 1e-6:
                    break
    return out


def fill_accompaniment(
    score: Score,
    *,
    pattern: str = 'bass',
    target_staff: Optional[int] = None,
    melody_staff: int = 0,
    mode: str = 'major',
    overwrite: bool = True,
) -> Dict[str, Any]:
    """根据旋律声部为配乐声部填入确定性伴奏。

    pattern:
      - bass：每小节一个低音根音（全音符/附点）
      - block：根-三-五柱式和弦
      - broken：分解（根-五-根-三）
      - alberti：阿尔贝蒂低音（根-五-三-五）
    """
    if score is None or not score.staves:
        raise ValueError('没有乐谱')
    pat = str(pattern or 'bass').strip().lower()
    if pat in ('根音', '低音线', 'root'):
        pat = 'bass'
    if pat in ('柱式', '和弦', 'chord', 'block_chord'):
        pat = 'block'
    if pat in ('分解', '琶音', 'arpeggio', 'broken_arpeggio'):
        pat = 'broken'
    if pat in ('阿尔贝蒂', 'alberti_bass'):
        pat = 'alberti'
    if pat not in ACCOMP_PATTERNS:
        raise ValueError(
            f'未知配乐型 {pattern!r}，可选：{", ".join(ACCOMP_PATTERNS)}')

    # 确保有配乐声部
    if target_staff is None:
        if len(score.staves) < 2:
            add_part_from_token(score, 'piano')
        target_staff = len(score.staves) - 1
    ti = int(target_staff)
    mi_mel = int(melody_staff)
    if not (0 <= ti < len(score.staves)):
        raise ValueError(f'配乐声部索引无效：{ti}')
    if not (0 <= mi_mel < len(score.staves)):
        raise ValueError(f'旋律声部索引无效：{mi_mel}')
    if ti == mi_mel:
        raise ValueError('配乐声部不能与旋律声部相同；请先 /addpart 添加声部')

    mel = score.staves[mi_mel]
    acc = score.staves[ti]
    # 对齐小节数
    while len(acc.measures) < len(mel.measures):
        acc.measures.append(Measure(number=len(acc.measures) + 1))
    sharps = int(getattr(getattr(score, 'key_signature', None), 'sharps', 0) or 0)
    tonic = _tonic_pc(sharps, mode)
    beats = float(score.time_signature.beats_per_measure())
    filled = 0
    for bi in range(len(mel.measures)):
        if not overwrite and acc.measures[bi].notes:
            # 已有内容且不覆盖
            if any(not n.is_rest for n in acc.measures[bi].notes):
                continue
        midis = _bar_midis(mel, bi)
        triad = _infer_triad_pcs(midis, tonic, mode)
        root, third, fifth = triad[0], triad[1], triad[2]
        # 低音区：C2–C3 一带
        r = _midi_in_octave(root, 2)
        t = _midi_in_octave(third, 3)
        f3 = _midi_in_octave(fifth, 3)

        def P(m: int) -> Pitch:
            return spell_midi(m, sharps)

        notes: List[Note] = []
        if pat == 'bass':
            if abs(beats - 3.0) < 1e-6:
                notes = [Note(P(r), NoteDuration.HALF, dots=1)]
            elif abs(beats - 2.0) < 1e-6:
                notes = [Note(P(r), NoteDuration.HALF)]
            else:
                notes = [Note(P(r), NoteDuration.WHOLE)]
        elif pat == 'block':
            tones = [P(t), P(r)]
            if abs(beats - 3.0) < 1e-6:
                chord = Note(P(f3), NoteDuration.HALF, dots=1, chord_tones=tones)
            elif abs(beats - 2.0) < 1e-6:
                chord = Note(P(f3), NoteDuration.HALF, chord_tones=tones)
            else:
                chord = Note(P(f3), NoteDuration.WHOLE, chord_tones=tones)
            notes = [chord]
        elif pat == 'broken':
            seq = [r, f3, t, f3]
            if abs(beats - 3.0) < 1e-6:
                durs = [NoteDuration.QUARTER] * 3
                seq = [r, f3, t]
            elif abs(beats - 2.0) < 1e-6:
                durs = [NoteDuration.QUARTER] * 2
                seq = [r, f3]
            else:
                durs = [NoteDuration.QUARTER] * 4
            notes = [Note(P(m), d) for m, d in zip(seq, durs)]
        else:  # alberti
            seq = [r, f3, t, f3]
            if abs(beats - 3.0) < 1e-6:
                notes = [Note(P(m), NoteDuration.QUARTER) for m in (r, f3, t)]
            elif abs(beats - 2.0) < 1e-6:
                notes = [Note(P(m), NoteDuration.QUARTER) for m in (r, f3)]
            else:
                notes = [Note(P(m), NoteDuration.EIGHTH) for m in seq + seq]
                # 8 个八分 = 4 拍
        acc.measures[bi].notes = _fill_beats(beats, notes)
        filled += 1

    return {
        'pattern': pat,
        'target_staff': ti,
        'melody_staff': mi_mel,
        'bars_filled': filled,
        'target_name': acc.name,
        'melody_name': mel.name,
    }


def detect_add_part_request(text: str) -> Optional[str]:
    """自然语言「添加声部」→ 预设 token；未命中返回 None。"""
    t = str(text or '').strip()
    if not t:
        return None
    if not re_search_any(t, r'添加.{0,8}声部|加(一|个)?声部|增加声部|加一行|加谱表|'
                            r'add\s*(a\s*)?(part|voice|staff)|new\s*(part|voice)'):
        return None
    # 指定乐器
    for key in ('钢琴', 'piano', '左手', 'lh', '大提琴', 'cello', '小提琴',
                'violin', '长笛', 'flute', '中提琴', 'viola', '低音', 'bass',
                '高音', 'treble', '贝斯', 'contrabass'):
        if key.lower() in t.lower() or key in t:
            return key
    return 'bass'


def detect_accomp_request(text: str) -> Optional[str]:
    """自然语言「设置/添加配乐」→ pattern；未命中返回 None。"""
    t = str(text or '').strip()
    if not t:
        return None
    if not re_search_any(t, r'配乐|伴奏|accompan|backing|'
                            r'加(个|一个)?(伴奏|配乐)|设置配乐|写配乐|填配乐'):
        return None
    if re_search_any(t, r'阿尔贝蒂|alberti'):
        return 'alberti'
    if re_search_any(t, r'分解|琶音|broken|arpeggio'):
        return 'broken'
    if re_search_any(t, r'柱式|和弦块|block'):
        return 'block'
    return 'bass'


def re_search_any(text: str, pattern: str) -> bool:
    import re
    return bool(re.search(pattern, text, re.I))
