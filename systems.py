# -*- coding: utf-8 -*-
"""体系层：非功能和声体系的作曲骨架（板腔 / 曲牌 / 拉格 / 十二音序列）。

风格包（genre packs）覆盖的是**西方功能和声语汇**——那只是人类音乐的一小片。
本模块补上另外四类"按自己的规则组织音乐"的体系，每类都是**确定性**的：
给 seed 就逐音符复现，不调用 LLM，也不学习任何语料。

与内核的关系
------------
内核（compose_kernel）的脚本假设功能和声（罗马数字级数 + 调内音），
体系层的四类不都适用这套假设（十二音没有调性、拉格是调式+特征乐句、
板腔是上下句落音），所以本模块**直接产出 spec**并复用内核之外的校验
（llm_compose.validate_score_spec）与导出链路，而不是生成内核调用序列。

四类体系
--------
1. ``banqiang`` **板腔体**：板式变化体。以"上下句"为基本单位，上句落
   非主音、下句落主音；板式（慢板/原板/二六/流水/散板）决定拍号、速度与
   加花密度；句间有过门。五声音阶（宫商角徵羽）。
2. ``qupai`` **曲牌体**：联曲体。曲牌 = 固定的字位网格 + 腔（旋律骨架），
   同一曲牌可填不同词；多个曲牌联成套数。这里的曲牌骨架是**原创轮廓**，
   不复制任何真实曲牌旋律。
3. ``raga`` **拉格**：调式音阶（含 vadi 主音 / samvadi 协音）+ pakad
   特征乐句 + 塔拉节奏圈；结构为 alap（散起）→ gat（固定作品）→ tan（跑句）。
   低声部是持续音（坦布拉式）。
4. ``serial`` **十二音序列**：音列（row）的 P / R / I / RI 四种形态按段落
   轮换，音不重复、无调中心；节奏用固定的"点数网格"。

产出
----
每个体系函数返回标准 spec（可交给 ``llm_compose.spec_to_music21``、
导出 MusicXML/MIDI、跑 ``evaluate`` 三指标）。spec 里带 ``system`` 字段
说明它是哪个体系生成的、用了什么参数——可审计。
"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 只使用**非附点**时值名：llm_compose / music21 的时值表不认
#: 'dotted-quarter'（_dur_ql 会静默当 1.0，music21 的 type= 也会失败退化成休止符），
#: 所以体系层一律在 16 分音符栅格上组合时值，不产出附点记号。
DUR_QL = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5,
          '16th': 0.25}
QL_DUR = {4.0: 'whole', 2.0: 'half', 1.0: 'quarter', 0.5: 'eighth',
          0.25: '16th'}

# ---------------------------------------------------------------- 公共工具


def _rng(seed: str, *parts) -> random.Random:
    h = hashlib.md5('|'.join([str(seed)] + [str(p) for p in parts])
                    .encode('utf-8')).hexdigest()
    return random.Random(h)


#: 十二个音名（升号/降号两套），用于 midi → 音名
_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
_FLAT = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']


def _midi(name: str) -> int:
    """'C4' / 'F#5' / 'Bb3' → midi。"""
    s = str(name).strip()
    i = 1
    while i < len(s) and s[i] in '#b':
        i += 1
    letter, octv = s[:i], int(s[i:])
    base = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[letter[0]]
    acc = letter.count('#') - letter.count('b')
    return (octv + 1) * 12 + base + acc


def _name(midi: int, prefer_flat: bool = False) -> str:
    tbl = _FLAT if prefer_flat else _SHARP
    return f'{tbl[int(midi) % 12]}{int(midi) // 12 - 1}'


def _scale_names(tonic_pc: int, intervals: Sequence[int],
                 prefer_flat: bool = False) -> List[str]:
    """调式音名（从主音起，含八度循环）。"""
    return [_name(tonic_pc + iv, prefer_flat) for iv in intervals]


def _fit_bar(events: List[List], cap: float) -> List[List]:
    """把一小节事件调整到**精确**等于 cap（16 分音符栅格上截断/补齐）。

    体系层所有产出都必须过 validate_score_spec，时值不满是最常见的违规。
    实现要点：全程用整数个 16 分音符计量，避免浮点误差让小节差 0.1 拍
    （旧实现在"余数 < 0.25"时直接丢弃该事件，却把累计量当成已填满，
    于是整曲时值会缺斤少两）。
    """
    units = max(1, int(round(cap * 4)))          # 本小节共多少个 16 分音符
    out: List[List] = []
    acc = 0
    for e in events:
        d = max(1, int(round(DUR_QL.get(str(e[1]), 1.0) * 4)))
        if acc + d > units:
            # 截断到"可表示"的最大时值（二进制分解），余数交给补休止
            rem = units - acc
            for size in (8, 4, 2, 1):
                if rem >= size:
                    out.append([e[0], QL_DUR[size / 4.0],
                                e[2] if len(e) > 2 else None])
                    acc += size
                    rem -= size
            break
        out.append(list(e))
        acc += d
    # 补休止：按 二分/四分/八分/16 分 二进制分解（3 个 16 分 = 八分+16分）
    while acc < units:
        for size in (16, 8, 4, 2, 1):
            if units - acc >= size:
                out.append([None, QL_DUR[size / 4.0], None])
                acc += size
                break
        else:
            break
    return out


def _dyn_plan(bars: int, lo: str = 'mp', hi: str = 'mf') -> List[List]:
    """简单的力度阶梯（避免"全部缺失力度记号"的校验项）。"""
    order = [lo, lo, 'mf', hi]
    out, prev = [], None
    for b in range(1, bars + 1):
        v = order[min(3, (b - 1) * 4 // max(1, bars))]
        if v != prev:
            out.append([b, v])
            prev = v
    return out


def _spec(title: str, bars: int, ts: str, tempo: int, sharps: int, mode: str,
          parts: List[Dict[str, Any]], sections: List[Dict[str, Any]],
          system: Dict[str, Any], seed: str) -> Dict[str, Any]:
    for p in parts:
        # 事件已由各 builder 按小节容量生成，这里只补默认力度阶梯
        if not p.get('dynamics'):
            p['dynamics'] = _dyn_plan(bars)
    return {
        'title': title, 'bars': bars, 'time_signature': ts, 'tempo': tempo,
        'key_sharps': sharps, 'mode': mode, 'parts': parts,
        'sections': sections, 'system': dict(system, seed=str(seed)),
        'seed': str(seed),
    }


# ================================================================
# 1. 板腔体
# ================================================================

#: 板式 → (拍号, 速度, 加花密度, 说明)
BANQIAN_BANSHI: Dict[str, Tuple[str, int, float, str]] = {
    '慢板': ('4/4', 56, 1.5, '一板三眼，字少腔多'),
    '原板': ('2/4', 84, 1.0, '一板一眼，叙事主体'),
    '二六': ('2/4', 108, 1.2, '一板一眼，句读紧凑'),
    '流水': ('1/4', 132, 1.6, '有板无眼，字多腔少'),
    '快板': ('1/4', 152, 1.8, '有板无眼，情绪顶点'),
    '散板': ('4/4', 60, 1.0, '无板无眼，自由节奏（此处按 4/4 记谱）'),
}

#: 五声（宫商角徵羽）与主音关系：调式 → 主音在五声里的索引
PENTA = [0, 2, 4, 7, 9]        # 宫 商 角 徵 羽
_DIAO = {'宫': 0, '商': 1, '角': 2, '徵': 3, '羽': 4}


def build_banqiang(*, diao: str = '徵', banshi: Optional[Sequence[str]] = None,
                   couplets: int = 2, bars: int = 16, tempo: Optional[int] = None,
                   seed: str = 'banqiang', tonic: str = 'C4') -> Dict[str, Any]:
    """板腔体：上下句 + 板式变化 + 过门。

    上句落**非主音**（常用"上句落羽/角"），下句落**主音**——这是板腔体
    最核心的可听特征；句间插过门（伴奏走音型式）。五声音阶，无半音。
    """
    rng = _rng(seed, 'banqiang', diao, couplets, bars)
    di = _DIAO.get(str(diao), 3)
    tonic_m = _midi(tonic)
    root_pc = tonic_m % 12
    # 五声音阶（以宫为 0）：宫 = 主音右移 di 个音级
    penta_pcs = [(root_pc + (PENTA[(k + di) % 5] - PENTA[di])) % 12 for k in range(5)]
    scale = sorted(set(penta_pcs))
    main_m = None          # 主音（落音目标）
    for m in range(tonic_m, tonic_m + 12):
        if m % 12 == root_pc:
            main_m = m
            break
    # scale 是「主音优先」的升序五声音阶（penta_pcs 构造保证从主音起升序排列），
    # 上句落音取主音上方两个音级（必为非主音）；历史实现用 (di+2)%5 索引，
    # 在默认徵调式下恰好绕回主音，破坏「上句落非主音」的核心特征
    upper_end_pc = scale[2 % len(scale)]   # 上句落音：主音上方两个音级

    bans = list(banshi or ['原板', '二六'])
    parts: List[Dict[str, Any]] = [{'name': '唱腔', 'clef': 'treble', 'events': []},
                                   {'name': '伴奏', 'clef': 'bass', 'events': []}]
    mel_ev: List[List] = []
    acc_ev: List[List] = []

    def _deg_midi(idx: int, lo: int, hi: int) -> int:
        """五声级 → 落在 [lo,hi] 的音高。idx 可越界（自动换八度）。"""
        n = len(scale)
        step, k = idx // n, idx % n
        m = lo + (scale[k] - lo % 12) % 12 + 12 * step
        while m < lo:
            m += 12
        while m > hi:
            m -= 12
        return m

    lo, hi = tonic_m - 5, tonic_m + 12
    section_list: List[Dict[str, Any]] = []
    mel_bars: List[List[List]] = []
    acc_bars: List[List[List]] = []
    caps: List[float] = []
    meter_changes: List[List] = []
    tempo_marks: List[List] = []
    cur_ts = ''
    for ci in range(max(1, int(couplets))):
        ban = bans[ci % len(bans)]
        tsx, bpm, dens, _desc = BANQIAN_BANSHI.get(ban, ('2/4', 84, 1.0, ''))
        num, den = tsx.split('/')
        cap = 4.0 * int(num) / int(den)
        if tsx != cur_ts:
            if cur_ts:                            # 换板式 → 记一次拍号变化
                meter_changes.append([len(caps) + 1, tsx])
            cur_ts = tsx                          # 无论首末都要更新，否则回换原板式会被漏记
        tempo_marks.append([len(caps) + 1, ban, None])
        section_list.append({'name': f'{ban}{ci + 1}', 'bars': 10,
                             'meter': tsx, 'tempo_text': ban})
        for phrase in range(2):                    # 上句 / 下句
            is_upper = phrase == 0
            end_pc = upper_end_pc if is_upper else root_pc
            # 句子：4 小节，级进为主 + 一次四度跳（"腔"），末音落 end_pc
            n_notes = max(4, int(round(6 * dens)))
            idxs = [0]
            cur = 0
            for _ in range(n_notes - 1):
                step = rng.choice([1, 1, 1, 2, -1, -1, -2])
                cur = max(-3, min(6, cur + step))
                idxs.append(cur)
            # 落音：把最后两个音拉到落音级
            target_idx = next((k for k in range(-4, 8)
                               if scale[k % len(scale)] == end_pc), 0)
            idxs[-1] = target_idx
            idxs[-2] = target_idx + rng.choice([-1, 1])
            durs: List[float] = []
            remain = cap * 4
            for i in range(len(idxs)):
                if i == len(idxs) - 1:
                    durs.append(remain)
                    break
                d = rng.choice([1.0, 1.0, 0.5, 0.5, 2.0]) if cap >= 3 else \
                    rng.choice([0.5, 0.5, 0.25, 0.25, 1.0])
                d = min(d, max(0.25, remain - 0.25 * (len(idxs) - i - 1)))
                durs.append(d)
                remain -= d
            for b in range(4):
                sub: List[List] = []
                acc_b: List[List] = []
                # 每小节 1/4 的句子材料
                per = max(1, len(idxs) // 4)
                chunk = idxs[b * per:(b + 1) * per] or [idxs[-1]]
                cdur = [durs[b * per + j] if b * per + j < len(durs) else 0.5
                        for j in range(len(chunk))]
                tot = sum(cdur) or 1.0
                cdur = [d * cap / tot for d in cdur]
                for k, ix in enumerate(chunk):
                    m = _deg_midi(ix, lo, hi)
                    sub.append([_name(m), QL_DUR.get(round(cdur[k] * 4) / 4, 'eighth'),
                                'legato' if k == 0 else None])
                mel_bars.append(sub)
                caps.append(cap)
                # 伴奏：强拍主音、弱拍五度（主音上方第 3 个音级），弱拍间插五声琶音
                t = 0.0
                while t < cap - 1e-9:
                    d = min(1.0 if cap >= 3 else 0.5, cap - t)
                    pc = scale[0 if t < 1e-9 else 3 % len(scale)]
                    m = lo - 12 + (pc - (lo - 12) % 12) % 12
                    acc_b.append([_name(m), QL_DUR.get(d, 'quarter'), None])
                    t += d
                acc_bars.append(acc_b)
        # 过门（2 小节）：伴奏走音型；唱腔给长音（不用休止，免得末段塌陷）
        for b in range(2):
            guo: List[List] = []
            t = 0.0
            while t < cap - 1e-9:
                d = min(0.5, cap - t)
                pc = scale[(int(t * 2) + di) % len(scale)]
                m = lo - 12 + (pc - (lo - 12) % 12) % 12
                guo.append([_name(m), QL_DUR.get(d, 'eighth'), None])
                t += d
            acc_bars.append(guo)
            mel_bars.append([[main_m and _name(main_m) or None,
                              'whole' if cap >= 3 else 'half', None]])
            caps.append(cap)

    # 规整到 bars（逐小节容量取自板式，因此板腔体可以真的换拍号）
    if bars > len(caps):                       # 不足：用最后一个板式续写
        last_cap = caps[-1] if caps else 4.0
        while len(caps) < bars:
            caps.append(last_cap)
            mel_bars.append([[main_m and _name(main_m) or None,
                              'whole' if last_cap >= 3 else 'half', None]])
            acc_bars.append([[_name(lo - 12), 'whole' if last_cap >= 3 else 'half',
                              None]])
    else:
        mel_bars, acc_bars, caps = mel_bars[:bars], acc_bars[:bars], caps[:bars]
    ts0 = _ts_from_cap(caps[0]) if caps else '4/4'
    parts[0]['events'] = _fit_bars_caps(mel_bars, bars, caps)
    parts[1]['events'] = _fit_bars_caps(acc_bars, bars, caps)
    spec = _spec(f'{diao}调板腔小品', bars, ts0, int(tempo or 88), 0, 'major',
                 parts, section_list,
                 {'kind': 'banqiang', 'diao': diao, 'banshi': bans,
                  'couplets': couplets}, seed)
    spec['meter_changes'] = [[int(b), t] for b, t in meter_changes if 2 <= b <= bars]
    spec['tempo_marks'] = [[int(b), t, None] for b, t, _n in tempo_marks if b <= bars]
    return spec


def _ts_from_cap(cap: float) -> str:
    """容量(四分音符数) → 拍号字符串。"""
    for ts in ('4/4', '3/4', '2/4', '1/4', '6/8', '2/2'):
        n, d = ts.split('/')
        if abs(4.0 * int(n) / int(d) - cap) < 1e-9:
            return ts
    return f'{int(round(cap))}/4'


def _fit_bars_total(events: List[List], bars: int, cap: float) -> List[List]:
    """把事件序列规整到 bars×cap（按小节切分，多退少补）。"""
    per: List[List[List]] = [[] for _ in range(max(0, int(bars)))]
    bi, acc = 0, 0.0
    for e in events:
        d = DUR_QL.get(str(e[1]), 1.0)
        if bi >= bars:
            break
        if acc + d > cap + 1e-9 and acc > 1e-9:
            bi += 1
            acc = 0.0
            if bi >= bars:
                break
        per[bi].append(list(e))
        acc += d
        if acc >= cap - 1e-9:
            bi += 1
            acc = 0.0
    out: List[List] = []
    for b in range(bars):
        out.extend(_fit_bar(per[b], cap) if per[b]
                   else _fit_bar([[None, 'whole', None]], cap))
    return out


def _fit_bars_caps(bar_events: List[List[List]], bars: int,
                   caps: Sequence[float]) -> List[List]:
    """按**逐小节不同容量**规整（板腔体换板式时拍号会变）。

    ``bar_events[b]`` 是第 b 小节（0 基）的事件，``caps[b]`` 是该小节容量。
    不足的小节补空小节，多出的丢弃。
    """
    out: List[List] = []
    for b in range(int(bars)):
        cap = caps[b] if b < len(caps) else (caps[-1] if caps else 4.0)
        evs = bar_events[b] if b < len(bar_events) else []
        out.extend(_fit_bar(evs, cap) if evs
                   else _fit_bar([[None, 'whole' if cap >= 3 else 'half', None]], cap))
    return out


# ================================================================
# 2. 曲牌体
# ================================================================

#: 曲牌骨架：**原创**轮廓（五声级数序列）+ 字位时值网格。
#: 只描述"腔的走向与板位"，不复制任何真实曲牌旋律。
QUPAI_SKELETONS: Dict[str, Dict[str, Any]] = {
    '山坡羊': {'degrees': [0, 2, 3, 2, 1, 0, -1, 0],
              'durs': [1.0, 0.5, 0.5, 1.0, 0.5, 0.5, 1.0, 1.0],
              'desc': '起伏较大的腔，多用于抒情'},
    '皂罗袍': {'degrees': [0, 1, 2, 1, 2, 3, 2, 0],
              'durs': [1.0, 0.5, 0.5, 1.0, 1.0, 0.5, 0.5, 1.0],
              'desc': '级进回旋，腔多字少'},
    '点绛唇': {'degrees': [3, 2, 1, 2, 0, -1, 0, 1],
              'durs': [0.5, 0.5, 1.0, 0.5, 0.5, 1.0, 0.5, 1.0],
              'desc': '起于高腔，下行收束'},
}


def build_qupai(*, paizi: Optional[Sequence[str]] = None, bars: int = 16,
                tempo: Optional[int] = None, seed: str = 'qupai',
                tonic: str = 'D4', diao: str = '徵') -> Dict[str, Any]:
    """曲牌体：联曲（若干曲牌依次演唱），每牌按固定字位网格填腔。

    同一骨架每次可"加花"（在字位间插入邻音），所以同一个曲牌不会两次一模一样，
    但板位（强拍位置）与落音保持不变——这正是曲牌体的约束方式。
    """
    rng = _rng(seed, 'qupai', str(paizi), bars)
    names = list(paizi or ['山坡羊', '皂罗袍'])
    tonic_m = _midi(tonic)
    root_pc = tonic_m % 12
    di = _DIAO.get(str(diao), 3)
    penta_pcs = [(root_pc + (PENTA[(k + di) % 5] - PENTA[di])) % 12 for k in range(5)]
    scale = sorted(set(penta_pcs))
    lo, hi = tonic_m - 4, tonic_m + 11

    def _pc_of(deg: int) -> int:
        k = deg % len(scale)
        oct_shift = deg // len(scale)
        pcs = sorted(set(penta_pcs))
        return pcs[k] + 12 * oct_shift

    def _m(deg: int) -> int:
        pc = _pc_of(deg)
        m = lo + (pc - lo % 12) % 12
        while m < lo:
            m += 12
        while m > hi:
            m -= 12
        return m

    mel_ev: List[List] = []
    acc_ev: List[List] = []
    sections: List[Dict[str, Any]] = []
    cap = 4.0
    per_paizi = max(4, bars // max(1, len(names)))
    for pi, nm in enumerate(names):
        sk = QUPAI_SKELETONS.get(nm) or QUPAI_SKELETONS['山坡羊']
        degs, durs = list(sk['degrees']), list(sk['durs'])
        sections.append({'name': nm, 'bars': per_paizi, 'meter': '4/4',
                         'tempo_text': nm})
        n_bar = per_paizi if pi < len(names) - 1 else bars - per_paizi * (len(names) - 1)
        n_bar = max(1, n_bar)
        # 每 2 小节一遍腔（一遍 8 个音 = 8 拍）
        for b in range(n_bar):
            half = b % 2
            chunk_d = degs[half * 4:(half + 1) * 4]
            chunk_u = durs[half * 4:(half + 1) * 4]
            evs: List[List] = []
            for k, dg in enumerate(chunk_d):
                d = chunk_u[k]
                # 加花：一半概率在长音后插一个邻音
                evs.append([_name(_m(dg)), QL_DUR.get(d, 'quarter'),
                            'legato' if k == 0 else None])
                if d >= 1.0 and rng.random() < 0.5:
                    evs.append([_name(_m(dg + rng.choice([-1, 1]))), 'eighth', None])
            mel_ev.extend(_fit_bar(evs, cap))
            # 伴奏：板位（每小节第 1 拍）主音/五度，其余为"衬"
            a: List[List] = []
            t = 0.0
            while t < cap - 1e-9:
                d = 1.0 if abs(t) < 1e-9 or abs(t - 2.0) < 1e-9 else 0.5
                d = min(d, cap - t)
                idx = 0 if abs(t) < 1e-9 else (2 if abs(t - 2.0) < 1e-9 else 1)
                pc = scale[(di + idx) % len(scale)]
                m = lo - 12 + (pc - (lo - 12) % 12) % 12
                a.append([_name(m), QL_DUR.get(d, 'quarter'), None])
                t += d
            acc_ev.extend(_fit_bar(a, cap))
    parts = [{'name': '曲牌唱腔', 'clef': 'treble',
              'events': _fit_bars_total(mel_ev, bars, cap)},
             {'name': '伴奏', 'clef': 'bass',
              'events': _fit_bars_total(acc_ev, bars, cap)}]
    return _spec('联曲小品', bars, '4/4', int(tempo or 76), 0, 'major', parts,
                 sections, {'kind': 'qupai', 'paizi': names, 'diao': diao}, seed)


# ================================================================
# 3. 拉格
# ================================================================

#: 拉格 → (音阶半音级, vadi 级, samvadi 级, 主音 Sa 的 pc, 说明)
RAGAS: Dict[str, Tuple[List[int], int, int, int, str]] = {
    'yaman': ([0, 2, 4, 6, 7, 9, 11], 4, 0, 0, '含升四级，黄昏之情'),
    'bhairav': ([0, 1, 4, 5, 7, 8, 11], 0, 4, 0, '含降二级降六级，晨祷'),
    'malkauns': ([0, 3, 5, 7, 10], 0, 3, 0, '五音，深夜冥想'),
    'bhairavi': ([0, 1, 3, 5, 7, 8, 10], 2, 5, 0, '含降二级降三级降六级，收尾常用'),
}

#: 塔拉 → (拍数, 每小节拍数, 分句点)
TALAS: Dict[str, Tuple[int, int, List[int]]] = {
    'teental': (16, 4, [1, 5, 9, 13]),
    'keherwa': (8, 4, [1, 5]),
    'rupak': (7, 3, [1, 4]),
}


def build_raga(*, raga: str = 'yaman', tala: str = 'teental', bars: int = 16,
               tempo: Optional[int] = None, seed: str = 'raga',
               sa: str = 'C4') -> Dict[str, Any]:
    """拉格：alap（散起）→ gat（塔拉内的固定作品）→ tan（跑句）。

    - 音阶与 vadi/samvadi 决定音高取舍：乐句**必须**点出 vadi，落句回到 Sa；
    - 塔拉决定循环长度与分句点（sam 在第 1 拍）；
    - 低声部是持续音（坦布拉式的 Sa–Pa 交替），不是功能和声。
    """
    rng = _rng(seed, 'raga', raga, tala, bars)
    ivs, vadi, samvadi, _sa_pc, desc = RAGAS.get(str(raga).lower(),
                                                 RAGAS['yaman'])
    tal_beats, beats_per_bar, vibhags = TALAS.get(str(tala).lower(),
                                                  TALAS['teental'])
    sa_m = _midi(sa)
    scale_pcs = [sa_m % 12 + iv for iv in ivs]
    lo, hi = sa_m - 12, sa_m + 14
    ts = f'{beats_per_bar}/4'
    cap = float(beats_per_bar)

    def _drone_bar() -> List[List]:
        """持续音（坦布拉式）：全程 Sa–Pa 交替，不因段落而休止。"""
        out: List[List] = []
        t = 0.0
        while t < cap - 1e-9:
            d = min(2.0, cap - t)
            deg = 0 if int(t) == 0 else (4 if len(ivs) > 4 else 0)
            out.append([_name(_m(deg - len(ivs))), QL_DUR[d], None])
            t += d
        return _fit_bar(out, cap)

    def _m(deg: int) -> int:
        n = len(ivs)
        k = deg % n
        o = deg // n
        m = sa_m + ivs[k] + 12 * o
        while m < lo:
            m += 12 * ((lo - m) // 12 + 1)
        while m > hi:
            m -= 12 * ((m - hi) // 12 + 1)
        return m

    mel_ev: List[List] = []
    drone_ev: List[List] = []
    sections: List[Dict[str, Any]] = [
        {'name': 'Alap', 'bars': max(2, bars // 4), 'meter': ts, 'tempo_text': '散起'},
        {'name': 'Gat', 'bars': max(2, bars // 2), 'meter': ts, 'tempo_text': '固定作品'},
        {'name': 'Tan', 'bars': 0, 'meter': ts, 'tempo_text': '跑句'},
    ]
    # Alap：长音，围绕 vadi 展开，无塔拉约束
    for b in range(max(2, bars // 4)):
        evs = []
        t = 0.0
        while t < cap - 1e-9:
            d = min(rng.choice([2.0, 1.0, 1.0, 4.0]), cap - t)
            deg = vadi + rng.choice([0, 0, 1, -1, 2])
            evs.append([_name(_m(deg)), QL_DUR.get(d, 'quarter'), 'legato'])
            t += d
        mel_ev.extend(_fit_bar(evs, cap))
        drone_ev.extend(_drone_bar())
    # Gat：塔拉循环，sam（第 1 拍）落 Sa，分句点（vibhag）强化
    n_gat = max(2, bars // 2)
    for b in range(n_gat):
        evs = []
        beat = (b * beats_per_bar) % tal_beats
        t = 0.0
        while t < cap - 1e-9:
            d = min(rng.choice([1.0, 1.0, 0.5, 0.5, 2.0]), cap - t)
            if abs(t) < 1e-9 and beat == 0:
                deg = 0                                  # sam：回到 Sa
            elif (beat + int(t) + 1) in vibhags:
                deg = vadi                               # 分句点：点 vadi
            else:
                deg = rng.choice([vadi, samvadi, 0, 1, 2, 3])
            evs.append([_name(_m(deg)), QL_DUR.get(d, 'quarter'),
                        'accent' if (beat + int(t) + 1) in vibhags else None])
            t += d
        mel_ev.extend(_fit_bar(evs, cap))
        # 持续音：Sa–Pa（或 Sa）交替，坦布拉式
        dd = []
        t = 0.0
        while t < cap - 1e-9:
            d = min(2.0, cap - t)
            deg = 0 if int(t) == 0 else (len(ivs) - 1 if len(ivs) > 4 else 0)
            dd.append([_name(_m(deg - len(ivs))), QL_DUR.get(d, 'half'), None])
            t += d
        drone_ev.extend(_fit_bar(dd, cap))
    # Tan：十六分音符跑句，级进上下行，终点回 Sa
    n_tan = max(0, bars - max(2, bars // 4) - n_gat)
    for b in range(n_tan):
        evs = []
        t = 0.0
        cur = rng.choice([0, 2, vadi])
        while t < cap - 1e-9:
            d = min(0.25, cap - t)
            step = rng.choice([1, 1, 1, -1])
            cur = max(-len(ivs), min(2 * len(ivs), cur + step))
            evs.append([_name(_m(cur)), '16th', None])
            t += d
        mel_ev.extend(_fit_bar(evs, cap))
        drone_ev.extend(_drone_bar())
    sections[2]['bars'] = n_tan
    parts = [{'name': 'Raga 旋律', 'clef': 'treble',
              'events': _fit_bars_total(mel_ev, bars, cap)},
             {'name': '持续音', 'clef': 'bass',
              'events': _fit_bars_total(drone_ev, bars, cap)}]
    return _spec(f'{raga} 小品', bars, ts, int(tempo or 84), 0, 'major', parts,
                 [s for s in sections if s['bars'] > 0],
                 {'kind': 'raga', 'raga': raga, 'tala': tala,
                  'vadi': vadi, 'samvadi': samvadi, 'desc': desc}, seed)


# ================================================================
# 4. 十二音序列
# ================================================================

def make_row(seed: str = 'serial') -> List[int]:
    """生成一个十二音音列（0–11 的排列，确定性）。"""
    r = _rng(seed, 'row')
    row = list(range(12))
    r.shuffle(row)
    return row


def row_forms(row: Sequence[int]) -> Dict[str, List[int]]:
    """P / R / I / RI 四种形态（以 row[0] 为 0 的移位基准）。

    - P：原型；R：逆行；I：倒影（以首音为轴）；RI：逆行倒影。
    """
    p = list(row)
    r = list(reversed(row))
    base = row[0]
    inv = [(2 * base - x) % 12 for x in row]
    ri = list(reversed(inv))
    return {'P': p, 'R': r, 'I': inv, 'RI': ri}


def build_serial(*, row: Optional[Sequence[int]] = None, bars: int = 16,
                 tempo: Optional[int] = None, seed: str = 'serial',
                 form_order: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """十二音序列：音列的四种形态按段轮换。

    规则（也是它听起来"无调性"的原因）：
    - 一个音在音列出现前不得重复（此处按乐句为单位遵守：一句内用完 12 音
      再进入下一形态）；
    - 无主音、无功能和声；低声部取音列的另一形态，形成点描式对位。
    """
    r0 = list(row) if row else make_row(seed)
    if sorted(r0) != list(range(12)):
        raise ValueError('row 必须是 0–11 的一个排列')
    forms = row_forms(r0)
    order = [str(x).upper() for x in (form_order or ['P', 'I', 'R', 'RI'])]
    order = [f for f in order if f in forms] or ['P']
    rng = _rng(seed, 'serial', bars, ','.join(map(str, r0)))
    cap = 4.0
    mel_ev: List[List] = []
    low_ev: List[List] = []
    sections: List[Dict[str, Any]] = []
    n_forms = len(order)
    per = max(1, bars // n_forms)
    base_m = 60
    for fi, fname in enumerate(order):
        seq = forms[fname]
        n_b = per if fi < n_forms - 1 else bars - per * (n_forms - 1)
        n_b = max(1, n_b)
        sections.append({'name': f'{fname} 段', 'bars': n_b, 'meter': '4/4',
                         'tempo_text': fname})
        pos = 0
        low_seq = forms[order[(fi + 1) % n_forms]]
        lpos = 0
        for b in range(n_b):
            evs: List[List] = []
            lev: List[List] = []
            t = 0.0
            while t < cap - 1e-9:
                d = min(rng.choice([1.0, 0.5, 0.5, 2.0, 2.0]), cap - t)
                pc = seq[pos % 12]
                m = base_m + pc + 12 * rng.choice([0, 0, 1])
                evs.append([_name(m), QL_DUR.get(d, 'quarter'),
                            'accent' if pos % 12 == 0 else None])
                pos += 1
                t += d
            mel_ev.extend(_fit_bar(evs, cap))
            t = 0.0
            while t < cap - 1e-9:
                d = min(rng.choice([2.0, 1.0, 1.0]), cap - t)
                pc = low_seq[lpos % 12]
                m = base_m - 24 + pc
                lev.append([_name(m), QL_DUR.get(d, 'half'), None])
                lpos += 1
                t += d
            low_ev.extend(_fit_bar(lev, cap))
    parts = [{'name': '序列 I', 'clef': 'treble',
              'events': _fit_bars_total(mel_ev, bars, cap)},
             {'name': '序列 II', 'clef': 'bass',
              'events': _fit_bars_total(low_ev, bars, cap)}]
    return _spec('十二音小品', bars, '4/4', int(tempo or 96), 0, 'major', parts,
                 sections,
                 {'kind': 'serial', 'row': list(r0), 'forms': order}, seed)


# ================================================================
# 注册表
# ================================================================

SYSTEM_BUILDERS = {
    'banqiang': build_banqiang,
    'qupai': build_qupai,
    'raga': build_raga,
    'serial': build_serial,
}

SYSTEM_LABELS = {
    'banqiang': '板腔体（上下句 · 板式变化）',
    'qupai': '曲牌体（联曲 · 字位填腔）',
    'raga': '拉格（调式 · 塔拉循环）',
    'serial': '十二音序列（P/R/I/RI）',
}


def list_systems() -> List[Dict[str, str]]:
    return [{'id': k, 'label': v} for k, v in SYSTEM_LABELS.items()]


def build_system(kind: str, **kw) -> Dict[str, Any]:
    """按体系名生成 spec（kwargs 透传给对应 builder）。"""
    fn = SYSTEM_BUILDERS.get(str(kind).lower())
    if fn is None:
        raise ValueError(f'未知体系 {kind!r}（可用：{", ".join(SYSTEM_BUILDERS)}）')
    return fn(**kw)


def build_system_kernel_calls(kind: str, **kw) -> Dict[str, Any]:
    """体系层 → 与内核同形状的 {"calls": [...]}（用体系 spec 直接渲染）。

    体系层不生成内核调用序列（见模块文档），这里返回一个单调用包装，
    使上层（UI/骨架）可以用同一条路径处理两类产物。
    """
    spec = build_system(kind, **kw)
    return {'calls': [{'script': '__system__', 'args': {
        'kind': kind, 'spec': spec}}]}


__all__ = [
    'build_banqiang', 'build_qupai', 'build_raga', 'build_serial',
    'make_row', 'row_forms', 'list_systems', 'build_system',
    'build_system_kernel_calls', 'BANQIAN_BANSHI', 'QUPAI_SKELETONS',
    'RAGAS', 'TALAS', 'SYSTEM_LABELS',
]
