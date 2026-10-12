"""
Score 模型 → music21 Score 导出器。

编辑器改完模型后经此转回 music21，用于回写 MusicXML（保持分析报告、
导出、归档与谱面一致）。与 m21_to_model 互为近似逆：音符/休止/和弦/附点/
变音/连音（三连音等）/倚音/延音线/圆滑线/演奏法与装饰音/力度（含小节中途）/
渐强渐弱/速度（文字 + MetronomeMark）/文字指示/排练号/歌词/和弦记号/
调号与拍号（含换调、换拍、C/¢）/谱号（含换谱号）/小节线与反复/房子号/弱起
均保留。
"""
import re
from typing import Optional

from .models import (
    Score, ClefType, NoteName, Accidental, NoteDuration,
)

_ACC_M21 = {
    Accidental.NATURAL: '',
    Accidental.SHARP: '#',
    Accidental.FLAT: '-',
    Accidental.DOUBLE_SHARP: '##',
    Accidental.DOUBLE_FLAT: '--',
}
_ARTICULATIONS = {'staccato', 'staccatissimo', 'accent', 'marcato', 'tenuto', 'breath'}
_EXPRESSIONS = {'fermata', 'trill', 'mordent', 'inverted_mordent', 'turn'}
_BPM_RE = re.compile(r'\s*♩\s*=\s*(\d+(?:\.\d+)?)\s*$')


def split_tempo_mark(text: Optional[str], bpm: Optional[int] = None):
    """'Andante ♩=72' → ('Andante', 72)；bpm 显式给出时优先。"""
    txt = (text or '').strip()
    num = bpm
    m = _BPM_RE.search(txt)
    if m:
        if num is None:
            try:
                num = int(round(float(m.group(1))))
            except ValueError:
                num = None
        txt = txt[:m.start()].strip()
    return txt, num


def _m21_clef(ct: ClefType):
    from music21 import clef
    return {
        ClefType.TREBLE: clef.TrebleClef,
        ClefType.BASS: clef.BassClef,
        ClefType.ALTO: clef.AltoClef,
        ClefType.TENOR: clef.TenorClef,
        ClefType.TREBLE_8VB: clef.Treble8vbClef,
        ClefType.BASS_8VB: clef.Bass8vbClef,
    }.get(ct, clef.TrebleClef)()


def _pitch_name(p) -> str:
    return p.name.name + _ACC_M21.get(p.accidental, '') + str(p.octave)


def _barline(style: Optional[str], side: str):
    from music21 import bar
    if not style or style == 'regular':
        return None
    if style == 'repeat_start':
        return bar.Repeat(direction='start') if side == 'left' else None
    if style == 'repeat_end':
        return bar.Repeat(direction='end') if side == 'right' else None
    return {'double': bar.Barline('double'), 'final': bar.Barline('final'),
            'dashed': bar.Barline('dashed')}.get(style)


def _attach_marks(el, marks, is_chord: bool):
    from music21 import articulations as A
    from music21 import expressions as E
    art_cls = {'staccato': A.Staccato, 'staccatissimo': A.Staccatissimo,
               'accent': A.Accent, 'marcato': A.StrongAccent, 'tenuto': A.Tenuto,
               'breath': A.BreathMark}
    exp_cls = {'fermata': E.Fermata, 'trill': E.Trill, 'mordent': E.Mordent,
               'inverted_mordent': E.InvertedMordent, 'turn': E.Turn}
    for mk in marks or []:
        if mk in art_cls:
            el.articulations.append(art_cls[mk]())
        elif mk in exp_cls:
            el.expressions.append(exp_cls[mk]())
        elif mk == 'arpeggio' and is_chord:
            el.expressions.append(E.ArpeggioMark())


def _build_element(n):
    """模型 Note → music21 Note/Chord/Rest（含时值、连音、倚音、延音线、记号、歌词）。"""
    from music21 import note, chord, duration, tie
    if n.is_rest:
        el = note.Rest()
    elif n.chord_tones:
        el = chord.Chord([_pitch_name(n.pitch)] + [_pitch_name(p) for p in n.chord_tones])
    else:
        el = note.Note(_pitch_name(n.pitch))
    d = duration.Duration(quarterLength=n.duration.value * 4.0)
    d.dots = int(n.dots or 0)
    tup = getattr(n, 'tuplet', None)
    if tup and not getattr(n, 'grace', False):
        t = duration.Tuplet(int(tup[0]), int(tup[1]))
        try:
            t.setDurationType(d.type, d.dots)
        except Exception:
            pass
        if n.tuplet_start:
            t.type = 'start'
            t.bracket = True
        elif n.tuplet_end:
            t.type = 'stop'
        d.appendTuplet(t)
    el.duration = d
    if getattr(n, 'grace', False) and not n.is_rest:
        el = el.getGrace()
    if not n.is_rest:
        if n.tie_start and n.tie_end:
            el.tie = tie.Tie('continue')
        elif n.tie_start:
            el.tie = tie.Tie('start')
        elif n.tie_end:
            el.tie = tie.Tie('stop')
        _attach_marks(el, n.marks, bool(n.chord_tones))
        if getattr(n, 'lyric', None):
            el.lyric = str(n.lyric)
    elif 'fermata' in (n.marks or []):
        _attach_marks(el, ['fermata'], False)
    return el


def model_to_m21(score: Score, title: Optional[str] = None,
                 composer: Optional[str] = None):
    """把编辑器模型转成 music21 Score（多声部 = 多 Part）。"""
    from music21 import stream, meter, key, tempo, spanner, harmony
    from music21 import dynamics as m21dynamics
    from music21 import expressions as m21expressions
    from music21 import instrument as m21instrument
    from music21 import metadata as m21metadata

    sc = stream.Score()
    meta = m21metadata.Metadata()
    meta.title = title if title is not None else score.title
    comp = composer if composer is not None else score.composer
    if comp:
        meta.composer = comp
    sc.insert(0, meta)

    built = []
    is_piano = bool(score.staff_groups) or (
        len(score.staves) == 2
        and any((s.name or '').upper() in ('RH', 'LH', 'PIANO')
                for s in score.staves)
    )
    for si, staff in enumerate(score.staves):
        part = stream.Part(id=f'P{si + 1}')
        if is_piano:
            try:
                inst = m21instrument.Piano()
            except Exception:
                inst = m21instrument.Instrument()
            label = staff.name or ('RH' if si == 0 else 'LH')
            inst.partName = f'Piano {label}' if label in ('RH', 'LH') else label
            inst.instrumentName = inst.partName
        else:
            inst = m21instrument.Instrument()
            inst.partName = staff.name or None
            if staff.name:
                inst.instrumentName = staff.name
        part.insert(0, inst)

        first_has_bpm = False
        if si == 0 and staff.measures:
            m0 = staff.measures[0]
            first_has_bpm = split_tempo_mark(m0.tempo_mark, m0.tempo_bpm)[1] is not None
        # 全局速度放进首声部（score 级 MetronomeMark 不参与 MusicXML 导出）
        if si == 0 and score.tempo and not first_has_bpm:
            part.insert(0, tempo.MetronomeMark(number=int(score.tempo)))

        slur_stack = []
        hairpin_open = None
        volta_run = []   # (volta_label, [measures])
        pickup = bool(staff.measures) and staff.measures[0].implicit
        prev_clef = staff.clef
        for mi, m in enumerate(staff.measures):
            meas = stream.Measure(number=mi if pickup else mi + 1)
            if mi == 0:
                meas.insert(0, _m21_clef(staff.clef))
                ks = m.key_signature or score.key_signature
                meas.insert(0, key.KeySignature(int(ks.sharps)))
            elif m.key_signature is not None:
                meas.insert(0, key.KeySignature(int(m.key_signature.sharps)))
            if m.clef is not None and (mi > 0 or m.clef != staff.clef) and m.clef != prev_clef:
                meas.insert(0, _m21_clef(m.clef))
            if m.clef is not None:
                prev_clef = m.clef
            ts = m.time_signature or (score.time_signature if mi == 0 else None)
            if ts is not None:
                mts = meter.TimeSignature(f'{ts.numerator}/{ts.denominator}')
                if getattr(ts, 'symbol', None) in ('common', 'cut'):
                    mts.symbol = ts.symbol
                meas.insert(0, mts)
            if m.dynamic:
                try:
                    meas.insert(0, m21dynamics.Dynamic(m.dynamic))
                except Exception:
                    pass
            if si == 0 and m.tempo_mark:
                txt, bpm = split_tempo_mark(m.tempo_mark, m.tempo_bpm)
                if txt:
                    meas.insert(0, m21expressions.TextExpression(txt))
                if bpm is not None:
                    meas.insert(0, tempo.MetronomeMark(number=bpm))
            elif si == 0 and m.tempo_bpm:
                meas.insert(0, tempo.MetronomeMark(number=int(m.tempo_bpm)))
            if m.text:
                meas.insert(0, m21expressions.TextExpression(str(m.text)))
            if si == 0 and m.rehearsal:
                meas.insert(0, m21expressions.RehearsalMark(str(m.rehearsal)))
            lb = _barline(m.barline_left, 'left')
            if lb is not None:
                meas.leftBarline = lb
            rb = _barline(m.barline_right, 'right')
            if rb is not None:
                meas.rightBarline = rb

            offset = 0.0
            for n in m.notes:
                el = _build_element(n)
                meas.insert(offset, el)
                if getattr(n, 'dynamic', None):
                    try:
                        meas.insert(offset, m21dynamics.Dynamic(n.dynamic))
                    except Exception:
                        pass
                if getattr(n, 'chord_symbol', None):
                    try:
                        meas.insert(offset, harmony.ChordSymbol(str(n.chord_symbol)))
                    except Exception:
                        pass
                if not n.is_rest:
                    if n.slur_end and slur_stack:
                        first = slur_stack.pop()
                        part.insert(0, spanner.Slur(first, el))
                    if n.slur_start:
                        slur_stack.append(el)
                if n.hairpin_end and hairpin_open is not None:
                    kind, first = hairpin_open
                    cls = m21dynamics.Crescendo if kind == 'crescendo' else m21dynamics.Diminuendo
                    part.insert(0, cls(first, el))
                    hairpin_open = None
                if n.hairpin_start:
                    hairpin_open = (n.hairpin_start, el)
                offset += float(el.quarterLength)
            if mi == 0 and m.implicit:
                bar_ql = (ts.numerator * 4.0 / ts.denominator) if ts else 4.0
                meas.paddingLeft = max(0.0, bar_ql - offset)

            if m.volta:
                if volta_run and volta_run[-1][0] == m.volta:
                    volta_run[-1][1].append(meas)
                else:
                    volta_run.append((m.volta, [meas]))
            else:
                volta_run.append((None, []))
            part.append(meas)
        for label, ms in volta_run:
            if label and ms:
                try:
                    part.insert(0, spanner.RepeatBracket(ms, number=str(label)))
                except Exception:
                    pass
        built.append(part)

    for part in built:
        sc.insert(0, part)

    groups = list(score.staff_groups or [])
    if not groups and is_piano and len(built) >= 2:
        groups = [{'parts': [0, 1], 'symbol': 'brace', 'name': 'Piano'}]
    for g in groups:
        try:
            from music21 import layout as m21layout
            idxs = [int(i) for i in (g.get('parts') or [])]
            parts = [built[i] for i in idxs if 0 <= i < len(built)]
            if len(parts) >= 2:
                sg = m21layout.StaffGroup(
                    parts, name=str(g.get('name') or 'Piano'),
                    symbol=str(g.get('symbol') or 'brace'), barTogether=True)
                sc.insert(0, sg)
        except Exception:
            pass
    return sc
