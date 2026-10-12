# -*- coding: utf-8 -*-
"""MusicXML 往返导入 + 再校验（P2）测试：导入/再校验/时值完整性/报告/重新导出/ /import 命令端到端。

运行：python tests/test_roundtrip.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import musicxml_roundtrip as M  # noqa: E402
import llm_compose as L         # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f'  ✅ {name}')
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {detail}')


def _gen_score(seed='rt'):
    import style_skeleton
    spec = style_skeleton.skeleton_spec('fugue', seed=seed, fugue='full', tries=1)
    return L.spec_to_music21(spec), spec


# ============================================================
# 1. 导入往返：生成 → 写 XML → 导入 → 再校验
# ============================================================
def test_import_roundtrip():
    print('[1] 导入往返')
    score, spec = _gen_score('rt-1')
    tmp = os.path.join(tempfile.gettempdir(), 'rt_src.musicxml')
    score.write('musicxml', fp=tmp)
    check('源 XML 已写出', os.path.exists(tmp))
    sc2 = M.import_musicxml(tmp)
    check('导入后解析出声部', len(sc2.parts) > 0)
    check('导入后解析出音符', sum(1 for _ in sc2.recurse().notes) > 0)
    res = M.revalidate(sc2, title='rt')
    check('再校验返回 voice_leading', 'voice_leading' in res)
    check('再校验返回 measures', 'measures' in res)
    check('再校验返回 range', 'range' in res)


# ============================================================
# 2. 时值完整性检测（构造一个不满的小节）
# ============================================================
def test_measure_completeness():
    print('[2] 小节时值完整性')
    from music21 import stream, note, meter as m21meter
    s = stream.Score()
    p = stream.Part()
    m = stream.Measure(number=1)
    m.insert(0, m21meter.TimeSignature('4/4'))
    m.append(note.Note('C4', quarterLength=1))
    m.append(note.Note('E4', quarterLength=1))  # 仅 2 拍，缺 2 拍
    p.append(m)
    s.append(p)
    inc = M._measure_completeness(s)
    check('检测到 1 个不满小节', len(inc) == 1, str(inc))
    check('deficit=2.0', inc and abs(inc[0]['deficit'] - 2.0) < 1e-6, str(inc))
    # 完整谱不应误报
    full, _ = _gen_score('rt-2')
    check('完整谱无误报', len(M._measure_completeness(full)) == 0)


# ============================================================
# 3. 干净生成谱的声部进行应无 MAJOR 问题（内核保证）
# ============================================================
def test_voice_leading_clean():
    print('[3] 干净谱声部进行')
    full, _ = _gen_score('rt-3')
    res = M.revalidate(full, title='rt3')
    c = res['voice_leading']['counts']
    check('MAJOR 平行五八度 = 0', c.get('MAJOR', 0) == 0, str(c))
    check('CRITICAL = 0', c.get('CRITICAL', 0) == 0, str(c))


# ============================================================
# 4. 报告 + 重新导出规范化
# ============================================================
def test_report_and_export():
    print('[4] 报告 + 重新导出')
    full, _ = _gen_score('rt-4')
    res = M.revalidate(full, pack_id='fugue', title='rt4')
    rep = M.revalidate_report(res)
    check('报告含「再校验」', '再校验' in rep)
    check('报告含「外声部声部进行」', '声部进行' in rep)
    check('报告含「可审计」', '可审计' in rep)
    check('报告含风格重叠率', '风格重叠率' in rep)
    xml2 = os.path.join(tempfile.gettempdir(), 'rt_canon.musicxml')
    mid2 = os.path.join(tempfile.gettempdir(), 'rt_canon.mid')
    out = M.export_canonical(full, xml2, mid2)
    check('重新导出 XML', os.path.exists(out.get('xml')))
    check('重新导出 MIDI', os.path.exists(out.get('mid')))
    sc3 = M.import_musicxml(out['xml'])
    check('导出文件可再次导入', len(sc3.parts) > 0)


# ============================================================
# 5. /import 命令端到端
# ============================================================
def test_command_e2e():
    print('[5] /import 命令端到端')
    from llm_compose import ComposeSession, compose_from_text, CommandOnly
    score, spec = _gen_score('rt-cmd')
    xml = os.path.join(tempfile.gettempdir(), 'rt_cmd_src.musicxml')
    score.write('musicxml', fp=xml)
    s = ComposeSession()
    s.prefs['genre_id'] = 'fugue'  # 触发风格重叠率比对
    try:
        sc, sp, note = compose_from_text(s, f'/import {xml}')
    except CommandOnly as e:
        check('/import 命令未抛 CommandOnly', False, str(e))
        return
    check('命令返回 score', sc is not None)
    check('命令返回 spec', sp is not None)
    check('note 含「再校验」', '再校验' in note, note[:60])
    check('note 含「MusicXML」', 'MusicXML' in note)
    check('session.imported_score 已写入', s.imported_score is not None)
    check('session 锁定 genre_id=fugue', s.prefs.get('genre_id') == 'fugue')
    # 不存在的文件
    try:
        compose_from_text(ComposeSession(), '/import /no/such/file.xml')
        check('不存在文件抛 CommandOnly', False)
    except CommandOnly:
        check('不存在文件抛 CommandOnly', True)
    # 无参数用法提示
    r = L.apply_command(ComposeSession(), '/import', '')
    check('无参数返回用法提示', '用法' in r, r)


if __name__ == '__main__':
    test_import_roundtrip()
    test_measure_completeness()
    test_voice_leading_clean()
    test_report_and_export()
    test_command_e2e()
    print(f'\n结果：{_PASSED} 通过 / {_FAILED} 失败')
    sys.exit(1 if _FAILED else 0)
