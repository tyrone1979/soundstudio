# -*- coding: utf-8 -*-
"""MusicXML → 纯中文文字描述 → MusicXML（莫扎特 K.525）。

主验收：无 JSON 的文字描述闭环；音符级一致。
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import compose_kernel as K
import score_reverse as R


@pytest.fixture(scope='module')
def mozart_spec():
    return R.load_mozart_nachtmusik_spec(short=True)


def test_mozart_fixture_shape(mozart_spec):
    spec = mozart_spec
    assert 'Nachtmusik' in spec['title'] or 'K.525' in spec['title']
    assert spec['bars'] >= 8
    assert len(spec['parts']) == 4
    assert int(spec['key_sharps']) == 1
    assert spec['mode'] == 'major'


def test_mozart_musicxml_to_text_to_musicxml(mozart_spec):
    """权威闭环：MusicXML/spec → 纯文字（无 JSON）→ 再生成 → 逐音符一致。"""
    src = mozart_spec
    text = R.build_score_description(src)
    assert '【声部' in text
    assert '第1小节：' in text
    assert '{' not in text and '[' not in text  # 无 JSON
    assert 'REGENERATION_PROGRAM' not in text
    assert 'write_part' not in text

    regen = R.text_to_spec(text)
    assert R.specs_note_equal(src, regen)


def test_mozart_reverse_score_description_field(mozart_spec):
    rev = R.reverse_score(mozart_spec)
    assert 'description' in rev
    assert rev['description'] == rev['prompt']
    assert '{' not in rev['description']
    regen = R.replay_description(rev['description'])
    assert R.specs_note_equal(mozart_spec, regen)


def test_mozart_path_writes_description_md(tmp_path):
    xml = R.mozart_nachtmusik_musicxml_path(short=True)
    out = tmp_path / 'rev'
    result = R.reverse_path(xml, out_dir=str(out))
    desc = out / 'score_description.md'
    assert desc.is_file()
    body = desc.read_text(encoding='utf-8')
    assert '【声部' in body
    assert '{' not in body
    # 不强制写 program.json
    assert not (out / 'regeneration_program.json').exists()

    src = R.musicxml_to_spec(xml)
    regen = R.text_to_spec(body)
    assert R.specs_note_equal(src, regen)

    rc = R.main([xml, '-o', str(tmp_path / 'cli'), '--replay'])
    assert rc == 0


def test_write_part_preserve_two_voice():
    prog = {
        'calls': [
            {'script': 'new_score', 'args': {
                'title': 'Mini', 'key_sharps': 0, 'mode': 'major',
                'meter': '4/4', 'tempo': 90, 'bars': 8, 'seed': 'mini-rev',
                'voices': 2,
            }},
            {'script': 'set_sections', 'args': {'sections': [
                {'name': 'A', 'bars': 8, 'dynamic': 'mf',
                 'tempo_text': 'Moderato', 'bpm': 90},
            ]}},
            {'script': 'set_harmony', 'args': {
                'section': 'A',
                'progression': ['I', 'V', 'vi', 'IV', 'I', 'ii', 'V7', 'I'],
            }},
            {'script': 'accompaniment', 'args': {
                'section': 'A', 'pattern': 'sustained',
                'register': 'low', 'density': 1.0,
            }},
            {'script': 'melody', 'args': {
                'section': 'A', 'register': 'high',
                'rhythm': 'sparse', 'contour': 'arch',
            }},
            {'script': 'narrative', 'args': {'appearances': False}},
            {'script': 'finalize', 'args': {}},
        ]
    }
    src = K.run_program(prog)
    text = R.build_score_description(src)
    assert '{' not in text
    assert R.specs_note_equal(src, R.text_to_spec(text))
