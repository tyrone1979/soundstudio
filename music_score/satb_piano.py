"""钢琴大谱表四部和声：把 SATB 实现写入 RH(S+A) / LH(T+B)。

确定性、不调 LLM；复用 chord_advisor.realize_satb。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .assist_engine import blank_score
from .models import (
    Accidental, Note, NoteDuration, NoteName, Pitch, Score,
)

_NAME = {
    'C': NoteName.C, 'D': NoteName.D, 'E': NoteName.E, 'F': NoteName.F,
    'G': NoteName.G, 'A': NoteName.A, 'B': NoteName.B,
}


def _midi_to_pitch(midi: int, prefer_name: Optional[str] = None) -> Pitch:
    """MIDI → Pitch；prefer_name 为顾问拼写（如 'Bb'/'F#'）时优先沿用。"""
    if prefer_name:
        raw = str(prefer_name).strip()
        # 剥八度数字
        i = len(raw)
        while i > 0 and raw[i - 1].isdigit():
            i -= 1
        letter = raw[:i]
        if letter:
            base = letter[0].upper()
            rest = letter[1:].replace('♯', '#').replace('♭', 'b')
            acc = Accidental.NATURAL
            if rest in ('#', '##', 'x'):
                acc = Accidental.SHARP if rest == '#' else Accidental.DOUBLE_SHARP
            elif rest in ('b', 'bb', '-'):
                acc = Accidental.FLAT if rest in ('b', '-') else Accidental.DOUBLE_FLAT
            nm = _NAME.get(base)
            if nm is not None:
                octv = midi // 12 - 1
                p = Pitch(nm, octv, acc)
                if p.to_midi() == midi:
                    return p
                # 八度校正
                p = Pitch(nm, octv + (midi - p.to_midi()) // 12, acc)
                if p.to_midi() == midi:
                    return p
    return Pitch.from_midi(int(midi))


def _pair_note(hi_midi: int, lo_midi: int, dur: NoteDuration,
               hi_name: Optional[str] = None,
               lo_name: Optional[str] = None) -> Note:
    """两音叠写：高音为主 pitch，低音进 chord_tones。"""
    if hi_midi < lo_midi:
        hi_midi, lo_midi = lo_midi, hi_midi
        hi_name, lo_name = lo_name, hi_name
    hi = _midi_to_pitch(hi_midi, hi_name)
    if hi_midi == lo_midi:
        return Note(hi, dur)
    lo = _midi_to_pitch(lo_midi, lo_name)
    return Note(hi, dur, chord_tones=[lo])


def _bar_duration(beats: float) -> NoteDuration:
    """按小节拍数选一个能填满的单一时值（不足则用全音符近似）。"""
    if abs(beats - 4.0) < 1e-6:
        return NoteDuration.WHOLE
    if abs(beats - 3.0) < 1e-6:
        return NoteDuration.HALF  # 半+附点由调用方补；此处用半+四分组合更好
    if abs(beats - 2.0) < 1e-6:
        return NoteDuration.HALF
    return NoteDuration.WHOLE


def _fill_measure_notes(beats: float, note: Note) -> List[Note]:
    """把一个和声事件按时值填满小节（3/4 → 附点二分；其它 → 单音）。"""
    if abs(beats - 3.0) < 1e-6:
        # 附点二分 = 3 拍
        n = Note(note.pitch, NoteDuration.HALF, dots=1,
                 chord_tones=list(note.chord_tones), marks=list(note.marks))
        return [n]
    if abs(beats - 4.0) < 1e-6:
        return [Note(note.pitch, NoteDuration.WHOLE,
                     chord_tones=list(note.chord_tones))]
    if abs(beats - 2.0) < 1e-6:
        return [Note(note.pitch, NoteDuration.HALF,
                     chord_tones=list(note.chord_tones))]
    # 兜底：重复四分至填满
    n_q = max(1, int(round(beats)))
    return [Note(note.pitch, NoteDuration.QUARTER,
                 chord_tones=list(note.chord_tones)) for _ in range(n_q)]


def apply_satb_to_score(score: Score, realizations: List[Dict],
                        *, start_bar: int = 0) -> int:
    """把 realizations 写入钢琴大谱表。返回写入的小节数。

    start_bar: 0-based。若谱面不够长则截断。
    """
    if len(score.staves) < 2:
        raise ValueError('需要钢琴大谱表（RH+LH）才能写入四部和声')
    rh, lh = score.staves[0], score.staves[1]
    beats = score.time_signature.beats_per_measure()
    written = 0
    for i, real in enumerate(realizations):
        mi = start_bar + i
        if mi >= len(rh.measures) or mi >= len(lh.measures):
            break
        voices = list(real.get('voices') or [])
        names = list(real.get('names') or [])
        if len(voices) < 4:
            continue
        s, a, t, b = (int(voices[0]), int(voices[1]),
                      int(voices[2]), int(voices[3]))
        sn = names[0] if len(names) > 0 else None
        an = names[1] if len(names) > 1 else None
        tn = names[2] if len(names) > 2 else None
        bn = names[3] if len(names) > 3 else None
        rh_note = _pair_note(s, a, _bar_duration(beats), sn, an)
        lh_note = _pair_note(t, b, _bar_duration(beats), tn, bn)
        rh.measures[mi].notes = _fill_measure_notes(beats, rh_note)
        lh.measures[mi].notes = _fill_measure_notes(beats, lh_note)
        written += 1
    return written


def build_satb_piano_score(
    progression: str,
    *,
    title: str = '四部和声',
    tempo: int = 72,
    meter: str = '4/4',
    sharps: Optional[int] = None,
    mode: Optional[str] = None,
) -> Tuple[Score, str, Dict]:
    """从进行文本生成钢琴大谱表四部和声 + 顾问报告。

    返回 (score_model, markdown_report, meta)。
    """
    import chord_advisor as ca

    arg = str(progression or '').strip()
    if not arg:
        raise ValueError('请提供进行，例如：I V vi IV 或 Bb I V vi IV')

    prog_text, _had_key = ca._strip_leading_key(arg)
    tonic_pc, parsed_mode, key_label = ca.parse_key(arg)
    use_mode = mode or parsed_mode
    if sharps is None:
        # 从 key_label / tonic 推调号（大调圈）
        major_sharps = {
            0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: 7,
            5: -1, 10: -2, 3: -3, 8: -4,
        }
        use_sharps = int(major_sharps.get(tonic_pc % 12, 0))
        if use_mode == 'minor':
            # 关系大调调号 = 小调主音 + 3 的大调
            rel = (tonic_pc + 3) % 12
            use_sharps = int(major_sharps.get(rel, 0))
    else:
        use_sharps = int(sharps)

    if not ca.looks_like_progression(prog_text):
        raise ValueError(
            f'无法识别为和弦进行：{prog_text!r}（示例：I V vi IV）')

    tokens = ca._split_progression(prog_text)
    analysis = ca.analyze_progression(tokens, tonic_pc, use_mode)
    real = ca.realize_satb(analysis, tonic_pc, use_mode)
    realizations = real['realizations']
    n = max(1, len(realizations))

    score = blank_score(
        title=title, bars=n, tempo=int(tempo), sharps=use_sharps,
        meter=meter, composer='四部和声', layout='piano')
    apply_satb_to_score(score, realizations)

    # 复用顾问报告，并注明已写入大谱表
    report = ca.advisor(arg)
    report += (
        '\n\n---\n'
        f'✅ **已写入钢琴大谱表**：{n} 小节 · RH=S+A · LH=T+B · '
        f'调号 {use_sharps:+d} · {meter} · ♩={tempo}\n'
        '可在编辑模式继续改写各声部；严格校验会提示平行五八度。'
    )
    meta = {
        'title': title,
        'bars': n,
        'key_sharps': use_sharps,
        'mode': use_mode,
        'time_signature': meter,
        'tempo': tempo,
        'staff_groups': list(score.staff_groups),
        'parallels': list(real.get('parallels') or []),
        'progression': tokens,
    }
    return score, report, meta
