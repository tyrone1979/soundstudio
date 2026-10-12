"""生成一份「brief 指定独奏乐器」的交响示例，验证旋律路由链路。

与 _gen_symphony.py 同一套确定性管线（不依赖远程 LLM），差别是 brief 点名了独奏
乐器，走一次真实的路由链路：

    brief 「…大提琴独奏…」
      → _ensure_instruments（交响分支注入双管编制）
      → _apply_solo_routing（识别独奏意图 → new_score.solo_instrument）
      → new_score 把该乐器提到声部 0
      → _s_melody / narrative / thicken / cadence 把旋律音区收进它的可奏音域

验证重点是：**把旋律交给中低音乐器后不能出现超音域的音**（大提琴上限 72，
而未收敛的 mid/high 旋律窗口上沿可达 81/94）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import compose_kernel as K  # noqa: E402
import llm_compose as L  # noqa: E402
import score_studio as SS  # noqa: E402
from notation import part_range  # noqa: E402

OUT = os.path.join('scores', 'chats')
os.makedirs(OUT, exist_ok=True)
CHAT = 'solo_cello_demo'

#: brief 点名独奏乐器（演示同时是回归用例：cello 音域上限仅 72，最容易暴露越界）
BRIEF = ('写一首交响乐《沉静的独白》，单乐章三段落：'
         '由大提琴独奏主题，管弦乐队伴奏，'
         '黎明（Andante）—抗争（Allegro）—黎明再现（Maestoso）')

program = {
    'calls': [
        {'script': 'new_score',
         'args': {'voices': 14, 'meter': '4/4', 'bars': 24, 'tempo': 88,
                  'title': '沉静的独白', 'seed': 'solo-cello-demo-v1'}},
        {'script': 'set_sections', 'args': {'sections': [
            {'name': '黎明', 'bars': 8, 'meter': '4/4',
             'tempo_text': 'Andante', 'dynamic': 'p'},
            {'name': '抗争', 'bars': 8, 'meter': '4/4',
             'tempo_text': 'Allegro', 'dynamic': 'ff'},
            {'name': '黎明再现', 'bars': 8, 'meter': '4/4',
             'tempo_text': 'Maestoso', 'dynamic': 'f'},
        ]}},
        {'script': 'set_harmony',
         'args': {'section': '黎明', 'progression': ['I', 'vi', 'IV', 'V'],
                  'key_sharps': 0, 'mode': 'major'}},
        {'script': 'set_harmony',
         'args': {'section': '抗争', 'progression': ['i', 'VI', 'III', 'VII'],
                  'key_sharps': 0, 'mode': 'minor'}},
        {'script': 'set_harmony',
         'args': {'section': '黎明再现', 'progression': ['I', 'IV', 'V', 'I'],
                  'key_sharps': 0, 'mode': 'major'}},
        # 旋律故意用偏高音区：未做音区收敛时，这里会把大提琴写到超音域
        {'script': 'melody', 'args': {'section': '黎明', 'register': 'high',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'melody', 'args': {'section': '抗争', 'register': 'very_high',
                                      'rhythm': 'driving', 'contour': 'ascending'}},
        {'script': 'melody', 'args': {'section': '黎明再现', 'register': 'high',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'accompaniment', 'args': {'section': '黎明', 'pattern': 'sustained',
                                             'register': 'low', 'density': 0.5}},
        {'script': 'accompaniment', 'args': {'section': '抗争', 'pattern': 'octave_pulse',
                                             'register': 'low', 'density': 2.0}},
        {'script': 'accompaniment', 'args': {'section': '黎明再现',
                                             'pattern': 'broken_arpeggio',
                                             'register': 'low', 'density': 1.0}},
        {'script': 'cadence', 'args': {'section': '黎明再现'}},
        {'script': 'finalize', 'args': {}},
    ]
}

plan = {'user_brief': BRIEF, 'summary': 'symphony'}
program, note_inst = L._ensure_instruments(program, plan)
program, note_solo = L._apply_solo_routing(program, plan)
print('编制注入：', note_inst)
print('旋律路由：', note_solo or '（未识别到独奏意图）')
ns = next(c for c in program['calls'] if c['script'] == 'new_score')
print('voices =', ns['args']['voices'])
print('solo_instrument =', ns['args'].get('solo_instrument'))

kernel_trace = []
spec = K.run_program(program, trace=kernel_trace)
spec['title'] = '沉静的独白'
spec['composer'] = 'composerchat'

# ---- 路由与音域核验 ----
p0 = spec['parts'][0]
print('\n== 核验 ==')
print('声部 0（旋律声部） =', p0['name'])
names = [p['name'] for p in spec['parts']]
print('编制次序 =', names)

rlo, rhi = part_range(p0['name'], p0['clef'])
print(f'{p0["name"]} 可奏音域 = [{rlo}, {rhi}]')

midis = []
for ev in p0['events'] or []:
    if not ev:
        continue
    nm = ev[0]
    if nm is None:
        continue
    for n in (nm if isinstance(nm, (list, tuple)) else [nm]):
        if n and not str(n).lower().startswith('r'):
            try:
                midis.append(K._midi(n))
            except Exception:
                pass
bad = sorted({m for m in midis if not (rlo <= m <= rhi)})
print(f'旋律音数 = {len(midis)}  实际范围 = [{min(midis)}, {max(midis)}]')
print('超音域音 =', bad if bad else '无 ✅')

m21 = L.spec_to_music21(spec)

# ---- 导出 MusicXML ----
xml_path = os.path.join(OUT, f'{CHAT}.xml')
m21.write('musicxml', fp=xml_path)
print('\nMusicXML →', xml_path)

# ---- 导出双报告 ----
trace = {
    'llm_calls': [
        {'role': 'user', 'content': BRIEF},
        {'role': 'assistant',
         'content': ('（确定性生成，未调用远程 LLM；编制由本地「交响」分支注入，'
                     '独奏路由由本地识别注入）')},
    ],
    'kernel_calls': kernel_trace,
    'constraint_fixes': [],
    'emotion_applied': False,
    'repair': None,
    'result': {},
    'skill_ids': ['compose-full'],
}
model = SS.m21_to_model(m21, title=spec.get('title', ''),
                        composer=spec.get('composer', ''))
rep_path = os.path.join(OUT, f'{CHAT}_report.md')
report, counts = SS.export_analysis_report(model, rep_path, spec=spec,
                                           notation_log=None, compose_trace=trace)
dev_path = os.path.splitext(rep_path)[0] + '_dev.md'
print('编辑者报告 →', rep_path)
print('程序员报告 →', dev_path)
print('检查计数：', counts)

# ---- 报告里的音域/可演奏性结论 ----
for line in (report or '').split('\n'):
    if ('音域' in line or '演奏' in line) and ('⚠️' in line or '❌' in line
                                              or '✅' in line):
        print('  报告·', line.strip()[:120])
