# -*- coding: utf-8 -*-
"""分层计数学习 + 剪枝蒸馏 → 规则脚本（dict，可直接写成 YAML）。

四个层级，每层的参数都是带编号、可读的规则：

    全曲层  乐句数、句终规划（第 k 句终止在哪个和弦，马尔可夫链）、反复（Barform）、
            旋律音区随乐句的走向
    乐句层  终止公式（句末三拍的和弦 + 女高音落音）、旋律轮廓模板（k-means 聚类）
    拍层    和弦转移：条件 = 前两拍和弦 + 句内位置（离终止几拍 / 强弱拍），
            Witten-Bell 回退（三元 → 二元+位置 → 二元 → 位置先验）
    声部层  女高音音程 / 和弦音角色、低音音程、内声部级进、重复音、间距、音域、
            导音与七音解决、八分经过音 / 辅助音概率

学到的是"规律"而非曲子：没有任何一条规则记录具体音符序列。
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from score_learning.learn_core import (  # noqa: F401 — 对外再导出，兼容旧 import
    BackoffModel, CTX_MIN, contour_name as _contour_name, dist as _dist,
    is_heldout, kmeans, logloss as _logloss, perplexity as _ppl,
    prune_with_regression, resample as _resample,
)
from .theory import (chord_members, chord_pcs, parse_chord_key, roman, QUALITIES,
                     SEVENTHS)

MIN_VOCAB = 8          # 和弦出现少于此数不入词表（视为语料噪声）
POS_CLASSES = ('start', 'mid_s', 'mid_w', 'cad3', 'cad2', 'cad1', 'cad0')

#: 女高音同音对数概率偏置。**实测校准**，不是拍脑袋的常数：
#: 原值 0.6 声称「生成曲同音比例约 22% 与语料一致」，实测却是 0.249
#: （训练外真实巴赫 0.159）——校准在其他项改动后失效了。扫 0.6/1.2/2.0/3.0，
#: 取 2.0：生成曲同音反复 0.138、3 音动机重复 0.263、级进占比 0.868、音域 11.1，
#: 分别对齐真实巴赫的 0.159/0.171/0.857/11.4，且硬规则零违例。
SOP_REPEAT_BIAS = 2.0


def pos_class(i: int, start: int, end: int, strong: bool) -> str:
    d = end - i
    if d == 0:
        return 'cad0'
    if d <= 3:
        return f'cad{d}'
    if i == start:
        return 'start'
    return 'mid_s' if strong else 'mid_w'


# ---------------------------------------------------------------- 序列化
def phrase_slots(piece) -> List[List[int]]:
    return [list(range(ph['start'], ph['end'] + 1)) for ph in piece['phrases']]


def harmony_events(piece, vocab: Optional[set] = None):
    """逐拍 (p2, p1, pos, chord)；跨句时 p1 = 上一句终止和弦（句与句之间的衔接）。"""
    out = []
    p2, p1 = 'BOS', 'BOS'
    for ph in piece['phrases']:
        for i in range(ph['start'], ph['end'] + 1):
            b = piece['beats'][i]
            c = b['chord']
            if vocab is not None and c not in vocab:
                p2, p1 = 'BOS', 'BOS'
                continue
            pos = pos_class(i, ph['start'], ph['end'], b['strong'])
            out.append((p2, p1, pos, c))
            p2, p1 = p1, c
    return out


# ---------------------------------------------------------------- 主学习
def learn(pieces: List[Dict[str, Any]], name: str = 'bach-chorale',
          heldout: bool = True, smoother: str = 'wb',
          smoother_kw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从众赞歌语料归纳 chorale 语法。

    ``smoother`` 选择拍层和弦转移的估计器（见 ``score_learning.smoothing``）：
    ``wb`` Witten-Bell（默认，与历史产出兼容）/ ``jm`` Jelinek-Mercer /
    ``ad`` Absolute Discounting / ``kn`` Kneser-Ney / ``mkn`` Modified KN。

    10 折GroupKFold 实测（chorale，pos=full，ctx_min=3）：``jm`` 在两个调式上
    分别比``wb`` 低 5.9% / 4.0% 留出困惑度，是当前最优；``kn``/``mkn`` 反而
    高 7%~10%（和声转移近确定性，continuation count 不适用）。详见
    ``docs/实验重设计与消融_2026-10-04.md`` 的 E1。
    """
    from score_learning.smoothing import make_smoother as _make_smoother
    train = [p for p in pieces if not (heldout and is_heldout(p['id']))]
    test = [p for p in pieces if heldout and is_heldout(p['id'])]
    script: Dict[str, Any] = {
        'grammar': name, 'version': 1,
        # ``smoother`` / ``smoother_kw`` 必须落进产物：YAML 的 ``evaluation``
        # 只记录**结果**（困惑度），读者拿到文件无法判断它是哪个估计器的
        # 产物，除非去读 Data availability 的散文。把配置写进 meta 是三行
        # 代价，直接消除一类「这份产物是哪个配置」的疑虑。
        'meta': {'pieces': len(pieces), 'train': len(train), 'heldout': len(test),
                 'beats': sum(len(p['beats']) for p in pieces),
                 'phrases': sum(len(p['phrases']) for p in pieces),
                 'smoother': smoother,
                 'smoother_kw': dict(smoother_kw or {})},
        'vocab': {}, 'form': {}, 'cadences': {}, 'harmony': {}, 'melody': {},
        'bass': {}, 'voicing': {}, 'ornament': {}, 'evaluation': {},
    }
    rid = Counter()

    def new_id(prefix):
        rid[prefix] += 1
        return f'{prefix}{rid[prefix]:03d}'

    for mode in ('major', 'minor'):
        tr = [p for p in train if p['mode'] == mode]
        te = [p for p in test if p['mode'] == mode]
        if not tr:
            continue
        cc = Counter(b['chord'] for p in tr for b in p['beats'] if b['chord'])
        vocab = sorted(c for c, n in cc.items() if n >= MIN_VOCAB)
        vset = set(vocab)
        script['vocab'][mode] = {c: roman(parse_chord_key(c), mode) for c in vocab}
        R = lambda c: script['vocab'][mode].get(c, c) if c not in ('BOS',) else '（曲首）'  # noqa: E731

        # ---------------- 拍层：和弦转移（共享核心：上下文剪枝 + 留出回归）
        # 估计器由 smoother 决定；所有实现共用 prune_with_regression 的语义
        # （snapshot / restore / prune_context），故此处只换构造方式。
        kw = dict(smoother_kw or {})
        model = (_make_smoother(smoother, vocab, **kw) if smoother != 'wb'
                 else BackoffModel(vocab))
        for p in tr:
            for ev in harmony_events(p, vset):
                model.add(*ev)
        ev_te = [e for p in te for e in harmony_events(p, vset)]
        prune_info = prune_with_regression(model, ev_te, levels=('L3', 'L2'),
                                           ctx_min=CTX_MIN)
        nopos = BackoffModel(vocab)
        for p in tr:
            for p2, p1, _pos, c in harmony_events(p, vset):
                nopos.add(p2, p1, 'x', c)
        prune_with_regression(nopos, [(a, b, 'x', d) for a, b, _c, d in ev_te],
                              levels=('L3', 'L2'), ctx_min=CTX_MIN)
        nopos_ppl = _ppl(nopos, [(a, b, 'x', d) for a, b, _c, d in ev_te]) if ev_te else None
        uni = Counter(e[3] for p in tr for e in harmony_events(p, vset))
        utot = sum(uni.values())
        uni_loss = sum(-math.log((uni.get(e[3], 0) + 1) / (utot + len(vocab)))
                       for e in ev_te) if ev_te else 0.0
        n = max(len(ev_te), 1)
        cover = sum(1 for _a, p1, _pos, c in ev_te if p1 == 'BOS' or model.attested(p1, c))
        script['evaluation'][mode] = {
            'heldout_slots': len(ev_te),
            'perplexity_script': prune_info['perplexity_script'] if ev_te else None,
            'perplexity_unpruned': prune_info['perplexity_unpruned'] if ev_te else None,
            'perplexity_no_position': nopos_ppl,
            'perplexity_unigram': round(math.exp(uni_loss / n), 2) if ev_te else None,
            'bigram_coverage': round(cover / n, 4) if ev_te else None,
            'vocab_size': len(vocab),
            'prune_kept': prune_info['pruned'],
        }
        hrules = {}
        for lv in ('L3', 'L2', 'L1', 'L0'):
            rules = []
            for h, cnt in sorted(model.t[lv].items(), key=lambda kv: -sum(kv[1].values())):
                tot = sum(cnt.values())
                when = dict(zip({'L3': ('prev2', 'prev', 'pos'), 'L2': ('prev', 'pos'),
                                 'L1': ('prev',), 'L0': ('pos',)}[lv], h))
                then = [[c, round(k / tot, 4), k] for c, k in cnt.most_common()]
                top = ', '.join(f'{R(c)} {pr:.0%}' for c, pr, _k in then[:3])
                ctx_s = ' + '.join(R(v) if k.startswith('prev') else v for k, v in when.items())
                rules.append({'id': new_id('H'), 'when': when, 'n': tot, 'types': len(cnt),
                              'then': then, 'say': f'{ctx_s} → {top}'})
            hrules[lv] = rules
        script['harmony'][mode] = hrules

        # ---------------- 乐句层：终止公式
        cad = defaultdict(Counter)
        for p in tr:
            b = p['beats']
            for ph in p['phrases']:
                e = ph['end']
                if e - 2 < ph['start']:
                    continue
                chs = tuple(b[i]['chord'] for i in (e - 2, e - 1, e))
                if not all(c in vset for c in chs):
                    continue
                tpc = p['tonic_pc']
                sop = tuple((b[i]['midi'][0] - tpc) % 12 for i in (e - 1, e))
                tgt = ':'.join(chs[-1].split(':')[:2])
                cad[tgt][(chs, sop)] += 1
        cads = {}
        for tgt, cnt in sorted(cad.items(), key=lambda kv: -sum(kv[1].values())):
            tot = sum(cnt.values())
            if tot < 5:
                continue
            forms = []
            for (chs, sop), k in cnt.most_common():
                if k < 2:
                    continue
                kind = _cadence_kind(chs, sop, mode)
                forms.append({'id': new_id('C'), 'chords': list(chs), 'soprano': list(sop),
                              'p': round(k / tot, 4), 'n': k, 'type': kind,
                              'say': ' – '.join(R(c) for c in chs)
                                     + f'（女高音 {_deg_name(sop[0], mode)}→{_deg_name(sop[1], mode)}，{kind}）'})
            if forms:
                cads[tgt] = {'n': tot, 'forms': forms}
        script['cadences'][mode] = cads

        # ---------------- 全曲层：句终规划
        plan = defaultdict(Counter)
        nph = Counter()
        firsts = Counter()
        for p in tr:
            tg = [':'.join(p['beats'][ph['end']]['chord'].split(':')[:2]) for ph in p['phrases']]
            nph[len(tg)] += 1
            prev = 'BOS'
            for k, t in enumerate(tg):
                phase = 'first' if k == 0 else 'last' if k == len(tg) - 1 else \
                    'penult' if k == len(tg) - 2 else 'middle'
                plan[(prev, phase)][t] += 1
                prev = t
            firsts[p['beats'][p['phrases'][0]['start']]['chord']] += 1
        prules = []
        for (prev, phase), cnt in sorted(plan.items(), key=lambda kv: -sum(kv[1].values())):
            tot = sum(cnt.values())
            if tot < 3:
                continue
            then = [[t, round(k / tot, 4), k] for t, k in cnt.most_common() if t in cads]
            if not then:
                continue
            tr_ = lambda t: R(t + ':0') if t != 'BOS' else '（曲首）'  # noqa: E731
            prules.append({'id': new_id('F'), 'when': {'prev': prev, 'phase': phase},
                           'n': tot, 'then': then,
                           'say': f'{_phase_zh(phase)}、上句终止在 {tr_(prev)} → '
                                  + ', '.join(f'{tr_(t)} {pr:.0%}' for t, pr, _k in then[:3])})
        script['form'][mode] = {
            'phrase_count': _dist(nph),
            'cadence_plan': prules,
            'first_chord': _dist(Counter({c: n for c, n in firsts.items() if c in vset})),
        }

        # ---------------- 声部层
        script['melody'][mode] = _learn_melody(tr, mode, R, new_id)
        script['voicing'][mode] = _learn_voicing(tr, mode, vset)
        script['bass'][mode] = _learn_bass(tr, vset)

    script['form']['barform_prob'] = round(
        sum(1 for p in train if p['has_repeat']) / max(len(train), 1), 3)
    phr_lay = Counter()
    pick_lay = Counter()
    layout = Counter()
    for p in train:
        if p['meter'] != '4/4':
            continue
        first = p['beats'][p['phrases'][0]['start']]['pos']
        if first in (0.0, 3.0):
            layout['pickup' if first == 3.0 else 'downbeat'] += 1
        for ph in p['phrases']:
            pos = p['beats'][ph['start']]['pos']
            if pos == 0.0:
                phr_lay[ph['end'] - ph['start'] + 1] += 1
            elif pos == 3.0:
                pick_lay[ph['end'] - ph['start'] + 1] += 1
    script['form']['downbeat_phrase_slots'] = _dist(phr_lay, top=6)
    script['form']['pickup_phrase_slots'] = _dist(pick_lay, top=6)
    lt = sum(layout.values()) or 1
    script['form']['layout'] = {'pickup': round(layout['pickup'] / lt, 3),
                                'downbeat': round(layout['downbeat'] / lt, 3),
                                'n': sum(layout.values())}
    script['ornament'] = _learn_ornament(train)
    script['voicing']['ranges'] = _learn_ranges(train)
    script['hard_rules'] = HARD_RULES
    script['calibration'] = {
        'sop_repeat_bias': SOP_REPEAT_BIAS,
        'say': f'女高音同音反复的对数概率偏置：和声、音程、角色、轮廓几项相乘时会重复奖励'
               f'"原地不动"。原值 0.6 的说明声称"生成曲同音比例约 22% 与语料一致"，'
               f'实测已失效（0.249，真实巴赫 0.159）——很是在其它项改动后没有重校。'
               f'扫 0.6/1.2/2.0/3.0 后取 {SOP_REPEAT_BIAS}：生成曲同音反复 0.138、'
               f'3 音动机重复 0.263、级进占比 0.868、音域 11.1，同音/级进/音域三项'
               f'分别对齐真实巴赫的 0.159/0.857/11.4，且硬规则零违例。',
    }
    return script


HARD_RULES = [
    {'id': 'R01', 'rule': '任意两声部不得连续纯五度 / 纯八度 / 同度（含反向）'},
    {'id': 'R02', 'rule': '外声部同向进入纯五 / 纯八度时，女高音须级进（禁隐伏五八度）'},
    {'id': 'R03', 'rule': '声部不交叉；相邻声部不越过对方上一拍的音（禁超越）'},
    {'id': 'R04', 'rule': '间距：女高–中音、中音–次中音不超过八度；次中音–低音不超过十二度'},
    {'id': 'R05', 'rule': '各声部在学到的音域内'},
    {'id': 'R06', 'rule': '外声部的导音在属→主时上行解决到主音'},
    {'id': 'R07', 'rule': '和弦七音级进下行解决（或在下一和弦里保持）'},
    {'id': 'R08', 'rule': '旋律不得增音程、不得大于八度的跳进、不得七度跳进'},
    {'id': 'R09', 'rule': '导音不重复；三和弦必须有三音，七和弦必须有根、三、七音'},
    {'id': 'R10', 'rule': '和弦转移须是巴赫语料里出现过的二元组（语法内）'},
    {'id': 'R11', 'rule': '每句以学到的终止公式结束；全曲以完全正格终止结束'},
]


def _phase_zh(ph):
    return {'first': '首句', 'middle': '中间句', 'penult': '倒数第二句', 'last': '末句'}[ph]


def _deg_name(rel: int, mode: str) -> str:
    from .theory import _degree_of
    d, a = _degree_of(rel, mode)
    return ('b' if a < 0 else '#' if a > 0 else '') + str(d + 1)


def _is_secondary_dominant(ch, mode) -> bool:
    """该和弦是不是本调的副属 V/X（非调内的大三／属七，且下五度目标在调内）。

    判据与 ``theory.roman`` 标注副属的判据一致，故可复现、不靠枚举。
    """
    from .theory import _is_diatonic_chord, scale_of
    r, q, _inv = ch
    if q not in ('M', '7'):
        return False
    if r == 0 and q != '7':
        return False
    if _is_diatonic_chord(r, q, mode):
        return False
    return (r + 5) % 12 in scale_of(mode)


def _cadence_kind(chs, sop, mode) -> str:
    """终止式类型判定表。

    前六类是一本标准教科书和声学的直接编码（PAC / IAC / PC / HC / DC /
    离调终止），后三类是为解决「其他终止」残留而补的**副调终止**：

    * **离调半终止（停于副属）**——终和弦本身是副属 V/X 时，乐句停在 X 的
      属上，即向 X 的半终止。判据复用 ``_is_secondary_dominant``，不枚举调。
    * **转调终止（离调后完全终止）**——把坐标平移到调内音级 T 后，终和弦成为
      T 的主和弦，且前两拍之一是 T 的属或副导。要求属—主关系真实存在。
    * **转调终止（离调后变格终止）**——同上但前件是 T 的下属。变格终止在终止式
      层级上弱于正格，故单列一类，便于读者按判定强度区别对待。

    这三类**不靠枚举候选调碰运气**：候选主音只取本调音级，且必须有属—主或
    下属—主的功能前件。已用 183 条已知类型规则做过稳定性回归，改动只落在
    原先判不出的条目上（详见 ``cadence_recheck.py`` 的文档串）。
    """
    from .theory import scale_of
    pen = parse_chord_key(chs[1])
    fin = parse_chord_key(chs[2])
    pre = parse_chord_key(chs[0])
    pen_dom = (pen[0] - fin[0]) % 12 == 7 and pen[1] in ('M', '7')
    pen_lt = (pen[0] - fin[0]) % 12 == 11 and pen[1] in ('d', 'o7', 'h7')
    if fin[0] == 0 and fin[1] in ('M', 'm'):
        if pen_dom and fin[2] == 0 and pen[2] == 0 and sop[1] == 0:
            return '完全正格终止 PAC'
        if pen_dom or pen_lt:
            return '不完全正格终止 IAC'
        if (pen[0] - fin[0]) % 12 == 5:
            return '变格终止 PC'
        return '主和弦终止'
    # 半终止：原判据只认原位大三 V，漏掉 V7 与小调自然小属 v
    if fin[0] == 7 and fin[1] in ('M', '7', 'm'):
        return '半终止 HC'
    if pen[0] == 7 and pen[1] in ('M', '7') and fin[0] in (8, 9):
        return '阻碍终止 DC'
    if pen_dom or pen_lt:
        return '离调终止（副属→目标）'
    if _is_secondary_dominant(fin, mode):
        return '离调半终止（停于副属）'
    for cand in scale_of(mode):
        if cand == 0:
            continue
        if (fin[0] - cand) % 12 != 0 or fin[1] not in ('M', 'm'):
            continue
        for src in (pen, pre):
            sr = (src[0] - cand) % 12
            if (sr == 7 and src[1] in ('M', '7')) or \
               (sr == 11 and src[1] in ('d', 'o7', 'h7')):
                return '转调终止（离调后完全终止）'
        for src in (pen, pre):
            if (src[0] - cand) % 12 == 5 and src[1] in ('M', 'm'):
                return '转调终止（离调后变格终止）'
    return '其他终止'


def _learn_melody(tr, mode, R, new_id):
    iv = defaultdict(Counter)
    role = defaultdict(Counter)
    recover = Counter()
    contours = []
    reg_arc = defaultdict(list)
    start_deg = Counter()
    join = Counter()
    sop_abs = []
    for p in tr:
        tpc = p['tonic_pc']
        b = p['beats']
        piece_mean = np.mean([x['midi'][0] for x in b])
        n_ph = len(p['phrases'])
        prev_end = None
        for k, ph in enumerate(p['phrases']):
            seq = [b[i]['midi'][0] for i in range(ph['start'], ph['end'] + 1)]
            sop_abs.extend(seq)
            if prev_end is not None:
                join[max(-12, min(12, seq[0] - prev_end))] += 1
            prev_end = seq[-1]
            if k == 0:
                start_deg[(seq[0] - tpc) % 12] += 1
            mean = float(np.mean(seq))
            reg_arc[min(4, int(5 * k / max(n_ph, 1)))].append(mean - piece_mean)
            if len(seq) >= 4:
                contours.append(np.array(_resample([s - mean for s in seq])))
            for j, i in enumerate(range(ph['start'], ph['end'] + 1)):
                c = b[i]['chord']
                if c:
                    r, q, _ = parse_chord_key(c)
                    rl = chord_members(r, q).get((b[i]['midi'][0] - tpc) % 12, 'N')
                    role[pos_class(i, ph['start'], ph['end'], b[i]['strong'])][rl] += 1
                if j:
                    d = seq[j] - seq[j - 1]
                    pc_ = pos_class(i, ph['start'], ph['end'], b[i]['strong'])
                    iv[pc_][max(-12, min(12, d))] += 1
                    if j >= 2 and abs(seq[j - 1] - seq[j - 2]) >= 5:
                        prev_d = seq[j - 1] - seq[j - 2]
                        ok = 0 < abs(d) <= 2 and (d > 0) != (prev_d > 0)
                        recover['step_back' if ok else 'other'] += 1
    X = np.array(contours)
    C, lab = kmeans(X, 5)
    cnt = Counter(lab.tolist())
    tmpl = []
    for j in np.argsort([-cnt[j] for j in range(len(C))]):
        tmpl.append({'id': new_id('M'), 'shape': [round(float(v), 2) for v in C[j]],
                     'p': round(cnt[j] / len(lab), 4), 'n': cnt[j],
                     'name': _contour_name(C[j])})
    tot_rec = sum(recover.values()) or 1
    return {
        'interval_by_pos': {k: _dist(v) for k, v in iv.items()},
        'chord_role_by_pos': {k: _dist(v) for k, v in role.items()},
        'leap_recovery_prob': round(recover['step_back'] / tot_rec, 3),
        'contour_templates': tmpl,
        'register_arc': {str(k): round(float(np.mean(v)), 2) for k, v in sorted(reg_arc.items())},
        'start_degree': _dist(start_deg),
        'phrase_join_interval': _dist(join),
        'abs_mean': round(float(np.mean(sop_abs)), 2),
        'abs_std': round(float(np.std(sop_abs)), 2),
    }


def _learn_bass(tr, vset):
    iv = Counter()
    for p in tr:
        b = p['beats']
        for ph in p['phrases']:
            for i in range(ph['start'] + 1, ph['end'] + 1):
                iv[max(-12, min(12, b[i]['midi'][3] - b[i - 1]['midi'][3]))] += 1
    return {'interval': _dist(iv)}


def _learn_voicing(tr, mode, vset):
    dbl = defaultdict(Counter)
    omit = defaultdict(Counter)
    inner = {'A': Counter(), 'T': Counter()}
    common_keep = Counter()
    lt_inner = Counter()
    spacing = {'SA': Counter(), 'AT': Counter(), 'TB': Counter()}
    #: 声部**对**的完整关系分布（旧版只留了 spacing 的分位数，生成端拿不到分布）：
    #: 间距分布 SA/AT/TB + 外声部 SB（织体宽度），以及每一对声部的运动关系。
    #: 运动关系是织体的核心：齐奏 = 全 similar/oblique，织体丰富 = contrary/oblique 交替。
    pair_span = {'SA': Counter(), 'AT': Counter(), 'TB': Counter(), 'SB': Counter()}
    pair_motion = {k: Counter() for k in ('SA', 'AT', 'TB', 'SB')}
    pair_motion_pos = defaultdict(Counter)
    #: **条件**运动分布：P(某声部往哪动 | 女高往哪动)。这是「声部之间关系」的
    #: 正确载体——`inner_motion` 那种边缘分布只存音程**绝对值**，丢掉了方向，
    #: 于是「与女高同向还是反向」这个关键相关性根本无法表达，生成器只能各走各的。
    cond_motion = {'A': Counter(), 'T': Counter(), 'B': Counter()}
    omit_tot = defaultdict(Counter)

    def _dir(d: int) -> str:
        return 'up' if d > 0 else ('down' if d < 0 else 'stay')
    for p in tr:
        tpc = p['tonic_pc']
        b = p['beats']
        for ph in p['phrases']:
            for i in range(ph['start'], ph['end'] + 1):
                c = b[i]['chord']
                if c not in vset:
                    continue
                r, q, inv = parse_chord_key(c)
                mem = chord_members(r, q)
                roles = [mem.get((m - tpc) % 12, 'N') for m in b[i]['midi']]
                rc = Counter(roles)
                key = f'{"7th" if q in SEVENTHS else "triad"}:{inv}'
                omit_tot[key]['n'] += 1
                for rl, k in rc.items():
                    if k >= 2 and rl != 'N':
                        dbl[key][rl] += 1
                for rl in ('R', '3', '5', '7'):
                    if rl in mem.values() and rl not in rc:
                        omit[key][rl] += 1
                s, a, t, bb = b[i]['midi']
                spacing['SA'][s - a] += 1
                spacing['AT'][a - t] += 1
                spacing['TB'][t - bb] += 1
                pair_span['SA'][s - a] += 1
                pair_span['AT'][a - t] += 1
                pair_span['TB'][t - bb] += 1
                pair_span['SB'][s - bb] += 1
                if i > ph['start']:
                    pv = b[i - 1]['midi']
                    for vi, nm in ((1, 'A'), (2, 'T')):
                        inner[nm][min(12, abs(b[i]['midi'][vi] - pv[vi]))] += 1
                    pc_prev = {(m - tpc) % 12 for m in pv}
                    pc_now = {(m - tpc) % 12 for m in b[i]['midi']}
                    for vi in (1, 2):
                        pcv = (pv[vi] - tpc) % 12
                        if pcv in pc_now and pcv in pc_prev:
                            common_keep['kept' if b[i]['midi'][vi] == pv[vi] else 'moved'] += 1
                    pch = b[i - 1]['chord']
                    if pch and c:
                        pr = parse_chord_key(pch)
                        if pr[0] == 7 and pr[1] in ('M', '7') and r == 0:
                            for vi in (1, 2):
                                if (pv[vi] - tpc) % 12 == 11:
                                    d = b[i]['midi'][vi] - pv[vi]
                                    lt_inner['up_to_tonic' if d == 1 else
                                             'down_to_fifth' if d in (-3, -4) else 'other'] += 1
                    # 声部对的运动关系：同向 / 反向 / 一动一静 / 全静
                    now = b[i]['midi']
                    pos = pos_class(i, ph['start'], ph['end'], b[i]['strong'])
                    sdir = _dir(now[0] - pv[0])
                    cond_motion['A'][(sdir, _dir(now[1] - pv[1]))] += 1
                    cond_motion['T'][(sdir, _dir(now[2] - pv[2]))] += 1
                    cond_motion['B'][(sdir, _dir(now[3] - pv[3]))] += 1
                    for (j, k), nm in (((0, 1), 'SA'), ((1, 2), 'AT'),
                                       ((2, 3), 'TB'), ((0, 3), 'SB')):
                        d0, d1 = now[j] - pv[j], now[k] - pv[k]
                        if d0 == 0 and d1 == 0:
                            kind = 'static'
                        elif d0 == 0 or d1 == 0:
                            kind = 'oblique'
                        elif (d0 > 0) == (d1 > 0):
                            kind = 'similar'
                        else:
                            kind = 'contrary'
                        pair_motion[nm][kind] += 1
                        pair_motion_pos[nm + ':' + pos][kind] += 1
    keep_tot = sum(common_keep.values()) or 1
    # 缺失音的对数惩罚：生成器在「该角色没出现」时要罚，罚多少直接取语料频率，
    # 而不是手写常数（旧版是 `if '5' not in roles: e -= 1.2`）。
    omit_logpen = {}
    for key, cnt in omit.items():
        tot = max(omit_tot[key]['n'], 1)
        omit_logpen[key] = {rl: round(math.log(max(v / tot, 1e-4)), 4)
                            for rl, v in cnt.items()}
    return {
        'doubling': {k: _dist(v) for k, v in dbl.items()},
        'omission': {k: _dist(v) for k, v in omit.items()},
        'omission_logpen': omit_logpen,
        'inner_motion': {k: _dist(v) for k, v in inner.items()},
        'common_tone_keep_prob': round(common_keep['kept'] / keep_tot, 3),
        'inner_leading_tone': _dist(lt_inner),
        'spacing': {k: {'p50': int(np.percentile(list(v.elements()), 50)),
                        'p95': int(np.percentile(list(v.elements()), 95)),
                        'max': int(max(v))} for k, v in spacing.items()},
        # ↓ 新增：以下三组是「声部之间关系」的完整分布，供生成端直接取用
        'pair_span': {k: _dist(v, top=16) for k, v in pair_span.items()},
        'pair_motion': {k: _dist(v) for k, v in pair_motion.items()},
        'pair_motion_by_pos': {k: _dist(v) for k, v in pair_motion_pos.items()},
        # 条件运动分布 P(声部方向 | 女高方向)：key 形如 'up|down'，值为 [次数, 概率]
        'cond_motion': {
            v: {f'{sd}|{id_}': [n, round(n / max(sum(x for (s2, _i2), x in c_.items()
                                                     if s2 == sd), 1), 4)]
                for (sd, id_), n in c_.items()}
            for v, c_ in cond_motion.items()},
    }


def _learn_ranges(train):
    vals = [[], [], [], []]
    for p in train:
        for b in p['beats']:
            for vi in range(4):
                vals[vi].append(b['midi'][vi])
    out = {}
    for vi, nm in enumerate(('S', 'A', 'T', 'B')):
        v = np.array(vals[vi])
        out[nm] = {'low': int(np.percentile(v, 1)), 'high': int(np.percentile(v, 99)),
                   'mean': round(float(v.mean()), 1)}
    return out


def _learn_ornament(train):
    """八分音装饰：拍间三度 → 经过音；同音 → 辅助音；按声部统计概率。"""
    stats = {nm: Counter() for nm in ('S', 'A', 'T', 'B')}
    for p in train:
        b = p['beats']
        for ph in p['phrases']:
            for i in range(ph['start'], ph['end']):
                for vi, nm in enumerate(('S', 'A', 'T', 'B')):
                    a, c = b[i]['midi'][vi], b[i + 1]['midi'][vi]
                    e = b[i]['eighth'][vi]
                    gap = abs(c - a)
                    if gap in (3, 4):
                        stats[nm]['third'] += 1
                        if e is not None and min(a, c) < e < max(a, c):
                            stats[nm]['passing'] += 1
                    elif gap == 0:
                        stats[nm]['same'] += 1
                        if e is not None and 0 < abs(e - a) <= 2:
                            stats[nm]['neighbor'] += 1
    out = {}
    for nm, s in stats.items():
        out[nm] = {'passing_prob': round(s['passing'] / max(s['third'], 1), 3),
                   'neighbor_prob': round(s['neighbor'] / max(s['same'], 1), 3),
                   'evidence': {'third': s['third'], 'same': s['same']}}
    return out
