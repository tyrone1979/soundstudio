"""
乐谱编辑内核测试：规则引擎 / 智能提示 / 编辑控制器 / model_to_m21 导出。

不依赖 Qt（editor_core 纯逻辑）；UI 冒烟单独用 offscreen 平台跑。
运行：python tests/test_score_editor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from music_score.models import (
    Score, Staff, Measure, Note, Pitch, ClefType, NoteName, Accidental,
    NoteDuration, TimeSignature, KeySignature,
)
from music_score.editor_core import (
    RuleEngine, SuggestionEngine, EditController, Verdict,
    scale_pcs, key_tonic, pitch_label, transposed_pitch,
    _scale_step_offset,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✅ {name}')
    else:
        FAIL += 1
        print(f'  ❌ {name}  {detail}')


def P(name: str, octave: int, acc: Accidental = Accidental.NATURAL) -> Pitch:
    return Pitch(NoteName[name], octave, acc)


def make_score(n_measures=2, staves=1, sharps=0, notes_per_measure=None):
    sc = Score(title='T', tempo=90, time_signature=TimeSignature(4, 4),
               key_signature=KeySignature(sharps))
    sc.staves = [Staff(clef=ClefType.TREBLE, name='V1') for _ in range(staves)]
    for st in sc.staves:
        for i in range(n_measures):
            m = Measure(number=i + 1)
            if notes_per_measure:
                for p, d in notes_per_measure:
                    m.notes.append(Note(P(*p) if isinstance(p, tuple) else p, d))
            st.measures.append(m)
    return sc


print('== 1. 音阶工具 ==')
check('C 大调音级集合', scale_pcs(0) == {0, 2, 4, 5, 7, 9, 11}, scale_pcs(0))
check('G 大调含 F#', 6 in scale_pcs(1) and 5 not in scale_pcs(1))
check('C 大调主音 C', key_tonic(0)[0] == NoteName.C)
check('Eb 大调主音 Eb', key_tonic(-3) == (NoteName.E, Accidental.FLAT),
      key_tonic(-3))
check('半音移调 C4→C#4', pitch_label(transposed_pitch(P('C', 4), 1)) == 'C#4')
check('调内级进 C4→D4', pitch_label(_scale_step_offset(P('C', 4), 1, 0)) == 'D4')
check('调内级进 B4→C5（跨八度）',
      pitch_label(_scale_step_offset(P('B', 4), 1, 0)) == 'C5',
      pitch_label(_scale_step_offset(P('B', 4), 1, 0)))

print('== 2. 规则引擎：放置校验 ==')
rules = RuleEngine(strict=True)
sc = make_score(notes_per_measure=[(('C', 4), NoteDuration.QUARTER),
                                   (('E', 4), NoteDuration.QUARTER),
                                   (('G', 4), NoteDuration.QUARTER)])
# 4/4 已有 3 个四分音符，剩余 1 拍
v = rules.check_placement(sc, 0, 0, P('A', 4), NoteDuration.QUARTER, 3)
check('剩余 1 拍放四分音符 → ok', v.level == 'ok', v.msg)
v = rules.check_placement(sc, 0, 0, P('A', 4), NoteDuration.HALF, 3)
check('剩余 1 拍放二分音符 → 硬禁止', v.level == 'error' and v.code == 'overflow',
      v)
v = rules.check_placement(sc, 0, 0, P('F', 9), NoteDuration.QUARTER, 3)
check('超出记谱范围 → 硬禁止', v.level == 'error' and v.code == 'range', v)
v = rules.check_placement(sc, 0, 0, P('F', 4, Accidental.SHARP),
                          NoteDuration.QUARTER, 3)
check('C 大调放 F# → 警告（不禁止）', v.level == 'warn' and v.code == 'chromatic', v)
v = rules.check_placement(sc, 0, 0, P('F', 1), NoteDuration.QUARTER, 3)
check('低到 3 条加线外 → 硬禁止（范围）', v.level == 'error', v)

print('== 3. 严格校验：平行五八度 ==')
rules = RuleEngine(strict=True)
sc2 = make_score(staves=2)
sc2.staves[0].measures[0].notes = [Note(P('C', 5), NoteDuration.HALF),
                                   Note(P('D', 5), NoteDuration.HALF)]
sc2.staves[1].measures[0].notes = [Note(P('F', 4), NoteDuration.HALF)]
# 声部2第二拍插入 G4：与声部1 D5 构成五度，与第一拍 C5/F4 平行
v = rules.check_placement(sc2, 1, 0, P('G', 4), NoteDuration.QUARTER, 1)
check('平行五度：严格模式禁止', v.level == 'strict' and v.code == 'parallel', v)
rules_loose = RuleEngine(strict=False)
v2 = rules_loose.check_placement(sc2, 1, 0, P('G', 4), NoteDuration.QUARTER, 1)
check('关闭严格 → 警告放行', v2.level == 'warn' and not v2.blocked, v2)
# 反向进行到五度 → 不算平行
v3 = rules.check_placement(sc2, 1, 0, P('B', 3), NoteDuration.QUARTER, 1)
check('非平行的五度到达 → ok', v3.level == 'ok', v3)
# 平行八度
sc3 = make_score(staves=2)
sc3.staves[0].measures[0].notes = [Note(P('C', 5), NoteDuration.HALF),
                                   Note(P('D', 5), NoteDuration.HALF)]
sc3.staves[1].measures[0].notes = [Note(P('C', 4), NoteDuration.HALF)]
v4 = rules.check_placement(sc3, 1, 0, P('D', 4), NoteDuration.HALF, 1)
check('平行八度禁止', v4.blocked and v4.code == 'parallel', v4)
# 同度保持（不动）不算平行
sc3b = make_score(staves=2)
sc3b.staves[0].measures[0].notes = [Note(P('C', 5), NoteDuration.HALF),
                                    Note(P('D', 5), NoteDuration.HALF)]
sc3b.staves[1].measures[0].notes = [Note(P('C', 5), NoteDuration.HALF)]
v5 = rules.check_placement(sc3b, 1, 0, P('C', 5), NoteDuration.HALF, 1)
check('同音保持 → ok', v5.level == 'ok', v5)

print('== 4. 超八度跳进 ==')
sc4 = make_score()
sc4.staves[0].measures[0].notes = [Note(P('C', 4), NoteDuration.QUARTER)]
v = rules.check_placement(sc4, 0, 0, P('D', 5), NoteDuration.QUARTER, 1)
check('上行 14 半音跳进：严格禁止', v.blocked and v.code == 'leap', v)
v = rules.check_placement(sc4, 0, 0, P('A', 4), NoteDuration.QUARTER, 1)
check('九度以内跳进 → ok', v.level == 'ok', v)

print('== 5. 智能提示 ==')
eng = SuggestionEngine(rules)
sc5 = make_score(n_measures=4)
# 前一小节结束在 B4（C 大调导音）
sc5.staves[0].measures[0].notes = [
    Note(P('E', 4), NoteDuration.QUARTER), Note(P('F', 4), NoteDuration.QUARTER),
    Note(P('G', 4), NoteDuration.QUARTER), Note(P('B', 4), NoteDuration.QUARTER),
]
cands = eng.suggest(sc5, 0, 1, 0, NoteDuration.QUARTER)
check('导音 B4 → 首选 C5 解决',
      cands and pitch_label(cands[0].pitch) == 'C5'
      and '解决' in cands[0].reason,
      [(c.label, c.reason) for c in cands[:3]])
check('候选含级进选项', any('级进' in c.reason for c in cands),
      [c.reason for c in cands])
check('候选 ≤ 5', len(cands) <= 5, len(cands))
# 时值钳制：小节只剩 1 拍时建议自适应为四分
sc6 = make_score()
sc6.staves[0].measures[0].notes = [
    Note(P('C', 4), NoteDuration.HALF), Note(P('E', 4), NoteDuration.QUARTER)]
cands = eng.suggest(sc6, 0, 0, 2, NoteDuration.HALF)
check('剩余 1 拍：建议时值自动钳到四分',
      all(c.duration == NoteDuration.QUARTER for c in cands),
      [(c.label, c.duration.name) for c in cands])
# 空谱：从主音起步
sc7 = make_score()
cands = eng.suggest(sc7, 0, 0, 0, NoteDuration.QUARTER)
check('无前音：首选主音起步',
      cands and '主音' in cands[0].reason, [(c.label, c.reason) for c in cands[:2]])
# 小节满 → 无候选
cands = eng.suggest(sc7, 0, 0, 4, NoteDuration.QUARTER)  # measure[0] 空, idx=4? 空 measure remaining=1
sc7.staves[0].measures[0].notes = [Note(P('C', 4), NoteDuration.WHOLE)]
cands = eng.suggest(sc7, 0, 0, 1, NoteDuration.QUARTER)
check('小节已满 → 无候选', cands == [], cands)

print('== 6. 编辑控制器：插入/撤销/重做/禁止 ==')
ctl = EditController()
statuses, verdicts, changes = [], [], []
ctl.score = make_score()
ctl.on_status = statuses.append
ctl.on_verdict = verdicts.append
ctl.on_change = lambda: changes.append(1)
ok = ctl.try_insert_note(0, 0, 0, P('C', 4), NoteDuration.QUARTER)
check('插入成功', ok and len(ctl.score.staves[0].measures[0].notes) == 1)
check('on_change 通知', len(changes) == 1)
check('on_violations 已挂（无违规）', ctl.lint() == [], ctl.lint())
ctl.undo()
check('撤销后清空', len(ctl.score.staves[0].measures[0].notes) == 0)
ctl.redo()
check('重做恢复', len(ctl.score.staves[0].measures[0].notes) == 1)
n0 = len(statuses)
ok = ctl.try_insert_note(0, 0, 1, P('G', 9), NoteDuration.QUARTER)
check('违规插入被拒且不改谱',
      not ok and len(ctl.score.staves[0].measures[0].notes) == 1)
check('被拒时状态栏出现 ✋', any('✋' in s for s in statuses[n0:]), statuses)
check('撤销栈不受被拒操作影响',
      ctl.undo() and len(ctl.score.staves[0].measures[0].notes) == 0)
ctl.redo()

print('== 7. 编辑操作：移调/删除/时值/演奏法/力度 ==')
ctl2 = EditController()
ctl2.score = make_score(notes_per_measure=[(('C', 4), NoteDuration.QUARTER),
                                          (('E', 4), NoteDuration.QUARTER),
                                          (('G', 4), NoteDuration.QUARTER),
                                          (('C', 5), NoteDuration.QUARTER)])
m0 = ctl2.score.staves[0].measures[0]
n = ctl2.transpose_steps_selected([(0, 0, 0)], 1)
check('↑ 一级 C4→D4', n == 1 and pitch_label(m0.notes[0].pitch) == 'D4',
      pitch_label(m0.notes[0].pitch))
n = ctl2.set_duration([(0, 0, 3)], NoteDuration.HALF)
check('末音改二分 → 溢出被拒', n == 0 and m0.notes[3].duration == NoteDuration.QUARTER)
n = ctl2.set_duration([(0, 0, 0), (0, 0, 1)], NoteDuration.EIGHTH)
check('批量改八分 ×2', n == 2 and all(x.duration == NoteDuration.EIGHTH
                                     for x in m0.notes[:2]))
n = ctl2.toggle_dots([(0, 0, 0)])
check('附点成功', n == 1 and m0.notes[0].dots == 1)
# 附点八分 0.1875 + 八分 0.125 + 四分×2 = 0.625，再加附点 → 0.25+... 校验溢出：
n = ctl2.toggle_dots([(0, 0, 0), (0, 0, 0)])
check('重复键去重：再加一点到 2', n == 1 and m0.notes[0].dots == 2,
      (n, m0.notes[0].dots))
n = ctl2.toggle_dots([(0, 0, 0)])
check('附点上限 2（第 3 次拒绝）', n == 0 and m0.notes[0].dots == 2, n)
n = ctl2.toggle_mark([(0, 0, 1)], 'staccato')
check('加跳音', n == 1 and 'staccato' in m0.notes[1].marks)
n = ctl2.toggle_mark([(0, 0, 1)], 'staccato')
check('再按取消跳音', n == 1 and 'staccato' not in m0.notes[1].marks)
ctl2.set_dynamics([0, 1], 'mf')
check('力度写入两小节', all(x.dynamic == 'mf'
                           for x in ctl2.score.staves[0].measures[:2]))
n = ctl2.delete_notes([(0, 0, 0), (0, 0, 1)])
check('批量删除 ×2', n == 2 and len(m0.notes) == 2)
ctl2.set_strict(False)
check('strict 切换生效', ctl2.strict is False and ctl2.rules.strict is False)

print('== 8. lint 全谱 ==')
sc8 = make_score(staves=2, n_measures=1)
sc8.staves[0].measures[0].notes = [Note(P('C', 5), NoteDuration.HALF),
                                   Note(P('D', 5), NoteDuration.HALF)]
sc8.staves[1].measures[0].notes = [Note(P('F', 4), NoteDuration.HALF),
                                   Note(P('G', 4), NoteDuration.HALF)]
viol = RuleEngine(strict=True).lint_score(sc8)
check('lint 抓到平行五度', any(v.code == 'parallel' for v in viol),
      [(v.code, v.msg) for v in viol])
check('lint 结果带定位', all(v.staff_idx in (0, 1) and v.measure_idx == 0
                            for v in viol))

print('== 9. model_to_m21 往返 ==')
from music_score.exporter import model_to_m21
src = Score(title='Round Trip', composer='Tester', tempo=96,
            time_signature=TimeSignature(3, 4), key_signature=KeySignature(2))
st = Staff(clef=ClefType.TREBLE, name='RH')
for i in range(2):
    m = Measure(number=i + 1)
    if i == 1:
        m.dynamic = 'mp'
        m.tempo_mark = '♩=96'
    m.notes = [Note(P('D', 5), NoteDuration.HALF),
               Note(P('E', 5, Accidental.SHARP), NoteDuration.QUARTER,
                    marks=['staccato'])]
    st.measures.append(m)
m_rest = Measure(number=3)
m_rest.notes = [Note(P('C', 4), NoteDuration.HALF, is_rest=True)]
st.measures.append(m_rest)
src.staves = [st]
sc9 = model_to_m21(src)
check('标题/作曲家', sc9.metadata.title == 'Round Trip'
      and sc9.metadata.composer == 'Tester')
check('速度 96', any(getattr(x, 'number', None) == 96
                    for x in sc9.recurse().getElementsByClass('MetronomeMark')))
p0 = sc9.parts[0]
measures = list(p0.getElementsByClass('Measure'))
check('3 小节', len(measures) == 3, len(measures))
from music21 import note as m21note, pitch as m21pitch
n1 = measures[0].notes[0]
check('音名/时值 D5 半音符(ql=2)', n1.pitch.nameWithOctave == 'D5'
      and n1.duration.quarterLength == 2.0,
      (n1.pitch.nameWithOctave, n1.duration.quarterLength))
n2 = measures[0].notes[1]
check('E#5 四分 + 跳音', n2.pitch.nameWithOctave == 'E#5'
      and n2.duration.quarterLength == 1.0
      and any(a.classes[0] == 'Staccato' for a in n2.articulations),
      (n2.pitch.nameWithOctave, [str(a) for a in n2.articulations]))
check('休止符', isinstance(measures[2].notesAndRests[0], m21note.Rest))
ks = list(p0.recurse().getElementsByClass('KeySignature'))
check('调号 2 个升号', ks and ks[0].sharps == 2)
ts = list(p0.recurse().getElementsByClass('TimeSignature'))
check('拍号 3/4', ts and ts[0].ratioString == '3/4', [t.ratioString for t in ts])

print(f'\n========== 汇总：{PASS} 通过 / {FAIL} 失败 ==========')
sys.exit(1 if FAIL else 0)
