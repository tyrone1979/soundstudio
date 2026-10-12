# -*- coding: utf-8 -*-
"""语料分析：每首曲子 → 调性、拍号、弱起、逐小节的和弦与旋律（调内坐标）。

只保留统计要用的特征（和弦 = 根音相对主音的半音数 + 性质，旋律 = 起拍位置、时值、音高），
不记忆任何曲子的音符序列本身。
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CORPUS = os.path.join(ROOT, 'scores', 'corpus')
CACHE_DIR = os.path.join(ROOT, 'runs', 'grammar_cache')
ANALYZER_VERSION = '8'
GRID = 0.25            # 起拍量化到十六分音符（谱面层没有三连音）

STYLES: Dict[str, Dict[str, Any]] = {
    'folk': {'name': '英伦/凯尔特民间舞曲', 'source': 'Nottingham Music Database（ABC，带和弦标记）',
             'pack': ('folk', 'british-folk-tune'), 'melody': 'violin',
             'subs': {'jig': '吉格（6/8）', 'reel': '里尔（4/4）', 'hornpipe': '角笛舞曲',
                      'waltz': '华尔兹（3/4）'}},
    'jazz': {'name': '爵士即兴独奏', 'source': 'Weimar Jazz Database（ODbL）',
             'pack': ('jazz', 'swing-standard'), 'melody': 'trumpet',
             'subs': {'swing': '摇摆乐', 'bebop': '比波普', 'cool': '酷爵士', 'hardbop': '硬波普',
                      'postbop': '后波普', 'traditional': '传统爵士'}},
    'pop': {'name': '流行歌', 'source': 'CoCoPops / McGill Billboard（仅提取统计量）',
            'pack': ('pop', 'pop-standard'), 'melody': 'voice', 'subs': {'pop': '流行歌'}},
    'madrigal': {'name': '蒙特威尔第牧歌', 'source': 'music21 语料（第 3–5 卷，附罗马数字分析）',
                 'pack': ('vocal', 'madrigal'), 'melody': 'soprano',
                 'subs': {'madrigal': '牧歌（最高声部 + 和声）'}},
    # 以下语料没有和声标注：逐拍从所有声部识别和弦（auto_harmony），子风格按拍号分
    'sonata': {'name': '古典钢琴奏鸣曲',
               'source': 'KernScores 贝多芬 / 莫扎特 / 海顿钢琴奏鸣曲（和声由分析器逐拍识别）',
               'pack': ('classical', 'piano-sonata'), 'melody': 'piano', 'texture': 'piano',
               'auto_harmony': True,
               'subs': {'allegro': '快板乐章（4/4）', 'minuet': '小步舞曲 / 谐谑曲（3/4）',
                        'duple': '二拍子乐章（2/4）', 'compound': '复拍子乐章（6/8）'}},
    'strings': {'name': '古典弦乐四重奏',
                'source': 'music21 贝多芬 / 莫扎特 / 海顿弦乐四重奏（和声由分析器逐拍识别）',
                'pack': ('classical', 'string-quartet'), 'melody': 'violin', 'texture': 'quartet',
                'auto_harmony': True,
                'subs': {'allegro': '快板乐章（4/4）', 'minuet': '小步舞曲 / 谐谑曲（3/4）',
                         'duple': '二拍子乐章（2/4）', 'compound': '复拍子乐章（6/8）'}},
    'symphony': {'name': '古典交响曲',
                 'source': 'KernScores 海顿《伦敦交响曲》+ CCARH 贝多芬九部交响曲（MuseData）'
                           ' + S3（莫扎特第 41 / 贝多芬第 9）'
                           '（和声由分析器逐拍识别）',
                 'pack': ('classical', 'symphony'), 'melody': 'violin', 'texture': 'orchestra',
                 'auto_harmony': True,
                 'subs': {'allegro': '快板乐章（4/4）', 'minuet': '小步舞曲（3/4）',
                          'duple': '二拍子乐章（2/4）', 'compound': '复拍子乐章（6/8）'}},
    'chopin': {'name': '肖邦钢琴小品',
               'source': 'KernScores 肖邦玛祖卡 / 前奏曲（和声由分析器逐拍识别）',
               'pack': ('romantic', 'chopin-piano'), 'melody': 'piano', 'texture': 'piano',
               'auto_harmony': True,
               'subs': {'mazurka': '玛祖卡（3/4）', 'prelude': '前奏曲'}},
}
_METER_SUB = {'4/4': 'allegro', '3/4': 'minuet', '2/4': 'duple', '6/8': 'compound'}
# 维特比换和弦代价（按拍的强弱；得分已归一到「最重的音 = 1」）。在牧歌人工罗马数字标注上
# 校准：根音逐拍一致率约 72%，每小节和弦数 1.89（人工 1.84）
SWITCH_COST = {'S': 1.0, 'M': 2.0, 'W': 2.0}
# 自动识别的和弦性质并类：识别噪声下细分性质只会稀释统计；保留属七、减七两个功能性七和弦
QUALITY_FOLD = {'M7': 'M', 'm7': 'm', 'h7': 'd', 'A': 'M'}
# 识别窗口（拍数）：4/4 快板按半小节，其余按拍
HARM_WINDOW = {'4/4': 2}
_STRING_KEYS = ('violin', 'viola', 'violoncello', 'cello', 'basso', 'contrabass', 'bass')

_FOLK_DIRS = {'jigs': 'jig', 'hpps': 'hornpipe', 'waltzes': 'waltz'}
_JAZZ_SUBS = ('swing', 'bebop', 'cool', 'hardbop', 'postbop', 'traditional')

# music21 ChordSymbol.chordKind → 和弦性质（与 grammar.theory.QUALITIES 一致）
_KIND_Q = {
    'major': 'M', 'major-sixth': 'M', 'power': 'M', 'suspended-second': 'M',
    'suspended-fourth': 'M', 'pedal': 'M',
    'minor': 'm', 'minor-sixth': 'm', 'minor-major-seventh': 'm',
    'dominant': '7', 'dominant-seventh': '7', 'dominant-ninth': '7', 'dominant-11th': '7',
    'dominant-13th': '7', 'seventh-flat-five': '7', 'suspended-fourth-seventh': '7',
    'augmented-seventh': '7',
    'major-seventh': 'M7', 'major-ninth': 'M7', 'major-11th': 'M7', 'major-13th': 'M7',
    'minor-seventh': 'm7', 'minor-ninth': 'm7', 'minor-11th': 'm7', 'minor-13th': 'm7',
    'half-diminished': 'h7', 'half-diminished-seventh': 'h7',
    'diminished': 'd', 'diminished-seventh': 'o7', 'augmented': 'A',
}
_MODE_OF = {'major': 'major', 'ionian': 'major', 'mixolydian': 'major', 'lydian': 'major',
            'minor': 'minor', 'aeolian': 'minor', 'dorian': 'minor', 'phrygian': 'minor'}


def style_files(style: str) -> List[Tuple[str, str]]:
    """[(文件路径, 子风格)]。"""
    out: List[Tuple[str, str]] = []
    if style == 'folk':
        for d in sorted(glob.glob(os.path.join(CORPUS, 'nottingham', '*'))):
            base = os.path.basename(d)
            sub = 'reel' if base.startswith('reels') else _FOLK_DIRS.get(base)
            if sub:
                out += [(f, sub) for f in sorted(glob.glob(os.path.join(d, '*.abc')))]
    elif style == 'jazz':
        for sub in _JAZZ_SUBS:
            out += [(f, sub) for f in
                    sorted(glob.glob(os.path.join(CORPUS, 'weimar-jazz', sub, '*.musicxml')))]
    elif style == 'pop':
        out = [(f, 'pop') for f in
               sorted(glob.glob(os.path.join(CORPUS, 'cocopops', 'Billboard', '*.musicxml')))]
    elif style == 'madrigal':
        from music21 import corpus
        for p in sorted(str(x) for x in corpus.getCorePaths()):
            if '/monteverdi/' in p and p.endswith('.rntxt') and os.path.isfile(p[:-6] + '.mxl'):
                out.append((p, 'madrigal'))
    elif style == 'sonata':
        for comp in ('beethoven', 'mozart', 'haydn'):
            out += [(f, '') for f in sorted(glob.glob(os.path.join(
                CORPUS, 'classical-kern', f'{comp}_piano_sonatas', '*.krn')))]
    elif style == 'strings':
        from music21 import corpus
        paths = sorted(str(x) for x in corpus.getCorePaths())
        for p in paths:
            if not any(f'/{c}/' in p for c in ('beethoven', 'mozart', 'haydn')):
                continue
            if p.endswith('.krn') and p[:-4] + '.mxl' in paths:
                continue                     # 同一乐章的 kern 与 mxl 只取一个
            if p.endswith(('.mxl', '.krn', '.xml')) and 'k545' not in p:   # k545 是钢琴奏鸣曲
                out.append((p, ''))
    elif style == 'symphony':
        out = [(f, '') for f in sorted(glob.glob(os.path.join(
            CORPUS, 'haydn-symphony', '*', '*.krn')))]
        # CCARH 贝多芬九部（MuseData .md2）
        out += [(f, '') for f in sorted(glob.glob(os.path.join(
            CORPUS, 'beethoven-symphony', '*.md2')))]
        # S3：莫扎特 / 贝多芬（及同库古典–浪漫交响；学习时按拍号进 sub）
        for f in sorted(glob.glob(os.path.join(CORPUS, 'mctl-symphony', '*.musicxml'))):
            out.append((f, ''))
        for f in sorted(glob.glob(os.path.join(CORPUS, 'mctl-symphony', '*.xml'))):
            out.append((f, ''))
    elif style == 'chopin':
        out = [(f, 'mazurka') for f in sorted(glob.glob(os.path.join(CORPUS, 'chopin-mazurkas', '*.krn')))]
        out += [(f, 'prelude') for f in sorted(glob.glob(os.path.join(CORPUS, 'chopin-preludes', '*.krn')))]
    else:
        raise ValueError(f'未知风格 {style!r}，可选：{", ".join(STYLES)}')
    return out


def chord_quality(cs) -> Optional[str]:
    kind = str(getattr(cs, 'chordKind', '') or '')
    if kind in ('', 'none') or type(cs).__name__ == 'NoChord':
        return None
    return _KIND_Q.get(kind)


def roman_quality(rn) -> Optional[str]:
    if rn.isDominantSeventh():
        return '7'
    if rn.isHalfDiminishedSeventh():
        return 'h7'
    if rn.isDiminishedSeventh():
        return 'o7'
    if rn.isSeventh():
        third = (rn.third.pitchClass - rn.root().pitchClass) % 12 if rn.third else 4
        sev = (rn.seventh.pitchClass - rn.root().pitchClass) % 12 if rn.seventh else 10
        return 'M7' if (third, sev) == (4, 11) else 'm7' if third == 3 else '7'
    if rn.isMajorTriad():
        return 'M'
    if rn.isMinorTriad():
        return 'm'
    if rn.isDiminishedTriad():
        return 'd'
    if rn.isAugmentedTriad():
        return 'A'
    if rn.root() is not None and rn.third is None:          # V[no3] 之类
        return 'M'
    return None


def meter_info(ts_str: str) -> Tuple[float, float, int]:
    """拍号 → (每小节四分音符数, 每拍四分音符数, 每小节拍数)。复合拍子按附点四分算一拍。"""
    num, den = (int(x) for x in ts_str.split('/'))
    bar_ql = 4.0 * num / den
    beat_ql = 1.5 if den == 8 and num % 3 == 0 else 1.0
    return bar_ql, beat_ql, int(round(bar_ql / beat_ql))


def strength(on: float, bar_ql: float, beat_ql: float) -> str:
    """小节内位置的强弱：S 强拍（第 1 拍及偶数拍子的中间拍）/ M 其余拍首 / W 拍内弱位置。"""
    beats = int(round(bar_ql / beat_ql))
    if abs(on) < 1e-6 or (beats % 2 == 0 and beats >= 2 and abs(on - bar_ql / 2) < 1e-6):
        return 'S'
    return 'M' if abs(on / beat_ql - round(on / beat_ql)) < 1e-6 else 'W'


# ------------------------------------------------------------------ 调性
def _chord_key_score(chords: List[Tuple[int, str, float]], tonic: int, mode: str,
                     ends: bool = True) -> float:
    scale = (0, 2, 4, 5, 7, 9, 11) if mode == 'major' else (0, 2, 3, 5, 7, 8, 10, 11)
    tq = ('M', 'M7') if mode == 'major' else ('m', 'm7')
    from score_learning.grammar.theory import QUALITIES
    sc = 0.0
    for root, q, dur in chords:
        rel = (root - tonic) % 12
        pcs = {(rel + i) % 12 for i in QUALITIES[q]}
        if rel == 0 and q in tq:
            sc += 3.0 * dur
        elif rel == 7 and q in ('M', '7'):
            sc += 2.0 * dur
        elif all(p in scale for p in pcs):
            sc += 1.0 * dur
        else:
            sc -= 0.5 * dur
    if chords and ends:
        if (chords[-1][0] - tonic) % 12 == 0 and chords[-1][1] in tq:
            sc += 4.0 * max(1.0, chords[-1][2])
        if (chords[0][0] - tonic) % 12 == 0 and chords[0][1] in tq:
            sc += 2.0
    return sc


KEY_REGION_MIN = 8       # 局部调区至少 8 小节（一个乐句单位），更短的并入前一个调区
KEY_SWITCH = 3.0         # 转调代价（× 每小节拍数）
KEY_HOME = 0.1           # 主调先验（× 每小节拍数 / 小节）


def local_keys(bar_chords: List[List[Tuple[int, str, float]]], tonic: int, mode: str,
               bar_beats: float) -> List[Tuple[int, str]]:
    """逐小节局部调（24 个调上的维特比）：发射 = 该小节和弦与调的契合分，转调代价 = 2 小节的
    满分量级，主调略有先验。返回每小节的 (主音 pc, 调式)。"""
    import numpy as np
    keys = [(t, m) for t in range(12) for m in ('major', 'minor')]
    home = keys.index((tonic, mode))
    switch = KEY_SWITCH * bar_beats
    best = None
    back = []
    for chs in bar_chords:
        em = np.array([_chord_key_score(chs, t, m, ends=False) for t, m in keys])
        em[home] += KEY_HOME * bar_beats
        if best is None:
            best = em
            back.append(None)
            continue
        j = int(np.argmax(best))
        take = best[j] - switch > best
        back.append(np.where(take, j, np.arange(len(keys))))
        best = np.where(take, best[j] - switch, best) + em
    k = int(np.argmax(best))
    path = [k]
    for bp in reversed(back[1:]):
        k = int(bp[k])
        path.append(k)
    path.reverse()
    out = [keys[k] for k in path]
    # 太短的调区并入前一个调区（开头的并入后一个）
    runs = []
    for i, kk in enumerate(out):
        if runs and runs[-1][2] == kk:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, kk])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for ri, (a, b, kk) in enumerate(runs):
            if b - a < KEY_REGION_MIN:
                tgt = ri - 1 if ri > 0 else ri + 1
                runs[tgt][0] = min(runs[tgt][0], a)
                runs[tgt][1] = max(runs[tgt][1], b)
                del runs[ri]
                merged = []
                for r in sorted(runs):
                    if merged and merged[-1][2] == r[2]:
                        merged[-1][1] = r[1]
                    else:
                        merged.append(r)
                runs = merged
                changed = True
                break
    res: List[Tuple[int, str]] = [out[0]] * len(out)
    for a, b, kk in runs:
        for i in range(a, b):
            res[i] = kk
    return res


def estimate_key(chords, sig_sharps: Optional[int]) -> Tuple[int, str]:
    """按和弦（主和弦 / 属和弦 / 调内和弦的时值、首尾和弦）在 24 个调里打分；
    有调号时只在调号对应的大调与关系小调里选。"""
    cands = []
    if sig_sharps is not None:
        maj = (7 * sig_sharps) % 12
        cands = [(maj, 'major'), ((maj + 9) % 12, 'minor')]
    else:
        cands = [(t, m) for t in range(12) for m in ('major', 'minor')]
    return max(cands, key=lambda tm: _chord_key_score(chords, tm[0], tm[1]))


# ------------------------------------------------------------------ 主分析
def _drop_second_endings(score, measures):
    drop = set()
    try:
        for rb in score.spannerBundle.getByClass('RepeatBracket'):
            nums = rb.getNumberList()
            if nums and 1 not in nums:
                drop |= {id(m) for m in rb.getSpannedElements()}
    except Exception:
        pass
    return [m for m in measures if id(m) not in drop]


def _measure_line(m, dur: float, harmony) -> List[list]:
    """小节内所有声部（Voice）合成一条单旋律线：[on, dur, midi|None]，重叠取后起者，空隙补休止。"""
    voices = list(m.voices)
    if not voices:
        voices = [m]
    events = []
    for v in voices:
        base = float(v.offset) if v is not m else 0.0
        for n in v.notesAndRests:
            if isinstance(n, harmony.Harmony) or n.isRest or float(n.quarterLength) <= 0:
                continue
            tie = getattr(n, 'tie', None)
            events.append([base + float(n.offset), float(n.quarterLength),
                           int(max(p.midi for p in n.pitches)),
                           tie is not None and tie.type in ('stop', 'continue')])
    events.sort(key=lambda e: (e[0], -e[2]))
    line: List[list] = []
    t = 0.0
    for on, d, midi, tied in events:
        if line and on < line[-1][0] + 1e-6:
            continue
        if line and line[-1][2] is not None and on < line[-1][0] + line[-1][1] - 1e-6:
            line[-1][1] = on - line[-1][0]
            t = on
        if on > t + 1e-6:
            line.append([t, on - t, None])
        if tied and line and line[-1][2] == midi and \
                abs(line[-1][0] + line[-1][1] - on) < 1e-6:
            line[-1][1] += d
        else:
            line.append([on, d, midi])
        t = on + d
    if t < dur - 1e-6:
        line.append([t, dur - t, None])
    if line and t > dur + 1e-6:
        line[-1][1] = max(0.0, dur - line[-1][0])
    return [x for x in line if x[1] > 1e-6]


_CANDS: List[Tuple[int, str]] = []


def _chord_scores(onset: Dict[int, float], offbeat: Dict[int, float], bass: Optional[int],
                  mode: str):
    """与 grammar.theory.recognize_chord 同一打分式，返回全部 (root, quality) 的得分向量。"""
    import numpy as np
    from score_learning.grammar.theory import QUALITIES, SEVENTHS, _Q_COST, scale_of
    if not _CANDS:
        _CANDS.extend((r, q) for r in range(12) for q in QUALITIES)
    scale = scale_of(mode)
    tot_on = sum(onset.values())
    tot_off = sum(offbeat.values())
    out = np.empty(len(_CANDS))
    for k, (root, q) in enumerate(_CANDS):
        ivs = QUALITIES[q]
        tpl = {(root + i) % 12 for i in ivs}
        if q in SEVENTHS and (root + ivs[3]) % 12 not in onset:
            out[k] = -1e9
            continue
        cov_on = sum(w for pc, w in onset.items() if pc in tpl)
        cov_off = sum(w for pc, w in offbeat.items() if pc in tpl)
        s = cov_on + 0.5 * cov_off - 1.6 * (tot_on - cov_on) - 0.4 * (tot_off - cov_off) - _Q_COST[q]
        if root not in onset:
            s -= 1.5
        if (root + ivs[1]) % 12 not in onset:
            s -= 0.9
        if q in ('d', 'A', 'h7', 'o7') and (root + ivs[2]) % 12 not in onset:
            s -= 0.9
        if bass is not None and bass == root:
            s += 0.35
        if all(pc in scale or (mode == 'minor' and pc == 11) for pc in tpl):
            s += 0.15
        out[k] = s
    return out


def _texture_chords(starts, ends, midis, t0: float, t1: float, ref: int, mode: str):
    """[t0, t1) 窗口内所有声部的发声音 → 全部候选和弦的得分（调内坐标以 ref 为主音）。
    拍首在响的音算拍首证据（低音加倍），窗口中途新起的音算弱证据。无音返回 None。"""
    on_mask = (starts <= t0 + 1e-6) & (ends > t0 + 1e-6)
    off_mask = (starts > t0 + 1e-6) & (starts < t1 - 1e-6)
    if not on_mask.any():
        if not off_mask.any():
            return None
        first = starts[off_mask].min()
        on_mask = off_mask & (starts <= first + 1e-6)
        off_mask = off_mask & ~on_mask
    onset: Dict[int, float] = {}
    offbeat: Dict[int, float] = {}
    for s, e, m in zip(starts[on_mask], ends[on_mask], midis[on_mask]):
        w = min(e, t1) - max(s, t0)
        pc = (int(m) - ref) % 12
        onset[pc] = onset.get(pc, 0.0) + w
    bass = int(midis[on_mask].min())
    bpc = (bass - ref) % 12
    onset[bpc] = onset.get(bpc, 0.0) + (t1 - t0)
    for s, e, m in zip(starts[off_mask], ends[off_mask], midis[off_mask]):
        pc = (int(m) - ref) % 12
        offbeat[pc] = offbeat.get(pc, 0.0) + (min(e, t1) - s)
    # 权重归一到「拍」量级，与 recognize_chord 里的固定惩罚项可比
    scale = 1.0 / max(1e-6, max(onset.values()))
    onset = {k: v * scale for k, v in onset.items()}
    offbeat = {k: v * scale for k, v in offbeat.items()}
    return _chord_scores(onset, offbeat, bpc, mode)


def _parse_score(path: str):
    """按扩展名选择 music21 解析格式（.md2 = MuseData）。"""
    from music21 import converter
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.md2', '.md', '.msd', '.musedata'):
        return converter.parse(path, format='musedata')
    return converter.parse(path)


def _analyze_texture(path: str, style: str, sub: str) -> Optional[Dict[str, Any]]:
    """无和声标注的多声部谱：旋律 = 平均音高最高的声部的最高音线；和弦 = 每拍从所有声部识别，
    低置信度沿用前一个和弦，弱拍上「X Y X」的经过和弦并回 X。"""
    import numpy as np
    from music21 import harmony, key as m21key, meter
    score = _parse_score(path)
    parts_all = list(score.parts)
    if not parts_all:
        return None
    n_parts = len(parts_all)
    # 配器族粗分（学习复杂度用；和声仍可只用弦乐）
    def _family(name: str) -> str:
        n = (name or '').lower()
        if any(k in n for k in _STRING_KEYS):
            return 'strings'
        if any(k in n for k in ('flute', 'oboe', 'clarinet', 'bassoon', 'piccolo',
                                '长笛', '双簧', '单簧', '大管')):
            return 'woodwind'
        if any(k in n for k in ('horn', 'trumpet', 'trombone', 'tuba', '圆号', '小号', '长号')):
            return 'brass'
        if any(k in n for k in ('timpani', 'drum', 'perc', '定音')):
            return 'percussion'
        if 'piano' in n or '钢琴' in n:
            return 'piano'
        return 'other'
    families = Counter(_family(str(p.partName or p.id or '')) for p in parts_all)
    parts = parts_all
    if style in ('strings', 'symphony'):
        strs = [p for p in parts
                if any(k in str(p.partName or p.id or '').lower() for k in _STRING_KEYS)]
        if len(strs) >= 3:
            parts = strs
        elif len(parts) > 5 and style == 'strings':
            return None
        # symphony：声部很多但弦乐不足时仍用全谱（S3 MusicXML 声部名可能不规范）

    def mean_pitch(p):
        ms = [pt.midi for n in p.flatten().notes for pt in n.pitches]
        return sum(ms) / len(ms) if ms else -1
    mel_part = max(parts, key=mean_pitch)
    measures = list(mel_part.getElementsByClass('Measure'))
    if len(measures) < 8:
        return None
    tss = {t.ratioString for t in mel_part.recurse().getElementsByClass(meter.TimeSignature)}
    tss = {'4/4' if t == '2/2' else t for t in tss}
    if len(tss) != 1:
        return None
    ts_str = tss.pop()
    if ts_str not in _METER_SUB:
        return None
    sub = sub or _METER_SUB[ts_str]
    bar_ql, beat_ql, beats = meter_info(ts_str)
    measures = _drop_second_endings(score, measures)
    sig = None
    for k in mel_part.recurse().getElementsByClass(m21key.KeySignature):
        sig = k.sharps
        break
    rows = []
    for p in parts:
        for n in p.flatten().notes:
            d = float(n.quarterLength)
            if d <= 0:
                continue
            for pt in n.pitches:
                rows.append((float(n.offset), float(n.offset) + d, pt.midi))
    if not rows:
        return None
    arr = np.array(rows)
    starts, ends, midis = arr[:, 0], arr[:, 1], arr[:, 2]

    win = beat_ql * HARM_WINDOW.get(ts_str, 1)

    def windows(m):
        m0, dur = float(m.offset), float(m.duration.quarterLength)
        out, t = [], 0.0
        while t < dur - 1e-6:
            out.append((t, m0 + t, m0 + min(t + win, dur)))
            t += win
        return out

    def recognize(ref, mode):
        """逐拍候选得分 + 维特比：换和弦有代价（强拍小、其余拍大），无音的拍沿用前一个和弦。"""
        wins, scores = [], []
        for bi, m in enumerate(measures):
            for on, t0, t1 in windows(m):
                sc = _texture_chords(starts, ends, midis, t0, t1, ref, mode)
                wins.append((bi, on))
                scores.append(sc)
        if not any(s is not None for s in scores):
            return []
        n_c = len(_CANDS)
        zero = np.zeros(n_c)
        best = None
        back = []
        for (bi, on), sc in zip(wins, scores):
            sc = zero if sc is None else sc
            if best is None:
                best = sc.copy()
                back.append(None)
                continue
            pen = SWITCH_COST[strength(on, bar_ql, beat_ql)]
            j = int(np.argmax(best))
            stay = best
            switch = best[j] - pen
            take_switch = switch > stay
            back.append(np.where(take_switch, j, np.arange(n_c)))
            best = np.where(take_switch, switch, stay) + sc
        k = int(np.argmax(best))
        path = [k]
        for bp in reversed(back[1:]):
            k = int(bp[k])
            path.append(k)
        path.reverse()
        seq = []
        for (bi, on), k in zip(wins, path):
            r, q = _CANDS[k]
            seq.append([bi, on, ((r + ref) % 12, QUALITY_FOLD.get(q, q))])
        return seq

    ref0 = (7 * sig) % 12 if sig is not None else 0
    seq = recognize(ref0, 'major')
    flat = [(c[0], c[1], beat_ql) for _b, _o, c in seq if c is not None]
    if len(flat) < 8:
        return None
    tonic, mode = estimate_key(flat, sig)
    if (tonic, mode) != (ref0, 'major'):
        seq = recognize(tonic, mode)
    by_bar: Dict[int, List[Tuple[float, int, str]]] = {}
    last = None
    for bi, on, c in seq:
        if c is None:
            continue
        if c != last or on < 1e-6:
            by_bar.setdefault(bi, []).append((on, c[0], c[1]))
        last = c
    bars_raw = []
    for bi, m in enumerate(measures):
        dur = float(m.duration.quarterLength)
        rb = m.rightBarline
        rep = rb is not None and type(rb).__name__ == 'Repeat' and \
            getattr(rb, 'direction', '') == 'end'
        bars_raw.append({'dur': dur, 'chords': by_bar.get(bi, []),
                         'notes': _measure_line(m, dur, harmony), 'key': None,
                         'pad': float(getattr(m, 'paddingLeft', 0) or 0), 'rep': rep})
    # 按局部调切成调区：每个调区单独成一段语料，和弦按该调区的主音 / 调式记
    bar_chords: List[List[Tuple[int, str, float]]] = [[] for _ in measures]
    for bi, _on, c in seq:
        if c is not None:
            bar_chords[bi].append((c[0], c[1], beat_ql))
    lk = local_keys(bar_chords, tonic, mode, bar_ql / beat_ql)
    out = []
    a = 0
    for i in range(1, len(lk) + 1):
        if i == len(lk) or lk[i] != lk[a]:
            kt, km = lk[a]
            p = _finish(path, style, sub, ts_str, bar_ql, beat_ql, bars_raw[a:i], kt, km,
                        'local-key', allow_pickup=(a == 0))
            if p:
                p['region'] = [a, i]
                # 不在乐章结尾的调区：末小节是转调处，不是全曲终止
                p['open_end'] = i < len(lk)
                p['n_parts'] = n_parts
                p['families'] = dict(families)
                p['n_phrases'] = max(1, (i - a + 7) // 8)
                out.append(p)
            a = i
    return out or None


def analyze_file(path: str, style: str, sub: str) -> Optional[Dict[str, Any]]:
    from music21 import converter, harmony, key as m21key, meter, roman
    if STYLES[style].get('auto_harmony'):
        return _analyze_texture(path, style, sub)
    rn_stream = None
    if style == 'madrigal':
        rn_stream = converter.parse(path)
        score = converter.parse(path[:-6] + '.mxl')
    else:
        score = converter.parse(path)
    part = score.parts[0] if score.parts else score
    measures = list(part.getElementsByClass('Measure'))
    if len(measures) < 8:
        return None
    ts = part.recurse().getElementsByClass(meter.TimeSignature).first()
    ts_str = ts.ratioString if ts is not None else '4/4'
    if ts_str == '2/2':
        ts_str = '4/4'
    if ts_str not in ('2/4', '3/4', '4/4', '6/8'):
        return None
    bar_ql, beat_ql, _beats = meter_info(ts_str)
    measures = _drop_second_endings(score, measures)
    # 和弦来源：ChordSymbol（folk/jazz/pop）或对齐的 RomanNumeral（madrigal）
    rn_by_num: Dict[int, List[Tuple[float, Any]]] = {}
    if rn_stream is not None:
        for rn in rn_stream.recurse().getElementsByClass(roman.RomanNumeral):
            rn_by_num.setdefault(int(rn.measureNumber or 0), []).append((float(rn.offset), rn))
    # 调性：显式 Key（带调式）> 调号 + 和弦打分 > 纯和弦打分
    explicit = None
    sig = None
    for k in part.recurse().getElementsByClass(m21key.KeySignature):
        if isinstance(k, m21key.Key) and style == 'folk':
            explicit = k
        sig = k.sharps
        break
    raw_chords: List[Tuple[int, str, float]] = []
    bars_raw = []
    for mi, m in enumerate(measures):
        dur = float(m.duration.quarterLength)
        chs = []
        if rn_stream is not None:
            for on, rn in rn_by_num.get(int(m.number or 0), []):
                q = roman_quality(rn)
                if q and rn.root() is not None:
                    chs.append((on, rn.root().pitchClass, q))
        else:
            for cs in m.getElementsByClass(harmony.ChordSymbol):
                q = chord_quality(cs)
                if q and cs.root() is not None:
                    chs.append((float(cs.offset), cs.root().pitchClass, q))
        notes = _measure_line(m, dur, harmony)
        mkey = None
        for k in m.getElementsByClass(m21key.KeySignature):
            mkey = k
        rb = m.rightBarline
        rep = rb is not None and type(rb).__name__ == 'Repeat' and \
            getattr(rb, 'direction', '') == 'end'
        bars_raw.append({'dur': dur, 'chords': chs, 'notes': notes, 'key': mkey,
                         'pad': float(getattr(m, 'paddingLeft', 0) or 0), 'rep': rep})
    # 原始和弦时值（估调用）
    flat = []
    for b in bars_raw:
        for i, (on, root, q) in enumerate(b['chords']):
            nxt = b['chords'][i + 1][0] if i + 1 < len(b['chords']) else b['dur']
            flat.append((root, q, max(0.25, nxt - on)))
    if len(flat) < 4:
        return None
    if explicit is not None and getattr(explicit, 'mode', None) in _MODE_OF:
        tonic, mode = explicit.tonic.pitchClass, _MODE_OF[explicit.mode]
        key_src = 'key'
    elif rn_stream is not None:
        first = next(iter(rn_stream.recurse().getElementsByClass(roman.RomanNumeral)), None)
        if first is None or first.key is None:
            return None
        tonic, mode = first.key.tonic.pitchClass, _MODE_OF.get(first.key.mode, 'major')
        key_src = 'roman'
    else:
        tonic, mode = estimate_key(flat, sig)
        key_src = 'chords'
    return _finish(path, style, sub, ts_str, bar_ql, beat_ql, bars_raw, tonic, mode, key_src)


def _finish(path, style, sub, ts_str, bar_ql, beat_ql, bars_raw, tonic, mode, key_src,
            allow_pickup: bool = True):
    from music21 import key as m21key
    if len(bars_raw) < 8:
        return None
    # 弱起：第一小节不满（paddingLeft 或时值不足）
    pickup = None
    start = 0
    if allow_pickup and (bars_raw[0]['dur'] < bar_ql - 1e-6 or bars_raw[0]['pad'] > 0):
        pk = bars_raw[0]
        pickup = {'ql': round(pk['dur'], 3),
                  'notes': [[round(on, 3), round(d, 3),
                             None if mi_ is None else (mi_ - tonic) % 12, mi_]
                            for on, d, mi_ in pk['notes']]}
        start = 1
    bars = []
    cur_tonic = tonic
    cur = None
    for b in bars_raw[start:]:
        if b['key'] is not None and style == 'folk' and isinstance(b['key'], m21key.Key) \
                and b['key'].mode in _MODE_OF and _MODE_OF[b['key'].mode] == mode:
            cur_tonic = b['key'].tonic.pitchClass       # 段落转调（A 段 A 大调、B 段 D 大调）
        chs = []
        for on, root, q in b['chords']:
            onq = round(round(on / beat_ql) * beat_ql, 3)       # 和弦变化对齐到拍
            tok = f'{(root - cur_tonic) % 12}:{q}'
            if chs and abs(chs[-1][0] - onq) < 1e-6:
                chs[-1] = [onq, tok]
            elif onq < bar_ql - 1e-6:
                chs.append([onq, tok])
        if not chs or chs[0][0] > 1e-6:
            if cur is None and not chs:
                cur = None
            elif cur is not None:
                chs.insert(0, [0.0, cur])
        if chs:
            cur = chs[-1][1]
        notes = []
        for on, d, midi in b['notes']:
            onq = round(round(on / GRID) * GRID, 3)
            dq = max(GRID, round(round(d / GRID) * GRID, 3))
            notes.append([onq, dq, None if midi is None else (midi - cur_tonic) % 12, midi])
        bars.append({'chords': chs, 'notes': notes,
                     'short': abs(b['dur'] - bar_ql) > 1e-6, 'rep': b['rep']})
    if sum(1 for b in bars if b['chords']) < len(bars) * 0.6:
        return None
    return {'id': os.path.splitext(os.path.basename(path))[0], 'style': style, 'sub': sub,
            'meter': ts_str, 'bar_ql': bar_ql, 'beat_ql': beat_ql, 'mode': mode,
            'tonic_pc': tonic, 'key_source': key_src, 'pickup': pickup, 'bars': bars}


def _work(args):
    path, style, sub = args
    try:
        return analyze_file(path, style, sub)
    except Exception:
        return None


def analyze_style(style: str, cache: bool = True, progress: bool = True,
                  procs: int = 0) -> List[Dict[str, Any]]:
    cache_path = os.path.join(CACHE_DIR, f'{style}.json')
    if cache and os.path.isfile(cache_path):
        with open(cache_path, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('version') == ANALYZER_VERSION:
            return data['pieces']
    files = style_files(style)
    jobs = [(p, style, s) for p, s in files]
    if progress:
        print(f'[{style}] 分析 {len(jobs)} 个文件…', flush=True)
    import multiprocessing as mp
    n = procs or max(1, min(8, (os.cpu_count() or 2) - 1))
    with mp.get_context('spawn').Pool(n) as pool:
        res = pool.map(_work, jobs, chunksize=8)
    pieces = []
    for r in res:
        if isinstance(r, list):
            pieces += r
        elif r:
            pieces.append(r)
    if progress:
        print(f'[{style}] 有效 {len(pieces)} 首', flush=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump({'version': ANALYZER_VERSION, 'pieces': pieces}, f, ensure_ascii=False)
    return pieces
