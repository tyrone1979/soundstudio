from score_learning.grammar.check import check_satb, structural_pitches
from score_learning.grammar.theory import QUALITIES, SEVENTHS, chord_members

RANGES = {k: {'low': 0, 'high': 127} for k in 'SATB'}


def beat(t, midi, root, q, phrase_start=False):
    mem = chord_members(root, q)
    return {'t': t, 'midi': list(midi), 'rel': [m % 12 for m in midi], 'members': mem,
            'lt_pc': 11 if root in (7, 11) and 11 in mem else None,
            'tonic_chord': root == 0,
            'seventh_pc': (root + QUALITIES[q][3]) % 12 if q in SEVENTHS else None,
            'is_seventh': q in SEVENTHS, 'phrase_start': phrase_start,
            'chord': f'{root}:{q}', 'root': root, 'quality': q, 'mode': 'major'}


def rules(beats, halves=None, refined=False):
    grid = []
    for b in beats:
        grid.append((b['t'], list(b['midi'])))
        if halves and b['t'] in halves:
            grid.append((b['t'] + 0.5, halves[b['t']]))
    return {v['rule'] for v in check_satb(grid, beats, RANGES, tb_max=24, refined=refined)}


def test_suspension_resolved_within_beat_completes_chord():
    b = beat(0.0, [74, 72, 67, 43], 7, 'M')
    half = [74, 71, 67, 43]
    assert structural_pitches(b, half) == [74, 71, 67, 43]
    assert 'R09' in rules([b], {0.0: half})
    assert 'R09' not in rules([b], {0.0: half}, refined=True)


def test_voices_parting_from_unison_is_not_overlap():
    bs = [beat(0.0, [72, 72, 64, 48], 0, 'M'), beat(1.0, [71, 67, 62, 43], 7, 'M')]
    assert 'R03' in rules(bs)
    assert 'R03' not in rules(bs, refined=True)


def test_hidden_octave_within_one_chord_is_licensed():
    bs = [beat(0.0, [76, 67, 60, 52], 0, 'M'), beat(1.0, [72, 67, 64, 48], 0, 'M')]
    assert 'R02' in rules(bs)
    assert 'R02' not in rules(bs, refined=True)


def test_descending_tritone_recovered_by_step():
    bs = [beat(0.0, [77, 74, 70, 46], 10, 'M'), beat(1.0, [76, 72, 64, 48], 0, 'M'),
          beat(2.0, [77, 72, 65, 53], 5, 'M')]
    assert 'R08' in rules(bs)
    assert 'R08' not in rules(bs, refined=True)


def test_seventh_rising_with_bass_in_v43_to_i6():
    bs = [beat(0.0, [71, 65, 55, 50], 7, '7'), beat(1.0, [72, 67, 60, 52], 0, 'M')]
    assert 'R07' in rules(bs)
    assert 'R07' not in rules(bs, refined=True)


def test_secondary_leading_tone_must_rise():
    bad = [beat(0.0, [78, 69, 62, 50], 2, 'M'), beat(1.0, [74, 71, 67, 43], 7, 'M')]
    good = [beat(0.0, [78, 69, 62, 50], 2, 'M'), beat(1.0, [79, 71, 62, 43], 7, 'M')]
    assert 'R06' not in rules(bad)
    assert 'R06' in rules(bad, refined=True)
    assert 'R06' not in rules(good, refined=True)


def test_diatonic_falling_fifth_is_not_secondary_dominant():
    bs = [beat(0.0, [76, 67, 60, 48], 0, 'M'), beat(1.0, [72, 69, 65, 53], 5, 'M')]
    assert 'R06' not in rules(bs, refined=True)
