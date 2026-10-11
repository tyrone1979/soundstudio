# -*- coding: utf-8 -*-
"""记谱符号：模型 / 渲染 / MusicXML 导出-导入往返；以及本轮脚本自查的回归点。"""
import os
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
warnings.filterwarnings('ignore')

from music_score.models import (  # noqa: E402
    Score, Staff, Measure, Note, Pitch, NoteName as N, Accidental as A,
    NoteDuration as D, ClefType, TimeSignature, KeySignature, CLEF_BOTTOM_LINE,
)

_PASSED = 0
_FAILED = 0
_APP = None


def check(name, cond, extra=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f'  ✅ {name}')
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {extra}')


def _n(name, octave, dur=D.QUARTER, acc=A.NATURAL, **kw):
    return Note(Pitch(name, octave, acc), dur, **kw)


def _rest(dur=D.QUARTER, **kw):
    return Note(Pitch(N.B, 4), dur, is_rest=True, **kw)


def build_demo_score() -> Score:
    rh = Staff(clef=ClefType.TREBLE, name='RH')
    lh = Staff(clef=ClefType.BASS, name='LH')
    m1 = Measure(number=1, dynamic='p', tempo_mark='Andante ♩=72', rehearsal='A',
                 text='dolce')
    m1.notes = [
        _n(N.F, 5, slur_start=True, marks=['staccato']),
        _n(N.F, 5, acc=A.SHARP, marks=['accent']),
        Note(Pitch(N.G, 4), D.QUARTER, chord_tones=[Pitch(N.B, 4), Pitch(N.D, 5)],
             marks=['fermata']),
        _n(N.A, 4, slur_end=True, marks=['tenuto', 'trill']),
    ]
    m2 = Measure(number=2)
    m2.notes = [
        _n(N.B, 4, D.EIGHTH, hairpin_start='crescendo'), _n(N.C, 5, D.EIGHTH),
        _n(N.D, 5, D.SIXTEENTH), _n(N.E, 5, D.SIXTEENTH),
        _n(N.F, 5, D.SIXTEENTH, acc=A.SHARP), _n(N.G, 5, D.SIXTEENTH),
        _n(N.A, 5, D.EIGHTH), _n(N.G, 5, D.SIXTEENTH),
        _n(N.F, 5, D.SIXTEENTH, acc=A.SHARP),
        _n(N.E, 5, tie_start=True, hairpin_end=True, dynamic='f'),
    ]
    m3 = Measure(number=3, barline_right='double')
    m3.notes = [
        _n(N.E, 5, tie_end=True),
        _n(N.D, 5, D.EIGHTH, tuplet=(3, 2), tuplet_start=True),
        _n(N.C, 5, D.EIGHTH, tuplet=(3, 2)),
        _n(N.B, 4, D.EIGHTH, tuplet=(3, 2), tuplet_end=True),
        _n(N.D, 5, D.EIGHTH, grace=True),
        _n(N.C, 5, D.HALF, marks=['mordent'], lyric='la'),
    ]
    m4 = Measure(number=4, key_signature=KeySignature(-1), barline_left='repeat_start',
                 volta='1')
    m4.notes = [
        Note(Pitch(N.C, 5), D.QUARTER, chord_tones=[Pitch(N.D, 5), Pitch(N.F, 4)],
             marks=['arpeggio'], chord_symbol='Dm/C'),
        _n(N.B, 4, marks=['marcato']),
        _n(N.C, 5, acc=A.DOUBLE_SHARP, marks=['staccatissimo']),
        _n(N.B, 3, marks=['turn', 'breath']),
    ]
    m5 = Measure(number=5, time_signature=TimeSignature(3, 4), barline_right='repeat_end',
                 volta='1')
    m5.notes = [_n(N.A, 4, acc=A.FLAT), _n(N.E, 5, acc=A.FLAT),
                _n(N.D, 5, acc=A.DOUBLE_FLAT)]
    m6 = Measure(number=6, time_signature=TimeSignature(4, 4, symbol='common'), volta='2',
                 barline_right='final')
    m6.notes = [_n(N.F, 4, D.WHOLE, marks=['inverted_mordent'])]
    rh.measures = [m1, m2, m3, m4, m5, m6]

    l1 = Measure(number=1)
    l1.notes = [Note(Pitch(N.G, 2), D.WHOLE, chord_tones=[Pitch(N.D, 3)])]
    l2 = Measure(number=2)
    l2.notes = [_n(N.G, 2, D.EIGHTH), _n(N.D, 3, D.EIGHTH), _n(N.B, 3, D.EIGHTH),
                _n(N.D, 3, D.EIGHTH), _rest(D.QUARTER, dots=1),
                _n(N.C, 3, D.EIGHTH, acc=A.SHARP)]
    l3 = Measure(number=3, clef=ClefType.TENOR, barline_right='double')
    l3.notes = [_n(N.C, 4, D.HALF), _rest(D.HALF)]
    l4 = Measure(number=4, key_signature=KeySignature(-1), clef=ClefType.BASS)
    l4.notes = [_rest(D.WHOLE)]
    l5 = Measure(number=5, time_signature=TimeSignature(3, 4), barline_right='repeat_end')
    l5.notes = [_rest(D.HALF, dots=1)]
    l6 = Measure(number=6, time_signature=TimeSignature(4, 4, symbol='common'),
                 barline_right='final')
    l6.notes = [_n(N.F, 2, D.WHOLE)]
    lh.measures = [l1, l2, l3, l4, l5, l6]
    return Score(title='记谱符号演示', time_signature=TimeSignature(4, 4),
                 key_signature=KeySignature(1), staves=[rh, lh],
                 staff_groups=[{'parts': [0, 1], 'symbol': 'brace', 'name': 'Piano'}])


def test_model():
    print('\n=== 模型 ===')
    trip = _n(N.C, 5, D.EIGHTH, tuplet=(3, 2))
    check('三连音八分 = 1/12 全音符', abs(trip.duration_value() - 1 / 12) < 1e-9,
          str(trip.duration_value()))
    check('倚音不占时值', _n(N.D, 5, D.EIGHTH, grace=True).duration_value() == 0.0)
    check('附点四分 = 3/8', abs(_n(N.C, 4, dots=1).duration_value() - 0.375) < 1e-9)
    for ct in (ClefType.TENOR, ClefType.TREBLE_8VB, ClefType.BASS_8VB):
        check(f'{ct.name} 有底线音高表', ct in CLEF_BOTTOM_LINE)
    pick = Measure(implicit=True, notes=[_n(N.G, 4)])
    check('弱起小节视为已满', pick.is_full(TimeSignature(4, 4))
          and pick.remaining_beats(TimeSignature(4, 4)) == 0.0)


def test_render():
    print('\n=== 渲染 ===')
    from PyQt5.QtWidgets import QApplication, QGraphicsScene, QGraphicsTextItem
    from music_score.renderer import ScoreRenderer, pitch_to_staff_position, \
        displayed_accidentals
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv)
    scene = QGraphicsScene()
    renderer = ScoreRenderer(scene)
    renderer.render_score(build_demo_score(), width=1400)
    texts = [it.toPlainText() for it in scene.items() if isinstance(it, QGraphicsTextItem)]
    check('渲染产生图元', len(scene.items()) > 200, str(len(scene.items())))
    for t in ('A', 'dolce', 'Dm/C', 'la', '3', 'tr', '1.', '2.'):
        check(f'谱面含 {t!r}', t in texts)
    check('次中音谱号中央 C 在第 4 线（位置 3.0 = 底线起第 3 线距）',
          pitch_to_staff_position(Pitch(N.C, 4), ClefType.TENOR) == 3.0)
    check('高音 8vb 谱号 E3 在底线',
          pitch_to_staff_position(Pitch(N.E, 3), ClefType.TREBLE_8VB) == 0)
    # G 大调里的 F#：调号已含，不再显示；同小节随后的 F 还原需要还原号，再回 F# 要升号
    g_major = {N.F: 1}
    state = {}
    shows = displayed_accidentals([Pitch(N.F, 5, A.SHARP)], state, g_major)
    check('调内升号不重复显示', shows == [None], str(shows))
    shows = displayed_accidentals([Pitch(N.F, 5, A.NATURAL)], state, g_major)
    check('调外还原显示还原号', shows == [A.NATURAL], str(shows))
    shows = displayed_accidentals([Pitch(N.F, 5, A.SHARP)], state, g_major)
    check('同小节还原后再升要补升号', shows == [A.SHARP], str(shows))
    shows = displayed_accidentals([Pitch(N.F, 5, A.SHARP)], {}, g_major, tied=True)
    check('延音线后半不画变音', shows == [None], str(shows))


_NF = ('is_rest', 'dots', 'tie_start', 'tie_end', 'slur_start', 'slur_end', 'tuplet',
       'tuplet_start', 'tuplet_end', 'grace', 'dynamic', 'hairpin_start', 'hairpin_end',
       'lyric', 'chord_symbol', 'duration')
_MF = ('time_signature', 'key_signature', 'clef', 'dynamic', 'tempo_mark', 'text',
       'rehearsal', 'barline_left', 'barline_right', 'volta', 'implicit')


def _diff_scores(a: Score, b: Score):
    out = []
    for si, (sa, sb) in enumerate(zip(a.staves, b.staves)):
        if sa.clef != sb.clef:
            out.append(f'S{si} clef')
        if len(sa.measures) != len(sb.measures):
            out.append(f'S{si} measure count')
            continue
        for mi, (ma, mb) in enumerate(zip(sa.measures, sb.measures)):
            for f in _MF:
                if getattr(ma, f) != getattr(mb, f):
                    out.append(f'S{si}M{mi + 1}.{f}: {getattr(ma, f)!r} → {getattr(mb, f)!r}')
            if len(ma.notes) != len(mb.notes):
                out.append(f'S{si}M{mi + 1} note count')
                continue
            for ni, (na, nb) in enumerate(zip(ma.notes, mb.notes)):
                for f in _NF:
                    if getattr(na, f) != getattr(nb, f):
                        out.append(f'S{si}M{mi + 1}N{ni}.{f}')
                if sorted(na.marks) != sorted(nb.marks):
                    out.append(f'S{si}M{mi + 1}N{ni}.marks {na.marks} → {nb.marks}')
                if not na.is_rest:
                    pa = sorted([na.pitch.to_midi()] + [p.to_midi() for p in na.chord_tones])
                    pb = sorted([nb.pitch.to_midi()] + [p.to_midi() for p in nb.chord_tones])
                    if pa != pb:
                        out.append(f'S{si}M{mi + 1}N{ni} pitches')
    return out


def _roundtrip(model: Score) -> Score:
    import tempfile
    from music21 import converter
    from music_score.exporter import model_to_m21
    import score_studio as ss
    fd, path = tempfile.mkstemp(suffix='.musicxml')
    os.close(fd)
    try:
        model_to_m21(model).write('musicxml', fp=path)
        return ss.m21_to_model(converter.parse(path), 'rt')
    finally:
        os.remove(path)


def test_roundtrip_demo():
    print('\n=== 导出 → 导入往返（全部新符号）===')
    src = build_demo_score()
    back = _roundtrip(src)
    diffs = _diff_scores(src, back)
    check('演示谱往返零差异', not diffs, '; '.join(diffs[:8]))
    check('首小节速度数值回读', back.staves[0].measures[0].tempo_bpm == 72)
    check('C 拍号回读', back.staves[0].measures[5].time_signature.symbol == 'common')


def test_pickup_roundtrip():
    print('\n=== 弱起小节 ===')
    st = Staff(clef=ClefType.TREBLE, name='Melody')
    p = Measure(number=0, implicit=True, notes=[_n(N.D, 5)])
    m1 = Measure(number=1, notes=[_n(N.G, 4, D.WHOLE)])
    st.measures = [p, m1]
    back = _roundtrip(Score(staves=[st], key_signature=KeySignature(1)))
    ms = back.staves[0].measures
    check('弱起小节仍是 2 小节', len(ms) == 2, str(len(ms)))
    check('弱起标记保留', ms and ms[0].implicit)
    check('弱起只含一个四分音符', ms and len(ms[0].notes) == 1, str(ms[0].notes if ms else ''))
    check('弱起编号为 0', ms and ms[0].number == 0)
    check('下一小节的音没有被吞进弱起', len(ms) > 1 and ms[1].notes[0].pitch.name == N.G)


def test_real_files():
    print('\n=== 真实曲目导入 ===')
    from music21 import converter
    import score_studio as ss
    path = os.path.join(ROOT, 'scores', 'chopin_mazurka.xml')
    if not os.path.isfile(path):
        check('chopin_mazurka.xml 存在', False, path)
        return
    m = ss.m21_to_model(converter.parse(path), 'mazurka')
    notes = [n for n in m.staves[0].measures[7].notes]
    trip = [n for n in notes if n.tuplet == (3, 2)]
    check('马祖卡第 8 小节三连音按 3:2 读入', len(trip) == 3, str([n.tuplet for n in notes]))
    check('三连音组首尾标记', trip and trip[0].tuplet_start and trip[-1].tuplet_end)
    mid = sorted([trip[1].pitch.to_midi()] + [p.to_midi() for p in trip[1].chord_tones]) \
        if trip else []
    check('三连音第二个和弦是 F#4+G#4（原导入误读成 Eb4+G#4）', mid == [66, 68], str(mid))
    back = _roundtrip(m)
    diffs = _diff_scores(m, back)
    check('马祖卡往返零差异', not diffs, '; '.join(diffs[:5]))


def test_deprecated_registry():
    print('\n=== 规则库：过期代码登记 ===')
    import rule_library as RL
    prompt_side = RL.list_rules(include_scripts=False)
    dep = RL.list_rules()
    dep_only = [r for r in dep if r.get('status') == 'deprecated']
    check('提示词侧（不含脚本）列表不含过期项',
          all(r.get('status') != 'deprecated' for r in prompt_side))
    check('显式 include_deprecated=False 不含过期项',
          all(r.get('status') != 'deprecated'
              for r in RL.list_rules(include_deprecated=False)))
    check('过期项 ≥ 8', len(dep_only) >= 8, str(len(dep_only)))
    ids = {r['id'] for r in dep_only}
    check('_fugue_counterpoint 已登记过期', any('fugue_counterpoint' in i for i in ids),
          str(sorted(ids)))
    doc = RL.format_rule_doc(dep_only[0]) if dep_only else ''
    check('过期项文档含状态行', '已过期' in doc, doc[:120])


def test_script_regressions():
    print('\n=== 确定性脚本回归 ===')
    import compose_kernel as K
    prog = {'calls': [
        {'script': 'new_score', 'args': {'title': 't', 'key_sharps': 0, 'meter': '4/4',
                                         'tempo': 96, 'bars': 8, 'voices': 5}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'A', 'bars': 8}]}},
        {'script': 'fugue', 'args': {}},
        {'script': 'finalize', 'args': {}},
    ]}
    try:
        K.run_program(prog)
        check('5 声部 fugue 报 KernelError', False, '未抛')
    except K.KernelError as e:
        check('5 声部 fugue 报 KernelError', '2–4' in str(e), str(e))
    except Exception as e:  # noqa: BLE001
        check('5 声部 fugue 报 KernelError', False, f'{type(e).__name__}: {e}')

    from llm_compose import ComposeSession, compose_from_text, CommandOnly
    try:
        compose_from_text(ComposeSession(), '/addbars 4')
        check('/addbars 返回界面标记', False, '未抛 CommandOnly')
    except CommandOnly as e:
        check('/addbars 返回干净的界面标记', e.message.strip() == '__ADDBARS__:4',
              repr(e.message[:60]))


if __name__ == '__main__':
    test_model()
    test_render()
    test_roundtrip_demo()
    test_pickup_roundtrip()
    test_real_files()
    test_deprecated_registry()
    test_script_regressions()
    print(f'\n通过 {_PASSED} / 失败 {_FAILED}')
    sys.exit(1 if _FAILED else 0)
