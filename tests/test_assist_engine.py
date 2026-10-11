"""辅助作曲引擎单测：空白谱、下一音候选、短分支、/assist 命令。"""
from music_score.assist_engine import (
    AssistEngine, blank_score, INSPIRATION_PROFILES,
)
from music_score.editor_core import EditController, pitch_midi, pitch_label
from music_score.models import (
    Note, NoteDuration, NoteName, Pitch, Accidental,
)


def test_blank_score_has_writable_grid():
    sc = blank_score(bars=16, sharps=0, meter='4/4')
    assert len(sc.staves) == 1
    assert len(sc.staves[0].measures) == 16
    # 空小节可插入四分音符
    m0 = sc.staves[0].measures[0]
    assert abs(m0.remaining_beats(sc.time_signature) - 1.0) < 1e-9


def test_assist_suggests_leading_tone_resolve():
    sc = blank_score(bars=8, sharps=0)
    # 前缀：B4（导音）→ 期望候选含 C5 主音解决
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.B, 4), NoteDuration.QUARTER))
    eng = AssistEngine()
    eng.enabled = True
    eng.mode = 'major'
    cands = eng.suggest(sc, 0, 0, 1, NoteDuration.QUARTER, limit=8)
    assert cands
    labels = [c.reason for c in cands]
    midis = [pitch_midi(c.pitch) for c in cands]
    # C5 = 72
    assert any(m % 12 == 0 and abs(m - 72) <= 12 for m in midis), midis
    assert any('导音' in r or '主音' in r or '级进' in r for r in labels), labels


def test_assist_chord_tone_preferred():
    sc = blank_score(bars=8, sharps=-2)  # Bb major
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.B, 4, Accidental.FLAT), NoteDuration.QUARTER))
    eng = AssistEngine()
    eng.harmony = {1: 'ii7'}  # Cm7 in Bb
    cands = eng.suggest(sc, 0, 0, 1, NoteDuration.QUARTER, limit=5)
    assert cands
    # 至少一个候选标了和弦音
    assert any('和弦' in (c.reason + ' '.join(c.reasons)) for c in cands), [
        (c.label, c.reason) for c in cands]


def test_phrase_beam_returns_multi_note():
    sc = blank_score(bars=8, sharps=0)
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.C, 5), NoteDuration.QUARTER))
    eng = AssistEngine()
    notes = eng.suggest(sc, 0, 0, 1, NoteDuration.QUARTER, limit=5)
    bar = eng.last_bar_fills()
    full = eng.last_full_fills()
    assert notes
    assert bar, 'expected one-bar fill'
    assert full, 'expected full-score fill'
    assert all(p.kind == 'phrase' for p in bar)
    assert all('本小节' in p.reason for p in bar)
    assert all('全曲' in p.reason for p in full)
    n_bar = max(len(p.preview_events) for p in bar)
    n_full = max(len(p.preview_events) for p in full)
    assert 2 <= n_bar <= 8, n_bar
    assert n_full > n_bar, (n_full, n_bar)


def test_more_prefix_changes_fill():
    sc = blank_score(bars=8, sharps=1)
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.G, 5), NoteDuration.HALF))
    eng = AssistEngine()
    a = eng.suggest(sc, 0, 0, 1, NoteDuration.HALF)
    fa = eng.last_phrases()
    assert fa and fa[0].kind == 'phrase'
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.D, 5), NoteDuration.HALF))
    b = eng.suggest(sc, 0, 1, 0, NoteDuration.HALF)
    fb = eng.last_phrases()
    assert fb and fb[0].kind == 'phrase'
    la = [str(e) for e in fa[0].preview_events[:8]]
    lb = [str(e) for e in fb[0].preview_events[:8]]
    assert la != lb


def test_ghost_index_switch():
    sc = blank_score(bars=4, sharps=0)
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.C, 5), NoteDuration.QUARTER))
    eng = AssistEngine()
    eng.suggest(sc, 0, 0, 1, NoteDuration.QUARTER)
    vs = eng.variants()
    assert len(vs) >= 2
    a0 = eng.active_variant()
    eng.set_ghost_index(1)
    a1 = eng.active_variant()
    assert a0 is not None and a1 is not None
    assert eng.ghost_index() == 1
    assert a0.reason != a1.reason or a0.label != a1.label


def test_assist_judge_heuristic():
    from music_score.assist_judge import heuristic_best, candidate_brief
    sc = blank_score(bars=4)
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.C, 5), NoteDuration.QUARTER))
    eng = AssistEngine()
    eng.suggest(sc, 0, 0, 1, NoteDuration.QUARTER)
    vs = eng.variants()
    assert vs
    i, reason = heuristic_best(vs)
    assert 0 <= i < len(vs)
    assert reason
    brief = candidate_brief(vs[0], 0)
    assert brief['id'] == 1
    assert brief['notes']


def test_edit_controller_delegates_to_assist():
    ctl = EditController()
    ctl.score = blank_score(bars=4)
    assert ctl.assist is not None
    cands = ctl.suggest(0, 0, 0, NoteDuration.QUARTER)
    assert cands
    assert any(
        '主音' in (c.reason + ' '.join(c.reasons))
        or '属音' in (c.reason + ' '.join(c.reasons))
        or '中音' in (c.reason + ' '.join(c.reasons))
        for c in cands)


def test_insert_phrase():
    ctl = EditController()
    ctl.score = blank_score(bars=4)
    events = [
        (Pitch(NoteName.C, 5), NoteDuration.QUARTER),
        (Pitch(NoteName.D, 5), NoteDuration.QUARTER),
        (Pitch(NoteName.E, 5), NoteDuration.QUARTER),
    ]
    n = ctl.try_insert_phrase(0, 0, 0, events)
    assert n == 3
    assert len(ctl.score.staves[0].measures[0].notes) == 3


def test_cmd_assist():
    from llm_compose import ComposeSession, apply_command
    s = ComposeSession()
    msg = apply_command(s, '/assist', 'inspire jazzy')
    assert 'jazzy' in msg
    assert s.prefs.get('assist_inspiration') == 'jazzy'
    msg2 = apply_command(s, '/assist', 'harmony 1=ii7 2=V7')
    assert '和声' in msg2 or 'ii7' in msg2
    assert s.prefs['assist_harmony'][1] == 'ii7'
    msg3 = apply_command(s, '/assist', 'off')
    assert 'OFF' in msg3
    assert s.prefs['assist_enabled'] is False


def test_g5_suggests_d5():
    """小夜曲开篇：G5 之后 D5 必须出现在 Top-5（分解和弦 / 主→属）。"""
    sc = blank_score(bars=8, sharps=1, meter='4/4')
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.G, 5), NoteDuration.HALF))
    eng = AssistEngine()
    eng.enabled = True
    eng.mode = 'major'
    eng.harmony = {1: 'I'}
    for insp in ('balanced', 'stepwise', 'arpeggio'):
        eng.inspiration = insp
        cands = eng.suggest(sc, 0, 0, 1, NoteDuration.HALF, limit=5)
        assert cands, insp
        hits = [
            c for c in cands
            if c.pitch.name == NoteName.D and c.pitch.octave == 5
            and c.pitch.accidental == Accidental.NATURAL
        ]
        assert hits, (
            f'{insp}: expected D5 in {[pitch_label(c.pitch) + " " + c.reason for c in cands]}'
        )
        # D5 应排在 D6 前面（近八度）
        labels = [pitch_label(c.pitch) for c in cands]
        if 'D6' in labels:
            assert labels.index('D5') < labels.index('D6'), labels
        assert labels[0] == 'D5', labels


def test_grammar_rules_drive_fill():
    """已学 grammar.yaml 驱动本小节 / 全曲铺满（默认 folk 4/4 → reel）。"""
    sc = blank_score(bars=8, sharps=0, meter='4/4')
    sc.staves[0].measures[0].notes.append(
        Note(Pitch(NoteName.C, 5), NoteDuration.QUARTER))
    eng = AssistEngine()
    eng.style_id = 'folk/british-folk-tune'
    eng.suggest(sc, 0, 0, 1, NoteDuration.QUARTER)
    assert eng._grammar is not None
    bar = eng.last_bar_fills()
    full = eng.last_full_fills()
    assert bar and full
    assert any('本小节' in p.reason for p in bar)
    assert any('全曲' in p.reason for p in full)
    assert all(p.kind == 'phrase' for p in bar)
    n_bar = max(len(p.preview_events) for p in bar)
    n_full = max(len(p.preview_events) for p in full)
    assert 2 <= n_bar <= 8, n_bar
    assert n_full > n_bar, (n_full, n_bar)


def test_inspiration_profiles_exist():
    assert set(INSPIRATION_PROFILES) >= {
        'balanced', 'stepwise', 'jazzy', 'angular', 'arpeggio'}
