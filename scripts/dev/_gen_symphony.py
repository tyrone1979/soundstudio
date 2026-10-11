"""生成一份「缩编交响乐」示例，验证系统能否承载交响编制（14 声部双管编制）。

不依赖远程 LLM：用确定性 program 走真实内核脚本链路，
并由 _ensure_instruments 的「交响」分支自动注入双管编制。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import compose_kernel as K  # noqa: E402
import llm_compose as L  # noqa: E402
import score_studio as SS  # noqa: E402

OUT = os.path.join('scores', 'chats')
os.makedirs(OUT, exist_ok=True)
CHAT = 'symphony_demo'

BRIEF = '写一首交响乐《黎明与抗争》，单乐章三段落：黎明（Andante）—抗争（Allegro）—黎明再现（Maestoso）'

program = {
    'calls': [
        {'script': 'new_score',
         'args': {'voices': 14, 'meter': '4/4', 'bars': 24, 'tempo': 88,
                  'title': '黎明与抗争', 'seed': 'symphony-demo-v1'}},
        {'script': 'set_sections', 'args': {'sections': [
            {'name': '黎明', 'bars': 8, 'meter': '4/4',
             'tempo_text': 'Andante', 'dynamic': 'p'},
            {'name': '抗争', 'bars': 8, 'meter': '4/4',
             'tempo_text': 'Allegro', 'dynamic': 'ff'},
            {'name': '黎明再现', 'bars': 8, 'meter': '4/4',
             'tempo_text': 'Maestoso', 'dynamic': 'f'},
        ]}},
        # 段落级调性：C 大调 → a 小调（关系小调，抗争）→ C 大调
        {'script': 'set_harmony',
         'args': {'section': '黎明', 'progression': ['I', 'vi', 'IV', 'V'],
                  'key_sharps': 0, 'mode': 'major'}},
        {'script': 'set_harmony',
         'args': {'section': '抗争', 'progression': ['i', 'VI', 'III', 'VII'],
                  'key_sharps': 0, 'mode': 'minor'}},
        {'script': 'set_harmony',
         'args': {'section': '黎明再现', 'progression': ['I', 'IV', 'V', 'I'],
                  'key_sharps': 0, 'mode': 'major'}},
        # 旋律：木管/弦乐最高声部承担
        {'script': 'melody', 'args': {'section': '黎明', 'register': 'high',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        {'script': 'melody', 'args': {'section': '抗争', 'register': 'very_high',
                                      'rhythm': 'driving', 'contour': 'ascending'}},
        {'script': 'melody', 'args': {'section': '黎明再现', 'register': 'high',
                                      'rhythm': 'flowing', 'contour': 'arch'}},
        # 伴奏：低音 + 内声部和弦填充
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

# 让「交响」分支自动注入双管编制（演示本地兜底，不依赖 LLM 翻译对不对）
plan = {'user_brief': BRIEF, 'summary': 'symphony'}
program, note = L._ensure_instruments(program, plan)
print('编制注入：', note)
ns = next(c for c in program['calls'] if c['script'] == 'new_score')
print('voices =', ns['args']['voices'], '| instruments =', ns['args']['instruments'])

kernel_trace = []
spec = K.run_program(program, trace=kernel_trace)
spec['title'] = '黎明与抗争'
spec['composer'] = 'composerchat'

m21 = L.spec_to_music21(spec)

# ---- 导出 MusicXML ----
xml_path = os.path.join(OUT, f'{CHAT}.xml')
m21.write('musicxml', fp=xml_path)
print('MusicXML →', xml_path)

# ---- 导出双报告（程序员版带简化 trace）----
trace = {
    'llm_calls': [
        {'role': 'user', 'content': BRIEF},
        {'role': 'assistant',
         'content': '（确定性生成，未调用远程 LLM；编制由本地「交响」分支注入）'},
    ],
    'kernel_calls': kernel_trace,
    'constraint_fixes': [],
    'emotion_applied': False,
    'repair': None,
    'result': {},
    'skill_ids': ['compose-full'],
}
model = SS.m21_to_model(m21, title=spec.get('title', ''), composer=spec.get('composer', ''))
rep_path = os.path.join(OUT, f'{CHAT}_report.md')
report, counts = SS.export_analysis_report(model, rep_path, spec=spec,
                                            notation_log=None, compose_trace=trace)
dev_path = os.path.splitext(rep_path)[0] + '_dev.md'
print('编辑者报告 →', rep_path)
print('程序员报告 →', dev_path)
print('检查计数：', counts)

# ---- 验证 ----
from music21 import converter
s = converter.parse(xml_path)
print('\n== 验证 ==')
print('声部数:', len(s.parts))
for p in s.parts:
    insts = list(p.recurse().getElementsByClass('Instrument'))
    nm = insts[0].instrumentName if insts else p.partName
    notes = len(list(p.recurse().notes))
    print(f'  {nm}: {notes} 音')
