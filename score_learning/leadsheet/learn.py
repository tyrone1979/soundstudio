# -*- coding: utf-8 -*-
"""分层计数学习 + 剪枝蒸馏 → 规则脚本（dict，可直接写成 YAML）。

每个子风格（吉格 / 里尔 / 比波普 …）单独学一套，四个层级：

    曲式层  8 小节为一段，段与段的字母曲式（AB / AABA …）、段落是否反复、弱起
    段落层  终止公式（段末两个和弦）、半终止落点、每小节的和声节奏、4 小节乐句轮廓
    小节层  和弦转移：条件 = 前两个和弦 + 段内第几小节第几拍，Witten-Bell 回退
            节奏模板（R 规则）与"这一小节照抄前面第几小节的节奏/旋律"的概率
    音符层  强/次强/弱位置上和弦音、调内经过音、半音的比例；非和弦音的进入与解决；
            音程分布、大跳后的回补、禁用跳进、音域、首音与末音

学到的是"规律"而非曲子：没有任何一条规则记录具体音符序列。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from score_learning.learn_core import (BackoffModel, CTX_MIN, _contour_name, _dist,
                                       _resample, is_heldout, kmeans, perplexity as _ppl,
                                       prune_with_regression)
from score_learning.grammar.theory import QUALITIES, parse_chord_key, roman

from .analyze import STYLES, meter_info, strength

MIN_VOCAB = 5
AUTO_VOCAB_SHARE = 0.01  # 自动识别和声：占全部和弦 ≥1% 才进词表（识别噪声都在长尾）
AUTO_FLOOR = 0.25        # 自动识别和声：转移概率与整体频率插值的比重
MIN_PIECES = 12          # 子风格 / 调式至少这么多首训练曲才学
MIN_LONG_PIECES = 4      # 或者：至少这么多首、合计 MIN_BARS 小节（古典乐章长、首数少）
MIN_BARS = 600
UNIT = 8                 # 8 小节一段
RHYTHM_TOP = 60
# 复杂度 → 学更多规则：声部多 / 配器族多 / 乐句多 → 放宽剪枝、提高容量
_TEXTURE_COMPLEXITY = {
    'melody_piano': 1.0, 'piano': 1.2, 'quartet': 1.6, 'orchestra': 2.2,
}
TEMPO = {'jig': 168, 'reel': 200, 'hornpipe': 150, 'waltz': 150, 'swing': 168, 'bebop': 220,
         'cool': 140, 'hardbop': 190, 'postbop': 180, 'traditional': 160, 'pop': 108,
         'madrigal': 72, 'allegro': 132, 'minuet': 126, 'duple': 104, 'compound': 96,
         'mazurka': 132, 'prelude': 84}
SCALE = {'major': (0, 2, 4, 5, 7, 9, 11), 'minor': (0, 2, 3, 5, 7, 8, 9, 10, 11)}
_ROLE = ('R', '3', '5', '7')


def _complexity_budget(tr: List[Dict[str, Any]], texture: str) -> Dict[str, Any]:
    """按声部数 / 配器族 / 乐句数决定本子风格要学多少参数。

    更复杂的织体（交响、四重奏）与更长的乐句结构 → 更低剪枝门槛、更多节奏模板与轮廓类。
    """
    n_parts = [int(p.get('n_parts') or 1) for p in tr]
    n_phr = [int(p.get('n_phrases') or max(1, (len(p.get('bars') or []) + UNIT - 1) // UNIT))
             for p in tr]
    fam_n = []
    for p in tr:
        fam = p.get('families') or {}
        fam_n.append(len(fam) if isinstance(fam, dict) else 1)
    parts_med = float(np.median(n_parts)) if n_parts else 1.0
    phr_med = float(np.median(n_phr)) if n_phr else 2.0
    fam_med = float(np.median(fam_n)) if fam_n else 1.0
    tex = float(_TEXTURE_COMPLEXITY.get(texture, 1.0))
    # 基准：钢琴旋律≈1；交响≈2.2 × 声部/乐句加成
    scale = tex * (1.0 + 0.08 * max(0.0, parts_med - 1)
                   + 0.06 * max(0.0, fam_med - 1)
                   + 0.04 * max(0.0, phr_med - 2))
    scale = max(1.0, min(3.5, scale))
    ctx = max(2, int(round(CTX_MIN / (1.0 + 0.35 * (scale - 1.0)))))
    rhythm_top = int(round(RHYTHM_TOP * scale))
    contour_k = max(4, min(16, int(round(6 * scale))))
    form_top = max(12, int(round(12 * scale)))
    cell_cap = max(1500, int(round(1500 * scale)))
    openings = [2, 3, 4] + ([5, 6] if scale >= 1.8 else []) + ([7, 8] if scale >= 2.4 else [])
    return {
        'scale': round(scale, 3),
        'ctx_min': ctx,
        'rhythm_top': rhythm_top,
        'contour_k': contour_k,
        'form_top': form_top,
        'cell_cap': cell_cap,
        'openings': openings,
        'n_parts_median': round(parts_med, 2),
        'n_families_median': round(fam_med, 2),
        'n_phrases_median': round(phr_med, 2),
        'pieces': len(tr),
    }


# ---------------------------------------------------------------- 工具
def tok_chord(tok: str) -> Tuple[int, str]:
    r, q = tok.split(':')
    return int(r), q


def tok_pcs(tok: str) -> List[int]:
    r, q = tok_chord(tok)
    return [(r + i) % 12 for i in QUALITIES[q]]


def tok_roman(tok: str, mode: str) -> str:
    r, q = tok_chord(tok)
    return roman((r, q, 0), mode)


_DIATONIC = {
    'major': {'0:M', '2:m', '4:m', '5:M', '7:M', '9:m', '11:d', '7:7', '11:o7'},
    'minor': {'0:m', '2:d', '3:M', '5:m', '7:M', '7:m', '8:M', '10:M', '11:d', '7:7', '11:o7'},
}
# 借用和弦（同主音大小调互借）与那不勒斯和弦
_MIXTURE = {'major': {'5:m', '8:M', '3:M', '10:M', '0:m', '1:M'},
            'minor': {'0:M', '5:M', '1:M'}}


def functional_chord(tok: str, mode: str) -> bool:
    """古典和声里有功能的和弦：调内、副属（属 / 属七 / 导七）、借用、那不勒斯。
    自动识别的和弦用它过滤：其余多是残缺和弦被误认（如大调里的 vii 小三和弦）。"""
    if tok in _DIATONIC[mode] or tok in _MIXTURE[mode]:
        return True
    r, q = tok_chord(tok)
    targets = {int(t.split(':')[0]) for t in _DIATONIC[mode] if not t.endswith(('d', 'o7'))}
    if q in ('M', '7'):
        return (r - 7) % 12 in targets
    if q in ('d', 'o7'):
        return (r + 1) % 12 in targets
    return False


def note_class(rel: int, tok: Optional[str], mode: str) -> str:
    """CT 和弦音 / ST 调内非和弦音 / CH 半音（调外）。"""
    if tok is not None and rel in tok_pcs(tok):
        return 'CT'
    return 'ST' if rel in SCALE[mode] else 'CH'


def pos_label(i: int, final: bool, beat: int) -> str:
    """和声位置：段首 0 / 半句末 3 / 段末 7 / 全曲末 F / 其他 o；小节中途换和弦加 m。"""
    k = i % UNIT
    base = 'F' if final else str(k) if k in (0, 3, 7) else 'o'
    return base if beat == 0 else base + 'm'


def rhythm_class(i: int, final: bool) -> str:
    if final:
        return 'final'
    return 'cad' if i % UNIT in (3, 7) else 'mid'


def bar_pattern(bar) -> str:
    return ' '.join(f'{on:g}{"r" if m is None else "n"}' for on, _d, _r, m in bar['notes'])


def bar_cells(bar, bar_ql: float, beat_ql: float) -> List[str]:
    """小节 → 每拍一个节奏单元（拍内相对起拍 + n/r），本拍没有新起音记作 '-'。"""
    beats = int(round(bar_ql / beat_ql))
    out = [[] for _ in range(beats)]
    for on, _d, _r, m in bar['notes']:
        k = min(beats - 1, int(on / beat_ql + 1e-6))
        out[k].append(f'{round(on - k * beat_ql, 3):g}{"r" if m is None else "n"}')
    return [' '.join(c) if c else '-' for c in out]


def chord_at(bar, on: float, prev: Optional[str]) -> Optional[str]:
    cur = prev
    for o, t in bar['chords']:
        if o <= on + 1e-6:
            cur = t
    return cur


def last_chord_idx(p) -> int:
    return max((i for i, b in enumerate(p['bars']) if b['chords']), default=-1)


def harmony_events(p, vocab: Optional[set] = None):
    out = []
    p2, p1 = 'BOS', 'BOS'
    last = last_chord_idx(p)
    for i, b in enumerate(p['bars'][:last + 1]):
        if not b['chords']:
            p2, p1 = 'BOS', 'BOS'
            continue
        for on, tok in b['chords']:
            if vocab is not None and tok not in vocab:
                p2, p1 = 'BOS', 'BOS'
                continue
            pos = pos_label(i, i == last and not p.get('open_end'), int(round(on / p['beat_ql'])))
            out.append((p2, p1, pos, tok))
            p2, p1 = p1, tok
    return out


def model_from_rules(levels: Dict[str, List[Dict]], vocab: List[str], floor: float = 0.0) -> BackoffModel:
    """规则脚本 → 回退模型（作曲器与评测共用；脚本里的计数就是模型参数）。"""
    m = BackoffModel(vocab, floor)
    for lv, rules in levels.items():
        for r in rules:
            w = r['when']
            h = {'L3': (w.get('p2'), w.get('p1'), w.get('pos')), 'L2': (w.get('p1'), w.get('pos')),
                 'L1': (w.get('p1'),), 'L0': (w.get('pos'),)}[lv]
            for c, _p, n in r['then']:
                m.t[lv][h][c] += n
    return m


# ---------------------------------------------------------------- 主学习
def enough(pieces: List[Dict[str, Any]]) -> bool:
    return len(pieces) >= MIN_PIECES or (
        len(pieces) >= MIN_LONG_PIECES and sum(len(p['bars']) for p in pieces) >= MIN_BARS)


def learn(pieces: List[Dict[str, Any]], style: str) -> Dict[str, Any]:
    info = STYLES[style]
    train_all = [p for p in pieces if not is_heldout(p['id'])]
    held_all = [p for p in pieces if is_heldout(p['id'])]
    script: Dict[str, Any] = {
        'grammar': style, 'name': info['name'], 'source': info['source'],
        'melody_instrument': info['melody'],
        'meta': {'pieces': len(pieces), 'train': len(train_all), 'heldout': len(held_all)},
        'subs': {},
    }
    for sub, (meter, tr, ho) in sub_splits(pieces, style).items():
        learned = _learn_sub(tr, ho, sub, info['subs'][sub], meter,
                             info.get('texture', 'melody_piano'), bool(info.get('auto_harmony')))
        if learned['vocab']:
            script['subs'][sub] = learned
    return script


def sub_splits(pieces: List[Dict[str, Any]], style: str) -> Dict[str, Tuple[str, List, List]]:
    """子风格 → (主拍号, 训练曲, 留出曲)；样本不足的子风格不出现。"""
    out = {}
    for sub in STYLES[style]['subs']:
        sp = [p for p in pieces if p['sub'] == sub]
        if not sp:
            continue
        meter = Counter(p['meter'] for p in sp).most_common(1)[0][0]
        sp = [p for p in sp if p['meter'] == meter]
        tr = [p for p in sp if not is_heldout(p['id'])]
        ho = [p for p in sp if is_heldout(p['id'])]
        if enough(tr):
            out[sub] = (meter, tr, ho)
    return out


def relearn_memory(script: Dict[str, Any], pieces: List[Dict[str, Any]], style: str) -> Dict[str, Any]:
    """只重算已有规则脚本里的跨段动机回忆统计（其余规则与编号不动）。"""
    for sub, (_m, tr, _h) in sub_splits(pieces, style).items():
        if sub in script['subs']:
            script['subs'][sub]['rhythm']['motif_recall'] = _learn_motif_recall(tr)
    return script


def _learn_sub(tr, ho, sub, sub_zh, meter, texture='melody_piano', auto=False) -> Dict[str, Any]:
    bar_ql, beat_ql, beats = meter_info(meter)
    floor = AUTO_FLOOR if auto else 0.0
    budget = _complexity_budget(tr, texture)
    ctx_min = int(budget['ctx_min'])
    seq = {'H': 0, 'C': 0, 'R': 0, 'M': 0}

    def new_id(prefix):
        seq[prefix] += 1
        return f'{prefix}{seq[prefix]:03d}'

    modes = Counter(p['mode'] for p in tr)
    out: Dict[str, Any] = {
        'name': sub_zh, 'texture': texture, 'meter': meter, 'bar_ql': bar_ql, 'beat_ql': beat_ql, 'beats': beats,
        'tempo': TEMPO.get(sub, 120), 'harmony_floor': floor,
        'pieces': {'train': len(tr), 'heldout': len(ho)},
        'modes': {m: n for m, n in modes.items()},
        'complexity': budget,
        'vocab': {}, 'harmony': {}, 'cadences': {}, 'first_chord': {}, 'evaluation': {},
    }
    # 配器 / 乐句分布（复杂织体才有意义）
    fam_tot: Counter = Counter()
    for p in tr:
        for k, v in (p.get('families') or {}).items():
            fam_tot[k] += int(v)
    if fam_tot:
        out['instrumentation'] = {
            'n_parts_median': budget['n_parts_median'],
            'n_families_median': budget['n_families_median'],
            'family_counts': dict(fam_tot.most_common()),
        }
    out['phrases'] = {
        'n_phrases_median': budget['n_phrases_median'],
        'unit_bars': UNIT,
    }
    for mode in ('major', 'minor'):
        mt = [p for p in tr if p['mode'] == mode]
        mh = [p for p in ho if p['mode'] == mode]
        if not enough(mt):
            continue
        cnt = Counter(t for p in mt for b in p['bars'] for _o, t in b['chords'])
        least = max(MIN_VOCAB, AUTO_VOCAB_SHARE * sum(cnt.values())) if auto else MIN_VOCAB
        # 复杂织体：略放宽词表门槛，学到更多和弦色彩
        if budget['scale'] >= 1.8 and auto:
            least = max(MIN_VOCAB, least * 0.7)
        vocab = sorted(t for t, n in cnt.items()
                       if n >= least and (not auto or functional_chord(t, mode)))
        vset = set(vocab)
        out['vocab'][mode] = {t: tok_roman(t, mode) for t in vocab}
        model = BackoffModel(vocab, floor)
        for p in mt:
            for e in harmony_events(p, vset):
                model.add(*e)
        held_ev = [e for p in mh for e in harmony_events(p, vset)]
        # 共享核心：只删样本不足的整条上下文；留出困惑度变差则自动回退
        prune_info = prune_with_regression(model, held_ev, levels=('L3', 'L2'),
                                           ctx_min=ctx_min)
        levels = {}
        for lv in ('L0', 'L1', 'L2', 'L3'):
            rules = []
            for h, c in sorted(model.t[lv].items(), key=lambda kv: -sum(kv[1].values())):
                n = sum(c.values())
                when = dict(zip({'L3': ('p2', 'p1', 'pos'), 'L2': ('p1', 'pos'),
                                 'L1': ('p1',), 'L0': ('pos',)}[lv], h))
                then = [[t, round(k / n, 4), k] for t, k in c.most_common()]
                rid = new_id('H')
                rules.append({'id': rid, 'when': when, 'then': then, 'n': n,
                              'say': _say_rule(when, then, mode)})
            levels[lv] = rules
        out['harmony'][mode] = levels
        # 评测：脚本模型 vs 去掉位置 vs 只用频率
        if held_ev:
            m_script = model_from_rules(levels, vocab, floor)
            m_nopos = BackoffModel(vocab, floor)
            m_uni = BackoffModel(vocab)
            for p in mt:
                for p2, p1, _pos, c in harmony_events(p, vset):
                    m_nopos.add('*', p1, '*', c)
                    m_uni.add('*', '*', '*', c)
            cover_tok = sum(1 for p in mh for b in p['bars'] for _o, t in b['chords']
                            if t in vset)
            cover_den = max(1, sum(len(b['chords']) for p in mh for b in p['bars']))
            # bigram_coverage：与 chorale 同口径（转移对是否在训练 L1 中 attested）
            bigram_cov = sum(
                1 for _a, p1, _pos, c in held_ev
                if p1 == 'BOS' or m_script.attested(p1, c)
            ) / max(len(held_ev), 1)
            out['evaluation'][mode] = {
                'vocab_size': len(vocab), 'heldout_events': len(held_ev),
                'perplexity_script': _ppl(m_script, held_ev),
                'perplexity_unpruned': prune_info['perplexity_unpruned'],
                'perplexity_no_position': _ppl(m_nopos, [(a, b, '*', c) for a, b, _x, c in held_ev]),
                'perplexity_unigram': _ppl(m_uni, [('*', '*', '*', c) for *_x, c in held_ev]),
                'coverage': round(cover_tok / cover_den, 3),
                'bigram_coverage': round(bigram_cov, 4),
                'prune_kept': prune_info['pruned'],
            }
        firsts = []
        for p in mt:
            ev = harmony_events(p, vset)
            if ev:
                firsts.append(ev[0][3])
        out['first_chord'][mode] = _dist(Counter(firsts), top=6)
        out['cadences'][mode] = _learn_cadences(mt, vset, mode, new_id)
    out['harmonic_rhythm'] = _learn_harmonic_rhythm(tr, beat_ql)
    out['form'] = _learn_form(tr, openings=budget['openings'], form_top=budget['form_top'])
    out['rhythm'] = _learn_rhythm(tr, bar_ql, beat_ql, new_id,
                                  rhythm_top=budget['rhythm_top'],
                                  ctx_min=ctx_min, cell_cap=budget['cell_cap'])
    out['rhythm']['motif_recall'] = _learn_motif_recall(tr)
    out['melody'] = _learn_melody(tr, bar_ql, beat_ql, new_id, contour_k=budget['contour_k'])
    out['hard_rules'] = _hard_rules(out)
    # 把可执行阈值挂到规则条目上：checker 读 param 而非自己写死字面量，
    # 这样「只改 learn.py」才真的能改变行为（见 hard_rule_params 文档）。
    _params = hard_rule_params(out)
    for r in out['hard_rules']:
        if r['id'] in _params:
            r['param'] = _params[r['id']]
    return out


def _say_rule(when, then, mode) -> str:
    def nm(t):
        return t if t in ('BOS', None) else tok_roman(t, mode)
    head = []
    if 'p2' in when:
        head.append(f'{nm(when["p2"])} → {nm(when["p1"])}')
    elif 'p1' in when:
        head.append(nm(when['p1']))
    if 'pos' in when:
        head.append(_pos_zh(when['pos']))
    return ' + '.join(head) + ' → ' + '，'.join(f'{nm(c)} {p * 100:.0f}%' for c, p, _n in then[:4])


_POS_BASE_ZH = {'0': '段首小节', '3': '半句末小节', '7': '段末小节', 'F': '全曲末小节',
                'o': '段中小节'}


def _pos_zh(pos: str) -> str:
    mid = pos.endswith('m')
    return _POS_BASE_ZH.get(pos.rstrip('m'), pos) + ('中途' if mid else '拍首')


def _learn_cadences(mt, vset, mode, new_id):
    final, mid, half = Counter(), Counter(), Counter()
    for p in mt:
        ev = harmony_events(p, vset)
        if len(ev) < 2:
            continue
        last = last_chord_idx(p)
        # 逐小节最后一个和弦的事件下标
        idx, k = {}, 0
        for i, b in enumerate(p['bars'][:last + 1]):
            for _on, t in b['chords']:
                if t in vset:
                    idx[i] = k
                    k += 1
        for i, k in idx.items():
            if k < 1 or k >= len(ev):
                continue
            c_last = ev[k][3]
            j = k - 1
            while j > 0 and ev[j][3] == c_last:
                j -= 1
            pair = (ev[j][3], c_last)
            if i == last and not p.get('open_end'):
                final[pair] += 1
            elif i % UNIT == 7:
                mid[pair] += 1
            elif i % UNIT == 3:
                half[c_last] += 1

    def rules(counter, kind):
        tot = sum(counter.values()) or 1
        out = []
        for (a, b), n in counter.most_common(10):
            if n < 2:
                continue
            out.append({'id': new_id('C'), 'kind': kind, 'chords': [a, b], 'p': round(n / tot, 4),
                        'n': n, 'say': f'{tok_roman(a, mode)} → {tok_roman(b, mode)}'})
        return out
    return {'final': rules(final, 'final'), 'mid': rules(mid, 'mid'),
            'half': _dist(half, top=6)}


def _learn_harmonic_rhythm(tr, beat_ql):
    by_pos = defaultdict(Counter)
    for p in tr:
        last = last_chord_idx(p)
        for i, b in enumerate(p['bars'][:last + 1]):
            if not b['chords'] or b['short']:
                continue
            pat = ','.join(str(int(round(on / beat_ql))) for on, _t in b['chords'])
            by_pos['F' if i == last else str(i % UNIT)][pat] += 1
    return {k: _dist(v, top=6) for k, v in sorted(by_pos.items())}


def _letters_from(bars) -> str:
    units = [bars[k:k + UNIT] for k in range(0, len(bars), UNIT)]
    if units and len(units[-1]) < UNIT // 2:
        units = units[:-1]
    sigs = [[(b['chords'][0][1] if b['chords'] else '-') for b in u] for u in units]
    letters, seen = [], []
    for s in sigs:
        hit = None
        for k, t in enumerate(seen):
            n = min(len(s), len(t))
            if n and sum(a == b for a, b in zip(s, t)) / n >= 0.75:
                hit = k
                break
        if hit is None:
            seen.append(s)
            hit = len(seen) - 1
        letters.append('ABCDEFGH'[min(hit, 7)])
    return ''.join(letters)


def _unit_grid(p) -> Tuple[str, int]:
    """(段落字母曲式, 首段起点小节)。前奏会让 8 小节网格错位，所以在 0–7 小节的起点偏移里
    取重复最多的一种。"""
    last = last_chord_idx(p)
    bars = p['bars'][:last + 1]
    first = next((i for i, b in enumerate(bars) if b['chords']), 0)
    best, start = None, first
    for off in range(UNIT):
        s = _letters_from(bars[first + off:])
        if s and (best is None or len(set(s)) / len(s) < len(set(best)) / len(best) - 1e-9):
            best, start = s, first + off
        if p['style'] == 'folk':
            break
    return best or '', start


def _unit_letters(p) -> str:
    return _unit_grid(p)[0]


def _learn_motif_recall(tr) -> Dict[str, Any]:
    """跨段动机回忆：新段落（非首段、非整段再现）里，某小节的节奏与音高照搬前面段落某一小节
    （原样 = 音高相同；模进 = 音程相同、整体移位）的比例。只统计段内第 1–7 小节、至少两个音的小节。"""
    n = same = shift = 0
    for p in tr:
        letters, start = _unit_grid(p)
        if not letters:
            continue
        bars = p['bars'][start:start + len(letters) * UNIT]
        pats = [bar_pattern(b) if not b['short'] and b['notes'] else None for b in bars]
        mel = [[m for *_x, m in b['notes'] if m is not None] for b in bars]
        seen = set()
        for k, L in enumerate(letters):
            new = L not in seen
            seen.add(L)
            if k == 0 or not new:
                continue
            for i in range(k * UNIT, min(k * UNIT + UNIT - 1, len(bars))):
                if pats[i] is None or len(mel[i]) < 2:
                    continue
                n += 1
                far = [j for j in range(k * UNIT) if pats[j] == pats[i]
                       and len(mel[j]) == len(mel[i])]
                if any(mel[j] == mel[i] for j in far):
                    same += 1
                elif any(np.all(np.diff(mel[j]) == np.diff(mel[i])) for j in far):
                    shift += 1
    return {'p_same': round(same / max(n, 1), 4), 'p_shift': round(shift / max(n, 1), 4),
            'bars': n}


def _relabel(s: str) -> str:
    m: Dict[str, str] = {}
    return ''.join(m.setdefault(c, 'ABCDEFGH'[len(m)]) for c in s)


def _learn_form(tr, openings=None, form_top=12):
    forms = Counter(_unit_letters(p) for p in tr)
    units = Counter(len(f) for f in forms.elements())
    # 长曲（流行歌常有 8 段以上）：作曲只写前几段时用"前 n 段的字母曲式"分布
    openings = list(openings or (2, 3, 4))
    opening_dist = {}
    for n in openings:
        # 恰好 n 段的曲子也计入「前 n 段」分布（用 >=，勿用严格 >）
        c = Counter(_relabel(f[:n]) for f in forms.elements() if len(f) >= n)
        if sum(c.values()) >= 10:
            opening_dist[n] = _dist(c, top=6)
    # 同一个和弦连续占满几小节（95% 分位）——防止一个和弦拖太久
    runs = []
    for p in tr:
        last = last_chord_idx(p)
        run, prev = 0, None
        for b in p['bars'][:last + 1]:
            if not b['chords']:
                continue
            toks = {t for _o, t in b['chords']}
            if len(toks) == 1 and prev in toks:
                run += 1
            else:
                if run:
                    runs.append(run + 1)
                run = 0
            prev = b['chords'][-1][1]
        if run:
            runs.append(run + 1)
    max_run = int(np.percentile(runs, 95)) if runs else 4
    rep_units = tot_units = 0
    for p in tr:
        last = last_chord_idx(p)
        for i, b in enumerate(p['bars'][:last + 1]):
            if i % UNIT == 7 or i == last:
                tot_units += 1
                rep_units += bool(b.get('rep'))
    return {'templates': _dist(forms, top=int(form_top)), 'units': _dist(units, top=8),
            'openings': opening_dist, 'max_same_chord_bars': max(2, max_run),
            'section_repeat_prob': round(rep_units / max(tot_units, 1), 3)}


def _learn_rhythm(tr, bar_ql, beat_ql, new_id, rhythm_top=None, ctx_min=None, cell_cap=None):
    rhythm_top = int(rhythm_top or RHYTHM_TOP)
    ctx_min = int(ctx_min if ctx_min is not None else CTX_MIN)
    cell_cap = int(cell_cap or 1500)
    by_cls = defaultdict(Counter)
    rep = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    mcopy = defaultdict(lambda: [0, 0, 0])        # lag → [节奏相同次数, 音程相同, 音高相同]
    pick_ql, pick_pat = Counter(), Counter()
    n_pick = 0
    for p in tr:
        last = last_chord_idx(p)
        bars = p['bars'][:last + 1]
        pats = [bar_pattern(b) if not b['short'] and b['notes'] else None for b in bars]
        for i, b in enumerate(bars):
            if pats[i] is None:
                continue
            by_cls[rhythm_class(i, i == last and not p.get('open_end'))][pats[i]] += 1
            pos = i % UNIT
            for lag in (1, 2, 4):
                j = i - lag
                if pos - lag < 0 or pats[j] is None:
                    continue
                r = rep[pos][lag]
                r[1] += 1
                if pats[i] == pats[j]:
                    r[0] += 1
                    a = [m for *_x, m in b['notes'] if m is not None]
                    c = [m for *_x, m in bars[j]['notes'] if m is not None]
                    mc = mcopy[lag]
                    mc[0] += 1
                    if len(a) == len(c) and len(a) > 1 and \
                            np.all(np.diff(a) == np.diff(c)):
                        mc[1] += 1
                    if a == c:
                        mc[2] += 1
        if p['pickup']:
            n_pick += 1
            pick_ql[p['pickup']['ql']] += 1
            base = bar_ql - p['pickup']['ql']
            pick_pat[(p['pickup']['ql'], ' '.join(
                f'{round(on - base, 3) if on >= base - 1e-6 else on:g}{"r" if m is None else "n"}'
                for on, _d, _r, m in p['pickup']['notes']))] += 1
    cells_beat, cells_ctx = defaultdict(Counter), defaultdict(Counter)
    beats = int(round(bar_ql / beat_ql))
    for p in tr:
        last = last_chord_idx(p)
        prev = 'BOS'
        for i, b in enumerate(p['bars'][:last + 1]):
            if b['short']:
                prev = 'BOS'
                continue
            for k, cell in enumerate(bar_cells(b, bar_ql, beat_ql)):
                key = f'F{k}' if i == last else str(k)
                cells_beat[key][cell] += 1
                cells_ctx[(prev, key)][cell] += 1
                prev = cell
    templates = {}
    for cls, cnt in by_cls.items():
        tot = sum(cnt.values())
        items = []
        for pat, n in cnt.most_common(rhythm_top):
            if n < 2:
                break
            items.append({'id': new_id('R'), 'pattern': pat, 'p': round(n / tot, 4), 'n': n,
                          'say': _say_rhythm(pat, bar_ql)})
        templates[cls] = {'coverage': round(sum(t['n'] for t in items) / max(tot, 1), 3),
                          'bars': tot, 'items': items}
    cov_mid = templates.get('mid', {}).get('coverage', 0.0)
    cell_rules = []
    for (prev, key), cnt in sorted(cells_ctx.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(cnt.values())
        if n < ctx_min * 2:
            continue
        cell_rules.append({'prev': prev, 'beat': key, 'n': n,
                           'then': [[c, round(k / n, 4), k] for c, k in cnt.most_common(12) if k >= 2]})
    return {
        # 小节模板覆盖不了（即兴独奏几乎每小节都不同）时，改用按拍的节奏单元马尔可夫链
        'mode': 'bar' if cov_mid >= 0.6 else 'cell',
        'templates': templates,
        'cells': {'by_beat': {k: _dist(v, top=16) for k, v in sorted(cells_beat.items())},
                  'by_prev': cell_rules[:cell_cap]},
        'repeat': {str(pos): {str(lag): round(a / b, 3) for lag, (a, b) in d.items() if b >= 10}
                   for pos, d in sorted(rep.items())},
        'melodic_copy': {str(lag): {'interval_same': round(v[1] / max(v[0], 1), 3),
                                    'pitch_same': round(v[2] / max(v[0], 1), 3), 'n': v[0]}
                         for lag, v in sorted(mcopy.items())},
        'pickup': {'prob': round(n_pick / max(len(tr), 1), 3), 'ql': _dist(pick_ql, top=4),
                   'patterns': [[ql, pat, p_, n] for (ql, pat), p_, n in _dist(pick_pat, top=8)]},
    }


_DUR_ZH = {4.0: '全音符', 3.0: '附点二分', 2.0: '二分', 1.5: '附点四分', 1.0: '四分',
           0.75: '附点八分', 0.5: '八分', 0.25: '十六分'}


def _say_rhythm(pat: str, bar_ql: float) -> str:
    toks = pat.split()
    ons = [float(t[:-1]) for t in toks]
    out = []
    for k, t in enumerate(toks):
        d = (ons[k + 1] if k + 1 < len(ons) else bar_ql) - ons[k]
        out.append(('休止' if t.endswith('r') else '') + _DUR_ZH.get(round(d, 3), f'{d:g}拍'))
    return ' '.join(out)


def _learn_melody(tr, bar_ql, beat_ql, new_id, contour_k=6):
    cls_by = defaultdict(Counter)
    nct_in = [0, 0]
    nct_out = [0, 0]
    nct_either = [0, 0]
    app = [0, 0]                         # 强拍非和弦音：下一音级进解决
    ivs = Counter()
    leaps = [0, 0]
    midis = []
    first, final = Counter(), Counter()
    roles = Counter()
    contours = []
    spans = []
    contour_k = max(4, int(contour_k or 6))
    for p in tr:
        mode = p['mode']
        last = last_chord_idx(p)
        prev_tok = None
        seq = []                          # [(midi, rel, cls, strength, bar_i)]
        for i, b in enumerate(p['bars'][:last + 1]):
            for on, _d, rel, m in b['notes']:
                tok = chord_at(b, on, prev_tok)
                if m is None:
                    seq.append(None)
                    continue
                st = strength(on, bar_ql, beat_ql)
                c = note_class(rel, tok, mode)
                seq.append((m, rel, c, st, i))
                cls_by[st][c] += 1
                if st == 'S' and abs(on) < 1e-6 and tok is not None:
                    r, q = tok_chord(tok)
                    iv = (rel - r) % 12
                    ivals = QUALITIES[q]
                    roles[_ROLE[ivals.index(iv)] if iv in ivals else 'N'] += 1
            if b['chords']:
                prev_tok = b['chords'][-1][1]
        notes = [x for x in seq if x is not None]
        if len(notes) < 8:
            continue
        midis += [x[0] for x in notes]
        spans.append(max(x[0] for x in notes) - min(x[0] for x in notes))
        first[notes[0][1]] += 1
        if not p.get('open_end'):
            final[notes[-1][1]] += 1
        for k in range(1, len(seq)):
            a, b_ = seq[k - 1], seq[k]
            if a is None or b_ is None:
                continue
            iv = b_[0] - a[0]
            if abs(iv) > 12:
                # 超过八度多是乐句 / 声部切换（流行语料约 10%），不当作旋律音程
                continue
            ivs[iv] += 1
            if b_[2] != 'CT':
                nct_in[1] += 1
                nct_in[0] += abs(iv) <= 2
                nxt = seq[k + 1] if k + 1 < len(seq) else None
                if nxt is not None:
                    out_step = abs(nxt[0] - b_[0]) <= 2
                    nct_out[1] += 1
                    nct_out[0] += out_step
                    nct_either[1] += 1
                    nct_either[0] += abs(iv) <= 2 or out_step
                    if b_[3] == 'S':
                        app[1] += 1
                        app[0] += out_step
            if abs(iv) >= 5 and k + 1 < len(seq) and seq[k + 1] is not None:
                nxt_iv = seq[k + 1][0] - b_[0]
                leaps[1] += 1
                leaps[0] += (nxt_iv * iv < 0) or abs(nxt_iv) <= 2
        # 4 小节乐句轮廓
        by_phrase = defaultdict(list)
        for x in notes:
            by_phrase[x[4] // 4].append(x[0])
        for vals in by_phrase.values():
            if len(vals) >= 6:
                v = np.array(_resample([float(x) for x in vals]))
                contours.append(v - v.mean())
    tot_iv = sum(ivs.values()) or 1
    abs_iv = Counter()
    for iv, n in ivs.items():
        abs_iv[abs(iv)] += n
    # 罕见跳进（< 0.3%）作为禁用跳进；最大跳进取 99.5% 分位
    # forbidden 用开区间上界，避免 R03 允许的 max_leap 同时被 R04 禁用
    srt = sorted((abs(iv), n) for iv, n in ivs.items())
    acc, max_leap = 0, 12
    for a, n in srt:
        acc += n
        if acc / tot_iv >= 0.995:
            max_leap = max(5, min(12, a))
            break
    forbidden = sorted(a for a in range(5, max_leap) if abs_iv.get(a, 0) / tot_iv < 0.003)
    C, lab = kmeans(np.array(contours), contour_k) if len(contours) >= max(30, contour_k * 5) else (
        np.zeros((1, 8)), np.zeros(1, int))
    templates = []
    for k in range(len(C)):
        n = int((lab == k).sum())
        if n == 0:
            continue
        templates.append({'id': new_id('M'), 'shape': [round(float(x), 2) for x in C[k]],
                          'p': round(n / len(lab), 4), 'n': n, 'name': _contour_name(C[k])})
    templates.sort(key=lambda t: -t['p'])
    arr = np.array(midis) if midis else np.array([60, 72])
    return {
        'class_by_strength': {st: _dist(c) for st, c in sorted(cls_by.items())},
        'nct_step_in': round(nct_in[0] / max(nct_in[1], 1), 3),
        'nct_step_out': round(nct_out[0] / max(nct_out[1], 1), 3),
        'nct_step_either': round(nct_either[0] / max(nct_either[1], 1), 3),
        'appoggiatura_resolve': round(app[0] / max(app[1], 1), 3),
        'intervals': [[iv, round(n / tot_iv, 4), n] for iv, n in sorted(ivs.items())],
        'leap_recovery_prob': round(leaps[0] / max(leaps[1], 1), 3),
        'max_leap': int(max_leap), 'forbidden_leaps': forbidden,
        'range': {'low': int(np.percentile(arr, 1)), 'high': int(np.percentile(arr, 99)),
                  'mean': round(float(arr.mean()), 1), 'span_median': int(np.median(spans or [12]))},
        'first_degree': _dist(first, top=5), 'final_degree': _dist(final, top=5),
        'downbeat_role': _dist(roles), 'contour_templates': templates,
    }


_R08_TEXT = {
    'melody_piano': '旋律、钢琴右手、钢琴左手两两之间不得同向进入平行五度 / 八度；'
                    '右手和弦不高过旋律，低音不高过旋律',
    'piano': '钢琴右手（旋律）与左手伴奏之间不得同向进入平行五度 / 八度（左手和弦按高→低拆成'
             '声部逐条检查，同手内只查五度）；左手不高过旋律，低音不高过旋律',
    'quartet': '第一小提琴、第二小提琴、中提琴、大提琴两两之间不得同向进入平行五度 / 八度；'
               '内声部不高过第一小提琴，大提琴不高过旋律',
    'orchestra': '四个弦乐声部与圆号两两之间不得同向进入平行五度 / 八度（长笛 / 双簧管加倍旋律、'
                 '大管加倍大提琴属于声部加倍，定音鼓是打击乐，不计）；内声部不高过第一小提琴，'
                 '大提琴不高过旋律',
}


def _hard_rules(sub) -> List[Dict[str, str]]:
    mel = sub['melody']
    cs = dict((k, p) for k, p, _n in mel['class_by_strength'].get('S', []))
    rules = []
    if cs.get('CT', 0) >= 0.7:
        rules.append({'id': 'R01', 'rule': f'强拍音必须是和弦音，或者是下一音级进解决的倚音'
                                          f'（语料强拍和弦音 {cs.get("CT", 0) * 100:.0f}%）'})
    else:
        rules.append({'id': 'R01', 'rule': f'强拍音必须是和弦音或调内音（延伸音），半音外音必须级进解决'
                                          f'（语料强拍和弦音 {cs.get("CT", 0) * 100:.0f}%，'
                                          f'调内非和弦音 {cs.get("ST", 0) * 100:.0f}%）'})
    if mel['nct_step_either'] >= 0.85:
        rules.append({'id': 'R02', 'rule': f'非和弦音必须级进进入或级进离开（经过音、邻音、倚音、'
                                          f'换音；语料 {mel["nct_step_either"] * 100:.0f}%）'})
    else:
        rules.append({'id': 'R02', 'rule': f'半音外音必须级进进入或级进离开；调内非和弦音可以跳进'
                                          f'（语料非和弦音级进比例只有 {mel["nct_step_either"] * 100:.0f}%）'})
    rules.append({'id': 'R03', 'rule': f'旋律跳进不超过 {mel["max_leap"]} 个半音（语料 99.5% 分位）'})
    if mel['forbidden_leaps']:
        rules.append({'id': 'R04', 'rule': '不用罕见跳进（语料中低于 0.3%）：' + '、'.join(
            f'{a} 半音' for a in mel['forbidden_leaps'])})
    rules.append({'id': 'R05', 'rule': f'旋律音域 MIDI {mel["range"]["low"]}–{mel["range"]["high"]}'
                                      f'（语料 1%–99% 分位）'})
    rules.append({'id': 'R06', 'rule': '全曲结束于主和弦，旋律末音为主音'})
    rules.append({'id': 'R07', 'rule': '和弦只用词表内的和弦，和弦转移必须在语料中出现过'})
    rules.append({'id': 'R08', 'rule': _R08_TEXT.get(sub.get('texture'), _R08_TEXT['melody_piano'])})
    rules.append({'id': 'R10', 'rule': f'同一个和弦连续不超过 {sub["form"]["max_same_chord_bars"]} 小节'
                                      f'（语料 95% 分位）'})
    if sub['rhythm']['mode'] == 'bar':
        rules.append({'id': 'R09', 'rule': '每小节的节奏取自学到的节奏模板（R 规则）'})
    else:
        rules.append({'id': 'R09', 'rule': '每拍的节奏单元取自学到的节奏单元转移表（即兴语料'
                                          '整小节模板覆盖率低，按拍学）'})
    return rules


#: R01/R02 由「宽松」切成「严格」的语料阈值。**单一事实来源**：
#: `check_piece` 从 YAML 的 ``hard_rules[*].param`` 读取它们（见
#: ``leadsheet/check.py``），不要在任何 Python 里再写一份字面量。
#: 出处见论文 Table 2：0.70 能把 18 个包（CT 0.83–0.93）与 7 个包（0.48–0.62）分开；
#: 0.85 对 21/25 个包都成立，判别力弱。
STRICT_CT_THRESHOLD = 0.70
STRICT_STEP_THRESHOLD = 0.85


def hard_rule_params(sub) -> Dict[str, Dict[str, Any]]:
    """把可机器执行的规则参数挂到 hard_rules 上。

    ``_hard_rules`` 只产出 ``{id, rule}`` 文本，文本无法被生成端消费——这正是
    「只改 learn.py 不改 check.py 等于白改」的根源：阈值 0.70/0.85 在
    ``check.py`` 里各写了一份字面量。这里把阈值与语料实测值一并序列化，
    checker 优先读 YAML、缺失时才退回默认常量，两边不再漂移。
    """
    mel = sub['melody']
    cs = dict((k, p) for k, p, _n in mel['class_by_strength'].get('S', []))
    ct_rate = float(cs.get('CT', 0.0) or 0.0)
    step_rate = float(mel.get('nct_step_either', 0.0) or 0.0)
    return {
        'R01': {
            'strict_ct_threshold': STRICT_CT_THRESHOLD,
            'corpus_ct_rate': round(ct_rate, 4),
            'strict': bool(ct_rate >= STRICT_CT_THRESHOLD),
        },
        'R02': {
            'strict_step_threshold': STRICT_STEP_THRESHOLD,
            'corpus_step_rate': round(step_rate, 4),
            'strict': bool(step_rate >= STRICT_STEP_THRESHOLD),
        },
        'R03': {'max_leap': mel['max_leap']},
        'R04': {'forbidden_leaps': list(mel.get('forbidden_leaps') or [])},
        'R05': {'range_low': mel['range']['low'], 'range_high': mel['range']['high']},
        'R10': {'max_same_chord_bars': sub['form'].get('max_same_chord_bars')},
    }
