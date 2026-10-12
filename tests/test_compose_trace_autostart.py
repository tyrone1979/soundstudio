# -*- coding: utf-8 -*-
"""直接调用 `compose_pipeline` 时，也必须留下「作曲过程透明化」痕迹。

新曲只走 `/compose-grammar`（可解释语法），不再经 Planner/LLM 写谱。
本文件验证：绕过 `compose_from_text` 直接调 `compose_pipeline` 时，
`session.compose_trace` 仍会自动建好，程序员版报告能展开第 4 节。

运行：python -m pytest tests/test_compose_trace_autostart.py -q
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import llm_compose as L                                       # noqa: E402
import score_studio as SS                                     # noqa: E402

PROMPT = '作一曲小步舞曲'


def test_direct_compose_pipeline_autostarts_trace():
    """不经 compose_from_text、直接调 compose_pipeline，痕迹必须完整可用。"""
    s = L.ComposeSession()
    score, spec, note = L.compose_pipeline(s, PROMPT)

    assert hasattr(s, 'compose_trace'), 'compose_pipeline 未建立 compose_trace'
    tr = s.compose_trace
    for key in ('user_text', 'llm_calls', 'kernel_calls', 'constraint_fixes',
                'pipeline', 'skill_ids', 'repair', 'generated_at'):
        assert key in tr, f'痕迹缺少键 {key}'
    assert tr['user_text'] == PROMPT
    assert 'learned_grammar' in str(tr.get('pipeline') or ''), tr.get('pipeline')
    assert tr.get('learned_grammar') or '识别为已学风格' in (note or '')
    assert spec.get('bars'), spec
    assert score is not None


def test_autostart_does_not_reset_existing_trace():
    """已有痕迹时不重置 —— 同一轮内多次调用累积同一条痕迹。"""
    s = L.ComposeSession()
    L._init_compose_trace(s, '第一次调用')
    s.compose_trace['kernel_calls'].append({'script': '__哨兵__', 'args': {}})

    L.compose_pipeline(s, PROMPT)

    assert s.compose_trace['user_text'] == '第一次调用', '已存在的痕迹被重置'
    assert any(c.get('script') == '__哨兵__' for c in s.compose_trace['kernel_calls']), \
        '已记录的条目被清空'


def test_dev_report_contains_section4():
    """痕迹到位后，程序员版报告必须出现第 4 节「作曲过程透明化」。"""
    s = L.ComposeSession()
    score, spec, _note = L.compose_pipeline(s, PROMPT)

    model = SS.m21_to_model(score, title=spec.get('title') or 'x', composer='test')
    tmp = tempfile.mkdtemp()
    rep = os.path.join(tmp, 'r.md')
    SS.export_analysis_report(model, rep, spec=spec, compose_trace=s.compose_trace)
    with open(os.path.splitext(rep)[0] + '_dev.md', encoding='utf-8') as f:
        dev = f.read()

    assert '## 4. 作曲过程透明化' in dev
    assert 'learned_grammar' in dev or '语法' in dev or 'compose-grammar' in dev.lower()


def test_no_trace_still_reports_cleanly():
    """反向对照：不传 compose_trace 时报告只有第 1–3 节（旧行为，未被改坏）。"""
    s = L.ComposeSession()
    score, spec, _note = L.compose_pipeline(s, PROMPT)

    model = SS.m21_to_model(score, title=spec.get('title') or 'x', composer='test')
    tmp = tempfile.mkdtemp()
    rep = os.path.join(tmp, 'r.md')
    SS.export_analysis_report(model, rep, spec=spec)
    with open(os.path.splitext(rep)[0] + '_dev.md', encoding='utf-8') as f:
        dev = f.read()

    assert '## 4. 作曲过程透明化' not in dev
    assert '无可用的 compose_trace' in dev
