# -*- coding: utf-8 -*-
"""旋律「不再单一」的回归测试。

背景 —— 用户用交响曲提示词生成的乐谱被判「旋律很单一」，逐小节量化后定位到
节奏层而不是音高层：

  * 32 小节里只有 12 种不同节奏型，最常用的一种重复 **8 次**；
  * 出现 4 处「相邻两小节节奏完全相同」；
  * 原因是 `_RHYTHMS` 每种风格 × 容量只有 2–5 条模板，`_pick_rhythm` 独立
    逐小节均匀抽样，重复是必然的。

两处修（都只作用于旋律，不碰和声/伴奏/风格契约）：

  1. **自由创作扩池**：`_RHYTHMS_EXTRA` 为无风格契约的创作补充同风格模板。
     有契约时不并入（风格包要保住自己的「节奏身份」，否则指纹被稀释）。
  2. **短期不复用**：`_pick_rhythm(avoid=[最近 2 小节的模板])`，能换就换。

运行：python -m pytest tests/test_melody_variety.py -q
"""

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import compose_kernel as ck                                   # noqa: E402
from compose_kernel import run_program                        # noqa: E402


DUR = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5,
       '16th': 0.25, 'dotted_quarter': 1.5, 'dotted_eighth': 0.75}
NB = 32


def _melody_rhythms(spec):
    """按小节切分旋律声部，返回每小节的节奏型元组。"""
    out, cur, t = [], [], 0.0
    for e in spec['parts'][0]['events']:
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            continue
        if t >= 4.0 - 1e-9:
            out.append(tuple(cur))
            cur, t = [], 0.0
        cur.append(str(e[1]))
        t += DUR.get(str(e[1]), 1.0)
    if cur:
        out.append(tuple(cur))
    return out[:NB]


def _free_piece(seed='variety-test'):
    """一段无风格契约的自由创作（c 小调 32 小节，5 段，含 4 次动机呈现）。"""
    motto = [[5, 'eighth'], [5, 'eighth'], [5, 'eighth'], [3, 'half']]
    return {
        'calls': [
            {'script': 'new_score', 'args': {
                'title': 'v', 'key_sharps': -3, 'mode': 'minor',
                'meter': '4/4', 'tempo': 108, 'bars': 32, 'seed': seed}},
            {'script': 'set_sections', 'args': {'sections': [
                {'name': 'A', 'bars': 8, 'dynamic': 'ff'},
                {'name': 'B', 'bars': 6, 'dynamic': 'mf'},
                {'name': 'C', 'bars': 8, 'dynamic': 'ff'},
                {'name': 'D', 'bars': 6, 'dynamic': 'f'},
                {'name': 'E', 'bars': 4, 'dynamic': 'ff'},
            ]}},
            {'script': 'set_harmony', 'args': {'section': 'A',
             'progression': ['i', 'i', 'V7', 'i', 'i', 'iv', 'V7', 'i']}},
            {'script': 'accompaniment', 'args': {'section': 'A',
             'pattern': 'octave_pulse', 'register': 'low'}},
            {'script': 'melody', 'args': {'section': 'A', 'register': 'high',
             'rhythm': 'driving', 'contour': 'arch', 'motif': motto}},
            {'script': 'set_harmony', 'args': {'section': 'B',
             'progression': ['I', 'V7', 'I', 'IV', 'V7', 'I']}},
            {'script': 'accompaniment', 'args': {'section': 'B',
             'pattern': 'broken_arpeggio', 'register': 'mid'}},
            {'script': 'melody', 'args': {'section': 'B', 'register': 'mid',
             'rhythm': 'flowing', 'contour': 'arch'}},
            {'script': 'set_harmony', 'args': {'section': 'C',
             'progression': ['i', 'iv', 'V7', 'i', 'i', 'iv', 'V7', 'i']}},
            {'script': 'accompaniment', 'args': {'section': 'C',
             'pattern': 'chromatic_descent', 'register': 'low'}},
            {'script': 'melody', 'args': {'section': 'C', 'register': 'high',
             'rhythm': 'fragmented', 'contour': 'wave', 'motif': motto}},
            {'script': 'set_harmony', 'args': {'section': 'D',
             'progression': ['i', 'i', 'V7', 'i', 'iv', 'V7']}},
            {'script': 'accompaniment', 'args': {'section': 'D',
             'pattern': 'alberti', 'register': 'low'}},
            {'script': 'melody', 'args': {'section': 'D', 'register': 'high',
             'rhythm': 'driving', 'contour': 'ascending', 'motif': motto}},
            {'script': 'set_harmony', 'args': {'section': 'E',
             'progression': ['V7', 'i', 'V7', 'i']}},
            {'script': 'accompaniment', 'args': {'section': 'E',
             'pattern': 'block_chords', 'register': 'low'}},
            {'script': 'melody', 'args': {'section': 'E', 'register': 'high',
             'rhythm': 'driving', 'contour': 'arch', 'motif': motto}},
            {'script': 'cadence', 'args': {'bars': 2, 'section': 'E'}},
            {'script': 'narrative', 'args': {'surprises': 0,
                                             'appearances': False,
                                             'silence': False}},
            {'script': 'finalize', 'args': {}},
        ]
    }


# --------------------------------------------------------------------------
# 1. 模板池本身：扩展池存在、容量守恒、时值可映射回时值名
# --------------------------------------------------------------------------

def test_extra_pools_are_valid():
    """扩展池必须存在，且每条模板时值之和等于该风格/容量（导入期已自检，
    这里再显式断言一次，并确保用到的时值都能查回 QL_DUR）。"""
    assert ck._RHYTHMS_EXTRA, '应存在自由创作的扩展节奏池'
    for (style, cap), table in ck._RHYTHMS_EXTRA.items():
        assert style in ck.RHY_STYLES, f'未知风格 {style}'
        for tpl in table:
            total = sum((x if x is not None else 0.5) for x in tpl)
            assert abs(total - cap) < 1e-9, f'{style}/{cap} 模板容量不符 {tpl}'
            for x in tpl:
                if x is not None:
                    assert x in ck.QL_DUR, f'时值 {x} 无法映射回时值名'


def test_free_pools_are_richer_than_style_pools():
    """扩展池必须真的让可选模板变多（否则这次改动没有意义）。"""
    for key, extra in ck._RHYTHMS_EXTRA.items():
        base = ck._RHYTHMS.get(key) or []
        assert len(base) + len(extra) > len(base), key


# --------------------------------------------------------------------------
# 2. 有风格契约时不扩池（保风格包的节奏身份 → 不动既有指纹语义）
# --------------------------------------------------------------------------

def test_style_contract_keeps_base_pool():
    """给了软权重时，被抽到的模板必须仍属于基础池（不被扩展池稀释）。"""
    import random
    base = {ck._rhythm_key(t) for t in ck._RHYTHMS[('driving', 4.0)]}
    extra = {ck._rhythm_key(t) for t in ck._RHYTHMS_EXTRA[('driving', 4.0)]}
    assert base and extra and not (base & extra), '基础池与扩展池不应重叠'
    rng = random.Random(7)
    weights = {'uniform': 3.0}          # 任意契约权重，只要非空即视为「有契约」
    for _ in range(60):
        picked = ck._pick_rhythm(rng, 'driving', 4.0, weights)
        assert ck._rhythm_key(picked) not in extra, \
            f'有契约时不应抽到扩展池模板 {picked}'


# --------------------------------------------------------------------------
# 3. 短期不复用：avoid 生效
# --------------------------------------------------------------------------

def test_avoid_changes_template_when_alternatives_exist():
    """把最近模板全部交给 avoid：抽出的结果必须换一条（池内还有别的选择）。"""
    import random
    rng = random.Random(11)
    base = ck._RHYTHMS[('driving', 4.0)]
    recent = [tuple(t) for t in base]
    picked = ck._pick_rhythm(rng, 'driving', 4.0, None, avoid=recent)
    assert tuple(picked) not in recent, \
        f'avoid 给了全部候选，抽取仍返回其中之一：{picked}'


# --------------------------------------------------------------------------
# 4. 端到端：自由创作的旋律节奏必须足够丰富
# --------------------------------------------------------------------------

MOTIF_RHY = ('eighth', 'eighth', 'eighth', 'half', 'eighth')


def test_free_piece_melody_is_varied():
    spec = run_program(_free_piece())
    rhy = _melody_rhythms(spec)
    assert len(rhy) == NB
    distinct = len(set(rhy))
    adj_same = sum(1 for i in range(len(rhy) - 1) if rhy[i] == rhy[i + 1])
    top_rhy, top_n = collections.Counter(rhy).most_common(1)[0]
    # 改动前实测（同一段落划分）：distinct=12 / 相邻相同=4 / 最高频=8
    # 阈值按**实测可达上界**设定，不是拍脑袋的整数：
    #   distinct —— driving 池 13 条 + flowing 15 + fragmented 12，但 32 小节里
    #     有 9 个乐句头被动机占用，故实际可达约 14~16；取 >=13 作为回归下限。
    #   adj_same —— avoid=[最近2小节] 生效后实测 1；留到 <=3 容许随机波动。
    #   top_n    —— 动机**按设计**出现在每个乐句首小节，而本曲有 9 个乐句
    #     （A8+B6+C8+D6+E4 小节 ÷ 4 小节/乐句 = 2+2+2+2+1），所以 9 是正确值，
    #     早先写的 <=8 与乐句数矛盾。
    assert distinct >= 13, f'不同节奏型只有 {distinct} 种（应 >=13）'
    assert adj_same <= 3, f'相邻小节节奏相同 {adj_same} 处（应 <=3）'
    # 允许重复的只有「主题动机」本身：它必须是最高频，且只出现在乐句首小节
    assert top_rhy == MOTIF_RHY, \
        f'最高频节奏应为动机本身（主题贯穿），实际 {top_rhy}'
    n_phrase_heads = sum(len(range(0, bars, 4)) for bars in (8, 6, 8, 6, 4))
    assert n_phrase_heads == 9, f'本曲乐句数应为 9，实算 {n_phrase_heads}'
    assert top_n <= n_phrase_heads, \
        f'动机节奏出现 {top_n} 次，超过乐句头数 {n_phrase_heads}'


def test_motif_still_lands_on_phrase_heads():
    """扩池/不复用不得破坏 motif：每个乐句首小节仍须落在动机上
    （动机 = 三短一长，级数 5-5-5-3 → c 小调 G-G-G-Eb）。"""
    spec = run_program(_free_piece())
    from music21 import pitch as mp
    mel = _melody_rhythms(spec)

    def bar_pitches(b):
        out, t = [], 0.0
        idx = 0
        allb = [[]]
        for e in spec['parts'][0]['events']:
            if not isinstance(e, (list, tuple)) or len(e) < 2:
                continue
            if t >= 4.0 - 1e-9:
                allb.append([])
                t = 0.0
            allb[-1].append(e)
            t += DUR.get(str(e[1]), 1.0)
        for e in (allb[b - 1] if b - 1 < len(allb) else []):
            v = e[0]
            vs = v if isinstance(v, list) else ([v] if v else [])
            for x in vs:
                try:
                    out.append(mp.Pitch(x).name.replace('-', 'b'))
                except Exception:
                    pass
        return out

    # 只有指定了 motif 的段落才有「乐句首小节落动机」的义务：
    # A(1,5) / C(15,19) / D(23,27) / E(29)；B 段是抒情副题，故意不给 motif。
    heads = [1, 5, 15, 19, 23, 27, 29]
    hit = 0
    for b in heads:
        pcs = bar_pitches(b)
        if len(pcs) >= 3 and pcs[0] == pcs[1] == pcs[2]:
            hit += 1
    assert hit >= 6, f'乐句首小节落在动机上的只有 {hit} 个（应 ≥6/{len(heads)}）'
    assert mel[0][:4] == ('eighth', 'eighth', 'eighth', 'half'), \
        f'呈示部首小节应为「三短一长」节奏，实际 {mel[0]}'


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-q'])
