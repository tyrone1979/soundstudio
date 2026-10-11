"""
【已过期】MusicXML importer using music21 library.
Converts music21 streams to our internal Score model.

只被旧版 main_window 使用；工作室走 score_studio.m21_to_model（支持延音线、
连音、换调换谱号等），本模块不再维护，后续版本删除。
"""
import warnings
from typing import List, Optional

warnings.warn('music_score.musicxml_importer 已过期，请使用 score_studio.m21_to_model',
              DeprecationWarning, stacklevel=2)

try:
    from music21 import converter, stream, note as m21_note, key as m21_key, meter as m21_meter, clef as m21_clef, chord as m21_chord
    HAS_MUSIC21 = True
except ImportError:
    HAS_MUSIC21 = False

from .models import (
    Score, Staff, Measure, Note, Pitch,
    ClefType, NoteName, Accidental, NoteDuration, TimeSignature, KeySignature
)


# Map music21 pitch names to our NoteName
PITCH_NAME_MAP = {
    'C': NoteName.C, 'D': NoteName.D, 'E': NoteName.E,
    'F': NoteName.F, 'G': NoteName.G, 'A': NoteName.A, 'B': NoteName.B,
}

# Map music21 durations to our NoteDuration (approximate)
def duration_to_enum(quarter_length: float) -> NoteDuration:
    if quarter_length >= 3.5:
        return NoteDuration.WHOLE
    elif quarter_length >= 1.75:
        return NoteDuration.HALF
    elif quarter_length >= 0.875:
        return NoteDuration.QUARTER
    elif quarter_length >= 0.4375:
        return NoteDuration.EIGHTH
    elif quarter_length >= 0.21875:
        return NoteDuration.SIXTEENTH
    else:
        return NoteDuration.THIRTY_SECOND


def m21_pitch_to_pitch(m21_pitch) -> Pitch:
    """Convert music21 pitch to our Pitch model."""
    name = PITCH_NAME_MAP.get(m21_pitch.step, NoteName.C)
    octave = m21_pitch.octave
    if m21_pitch.accidental:
        acc_name = m21_pitch.accidental.name
        if acc_name == 'sharp':
            accidental = Accidental.SHARP
        elif acc_name == 'flat':
            accidental = Accidental.FLAT
        elif acc_name == 'natural':
            accidental = Accidental.NATURAL
        elif acc_name == 'double-sharp':
            accidental = Accidental.DOUBLE_SHARP
        elif acc_name == 'double-flat':
            accidental = Accidental.DOUBLE_FLAT
        else:
            accidental = Accidental.NATURAL
    else:
        accidental = Accidental.NATURAL
    return Pitch(name, octave, accidental)


def m21_clef_to_cleftype(m21_clef_obj) -> ClefType:
    """Convert music21 clef to our ClefType."""
    if isinstance(m21_clef_obj, m21_clef.TrebleClef):
        return ClefType.TREBLE
    elif isinstance(m21_clef_obj, m21_clef.BassClef):
        return ClefType.BASS
    elif isinstance(m21_clef_obj, m21_clef.AltoClef):
        return ClefType.ALTO
    else:
        return ClefType.TREBLE


def import_musicxml(path: str) -> Optional[Score]:
    """Import a MusicXML file and convert to our Score model."""
    if not HAS_MUSIC21:
        raise ImportError("music21 library is not installed. Run: pip install music21")

    m21_score = converter.parse(path)
    return convert_m21_score(m21_score)


def convert_m21_score(m21_score) -> Score:
    """Convert a music21 Score to our Score model."""
    title = "Untitled"
    composer = ""
    if m21_score.metadata:
        title = m21_score.metadata.title or title
        composer = m21_score.metadata.composer or composer
        # music21 sometimes stores title in movementName
        if title == "Untitled" and hasattr(m21_score.metadata, 'movementName') and m21_score.metadata.movementName:
            title = m21_score.metadata.movementName
        # Also check all metadata
        if title == "Untitled":
            for key_val, val in m21_score.metadata.all():
                if key_val == 'movementName' and val:
                    title = val
                    break

    score = Score(title=title, composer=composer)
    score.staves = []

    # Get global time signature and key signature from first part
    global_ts = TimeSignature(4, 4)
    global_ks = KeySignature(0)

    for part in m21_score.parts:
        staff = Staff()
        measures = list(part.getElementsByClass('Measure'))

        if not measures:
            continue

        # Extract clef from first measure
        first_measure = measures[0]
        clef_objs = list(first_measure.getElementsByClass('Clef'))
        if clef_objs:
            staff.clef = m21_clef_to_cleftype(clef_objs[0])

        # Extract key signature
        key_objs = list(first_measure.getElementsByClass('KeySignature'))
        if key_objs:
            ks = key_objs[0]
            global_ks = KeySignature(ks.sharps)

        # Extract time signature
        ts_objs = list(first_measure.getElementsByClass('TimeSignature'))
        if ts_objs:
            ts = ts_objs[0]
            global_ts = TimeSignature(ts.numerator, ts.denominator)

        score.time_signature = global_ts
        score.key_signature = global_ks

        for m21_measure in measures:
            measure = Measure()
            measure.time_signature = global_ts
            measure.key_signature = global_ks
            measure.clef = staff.clef

            # Get all notes and rests
            for element in m21_measure.notesAndRests:
                if isinstance(element, m21_note.Rest):
                    dur = duration_to_enum(element.quarterLength)
                    measure.notes.append(Note(
                        Pitch(NoteName.C, 4),
                        dur,
                        is_rest=True
                    ))
                elif isinstance(element, m21_note.Note):
                    dur = duration_to_enum(element.quarterLength)
                    pitch = m21_pitch_to_pitch(element.pitch)
                    dots = 0
                    # Simple dot detection
                    ql = element.quarterLength
                    base_dur = dur.value * 4  # Convert to quarter length
                    if ql > base_dur:
                        dots = 1
                    measure.notes.append(Note(pitch, dur, dots=dots))
                elif isinstance(element, m21_chord.Chord):
                    # For chords, just take the top note for simplicity
                    if element.pitches:
                        dur = duration_to_enum(element.quarterLength)
                        pitch = m21_pitch_to_pitch(element.pitches[-1])
                        measure.notes.append(Note(pitch, dur))

            staff.measures.append(measure)

        score.staves.append(staff)

    if not score.staves:
        score.staves.append(Staff())

    return score
