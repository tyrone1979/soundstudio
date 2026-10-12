# -*- coding: utf-8 -*-
"""众赞歌语料分析：逐拍切片 → 和弦 / 四声部音高 / 乐句与终止 / 八分音装饰。

输出每首曲子的结构化事件流（JSON 可序列化），供 learn.py 计数学习。
不记忆任何音符序列本身——只保留用于统计的调内坐标特征。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from .theory import chord_key, recognize_chord, tonic_letter_pc

VOICES = ('S', 'A', 'T', 'B')
ANALYZER_VERSION = '2'   # 改了切片/识别逻辑就加一，作废旧缓存


def chorale_paths() -> List[str]:
    """music21 自带的巴赫四部众赞歌（Riemenschneider 编号，371 首）。"""
    from music21 import corpus
    it = corpus.chorales.Iterator(numberingSystem='riemenschneider', returnType='filename')
    return list(it)


def _timeline(part) -> List[Dict[str, Any]]:
    """声部 → [{'on', 'off', 'midi', 'fermata', 'attack'}]（连音线合并为一个音）。"""
    from music21 import expressions
    out: List[Dict[str, Any]] = []
    for n in part.flatten().notesAndRests:
        on = float(n.offset)
        off = on + float(n.quarterLength)
        if n.quarterLength <= 0:
            continue
        if n.isRest:
            out.append({'on': on, 'off': off, 'midi': None, 'fermata': False})
            continue
        midi = int(n.pitches[-1].midi) if n.isChord else int(n.pitch.midi)
        ferm = any(isinstance(e, expressions.Fermata) for e in n.expressions)
        tie = getattr(n, 'tie', None)
        if tie is not None and tie.type in ('stop', 'continue') and out and \
                out[-1]['midi'] == midi and abs(out[-1]['off'] - on) < 1e-6:
            out[-1]['off'] = off
            out[-1]['fermata'] = out[-1]['fermata'] or ferm
            continue
        out.append({'on': on, 'off': off, 'midi': midi, 'fermata': ferm})
    return out


def _at(tl, t: float):
    """t 时刻发声的音（及是否在 t 起音）。"""
    for ev in tl:
        if ev['on'] - 1e-6 <= t < ev['off'] - 1e-6:
            return ev, abs(ev['on'] - t) < 1e-6
    return None, False


def _bar_starts(part):
    from music21 import stream
    out = []
    for m in part.getElementsByClass(stream.Measure):
        try:
            bar_len = float(m.barDuration.quarterLength)
        except Exception:
            bar_len = 4.0
        out.append((float(m.offset), bar_len, float(getattr(m, 'paddingLeft', 0) or 0)))
    return out


def analyze_chorale(path_or_score, pid: str = '') -> Optional[Dict[str, Any]]:
    """单首众赞歌 → 结构化特征；非四声部 / 非 x/4 拍的返回 None。"""
    import os
    from music21 import converter, corpus, meter, stream, bar as m21bar
    if isinstance(path_or_score, str):
        s = converter.parse(path_or_score) if os.path.isfile(path_or_score) \
            else corpus.parse(path_or_score)
    else:
        s = path_or_score
    parts = list(s.parts)
    if len(parts) != 4:
        return None
    ts_list = s.flatten().getElementsByClass(meter.TimeSignature)
    ts = ts_list[0] if len(ts_list) else None
    if ts is None or ts.denominator != 4 or ts.numerator not in (3, 4):
        return None
    k = s.analyze('key')
    mode = 'minor' if k.mode == 'minor' else 'major'
    tonic = k.tonic.name.replace('-', 'b')
    _, tpc = tonic_letter_pc(tonic)
    tls = [_timeline(p) for p in parts]
    if not all(tls):
        return None
    end = min(tl[-1]['off'] for tl in tls)
    bars = _bar_starts(parts[0])

    def beat_pos(t):
        for start, blen, pad in reversed(bars):
            if t >= start - 1e-6:
                return (t - start) + (pad if pad else 0.0), blen
        return t, float(ts.numerator)

    first_on = min(tl[0]['on'] for tl in tls if tl)
    beats: List[Dict[str, Any]] = []
    t = float(int(first_on))
    while t < end - 1e-6:
        evs = [_at(tl, t) for tl in tls]
        mids = [e['midi'] if e else None for e, _a in evs]
        if any(m is None for m in mids):
            t += 1.0
            continue
        onset: Dict[int, float] = {}
        for vi, m in enumerate(mids):
            w = 2.0 if vi == 3 else 1.0
            onset[(m - tpc) % 12] = onset.get((m - tpc) % 12, 0.0) + w
        offbeat: Dict[int, float] = {}
        eighth = []
        for vi, tl in enumerate(tls):
            e2, att = _at(tl, t + 0.5)
            m2 = e2['midi'] if (e2 and att) else None
            eighth.append(m2)
            if m2 is not None and m2 != mids[vi]:
                offbeat[(m2 - tpc) % 12] = offbeat.get((m2 - tpc) % 12, 0.0) + 1.0
        ch, conf = recognize_chord(onset, (mids[3] - tpc) % 12, mode, offbeat)
        pos, blen = beat_pos(t)
        beats.append({
            't': t, 'pos': round(pos, 3), 'bar_len': blen,
            'strong': abs(pos) < 1e-6 or (blen == 4.0 and abs(pos - 2.0) < 1e-6),
            'downbeat': abs(pos) < 1e-6,
            'midi': mids,
            'attack': [a for _e, a in evs],
            'eighth': eighth,
            'chord': chord_key(ch) if ch else None,
            'conf': round(conf, 3),
            'fermata': bool(evs[0][0] and evs[0][0]['fermata'] and evs[0][1]),
            'fermata_off': evs[0][0]['off'] if evs[0][0] else t + 1,
        })
        t += 1.0
    if len(beats) < 8:
        return None
    # 乐句：女高音延长记号起音的拍 = 终止拍；延长音持续的后续拍不计入
    phrases = []
    start = 0
    i = 0
    while i < len(beats):
        b = beats[i]
        if b['fermata']:
            if i - start >= 2:
                phrases.append({'start': start, 'end': i,
                                'hold': round(b['fermata_off'] - b['t'], 3)})
            j = i + 1
            while j < len(beats) and beats[j]['t'] < b['fermata_off'] - 1e-6:
                beats[j]['held'] = True
                j += 1
            start = j
            i = j
            continue
        i += 1
    inferred = False
    if not phrases:
        # 无延长记号（非巴赫来源）：按两小节一句切分，仅供评测对比
        span = int(2 * ts.numerator)
        phrases = [{'start': s0, 'end': min(s0 + span, len(beats)) - 1, 'hold': 1.0}
                   for s0 in range(0, len(beats), span) if min(s0 + span, len(beats)) - s0 >= 3]
        inferred = True
        start = len(beats)
    if not phrases:
        return None
    if phrases[-1]['end'] < len(beats) - 3 and len(beats) - 1 - start >= 2:
        # 结尾没有延长记号：最后一拍当终止
        phrases.append({'start': start, 'end': len(beats) - 1, 'hold': 1.0})
    has_repeat = False
    for m in parts[0].getElementsByClass(stream.Measure):
        for bl in (m.leftBarline, m.rightBarline):
            if isinstance(bl, m21bar.Repeat):
                has_repeat = True
    for b in beats:
        b.pop('fermata_off', None)
    return {
        'id': pid or (path_or_score if isinstance(path_or_score, str) else 'score'),
        'tonic': tonic, 'tonic_pc': tpc, 'mode': mode,
        'meter': f'{ts.numerator}/{ts.denominator}',
        'beats': beats, 'phrases': phrases, 'has_repeat': has_repeat,
        'phrases_inferred': inferred,
    }


def analyze_corpus(paths: Optional[List[str]] = None, cache: Optional[str] = None,
                   progress: bool = True) -> List[Dict[str, Any]]:
    """分析整套语料（带缓存：同一组文件只分析一次）。"""
    paths = paths or chorale_paths()
    sig = hashlib.sha1((ANALYZER_VERSION + '|' + '|'.join(map(str, paths))).encode()
                       ).hexdigest()[:10]
    if cache and os.path.isfile(cache):
        try:
            with open(cache, encoding='utf-8') as f:
                data = json.load(f)
            if data.get('sig') == sig:
                return data['pieces']
        except Exception:
            pass
    pieces = []
    for i, p in enumerate(paths):
        try:
            a = analyze_chorale(p, pid=os.path.basename(str(p)))
        except Exception:
            a = None
        if a:
            pieces.append(a)
        if progress and (i + 1) % 50 == 0:
            print(f'  分析 {i + 1}/{len(paths)}（有效 {len(pieces)}）', flush=True)
    if cache:
        os.makedirs(os.path.dirname(os.path.abspath(cache)), exist_ok=True)
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump({'sig': sig, 'pieces': pieces}, f, ensure_ascii=False)
    return pieces
