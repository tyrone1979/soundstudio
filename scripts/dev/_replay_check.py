# -*- coding: utf-8 -*-
"""确定性重放：按用户提示词的典型翻译器调用序列跑 kernel，检查
冲突段（第5小节起）旋律是否仍然震荡、以及速度过渡。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from compose_kernel import run_program

PROG = {'calls': [
    {'script': 'new_score', 'args': {'title': '黄昏的回忆', 'key_sharps': -5,
                                    'mode': 'major', 'meter': '4/4',
                                    'tempo': 76, 'bars': 24}},
    {'script': 'set_sections', 'args': {'sections': [
        {'name': '前奏', 'bars': 4, 'dynamic': 'p', 'tempo_text': 'Andante'},
        {'name': '冲突', 'bars': 8, 'dynamic': 'f', 'tempo_text': 'Agitato', 'bpm': 126},
        {'name': '高潮', 'bars': 6, 'dynamic': 'ff', 'tempo_text': 'Agitato'},
        {'name': '收束', 'bars': 6, 'dynamic': 'pp', 'tempo_text': 'Rit. e dim.', 'bpm': 60}]}},
    {'script': 'set_harmony', 'args': {'section': '前奏',
        'progression': ['I', 'V7', 'I', 'vi7']}},
    {'script': 'accompaniment', 'args': {'section': '前奏',
        'pattern': 'broken_arpeggio', 'register': 'low', 'density': 1}},
    {'script': 'melody', 'args': {'section': '前奏', 'register': 'mid',
        'rhythm': 'sparse', 'contour': 'arch',
        'motif': [[5, 'quarter'], [4, 'eighth'], [3, 'eighth'], [2, 'half']]}},
    {'script': 'set_harmony', 'args': {'section': '冲突',
        'progression': ['bII', 'vii°7', 'V7', 'bII', 'vii°7', 'iv', 'V7', 'vii°7']}},
    {'script': 'accompaniment', 'args': {'section': '冲突',
        'pattern': 'chromatic_descent', 'register': 'low', 'density': 2}},
    {'script': 'melody', 'args': {'section': '冲突', 'register': 'high',
        'rhythm': 'fragmented', 'contour': 'wave'}},
    {'script': 'set_harmony', 'args': {'section': '高潮',
        'progression': ['IV', 'V7', 'IV', 'V7', 'ii', 'V7']}},
    {'script': 'accompaniment', 'args': {'section': '高潮',
        'pattern': 'broken_arpeggio', 'register': 'low', 'density': 2}},
    {'script': 'melody', 'args': {'section': '高潮', 'register': 'high',
        'rhythm': 'driving', 'contour': 'arch'}},
    {'script': 'thicken', 'args': {'section': '高潮'}},
    {'script': 'set_harmony', 'args': {'section': '收束',
        'progression': ['IV', 'I', 'ii', 'V7', 'I', 'I']}},
    {'script': 'accompaniment', 'args': {'section': '收束',
        'pattern': 'broken_arpeggio', 'register': 'low', 'density': 0.5}},
    {'script': 'melody', 'args': {'section': '收束', 'register': 'mid',
        'rhythm': 'sparse', 'contour': 'descending'}},
    {'script': 'cadence', 'args': {'bars': 2}},
    {'script': 'finalize', 'args': {}},
]}

spec = run_program(PROG)
print('tempo_marks:', spec['tempo_marks'])
print('dynamics:', spec.get('dynamics'))
parts = spec['parts']
for pi, p in enumerate(parts):
    print(f"=== 声部{pi + 1} {p.get('name')}")
    # events 展开为逐小节
    by_bar = {}
    for ev in p['events']:
        bar = ev[3] if len(ev) > 3 else None
        by_bar.setdefault(bar, []).append(ev)
    for bar in sorted(by_bar, key=lambda x: (x is None, x)):
        evs = by_bar[bar]
        desc = []
        for ev in evs:
            pitches = ev[0] if isinstance(ev[0], list) else [ev[0]]
            if pitches[0] is None:
                desc.append(f'R{ev[1]}')
            else:
                desc.append('/'.join(pitches) + f':{ev[1]}')
        print(f'  m{bar}: ' + ' '.join(desc))
