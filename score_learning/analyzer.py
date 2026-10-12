# -*- coding: utf-8 -*-
"""Quantitative Score Analyzer — MusicXML/MIDI → Musical Feature Set.

Program measures; LLM must not invent these numbers.
Never store full pitch sequences as skill content.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


def _midi(n) -> Optional[int]:
    try:
        return int(n.pitch.midi)
    except Exception:
        return None


def _interval_semitones(a: int, b: int) -> int:
    return abs(b - a)


_PERCUSSION_WORDS = (
    'timpani', 'drum', 'perc', 'triangle', 'cymbal', 'tambourine',
    'snare', 'bass drum', 'woodblock', 'castanet', 'glocken',
)


def _collect_part_notes(part) -> List[int]:
    """取一个声部的音高（含和弦的最高音），跳过休止。"""
    from music21 import chord as m21chord
    out: List[int] = []
    for n in part.recurse().notes:
        if n.isNote:
            m = _midi(n)
            if m is not None:
                out.append(m)
        elif n.isChord and getattr(n, 'pitches', None):
            try:
                out.append(max(int(p.midi) for p in n.pitches))
            except Exception:
                pass
    return out


def _pick_melody_part(score):
    """选主旋律声部 —— 音区为主，密度/跨度仅在音区接近时决胜。

    历史演进（两个坑都踩过）：
    1. 最初只取「平均音高最高」的声部，会挑到总谱里的短笛/铃鼓这类
       高而稀疏的点缀声部。
    2. 改成 min-max 归一化后加权纠正，但又引入更隐蔽的回归：min-max 会把
       声部间**极微小**的差异拉伸到 0~1 全区间。实测巴赫众赞歌 bwv66.6 里
       密度仅 3.70 vs 4.50 notes/bar（差 0.8），却被拉成 0.0 与 1.0，
       密度权重被人为放大，结果把女高（正确的旋律声部）压成中音部。

    正确做法：绝对音区是主判据（因为「旋律在高音区」是强先验）；
    只有当若干声部平均音高接近（差值 < 2 半音）时，才用密度与跨度决胜。
    """
    cands = []
    for p in score.parts:
        # music21 的 part.id 不保证是字符串——写入 MusicXML 时若 part id 是
        # 纯数字，读回来就是 int，直接 .lower() 会崩。统一转成 str 再用。
        raw_name = getattr(p, 'partName', None) or getattr(p, 'id', '') or ''
        name = str(raw_name).lower()
        if any(x in name for x in _PERCUSSION_WORDS):
            continue
        midis = _collect_part_notes(p)
        if len(midis) < 4:
            continue
        n_meas = max(1, len(p.getElementsByClass('Measure')))
        cands.append((p, sum(midis) / len(midis), len(midis) / n_meas,
                      max(midis) - min(midis)))
    if not cands:
        return score.parts[0] if score.parts else None
    if len(cands) == 1:
        return cands[0][0]

    # 主判据：平均音高降序
    cands.sort(key=lambda c: -c[1])
    best = cands[0]
    # 音区接近时才用密度/跨度决胜（避免不分主次的整体加权）
    close = [c for c in cands if abs(c[1] - best[1]) < 2.0]
    if len(close) > 1:
        best = max(close, key=lambda c: (c[2], c[3]))
    return best[0]


def _ql_bucket(ql: float) -> str:
    if ql <= 0:
        return '0'
    pairs = [
        (0.25, '16th'), (1 / 3, 'triplet'), (0.5, '8th'), (0.75, 'dotted_8th'),
        (1.0, 'quarter'), (1.5, 'dotted_quarter'), (2.0, 'half'),
        (3.0, 'dotted_half'), (4.0, 'whole'),
    ]
    for target, name in pairs:
        if abs(ql - target) < 0.04:
            return name
    return f'ql={round(ql, 2)}'


def _beat_in_bar(offset_in_bar: float, beat_ql: float) -> int:
    if beat_ql <= 0:
        return 1
    return int(offset_in_bar / beat_ql) + 1


# ---------------------------------------------------------------- 曲式检测
def _bar_events(mel_part) -> Dict[int, List[Tuple[float, int]]]:
    """每小节的旋律事件 [(小节内起始位置, midi), ...]。"""
    out: Dict[int, List[Tuple[float, int]]] = {}
    if mel_part is None:
        return out
    for m in mel_part.getElementsByClass('Measure'):
        num = getattr(m, 'number', None)
        if num is None:
            continue
        evs: List[Tuple[float, int]] = []
        for n in m.recurse().notes:
            if n.isNote:
                mi = _midi(n)
                if mi is not None:
                    evs.append((round(float(n.getOffsetInHierarchy(m)), 4), mi))
            elif n.isChord and getattr(n, 'pitches', None):
                try:
                    evs.append((round(float(n.getOffsetInHierarchy(m)), 4),
                                max(int(p.midi) for p in n.pitches)))
                except Exception:
                    pass
        out[int(num)] = sorted(evs)
    return out


def _bar_signature(evs, prev_tail) -> tuple:
    """小节旋律签名：只用音程关系，忽略绝对音高 —— 移调后仍可比对。

    shape = (音符数, 进入音程, 内部音程轮廓)
    """
    if not evs:
        return ('R', 0, ())
    pitches = [m for _, m in evs]
    contour = tuple(pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1))
    entry = (pitches[0] - prev_tail) if prev_tail is not None else 0
    return (len(pitches), entry, contour)


def detect_form(mel_part, max_motifs: int = 26) -> Dict[str, Any]:
    """基于动机重复的曲式检测 —— 替代原先按小节数硬编码的假结构。

    旧实现是 `bars>=8 → A, >=16 → A2, >=24 → B`，结果 celtic-jig 273 首里
    有 224 首得到**完全相同**的 `[A:1-8, A2:9-16]`，而这个 form.count 还会
    直接进入风格指纹向量。那不是分析，是编造。

    这里只输出**可测量的事实**：动机数量、重复比、乐句长度。无法判断时
    返回空 form 并标记 method='no_clear_repetition'（宁缺毋滥）。
    """
    bars = _bar_events(mel_part)
    if len(bars) < 4:
        return {'form': [], 'method': 'too_short', 'unique_motifs': None,
                'repetition_ratio': None, 'phrase_len': None}
    order = sorted(bars)
    labels: Dict[Any, str] = {}
    seq: List[Tuple[int, str]] = []
    prev_tail: Optional[int] = None
    for b in order:
        sig = _bar_signature(bars[b], prev_tail)
        if sig not in labels:
            labels[sig] = chr(ord('A') + len(labels))
        seq.append((b, labels[sig]))
        if bars[b]:
            prev_tail = bars[b][-1][1]

    merged: List[List] = []
    for b, lab in seq:
        if merged and merged[-1][0] == lab:
            merged[-1][2] = b
        else:
            merged.append([lab, b, b])
    form = [{'section': lab, 'bars': (str(s) if s == e else f'{s}-{e}')}
            for lab, s, e in merged]

    n_motifs = len(labels)
    n_bars = len(order)
    repetition = round(1.0 - n_motifs / max(n_bars, 1), 3)
    # 判定标准必须是「真的检测到重复」，而不是动机数量没超上限。
    # 众赞歌这类通篇自由对位的作品，每小节动机都不同（repetition≈0），
    # 此时 A/B/C… 只是给小节编号，不是曲式 —— 应当诚实标记未检出。
    detected = (n_motifs <= max_motifs) and repetition >= 0.25
    method = 'motif_repetition' if detected else 'no_clear_repetition'
    if not detected:
        form = []
    return {
        'form': form,
        'method': method,
        'unique_motifs': n_motifs,
        'repetition_ratio': repetition,
        'phrase_len': round(n_bars / len(merged), 2) if merged else None,
    }


# ---------------------------------------------------------------- 和弦品质
# 谱面显式写出的 chordKind → 归一化品质名。
#
# 为什么必须走 chordKind：`Chord.quality` 只回答「三和弦层级的大/小/减/增」，
# 实测 C7 与 Cmaj7 都报 'major'，Cm7 与 Cm 都报 'minor'，Cdim7 与 Cm7b5 都报
# 'diminished'。也就是说**用 .quality 永远测不出七和弦占比**，而那恰恰是
# 区分钟士套与众赞歌唯一的量化指纹。chordKind 经 MusicXML 往返保真，
# 是和弦身份最可靠的事实来源。
_KIND_TO_QUALITY = {
    'major': 'triad',
    'minor': 'triad',
    'diminished': 'triad',
    'augmented': 'augmented',
    'dominant-seventh': 'dominant7',
    'major-seventh': 'major7',
    'minor-seventh': 'minor7',
    'diminished-seventh': 'diminished7',
    'half-diminished-seventh': 'half_diminished7',
    'dominant-ninth': 'dominant7',
    'major-ninth': 'major7',
    'minor-ninth': 'minor7',
    # 六和弦在我们的契约里没有对应（内核不支持 6 后缀）：如实记为 other，
    # 而不是硬塞进某个「能用得上」的类别里假装学到了
    'major-sixth': 'other',
    'minor-sixth': 'other',
}


def _quality_from_kind(kind: str, fallback_quality: str = '') -> str:
    return _KIND_TO_QUALITY.get(
        str(kind or '').strip().lower(),
        normalize_chord_quality(fallback_quality) if fallback_quality else 'other',
    )


def normalize_chord_quality(raw: str) -> str:
    """把 music21 quality 归一到风格契约的品质名（见 style_contract.QUALITY_KEYS）。

    只记录 root().name 是学不到爵士的 —— 爵士的灵魂在七和弦与延伸音，
    而 `C major` 与 `C dominant-seventh` 的 root 都是 'C'。
    """
    try:
        from style_contract import normalize_quality
        return normalize_quality(raw)
    except Exception:
        pass
    k = str(raw or '').strip().lower().replace('_', '-').replace(' ', '-')
    if 'half-diminished' in k:
        return 'half_diminished7'
    if 'diminished-seventh' in k:
        return 'diminished7'
    if 'major-seventh' in k:
        return 'major7'
    if 'minor-seventh' in k:
        return 'minor7'
    if 'dominant' in k:
        return 'dominant7'
    if 'augmented' in k:
        return 'augmented'
    if k in ('major', 'minor', 'diminished'):
        return 'triad'
    return 'other'


# ---------------------------------------------------------------- 调性归一
_MAJOR_DEGREES = frozenset({0, 2, 4, 5, 7, 9, 11})
_MINOR_DEGREES = frozenset({0, 2, 3, 5, 7, 8, 10})


def degree_stats(midis: List[int], tonic_pc: Optional[int],
                 mode: str = 'major') -> Optional[Dict[str, Any]]:
    """把音高转到「相对主音的音级」后再统计 —— 跨曲可比的正确做法。

    直接统计 MIDI 音高或 pitch-class 是无效统计量：C 大调的众赞歌和
    F# 大调的众赞歌会得到完全不同的分布，但它们其实是同一种风格。
    转到相对主音后，两者才落在同一坐标系里。
    """
    if tonic_pc is None or not midis:
        return None
    hist = Counter(((m - int(tonic_pc)) % 12) for m in midis)
    total = sum(hist.values()) or 1
    allow_set = _MINOR_DEGREES if str(mode).startswith('minor') else _MAJOR_DEGREES
    chromatic = sum(c for d, c in hist.items() if d not in allow_set)
    return {
        'tonic_pc': int(tonic_pc),
        'degree_hist': {str(d): round(hist.get(d, 0) / total, 4)
                        for d in range(12)},
        'chromatic_ratio': round(chromatic / total, 4),
        'leading_tone_ratio': round(hist.get(11, 0) / total, 4),
    }


def _tonic_pc(key_name: Optional[str]) -> Optional[int]:
    """'C major' → 0。key 无效时返回 None（宁缺毋滥，不猜 C=0）。"""
    if not key_name:
        return None
    tok = str(key_name).split()[0] if str(key_name).split() else ''
    if not tok:
        return None
    try:
        from music21 import pitch as m21pitch
        return int(m21pitch.Pitch(tok).pitchClass)
    except Exception:
        return None


def _swing_and_triplet(mel_part) -> Dict[str, Optional[float]]:
    """律动特征：八分音符对的长短比 + 三连音占比。

    符号层面常常测不出 swing（大多记为等分八分靠演奏习惯），但若曲子真的
    写了不等分八分或 shuffle 记谱，这里能量出来；两者都为空表示记谱平直。

    本函数重写过一次，因为旧实现会把**古典音乐的附点音型误判成 swing**：
    实测海顿《第 99 交响曲》第一乐章得到 swing_ratio=2.73 —— 而 2.73 正是
    附点八分(0.75)+十六分(0.25) 的时值比。旧实现的两个漏洞：

    1. **没有「八分音域」约束**：任何两个时值之和接近一拍的音符都被当成
       一对八分音符。附点八分+十六分、前八后十六、后十六都是典型古典音型，
       却同样满足 sum≈1.0 且长短悬殊。
    2. **没有相邻性校验**：直接按出现顺序取相邻两个音符，中间隔着一整个
       休止符也算「成对」。

    修正：要求两个音符**首尾相接**（不间断），且两者都落在八分音符区间
    [0.30, 0.80] 内。附点八分虽在区间内，但它的搭档十六分(0.25)不在，
    因而被正确排除；真正的 triple-feel 记谱（0.667 + 0.333）则保留。

    另外补出 swing_pair_share —— 只给「比值」是不够的：一首曲子里偶尔出现
    两三个不等分音型就让比值成立，会让统计看起来像整曲都在 swing。
    「多少比例的八分音符对是不等分的」才是可靠的判别量。
    """
    if mel_part is None:
        return {'swing_ratio': None, 'triplet_ratio': None,
                'swing_pair_share': None}

    # 八分音符区间的上下界（四分音符=1.0 的单位下）
    _EIGHTH_LO, _EIGHTH_HI = 0.30, 0.80

    events: List[Tuple[float, float]] = []   # [(onset, duration), ...]
    triplet_notes = 0
    total_notes = 0
    for n in mel_part.recurse().notes:
        try:
            ql = float(n.quarterLength)
            off = float(n.getOffsetInHierarchy(mel_part))
        except Exception:
            continue
        if ql <= 0:
            continue
        total_notes += 1
        events.append((off, ql))
        # 三连音：music21 通常记为 2/3 或 1/3 的近似值
        if abs(ql - 2 / 3) < 0.02 or abs(ql - 1 / 3) < 0.02:
            triplet_notes += 1

    out: Dict[str, Optional[float]] = {
        'swing_ratio': None, 'triplet_ratio': None, 'swing_pair_share': None}
    if not events or total_notes == 0:
        return out
    out['triplet_ratio'] = round(triplet_notes / total_notes, 4)

    events.sort(key=lambda e: e[0])
    eighth_pairs = 0
    ratios: List[float] = []
    for i in range(len(events) - 1):
        off_a, dur_a = events[i]
        off_b, dur_b = events[i + 1]
        # 首尾相接：b 的起点就是 a 的落点（容差吸收浮点误差）
        if abs(off_b - (off_a + dur_a)) > 1e-3:
            continue
        # 两者都得是「八分音符家族」，否则可能是附点/十六分音型
        if not (_EIGHTH_LO <= dur_a <= _EIGHTH_HI
                and _EIGHTH_LO <= dur_b <= _EIGHTH_HI):
            continue
        # 一对「不等分八分」：总和约等于一个四分
        if not (0.90 < dur_a + dur_b < 1.10):
            continue
        eighth_pairs += 1
        if abs(dur_a - dur_b) > 0.08:
            ratios.append(max(dur_a, dur_b) / min(dur_a, dur_b))

    if eighth_pairs:
        out['swing_pair_share'] = round(len(ratios) / eighth_pairs, 4)
    # 样本太少（整段只出现一两次）不足以断言「这首曲子在 swing」
    if len(ratios) >= 4:
        out['swing_ratio'] = round(sum(ratios) / len(ratios), 3)
    return out


_SEVENTH_KEYS = (
    'dominant7', 'major7', 'minor7', 'diminished7', 'half_diminished7')


def _common_name_quality(ch) -> str:
    """由 music21 的 commonName 得到品质 —— 唯一能区分 C7 / Cmaj7 的通用途径。

    `Chord.commonName` 会给出 'dominant seventh chord'、'major seventh chord'、
    'minor seventh chord'、'half-diminished seventh chord'、'diminished seventh
    chord' 这些真正区分七和弦的名字，不像 `.quality` 那样统统塌成 'major'。

    已知局限：在十二平均律的 pitch-class 空间里，C6（C-E-G-A）与 Cm7
    （C-E-G-B♭）是同一个集合，commonName 会报 'minor seventh chord'。
    六和弦因此被算成小七 —— 接受这一妥协，因为比起「测量值恒为 0」，
    这个偏差要小得多。谱面写明了 chordKind 时我们优先用 chordKind，
    不受此影响（见 _harmony_from_written_symbols）。
    """
    try:
        name = str(ch.commonName or '').strip().lower()
    except Exception:
        return 'other'
    if not name:
        return 'other'
    if 'half-diminished' in name:
        return 'half_diminished7'
    if 'diminished seventh' in name:
        return 'diminished7'
    if 'major seventh' in name or 'major-ninth' in name:
        return 'major7'
    if 'minor seventh' in name or 'minor-ninth' in name:
        return 'minor7'
    if 'dominant seventh' in name or 'dominant-ninth' in name:
        return 'dominant7'
    if 'augmented' in name:
        return 'augmented'
    if 'diminished' in name or 'minor' in name or 'major' in name:
        return 'triad'
    return 'other'


def _harmony_summary(roots: List[str], qual_counter: Counter,
                     bars: int, source: str) -> Dict[str, Any]:
    """把「根音序列 + 品质计数」整理成统一的和声特征 dict。

    两个和声来源（谱面符号 / chordify 推断）共用这一段，避免两边各自的
    统计口径悄悄跑偏 —— 以前就是这样的一致性风险。
    """
    prog: List[str] = []
    for r in roots:
        if not prog or prog[-1] != r:
            prog.append(r)
    n_q = sum(qual_counter.values()) or 1
    quality_hist = {k: round(v / n_q, 4) for k, v in qual_counter.items()}
    seventh_ratio = round(
        sum(qual_counter.get(k, 0) for k in _SEVENTH_KEYS) / n_q, 4)
    pairs = [f'{prog[i]}-{prog[i + 1]}' for i in range(len(prog) - 1)]
    return {
        'available': True,
        'source': source,
        'unique_roots': len(set(prog)),
        'chords_estimated': len(roots),
        'harmonic_rhythm_chords_per_bar': round(len(roots) / max(bars, 1), 2),
        'root_pair_top': dict(Counter(pairs).most_common(8)),
        'root_sample': prog[:24],
        # ↓ 体裁辨识的关键：只看 root 的话 C 与 C7 无法区分
        'quality_hist': quality_hist,
        'seventh_ratio': seventh_ratio,
    }


def _harmony_from_written_symbols(score, bars: int) -> Optional[Dict[str, Any]]:
    """读取谱面**已写出**的和弦符号。返回 None 表示这份谱没有和声标记。

    单声部的爵士 lead sheet 走 chordify() 是解不出和声的：旋律一次只响
    一个音，chordify 只会得到一堆「只有一个音」的伪和弦，再被退化过滤统统
    丢掉，于是 quality_hist 全空、七和弦占比恒为 0 —— 而「七和弦多不多」
    恰恰是爵士与众赞歌唯一的量化分野。已有的和声标记才是事实来源。
    """
    try:
        from music21 import harmony as m21harmony
    except Exception:
        return None
    roots: List[str] = []
    qual_counter: Counter = Counter()
    for cs in score.recurse().getElementsByClass('ChordSymbol'):
        try:
            root = cs.root().name
        except Exception:
            continue
        # 谱面写明 identity → 信 identity；不明则退回 commonName（七和弦感知）
        kind = str(getattr(cs, 'chordKind', '') or '').strip()
        if kind:
            q = _quality_from_kind(kind)
        else:
            q = normalize_chord_quality(_common_name_quality(cs))
        roots.append(root)
        qual_counter[q] += 1
    if not roots:
        return None
    return _harmony_summary(roots, qual_counter, bars,
                            'written_chord_symbols')


def _harmony_from_chordify(score, bars: int) -> Dict[str, Any]:
    """没有和声标记时的回退：从实际声部叠置推断和弦。"""
    try:
        ch = score.chordify()
        roots: List[str] = []
        valid = []
        for c in ch.recurse().getElementsByClass('Chord'):
            # 过滤退化的「伪和弦」——chordify 在密集伴奏（如拉格泰姆的 oom-pah
            # 左手、爵士的 walking bass）上会把同时发声的重复单音聚成 Chord，
            # 得到形如 Eb+['Eb','Eb'] 的二音伪和弦。实测 Joplin《枫叶拉格》里
            # 这类伪和弦占 34.9%，被记成 'other' 后把真正的品质分布冲淡了。
            # 少于 3 个不同音级不构成和弦，不计入和声统计。
            try:
                pcs = {p.pitchClass for p in c.pitches}
            except Exception:
                continue
            if len(pcs) < 3:
                continue
            try:
                root = c.root().name
            except Exception:
                continue
            # chordify 出来的 Chord 没有谱面身份，只能用 commonName 推断品质
            q = _common_name_quality(c)
            # roots 与 qual_counter 必须来自同一批有效和弦，否则两路口径不一致
            roots.append(root)
            valid.append(q)
        if not roots:
            return {'available': False, 'error': 'no valid chords'}
        return _harmony_summary(roots, Counter(valid), bars, 'chordify')
    except Exception as e:
        return {'available': False, 'error': str(e)[:120]}


def analyze_m21(score, source: str = '', genre_hint: str = '') -> Dict[str, Any]:
    from music21 import chord as m21chord
    from music21 import key as m21key
    from music21 import meter as m21meter
    from music21 import note as m21note
    from music21 import tempo as m21tempo

    md = getattr(score, 'metadata', None)
    # Opus（多乐章/多曲容器）没有 .parts，统一在入口降为单个 Score
    if hasattr(score, 'getScores'):
        score = _coerce_to_score(score)
        if score is None:
            return {
                'metadata': {'title': 'Untitled', 'composer': '',
                             'genre_hint': genre_hint, 'source': source,
                             'error': 'empty Opus / no scores'},
                'global': {}, 'form': [], 'form_metrics': {},
                'melody': {}, 'tonal': None, 'groove': {}, 'rhythm': {},
                'harmony': {'available': False}, 'texture': {},
            }
    if md is None:
        md = getattr(score, 'metadata', None)
    if md is None:
        class _M:                      # minimal shim
            bestTitle = None
            title = None
            movementName = None
            composer = ''
        md = _M()
    title = 'Untitled'
    composer = ''
    for cand in (
        getattr(md, 'bestTitle', None), getattr(md, 'title', None),
        getattr(md, 'movementName', None),
    ):
        if cand and str(cand).strip():
            title = str(cand).strip()
            break
    composer = str(getattr(md, 'composer', None) or '')

    bars = 0
    for p in score.parts:
        bars = max(bars, len(p.getElementsByClass('Measure')))

    ts_obj = score.recurse().getElementsByClass(m21meter.TimeSignature).first()
    meter_str = ts_obj.ratioString if ts_obj else '4/4'
    try:
        num, den = meter_str.split('/')
        beat_ql = 4.0 / float(den)
        beats_per_bar = int(num)
        bar_ql = beats_per_bar * beat_ql
    except Exception:
        beat_ql, beats_per_bar, bar_ql = 1.0, 4, 4.0

    tempo_n = None
    mm = score.recurse().getElementsByClass(m21tempo.MetronomeMark).first()
    if mm is not None and getattr(mm, 'number', None):
        tempo_n = int(round(float(mm.number)))

    key_name = None
    sharps = None
    ks = score.recurse().getElementsByClass(m21key.KeySignature).first()
    if ks is not None:
        sharps = int(ks.sharps)
    try:
        k = score.analyze('key')
        key_name = f'{k.tonic.name} {k.mode}'
        if sharps is None:
            sharps = int(k.sharps)
    except Exception:
        if sharps is not None:
            key_name = f'sharps={sharps}'

    parts_meta = [p.partName or p.id for p in score.parts]

    # ---- melody stats ----
    mel_part = _pick_melody_part(score)
    mel_midis: List[int] = []
    mel_intervals: List[int] = []
    mel_qls: List[float] = []
    if mel_part is not None:
        prev = None
        for n in mel_part.recurse().notes:
            if not n.isNote:
                continue
            m = _midi(n)
            if m is None:
                continue
            mel_midis.append(m)
            mel_qls.append(float(n.quarterLength))
            if prev is not None:
                mel_intervals.append(_interval_semitones(prev, m))
            prev = m

    stepwise = sum(1 for i in mel_intervals if i in (1, 2))
    leaps = sum(1 for i in mel_intervals if i >= 5)
    large = sum(1 for i in mel_intervals if i >= 12)
    n_int = len(mel_intervals) or 1
    repeated = 0
    for i in range(1, len(mel_midis)):
        if mel_midis[i] == mel_midis[i - 1]:
            repeated += 1
    n_notes = len(mel_midis) or 1

    def midi_name(m: int) -> str:
        from music21 import pitch
        return pitch.Pitch(midi=m).nameWithOctave

    melody = {
        'part': mel_part.partName if mel_part is not None else None,
        'range': (
            [midi_name(min(mel_midis)), midi_name(max(mel_midis))]
            if mel_midis else None
        ),
        'mean_midi': round(sum(mel_midis) / n_notes, 2) if mel_midis else None,
        'stepwise_ratio': round(stepwise / n_int, 3),
        'leap_ratio': round(leaps / n_int, 3),
        'large_leap_ratio': round(large / n_int, 3),
        'repeated_note_ratio': round(repeated / max(n_notes - 1, 1), 3),
        'interval_hist': dict(Counter(mel_intervals).most_common(12)),
        'dur_hist': dict(Counter(_ql_bucket(q) for q in mel_qls)),
    }

    # ---- rhythm / beat emphasis (all parts) ----
    onset_beats: Counter = Counter()
    rest_ql = 0.0
    sound_ql = 0.0
    note_events = 0
    bass_beat1_bars = 0
    sounding_bars_checked = 0

    # bass: lowest part by mean pitch
    bass_part = None
    lowest_mean = 999
    for p in score.parts:
        midis = [_midi(n) for n in p.recurse().notes if n.isNote]
        midis = [m for m in midis if m is not None]
        if not midis:
            continue
        mean = sum(midis) / len(midis)
        if mean < lowest_mean:
            lowest_mean = mean
            bass_part = p

    for p in score.parts:
        for m in p.getElementsByClass('Measure'):
            if m.number is None:
                continue
            for el in m.notesAndRests:
                off = float(el.offset)
                beat = _beat_in_bar(off, beat_ql)
                beat = max(1, min(beats_per_bar, beat))
                if el.isRest:
                    rest_ql += float(el.quarterLength)
                    continue
                sound_ql += float(el.quarterLength)
                note_events += 1
                onset_beats[beat] += 1

    if bass_part is not None and meter_str.startswith('3/'):
        for m in bass_part.getElementsByClass('Measure'):
            if m.number is None:
                continue
            notes = [el for el in m.notes if el.isNote or el.isChord]
            if not notes:
                continue
            sounding_bars_checked += 1
            first = min(notes, key=lambda e: float(e.offset))
            if float(first.offset) < beat_ql * 0.5:
                bass_beat1_bars += 1

    total_onsets = sum(onset_beats.values()) or 1
    syncop_approx = 0.0
    if beats_per_bar >= 3:
        # weak beats 2..n share of onsets — high on beat1 is dance-like
        weak = sum(onset_beats[b] for b in range(2, beats_per_bar + 1))
        syncop_approx = round(weak / total_onsets, 3)

    rhythm = {
        'dur_hist_global': dict(Counter(
            _ql_bucket(float(n.quarterLength))
            for n in score.recurse().notes
        ).most_common(16)),
        'onset_beat_share': {
            str(b): round(onset_beats[b] / total_onsets, 3)
            for b in range(1, beats_per_bar + 1)
        },
        'beat1_onset_share': round(onset_beats[1] / total_onsets, 3),
        'syncopation_proxy': syncop_approx,
        'rest_ratio': round(rest_ql / max(rest_ql + sound_ql, 1e-6), 3),
        'density_notes_per_bar': round(note_events / max(bars, 1), 2),
        'bass_beat1_ratio': (
            round(bass_beat1_bars / sounding_bars_checked, 3)
            if sounding_bars_checked else None
        ),
    }

    # ---- harmony (optional) ----
    # 先看有没有谱面上写死的和声标记，没有才回退到 chordify 推断。
    harmony = _harmony_from_written_symbols(score, bars)
    if harmony is None:
        harmony = _harmony_from_chordify(score, bars)

    # ---- form: 真实动机重复检测（旧实现是按小节数硬编码，纯属编造）----
    form_info = detect_form(mel_part)
    form = form_info['form']

    groovefeat = _swing_and_triplet(mel_part)
    return {
        'metadata': {
            'title': title,
            'composer': composer,
            'genre_hint': genre_hint,
            'source': source,
        },
        'global': {
            'key': key_name,
            'key_sharps': sharps,
            'meter': meter_str,
            'tempo': tempo_n,
            'bars': bars,
            'parts': parts_meta,
        },
        'form': form,
        # 旧字段只有 form 列表；这里补出可测量的统计量，供风格契约使用
        'form_metrics': {
            'method': form_info['method'],
            'unique_motifs': form_info['unique_motifs'],
            'repetition_ratio': form_info['repetition_ratio'],
            'phrase_len': form_info['phrase_len'],
        },
        'melody': melody,
        'tonal': degree_stats(mel_midis, _tonic_pc(key_name),
                              (key_name or '').split()[-1] if key_name else 'major'),
        'groove': groovefeat,
        'rhythm': rhythm,
        'harmony': harmony,
        'texture': {
            'voice_count': len(score.parts),
            'bass_part': bass_part.partName if bass_part else None,
        },
    }


def _coerce_to_score(obj):
    """把 parse 结果规整成一个 Score —— 兼容 Opus（多曲/多乐章容器）。

    music21 对多乐章 MIDI、多首 ABC 谱集会返回 Opus，它没有 .parts，
    直接喂进分析会抛 AttributeError。这里取其中音符最多的那一首作为
    代表，避免把整本谱集硬当成一首曲子。
    """
    if obj is None:
        return None
    if hasattr(obj, 'getScores'):
        subs = []
        try:
            subs = list(obj.getScores())
        except Exception:
            subs = []
        if not subs:
            return None
        best, best_n = subs[0], -1
        for s in subs:
            try:
                n = len(list(s.recurse().notes))
            except Exception:
                n = 0
            if n > best_n:
                best, best_n = s, n
        return best
    return obj


def analyze_path(path: str, genre_hint: str = '') -> Dict[str, Any]:
    from music21 import converter
    obj = converter.parse(path)
    score = _coerce_to_score(obj)
    if score is None:
        raise ValueError(f'无法解析或不含音符：{path}')
    return analyze_m21(score, source=os.path.abspath(path), genre_hint=genre_hint)
