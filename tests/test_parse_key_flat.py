"""music21 式降号调性（B- = Bb）解析。"""
from style_skeleton import parse_key


def test_music21_dash_flat_keys():
    assert parse_key('B- major') == (-2, 'major')
    assert parse_key('Bb major') == (-2, 'major')
    assert parse_key('E- major') == (-3, 'major')
    assert parse_key('E- minor') == (-6, 'minor')
    assert parse_key('A- major') == (-4, 'major')
    # 真升号调不受影响
    assert parse_key('B major') == (5, 'major')


def test_jazz_skeleton_uses_bb_not_b():
    import style_skeleton as sk
    from compose_kernel import run_program

    calls = sk.build_skeleton_calls(
        'genre:jazz/swing-standard', bars=16, voices=3,
        seed='parse-key-bb', fugue='off')
    assert calls['calls'][0]['args']['key_sharps'] == -2
    assert calls['calls'][0]['args']['tempo'] == 132
    spec = run_program(calls)
    # ii7–V7–Imaj7 不被叙事惊喜和弦打断
    assert spec['harmony'][1] == ['C', 'Eb', 'G', 'Bb']
    assert spec['harmony'][2] == ['F', 'A', 'C', 'Eb']
    assert spec['harmony'][3] == ['Bb', 'D', 'F', 'A']
    nar = spec.get('narrative') or {}
    assert not (nar.get('surprises') or [])
