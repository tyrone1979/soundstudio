# -*- coding: utf-8 -*-
"""测试：外部语料采集器 + 本轮由真实数据暴露出的三个解析缺陷。

三个缺陷都是「接进真语料才暴露」的，合成小样本测不出来：

  A. swing 检测把古典附点音型当成 swing：海顿第 99 交响曲算出 2.731，
     而 2.731 正是附点八分(0.75)+十六分(0.25) 的比值。
  B. 和声品质用 Chord.quality：它只回答三和弦层级，C7 与 Cmaj7 都报
     'major'，导致「七和弦占比」在真实乐谱上恒为 0，爵士与古典无从区分。
  C. 谱面已有的和弦符号被忽略：单声部爵士 lead sheet 走 chordify 只能得到
     同音伪和弦，等于学不到爵士的和声。

另外 music21 的 ChordSymbol('Bb7') 会静默解析成 B-D#-F#-A，所以 Weimar 的
和弦记法必须自己拆——用例 C 同时钉住这一点。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import score_learning.analyzer as A      # noqa: E402
import score_learning.sources as S       # noqa: E402
from music21 import chord, harmony, key, meter, note, stream  # noqa: E402

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


# --------------------------------------------------------------------------
# A. swing 检测
# --------------------------------------------------------------------------

def _part_of(pairs):
    """pairs = [(时值, 是否休止), ...] 顺序相接的一段单声部。"""
    p = stream.Part()
    p.insert(0, meter.TimeSignature('4/4'))
    off = 0.0
    for ql, is_rest in pairs:
        if is_rest:
            r = note.Rest()
            r.quarterLength = ql
            p.insert(off, r)
        else:
            n = note.Note('C4')
            n.quarterLength = ql
            p.insert(off, n)
        off += ql
    return p


def test_swignot_dotted_figures():
    print('\n=== A. 古典附点音型不得被判成 swing ===')
    # 附点八分(0.75) + 十六分(0.25)，古典里最常见的音型，重复 8 次
    pairs = [(0.75, False), (0.25, False)] * 8
    g = A._swing_and_triplet(_part_of(pairs))
    check('附点八分+十六分不产生 swing_ratio',
          g['swing_ratio'] is None, str(g))
    # 附点音型里根本不存在「成对的八分音符」，占比应是无证据(None)而非 0.0——
    # 「测出来是 0」与「没得可测」是两回事，不能混
    check('无八分音对时占比为无证据(None)，而非 0',
          g.get('swing_pair_share') is None, str(g))

    # 真正的 triple-feel 记谱：0.667 + 0.333
    pairs = [(2 / 3, False), (1 / 3, False)] * 8
    g = A._swing_and_triplet(_part_of(pairs))
    check('真正的长短八分被识别为 swing',
          g['swing_ratio'] is not None, str(g))
    check('swing 比值约 2.0',
          g['swing_ratio'] is not None and abs(g['swing_ratio'] - 2.0) < 0.15,
          str(g))
    check('不等分占比为 1.0', g.get('swing_pair_share') == 1.0, str(g))

    # 被休止隔开的「伪八分对」：旧实现会当成一对
    pairs = [(0.5, False), (0.5, True), (0.75, False), (0.25, False)]
    g = A._swing_and_triplet(_part_of(pairs))
    check('跨休止不算成对八分', g['swing_ratio'] is None, str(g))


# --------------------------------------------------------------------------
# B/C. 和声品质与谱面和弦符号
# --------------------------------------------------------------------------

def test_seventh_aware_quality():
    print('\n=== B. 和声品质必须区分七和弦 ===')
    cases = {
        'dominant7': (['C', 'E', 'G', 'B-'], 'dominant7'),
        'major7': (['C', 'E', 'G', 'B'], 'major7'),
        'minor7': (['C', 'E-', 'G', 'B-'], 'minor7'),
        'halfdim7': (['C', 'E-', 'G-', 'B-'], 'half_diminished7'),
        'dim7': (['C', 'E-', 'G-', 'A'], 'diminished7'),
        'major': (['C', 'E', 'G'], 'triad'),
        'minor': (['C', 'E-', 'G'], 'triad'),
        'augmented': (['C', 'E', 'G#'], 'augmented'),
    }
    for label, (pitches, expect) in cases.items():
        got = A._common_name_quality(chord.Chord(pitches))
        check(f'{label} → {expect}', got == expect, f'得到 {got}')

    # 用 .quality 这条路是走不通的（钉住「为什么必须换实现」）
    q_dom = chord.Chord(['C', 'E', 'G', 'B-']).quality
    q_maj = chord.Chord(['C', 'E', 'G', 'B']).quality
    check('Chord.quality 确实分不开 C7 与 Cmaj7（换实现的理由）',
          q_dom == q_maj, f'{q_dom} vs {q_maj}')


def test_written_symbols_preferred():
    print('\n=== C. 谱面已有和弦符号优先，且可读出七和弦指纹 ===')
    p = stream.Part()
    p.insert(0, meter.TimeSignature('4/4'))
    for i, (root, kind) in enumerate([
            ('C', 'minor-seventh'), ('F', 'dominant-seventh'),
            ('B-', 'major-seventh'), ('E', 'half-diminished-seventh')]):
        p.insert(float(i) * 4, harmony.ChordSymbol(root=root, kind=kind))
        n = note.Note('C4')
        n.quarterLength = 2.0
        p.insert(float(i) * 4, n)
    sc = stream.Score()
    sc.insert(0, p)

    h = A._harmony_from_written_symbols(sc, bars=8)
    check('识别到谱面符号来源',
          h is not None and h.get('source') == 'written_chord_symbols',
          str(h))
    check('七和弦占比为 1.0', h and h.get('seventh_ratio') == 1.0, str(h))
    check('品质分布含属七/大七/小七',
          h and {'dominant7', 'major7', 'minor7'} <= set(
              h.get('quality_hist') or {}), str(h))

    # 没有和弦符号时回退到 chordify，不得报错
    p2 = _part_of([(1.0, False)] * 8)
    sc2 = stream.Score()
    sc2.insert(0, p2)
    h2 = A._harmony_from_written_symbols(sc2, bars=4)
    check('无和弦符号时返回 None（交给 chordify 回退）', h2 is None, str(h2))
    h3 = A._harmony_from_chordify(sc2, bars=4)
    check('chordify 回退不抛异常', isinstance(h3, dict), str(h3))


# --------------------------------------------------------------------------
# 采集器
# --------------------------------------------------------------------------

def test_weimar_chord_parsing():
    print('\n=== Weimar 和弦记法解析（含 music21 的 Bb7 陷阱）===')
    cases = {
        'Bb7': ('B-', 'dominant-seventh', None),
        'Eb7': ('E-', 'dominant-seventh', None),
        'C-7': ('C', 'minor-seventh', None),
        'Am7b5': ('A', 'half-diminished-seventh', None),
        'Bbo7': ('B-', 'diminished-seventh', None),
        'Fj7': ('F', 'major-seventh', None),        # Weimar 用 j 表示大七
        'Bb6': ('B-', 'major-sixth', None),
        'D79b': ('D', 'dominant-seventh', None),
        'G7alt': ('G', 'dominant-seventh', None),
        'C': ('C', 'major', None),
        'C-': ('C', 'minor', None),
    }
    for src, expect in cases.items():
        got = S.parse_weimar_chord(src)
        check(f'{src} → {expect}', got == expect, f'得到 {got}')

    check('NC（无和弦）返回 None', S.parse_weimar_chord('NC') is None)
    check('空串返回 None', S.parse_weimar_chord('') is None)
    check('转位和弦带出低音', S.parse_weimar_chord('F/A') == ('F', 'major', 'A'),
          str(S.parse_weimar_chord('F/A')))

    # 钉住「为什么不能朴素喂给 music21」
    naive = harmony.ChordSymbol('Bb7')
    check('朴素 ChordSymbol("Bb7") 确实会解析错（换实现的理由）',
          naive.root().name.replace('-', 'b') != 'B-'
          or naive.chordKind != 'dominant-seventh',
          f'root={naive.root().name} kind={naive.chordKind}')


def test_harte_chord_parsing():
    print('\n=== CoCoPops harte 记法解析 ===')
    for src, expect in {
        'E:min': ('E', 'minor'),
        'A:maj6': ('A', 'major-sixth'),
        'A:min7': ('A', 'minor-seventh'),
        'B:7': ('B', 'dominant-seventh'),
        'C:maj': ('C', 'major'),
    }.items():
        got = S.parse_harte_chord(src)
        check(f'{src} → {expect}', got == expect, f'得到 {got}')
    check('延续记号 "." 返回 None', S.parse_harte_chord('.') is None)


def test_abc_splitting():
    print('\n=== 多曲 ABC 拆分 ===')
    text = (
        'X: 1\nT:First Tune\nM:6/8\nK:D\nDEF|GAB|A3||\n'
        'X: 2\nT:Second Tune\nM:4/4\nK:G\nGABc|dedc|d4||\n'
    )
    tunes = S.split_abc_collection(text, 'jigs')
    check('拆出 2 首', len(tunes) == 2, str(len(tunes)))
    check('第一首标题进文件名', 'First_Tune' in tunes[0][0], tunes[0][0])
    check('每首都保留自己的 X: 头', all('X:' in b for _, b in tunes))

    # 单个集合里没有曲子的情况不应炸
    check('空输入不炸', S.split_abc_collection('', 'x') == [])


def test_kern_sanitize():
    print('\n=== Kern 清洗（music21 的 OCO 崩溃）===')
    src = '!!!OTL: Test\n!!!OCO: commissioned by someone\n**kern\n4c\n'
    out = S.sanitize_kern_text(src)
    check('OCO 被降为普通注释', '!!!OCO:' not in out, out)
    check('其余全局引用保留', '!!!OTL: Test' in out, out)


def test_kern_token_and_source_registry():
    print('\n=== kern token 解析与源注册表 ===')
    check('8g → 0.5 个四分音符',
          S._kern_token_to_note('8g') == ('G4', 0.5),
          str(S._kern_token_to_note('8g')))
    check('4r → 休止 1 拍',
          S._kern_token_to_note('4r') == (None, 1.0),
          str(S._kern_token_to_note('4r')))
    check('附点四分 → 1.5 拍',
          abs((S._kern_token_to_note('4.g') or (None, 0))[1] - 1.5) < 1e-6,
          str(S._kern_token_to_note('4.g')))
    check('延续记号 "." 不占位', S._kern_token_to_note('.') is None)

    ids = set(S.SOURCES)
    for need in ('nottingham', 'weimar-jazz', 'haydn-symphony',
                 'classical-kern', 'cocopops'):
        check(f'语料源已登记：{need}', need in ids, str(sorted(ids)))
    check('每个源都声明了许可',
          all(s.license.spdx and s.license.url for s in S.SOURCES.values()))
    check('Weimar 是开放许可，不需确认',
          not S.SOURCES['weimar-jazz'].license.ack_required)
    check('CCARH 系需要确认条款',
          S.SOURCES['haydn-symphony'].license.ack_required)

    # 未确认条款的源不得下载
    try:
        S.fetch_source('haydn-symphony', accept_license=False)
        check('未确认许可时拒绝下载', False, '竟然成功了')
    except PermissionError:
        check('未确认许可时拒绝下载', True)
    except Exception as e:
        check('未确认许可时拒绝下载', isinstance(e, PermissionError), repr(e))


def test_corpus_sources_all():
    print('\n=== 外部语料采集器 ===')
    test_swignot_dotted_figures()
    test_seventh_aware_quality()
    test_written_symbols_preferred()
    test_weimar_chord_parsing()
    test_harte_chord_parsing()
    test_abc_splitting()
    test_kern_sanitize()
    test_kern_token_and_source_registry()
    print(f'\n总计：✅ {_PASSED} / ❌ {_FAILED}')
    assert _FAILED == 0, f'{_FAILED} 项失败'
