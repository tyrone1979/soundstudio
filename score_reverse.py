# -*- coding: utf-8 -*-
"""乐谱 → 纯文字描述 → 乐谱（音符级逆向，无 JSON）。

权威复现路径：
  MusicXML / score_spec
    → build_score_description(...)   # 纯中文逐音文字
    → parse_score_description / text_to_spec / text_to_musicxml
    → 逐音符一致

内部仍可生成 regeneration_program.json 供内核回放；对外主路径是文字描述。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import compose_kernel as K

# 嵌入提示词中的 program 标记（旧格式兼容；新格式为纯文字，无 JSON）
_PROG_BEGIN = '<!-- REGENERATION_PROGRAM'
_PROG_END = '-->'

# 时值中文名（文字描述 ↔ DUR_QL）
_DUR_TO_CN = {
    'whole': '全音符', 'half': '二分', 'quarter': '四分', 'eighth': '八分',
    '16th': '十六分', '32nd': '三十二分',
    'dotted_half': '附点二分', 'dotted_quarter': '附点四分',
    'dotted_eighth': '附点八分',
}
_CN_TO_DUR = {v: k for k, v in _DUR_TO_CN.items()}
_CN_TO_DUR.update({
    '全': 'whole', '二分音符': 'half', '四分音符': 'quarter',
    '八分音符': 'eighth', '十六分音符': '16th', '三十二分音符': '32nd',
})

_KEY_LABEL = {
    0: 'C', 1: 'G', 2: 'D', 3: 'A', 4: 'E', 5: 'B', 6: 'F#', 7: 'C#',
    -1: 'F', -2: 'Bb', -3: 'Eb', -4: 'Ab', -5: 'Db', -6: 'Gb', -7: 'Cb',
}

_INSTR_ALIASES = {
    'violin': 'violin', 'violin i': 'violin', 'violin ii': 'violin',
    'viola': 'viola', 'cello': 'cello', 'violoncello': 'cello',
    'contrabass': 'contrabass', 'double bass': 'contrabass',
    'bass': 'contrabass', 'flute': 'flute', 'oboe': 'oboe',
    'clarinet': 'clarinet', 'bassoon': 'bassoon', 'horn': 'horn',
    'trumpet': 'trumpet', 'trombone': 'trombone', 'tuba': 'tuba',
    'timpani': 'timpani', 'piano': 'piano', 'piano rh': 'piano',
    'piano lh': 'piano',
}


def _ql_to_dur(ql: float) -> str:
    """四分音符长度 → DUR_QL 键；优先精确匹配，否则拆最近合法时值。"""
    if ql <= 1e-9:
        raise ValueError(f'无法映射时值 ql={ql}')
    for name, v in K.DUR_QL.items():
        if abs(v - ql) < 1e-6:
            return name
    # 三连音近似：1/3、2/3
    if abs(ql - 1.0 / 3.0) < 0.02:
        # 内核无 triplet 键时用两个 16th 近似会破坏精确复现；
        # 登记为自定义键需内核支持——这里拒绝，由上层改用 excerpt。
        raise ValueError(f'无法映射三连音时值 ql={ql}')
    best = min(K.DUR_QL.items(), key=lambda kv: abs(kv[1] - ql))
    if abs(best[1] - ql) < 0.02:
        return best[0]
    raise ValueError(f'无法映射时值 ql={ql}')


def _split_ql_to_durs(ql: float) -> List[str]:
    """把任意 ql 拆成 DUR_QL 时值名序列（贪婪）。"""
    rem = float(ql)
    out: List[str] = []
    legal = sorted(set(K.DUR_QL.values()), reverse=True)
    while rem > 1e-9:
        step = next((d for d in legal if d <= rem + 1e-9), None)
        if step is None:
            raise ValueError(f'无法拆分时值 ql 剩余={rem}')
        out.append(_ql_to_dur(step))
        rem -= step
    return out


def _norm_instrument(name: str) -> str:
    key = str(name or '').strip().lower()
    if key in _INSTR_ALIASES:
        return _INSTR_ALIASES[key]
    for a, v in _INSTR_ALIASES.items():
        if a in key:
            return v
    return key.replace(' ', '_') or 'violin'


def _event_pitch(p: Any) -> Any:
    if p is None:
        return None
    if isinstance(p, (list, tuple)):
        return [str(x) for x in p]
    return str(p)


def _split_events_by_bars(events: List[List], bars: int,
                          meter: str = '4/4') -> Dict[int, List[List]]:
    try:
        num, den = meter.split('/')
        cap = 4.0 * int(num) / int(den)
    except Exception:
        cap = 4.0
    out: Dict[int, List[List]] = {b: [] for b in range(1, bars + 1)}
    bar, used = 1, 0.0
    for e in events:
        pitch = _event_pitch(e[0] if e else None)
        dur = str(e[1]) if len(e) > 1 else 'quarter'
        mark = e[2] if len(e) > 2 else None
        if dur not in K.DUR_QL:
            # 尝试把数字 ql 转名
            try:
                dur = _ql_to_dur(float(dur))
            except Exception:
                dur = 'quarter'
        dq = K.DUR_QL[dur]
        while bar <= bars and used + dq > cap + 1e-9 and abs(used - cap) > 1e-9:
            # 当前小节未满却放不下 → 不应发生；强行切到下一小节
            bar += 1
            used = 0.0
        if bar > bars:
            break
        out[bar].append([pitch, dur, mark])
        used += dq
        if abs(used - cap) < 1e-6:
            bar += 1
            used = 0.0
    return out


def _infer_sections(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按 tempo_marks / key_changes 切段；无标记则单段 Full。"""
    bars = int(spec.get('bars') or 8)
    marks = list(spec.get('tempo_marks') or [])
    keys = list(spec.get('key_changes') or [])
    cuts = {1}
    for m in marks:
        try:
            cuts.add(int(m[0]))
        except Exception:
            pass
    for k in keys:
        try:
            cuts.add(int(k[0]))
        except Exception:
            pass
    starts = sorted(c for c in cuts if 1 <= c <= bars)
    if len(starts) <= 1:
        return [{'name': 'Full', 'bars': bars,
                 'dynamic': 'mf', 'tempo_text': 'Allegro',
                 'bpm': int(spec.get('tempo') or 120)}]
    sections = []
    for i, s in enumerate(starts):
        end = (starts[i + 1] - 1) if i + 1 < len(starts) else bars
        n = end - s + 1
        if n < 1:
            continue
        bpm = int(spec.get('tempo') or 120)
        ttext = 'Allegro'
        for m in marks:
            try:
                if int(m[0]) == s:
                    if len(m) > 2 and m[2]:
                        bpm = int(m[2])
                    if len(m) > 1 and m[1]:
                        ttext = str(m[1])
            except Exception:
                pass
        sections.append({
            'name': f'S{i + 1}', 'bars': n,
            'dynamic': 'mf', 'tempo_text': ttext, 'bpm': bpm,
        })
    total = sum(s['bars'] for s in sections)
    if total != bars and sections:
        sections[-1]['bars'] += bars - total
    return sections or [{'name': 'Full', 'bars': bars,
                         'dynamic': 'mf', 'tempo_text': 'Allegro',
                         'bpm': int(spec.get('tempo') or 120)}]


def _instruments_from_spec(spec: Dict[str, Any]) -> List[str]:
    parts = spec.get('parts') or []
    return [_norm_instrument(p.get('name') or f'part{i}')
            for i, p in enumerate(parts)]


def build_regeneration_program(spec: Dict[str, Any],
                               *, seed: Optional[str] = None
                               ) -> Dict[str, Any]:
    """从 score_spec 构建权威复现 program（write_part + finalize preserve）。"""
    bars = int(spec.get('bars') or 8)
    if bars < 8:
        raise ValueError(f'逆向复现要求 bars≥8（内核下限），收到 {bars}')
    if bars > 256:
        raise ValueError(f'逆向复现要求 bars≤256，收到 {bars}')
    seed = str(seed or spec.get('seed') or 'reverse-default').strip()
    meter = str(spec.get('time_signature') or '4/4')
    tempo = int(spec.get('tempo') or 120)
    sharps = int(spec.get('key_sharps') or 0)
    mode = str(spec.get('mode') or 'major')
    title = str(spec.get('title') or 'Reversed').strip() or 'Reversed'
    parts = spec.get('parts') or []
    if not parts:
        raise ValueError('score_spec 无 parts')
    voices = len(parts)
    instruments = _instruments_from_spec(spec)
    sections = _infer_sections(spec)

    ns_args: Dict[str, Any] = {
        'title': title,
        'key_sharps': sharps,
        'mode': mode,
        'meter': meter,
        'tempo': tempo,
        'bars': bars,
        'seed': seed,
        'voices': voices,
        'instruments': instruments,
    }
    if bars > 64:
        ns_args['allow_long'] = True

    calls: List[Dict[str, Any]] = [
        {'script': 'new_score', 'args': ns_args},
        {'script': 'set_sections', 'args': {'sections': sections}},
    ]
    # 每段给占位和声（I 循环），write_part 不依赖它，但保持脚本契约完整
    for sec in sections:
        n = int(sec['bars'])
        # set_harmony progression 不宜过长；循环即可
        prog = ['I', 'V', 'I', 'V']
        calls.append({
            'script': 'set_harmony',
            'args': {
                'section': sec['name'],
                'progression': prog,
            },
        })
    for pi, part in enumerate(parts):
        events = list(part.get('events') or [])
        # 规范化事件时值名
        flat: List[List] = []
        for e in events:
            pitch = _event_pitch(e[0] if e else None)
            dur = str(e[1]) if len(e) > 1 else 'quarter'
            if dur not in K.DUR_QL:
                try:
                    dur = _ql_to_dur(float(dur))
                except Exception:
                    dur = 'quarter'
            mark = e[2] if len(e) > 2 else None
            flat.append([pitch, dur, mark])
        calls.append({
            'script': 'write_part',
            'args': {'part': pi, 'notes': flat},
        })
    calls.append({'script': 'finalize', 'args': {'preserve': True}})
    return {'seed': seed, 'calls': calls}


def _event_to_cn(e: List) -> str:
    """[pitch|null|list, dur, …] → 『B4+G5(四分)』/『休止(八分)』。"""
    pitch, dur = e[0], str(e[1])
    dur_cn = _DUR_TO_CN.get(dur, dur)
    if pitch is None:
        return f'休止({dur_cn})'
    if isinstance(pitch, (list, tuple)):
        return f"{'+'.join(str(x) for x in pitch)}({dur_cn})"
    return f'{pitch}({dur_cn})'


def _parse_cn_event(tok: str) -> List:
    """『B4+G5(四分)』/『休止(八分)』→ [pitch, dur, None]。"""
    tok = tok.strip().strip('、，,')
    m = re.match(r'^(.+)\(([^)]+)\)\s*$', tok)
    if not m:
        raise ValueError(f'无法解析音符描述：{tok!r}')
    head, dur_cn = m.group(1).strip(), m.group(2).strip()
    dur = _CN_TO_DUR.get(dur_cn) or (dur_cn if dur_cn in K.DUR_QL else None)
    if dur is None:
        raise ValueError(f'未知时值 {dur_cn!r}（在 {tok!r}）')
    if head in ('休止', 'rest', 'Rest', 'R'):
        return [None, dur, None]
    if '+' in head:
        return [[p.strip() for p in head.split('+') if p.strip()], dur, None]
    return [head, dur, None]


def _split_events_to_bars(events: List[List], bars: int,
                          meter: str = '4/4') -> Dict[int, List[List]]:
    try:
        num, den = meter.split('/')
        cap = 4.0 * int(num) / int(den)
    except Exception:
        cap = 4.0
    out: Dict[int, List[List]] = {b: [] for b in range(1, bars + 1)}
    bar, used = 1, 0.0
    for e in events:
        pitch = _event_pitch(e[0] if e else None)
        dur = str(e[1]) if len(e) > 1 else 'quarter'
        if dur not in K.DUR_QL:
            try:
                dur = _ql_to_dur(float(dur))
            except Exception:
                dur = 'quarter'
        mark = e[2] if len(e) > 2 else None
        dq = K.DUR_QL[str(dur)]
        if bar > bars:
            break
        if used + dq > cap + 1e-9 and abs(used - cap) > 1e-9:
            # 小节未满却放不下：推进到下一小节
            bar += 1
            used = 0.0
            if bar > bars:
                break
        out[bar].append([pitch, str(dur), mark])
        used += dq
        if abs(used - cap) < 1e-6:
            bar += 1
            used = 0.0
    return out


def build_score_description(spec: Dict[str, Any]) -> str:
    """MusicXML/spec → 纯中文逐音文字描述（无 JSON）。

    格式可供 ``parse_score_description`` 确定性编译回乐谱。
    """
    bars = int(spec.get('bars') or 8)
    meter = str(spec.get('time_signature') or '4/4')
    tempo = int(spec.get('tempo') or 120)
    sharps = int(spec.get('key_sharps') or 0)
    mode = str(spec.get('mode') or 'major')
    title = str(spec.get('title') or 'Untitled').strip() or 'Untitled'
    instruments = _instruments_from_spec(spec)
    key_label = _KEY_LABEL.get(sharps, f'sharps={sharps}')
    mode_cn = '大调' if mode == 'major' else '小调'
    seed = str(spec.get('seed') or '').strip()

    lines = [
        f'# 乐谱文字描述 ·《{title}》',
        '',
        '本文件是 MusicXML 的纯文字还原描述（不含 JSON）。',
        '用 `score_reverse.parse_score_description` / `text_to_musicxml` 可再生成乐谱。',
        '',
        f'【作品】《{title}》，{key_label} {mode_cn}（key_sharps={sharps}，mode={mode}），',
        f'{meter}，共 {bars} 小节，速度 ♩={tempo}。',
        '',
        f'【编制】{", ".join(instruments)}（共 {len(instruments)} 声部）。',
        '',
        '【记谱约定】',
        '1. 各声部按「第N小节：音名(时值)、…」逐音写出；休止写「休止(时值)」。',
        '2. 和弦音用「+」连接，如 B4+G5(四分)。',
        '3. 时值用中文：全音符/二分/四分/八分/十六分/三十二分/附点四分/附点八分。',
        '4. 还原时按文字逐音写入，不要重新创作，不要赋格，不要动机变形。',
        '',
    ]
    if seed:
        lines.append(f'【复现种子】{seed}')
        lines.append('')

    for pi, part in enumerate(spec.get('parts') or []):
        name = part.get('name') or f'声部{pi + 1}'
        lines.append(f'【声部 {pi + 1} · {name}】')
        by_bar = _split_events_to_bars(list(part.get('events') or []), bars, meter)
        for b in range(1, bars + 1):
            evs = by_bar.get(b) or []
            if not evs:
                lines.append(f'第{b}小节：休止(全音符)')
            else:
                body = '、'.join(_event_to_cn(e) for e in evs)
                lines.append(f'第{b}小节：{body}')
        lines.append('')

    lines.append('【结束】以上为全曲逐音描述。')
    return '\n'.join(lines).rstrip() + '\n'


def parse_score_description(text: str) -> Dict[str, Any]:
    """纯中文乐谱文字描述 → score_spec（确定性，无 LLM、无 JSON）。"""
    if not (text or '').strip():
        raise ValueError('文字描述为空')
    # 拒绝旧版 JSON 提示词被误当作「文字描述」主路径的静默成功
    if _PROG_BEGIN in text and '【声部' not in text:
        raise ValueError(
            '这是旧版内嵌 JSON 的复现提示词；请使用含「【声部 N · …】」的纯文字描述')

    title = 'Untitled'
    m = re.search(r'【作品】《([^》]+)》', text)
    if m:
        title = m.group(1).strip()
    else:
        m = re.search(r'# 乐谱文字描述 ·《([^》]+)》', text)
        if m:
            title = m.group(1).strip()

    sharps, mode = 0, 'major'
    m = re.search(r'key_sharps\s*=\s*(-?\d+)', text)
    if m:
        sharps = int(m.group(1))
    m = re.search(r"mode\s*=\s*(major|minor)", text)
    if m:
        mode = m.group(1)

    meter = '4/4'
    m = re.search(r'(\d+/\d+)\s*，\s*共', text)
    if m:
        meter = m.group(1)
    bars = 8
    m = re.search(r'共\s*(\d+)\s*小节', text)
    if m:
        bars = int(m.group(1))
    tempo = 120
    m = re.search(r'♩\s*=\s*(\d+)', text)
    if m:
        tempo = int(m.group(1))
    seed = ''
    m = re.search(r'【复现种子】\s*(\S+)', text)
    if m:
        seed = m.group(1).strip()

    # 编制
    instruments: List[str] = []
    m = re.search(r'【编制】\s*(.+?)（共\s*(\d+)\s*声部）', text)
    if m:
        raw = m.group(1).strip()
        instruments = [_norm_instrument(x.strip()) for x in raw.split(',') if x.strip()]

    # 声部块（名称止于全角】，勿用 ASCII ]——否则会吞掉后续全部声部）
    part_blocks = list(re.finditer(
        r'【声部\s*(\d+)\s*·\s*([^】]+)】\s*\n(.*?)(?=\n【声部|\n【结束】|\Z)',
        text, re.S))
    if not part_blocks:
        raise ValueError('文字描述中未找到【声部 N · 名称】区块')

    parts_out: List[Dict[str, Any]] = []
    try:
        num, den = meter.split('/')
        cap = 4.0 * int(num) / int(den)
    except Exception:
        cap = 4.0

    for bm in part_blocks:
        pidx = int(bm.group(1))
        pname = bm.group(2).strip()
        body = bm.group(3)
        flat: List[List] = []
        for b in range(1, bars + 1):
            mm = re.search(rf'第{b}小节：\s*(.+)', body)
            if not mm:
                # 缺小节 → 全休止
                flat.append([None, _ql_to_dur(cap) if cap in K.DUR_QL.values() else 'whole', None])
                continue
            cells = [c.strip() for c in re.split(r'[、，,]', mm.group(1)) if c.strip()]
            bar_evs = [_parse_cn_event(c) for c in cells]
            used = sum(K.DUR_QL[str(e[1])] for e in bar_evs)
            if abs(used - cap) > 1e-6:
                raise ValueError(
                    f'声部{pidx} 第{b}小节时值 {used:g} ≠ {cap:g}：{mm.group(1)}')
            flat.extend(bar_evs)
        if not instruments or len(instruments) < pidx:
            instruments = list(instruments) + [_norm_instrument(pname)] * (
                pidx - len(instruments))
        else:
            # 保留编制行；声部名仅作 part name
            pass
        parts_out.append({
            'name': pname, 'program': None, 'clef': 'treble',
            'events': flat, 'dynamics': [],
        })

    # 按声部序号排序
    # part_blocks 已按出现序；校验数量
    if instruments and len(instruments) != len(parts_out):
        instruments = [_norm_instrument(p['name']) for p in parts_out]

    return {
        'title': title,
        'key_sharps': sharps,
        'mode': mode,
        'time_signature': meter,
        'tempo': tempo,
        'bars': bars,
        'seed': seed or hashlib.md5(text.encode('utf-8')).hexdigest()[:12],
        'parts': parts_out,
    }


def text_to_spec(text: str) -> Dict[str, Any]:
    """文字描述 → 经内核写回的 score_spec（音符级）。"""
    draft = parse_score_description(text)
    program = build_regeneration_program(draft)
    return K.run_program(program)


def text_to_musicxml(text: str, out_path: Optional[str] = None):
    """文字描述 → MusicXML（可选写盘）。返回 music21 Score。"""
    import llm_compose as L
    spec = text_to_spec(text)
    score = L.spec_to_music21(spec)
    if out_path:
        score.write('musicxml', fp=out_path)
    return score


def _deprecated(name: str, use: str) -> None:
    import warnings
    warnings.warn(f'score_reverse.{name} 已过期，请改用 {use}',
                  DeprecationWarning, stacklevel=3)


def build_regeneration_prompt(spec: Dict[str, Any],
                              program: Optional[Dict[str, Any]] = None) -> str:
    """【已过期】兼容旧名：返回纯中文乐谱文字描述（无 JSON）。"""
    _deprecated('build_regeneration_prompt', 'build_score_description')
    return build_score_description(spec)


def compile_reverse_prompt(prompt_text: str) -> Dict[str, Any]:
    """【已过期】兼容旧名：纯文字描述 → 内核 program；若含旧 JSON 块则仍可提取。"""
    _deprecated('compile_reverse_prompt', 'text_to_spec')
    if _PROG_BEGIN in (prompt_text or '') and '【声部' not in (prompt_text or ''):
        m = re.search(
            re.escape(_PROG_BEGIN) + r'\s*(.*?)\s*' + re.escape(_PROG_END),
            prompt_text or '', re.DOTALL)
        if not m:
            raise ValueError('提示词中未找到 REGENERATION_PROGRAM 块')
        program = json.loads(m.group(1))
        if not isinstance(program, dict) or not isinstance(program.get('calls'), list):
            raise ValueError('REGENERATION_PROGRAM 须为 {calls:[...]} 对象')
        return program
    draft = parse_score_description(prompt_text)
    return build_regeneration_program(draft)


def events_signature(spec: Dict[str, Any]) -> List[Tuple]:
    """用于逐音符比较的签名：(part, pitch_repr, dur)。"""
    sig: List[Tuple] = []
    for pi, part in enumerate(spec.get('parts') or []):
        for e in part.get('events') or []:
            pitch = e[0]
            if isinstance(pitch, (list, tuple)):
                pr = tuple(str(x) for x in pitch)
            else:
                pr = None if pitch is None else str(pitch)
            dur = str(e[1]) if len(e) > 1 else ''
            sig.append((pi, pr, dur))
    return sig


def specs_note_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return events_signature(a) == events_signature(b)


def reverse_score(spec: Dict[str, Any], *,
                  seed: Optional[str] = None) -> Dict[str, Any]:
    """主入口：spec → {description, prompt, program, manifest}。

    - description / prompt：纯中文逐音文字（无 JSON）——主产物
    - program：内部实现细节（可选落盘，不作为「提示词」）
    """
    if seed:
        spec = dict(spec)
        spec['seed'] = seed
    description = build_score_description(spec)
    program = build_regeneration_program(spec, seed=seed)
    raw = json.dumps(spec.get('parts') or [], ensure_ascii=False, sort_keys=True)
    manifest = {
        'title': spec.get('title'),
        'bars': spec.get('bars'),
        'voices': len(spec.get('parts') or []),
        'seed': program.get('seed'),
        'source_hash': hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16],
        'instruments': _instruments_from_spec(spec),
        'format': 'zh_score_description_v1',
    }
    return {
        'description': description,
        'prompt': description,  # 兼容旧字段名
        'program': program,
        'manifest': manifest,
    }


def musicxml_to_spec(path: str, *, max_bars: Optional[int] = None,
                     title_override: Optional[str] = None) -> Dict[str, Any]:
    """MusicXML → 最小 score_spec（供 reverse）。

    max_bars: 只取前 N 小节（用于莫扎特小夜曲全乐章截取）。
    跳过装饰音（ql≈0）；32 分音符映射为 32nd。
    """
    from music21 import converter, note as m21note, chord as m21chord
    from music21 import tempo as m21tempo, key as m21key, meter as m21meter

    score = converter.parse(path)
    title = title_override or 'Imported'
    if not title_override:
        md = getattr(score, 'metadata', None)
        if md:
            for cand in (getattr(md, 'bestTitle', None), getattr(md, 'title', None)):
                if cand and str(cand).strip():
                    title = str(cand).strip()
                    break
    tempo_n = 120
    mm = score.recurse().getElementsByClass(m21tempo.MetronomeMark).first()
    if mm is not None and getattr(mm, 'number', None):
        tempo_n = int(round(float(mm.number)))
    sharps = 0
    mode = 'major'
    ks = score.recurse().getElementsByClass(m21key.KeySignature).first()
    if ks is not None:
        sharps = int(ks.sharps)
    ky = score.recurse().getElementsByClass(m21key.Key).first()
    if ky is not None:
        sharps = int(ky.sharps)
        mode = 'minor' if 'minor' in str(ky.mode).lower() else 'major'
    meter = '4/4'
    ts = score.recurse().getElementsByClass(m21meter.TimeSignature).first()
    if ts is not None:
        meter = f'{ts.numerator}/{ts.denominator}'
    try:
        num, den = meter.split('/')
        cap = 4.0 * int(num) / int(den)
    except Exception:
        cap = 4.0

    parts_out = []
    file_bars = 0
    for part in score.parts:
        name = part.partName or part.id or 'Part'
        measures = list(part.getElementsByClass('Measure'))
        file_bars = max(file_bars, len(measures))
        if max_bars is not None:
            measures = measures[:max_bars]
        events: List[List] = []
        for m in measures:
            for el in m.notesAndRests:
                # 装饰音 / grace：跳过
                if getattr(el, 'duration', None) and getattr(el.duration, 'isGrace', False):
                    continue
                ql = float(el.quarterLength)
                if ql <= 1e-9:
                    continue
                try:
                    durs = [_ql_to_dur(ql)]
                except ValueError:
                    try:
                        durs = _split_ql_to_durs(ql)
                    except ValueError as e:
                        raise ValueError(
                            f'{name} 第 {getattr(m, "number", "?")} 小节：{e}'
                        ) from e
                for di, dur in enumerate(durs):
                    if el.isRest:
                        events.append([None, dur, None])
                    elif isinstance(el, m21chord.Chord):
                        pitches = [p.nameWithOctave for p in el.pitches[:4]]
                        events.append([pitches if di == 0 else pitches, dur, None])
                    elif isinstance(el, m21note.Note):
                        events.append([el.nameWithOctave, dur, None])
        parts_out.append({
            'name': name, 'program': None, 'clef': 'treble',
            'events': events, 'dynamics': [],
        })
    used_bars = max_bars if max_bars is not None else file_bars
    bars = max(8, int(used_bars or 8))
    # 若某声部事件时值不足 bars*cap，末尾补休止；超出则报错提示
    need = bars * cap
    legal = sorted(set(K.DUR_QL.values()), reverse=True)
    for p in parts_out:
        have = sum(K.DUR_QL.get(str(e[1]), 0.0) for e in p['events'])
        if have > need + 1e-6:
            # 截断多余（罕见：不完全拍）
            keep: List[List] = []
            acc = 0.0
            for e in p['events']:
                dq = K.DUR_QL.get(str(e[1]), 0.0)
                if acc + dq > need + 1e-9:
                    break
                keep.append(e)
                acc += dq
            p['events'] = keep
            have = acc
        rem = need - have
        while rem > 1e-6:
            step = next((d for d in legal if d <= rem + 1e-9), None)
            if step is None:
                break
            p['events'].append([None, _ql_to_dur(step), None])
            rem -= step
    seed_src = f'{os.path.abspath(path)}|{bars}|{title}'
    return {
        'title': title,
        'key_sharps': sharps,
        'mode': mode,
        'time_signature': meter,
        'tempo': tempo_n,
        'bars': bars,
        'seed': hashlib.md5(seed_src.encode()).hexdigest()[:12],
        'parts': parts_out,
        'source_path': path,
        'source_file_bars': file_bars,
    }


def mozart_nachtmusik_musicxml_path(*, short: bool = True) -> str:
    """music21 自带的莫扎特 K.525《G 大调弦乐小夜曲》Ground-Truth MusicXML。

    short=True → omr/k525GTshort.xml（约 20 小节摘录）
    short=False → omr/k525GTMvt1.xml（第一乐章全曲，约 137 小节）
    """
    import music21
    name = 'k525GTshort.xml' if short else 'k525GTMvt1.xml'
    path = os.path.join(os.path.dirname(music21.__file__), 'omr', name)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f'未找到 music21 OMR 金样 {name}（路径 {path}）。'
            f'请确认已安装 music21 且含 omr 数据包。')
    return path


def load_mozart_nachtmusik_spec(*, short: bool = True,
                                max_bars: Optional[int] = None
                                ) -> Dict[str, Any]:
    """加载莫扎特《一小夜曲》K.525 为 score_spec。

    默认用 GT short 摘录（可精确逆向）；全乐章可设 short=False，
    并用 max_bars 截取（全曲含三连音，截取段需避开三连音小节或先转换）。
    """
    path = mozart_nachtmusik_musicxml_path(short=short)
    title = 'Eine kleine Nachtmusik K.525'
    if short:
        title += ' (GT excerpt)'
    else:
        title += ' I. Allegro'
        if max_bars:
            title += f' (bars 1–{max_bars})'
    return musicxml_to_spec(path, max_bars=max_bars, title_override=title)


def reverse_path(path: str, *, out_dir: Optional[str] = None
                 ) -> Dict[str, Any]:
    """从 MusicXML 或 JSON score_spec 文件逆向并可选写盘。

    主产物：``score_description.md``（纯中文逐音文字，无 JSON）。
    """
    if path.lower().endswith('.json'):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data.get('compose'), dict) and data['compose'].get('score_spec'):
            spec = data['compose']['score_spec']
        elif data.get('parts'):
            spec = data
        else:
            raise ValueError(f'无法从 {path} 识别 score_spec')
    else:
        spec = musicxml_to_spec(path)
    result = reverse_score(spec)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        desc_path = os.path.join(out_dir, 'score_description.md')
        with open(desc_path, 'w', encoding='utf-8') as f:
            f.write(result['description'])
        # 兼容旧文件名
        with open(os.path.join(out_dir, 'regeneration_prompt.md'),
                  'w', encoding='utf-8') as f:
            f.write(result['description'])
        with open(os.path.join(out_dir, 'manifest.json'),
                  'w', encoding='utf-8') as f:
            json.dump(result['manifest'], f, ensure_ascii=False, indent=2)
        result['out_dir'] = out_dir
        result['description_path'] = desc_path
    return result


def replay_program(program: Dict[str, Any]) -> Dict[str, Any]:
    """【已过期】执行复现 program，返回 score_spec。"""
    _deprecated('replay_program', 'compose_kernel.run_program')
    return K.run_program(program)


def replay_description(text: str) -> Dict[str, Any]:
    """纯文字描述 → score_spec。"""
    return text_to_spec(text)


def golden_g_major_string_program(seed: str = 'g-serenade-gold-v1'
                                  ) -> Dict[str, Any]:
    """【已过期】合成弦乐快板样例；逆向主验收请用 load_mozart_nachtmusik_spec。"""
    _deprecated('golden_g_major_string_program', 'load_mozart_nachtmusik_spec')
    instruments = ['violin', 'violin', 'viola', 'cello', 'contrabass']
    return {
        'calls': [
            {'script': 'new_score', 'args': {
                'title': '晨光小夜曲·快板',
                'key_sharps': 1, 'mode': 'major', 'meter': '4/4',
                'tempo': 120, 'bars': 32, 'seed': seed,
                'voices': 5, 'instruments': instruments,
            }},
            {'script': 'set_sections', 'args': {'sections': [
                {'name': '呈示主部', 'bars': 8, 'dynamic': 'f',
                 'tempo_text': 'Allegro', 'bpm': 120},
                {'name': '呈示副部', 'bars': 8, 'dynamic': 'mf',
                 'tempo_text': 'Allegro', 'bpm': 120},
                {'name': '展开', 'bars': 8, 'dynamic': 'ff',
                 'tempo_text': 'Agitato', 'bpm': 126},
                {'name': '再现尾声', 'bars': 8, 'dynamic': 'f',
                 'tempo_text': 'Allegro', 'bpm': 120},
            ]}},
            {'script': 'set_harmony', 'args': {
                'section': '呈示主部',
                'progression': ['I', 'V', 'vi', 'IV', 'I', 'ii', 'V7', 'I'],
            }},
            {'script': 'accompaniment', 'args': {
                'section': '呈示主部', 'pattern': 'octave_pulse',
                'register': 'low', 'density': 1.0,
            }},
            {'script': 'melody', 'args': {
                'section': '呈示主部', 'register': 'high',
                'rhythm': 'sparse', 'contour': 'arch',
            }},
            {'script': 'set_harmony', 'args': {
                'section': '呈示副部',
                'progression': ['I', 'V7', 'I', 'IV', 'I', 'V7', 'I', 'I'],
                'key_sharps': 2, 'mode': 'major',
            }},
            {'script': 'accompaniment', 'args': {
                'section': '呈示副部', 'pattern': 'broken_arpeggio',
                'register': 'low', 'density': 1.0,
            }},
            {'script': 'melody', 'args': {
                'section': '呈示副部', 'register': 'high',
                'rhythm': 'sparse', 'contour': 'arch',
            }},
            {'script': 'set_harmony', 'args': {
                'section': '展开',
                'progression': ['i', 'VI', 'iv', 'V7', 'i', 'VI', 'V7', 'i'],
                'key_sharps': 1, 'mode': 'minor',
            }},
            {'script': 'accompaniment', 'args': {
                'section': '展开', 'pattern': 'chromatic_descent',
                'register': 'low', 'density': 1.0,
            }},
            {'script': 'melody', 'args': {
                'section': '展开', 'register': 'high',
                'rhythm': 'sparse', 'contour': 'wave',
            }},
            {'script': 'set_harmony', 'args': {
                'section': '再现尾声',
                'progression': ['I', 'IV', 'V7', 'I', 'I', 'V7', 'I', 'I'],
                'key_sharps': 1, 'mode': 'major',
            }},
            {'script': 'accompaniment', 'args': {
                'section': '再现尾声', 'pattern': 'octave_pulse',
                'register': 'low', 'density': 1.0,
            }},
            {'script': 'melody', 'args': {
                'section': '再现尾声', 'register': 'high',
                'rhythm': 'sparse', 'contour': 'ascending',
            }},
            {'script': 'cadence', 'args': {'section': '再现尾声', 'bars': 2}},
            {'script': 'narrative', 'args': {'appearances': False}},
            {'script': 'finalize', 'args': {}},
        ]
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description='乐谱逆向 → 纯中文文字描述；文字 → MusicXML')
    ap.add_argument('path', nargs='?', default=None,
                    help='MusicXML / score_spec JSON；或配合 --from-text 的文字文件')
    ap.add_argument('-o', '--out', default=None,
                    help='输出目录（默认 runs/reverse_<hash>/）')
    ap.add_argument('--replay', action='store_true',
                    help='逆向后把文字描述再编译回乐谱并校验音符一致')
    ap.add_argument('--from-text', action='store_true',
                    help='path 是文字描述文件：编译为 MusicXML')
    args = ap.parse_args(list(argv) if argv is not None else None)
    if not args.path:
        ap.print_help()
        return 2

    if args.from_text:
        with open(args.path, 'r', encoding='utf-8') as f:
            text = f.read()
        out = args.out or os.path.join(
            'runs', 'from_text_' + hashlib.md5(args.path.encode()).hexdigest()[:10])
        os.makedirs(out, exist_ok=True)
        xml_path = os.path.join(out, 'from_text.musicxml')
        text_to_musicxml(text, xml_path)
        print(f'文字 → MusicXML：{xml_path}')
        return 0

    out = args.out
    if not out:
        h = hashlib.md5(os.path.abspath(args.path).encode()).hexdigest()[:10]
        out = os.path.join('runs', f'reverse_{h}')
    result = reverse_path(args.path, out_dir=out)
    print(f'已写入 {out}/score_description.md（纯文字，无 JSON）')
    print(f"  seed={result['manifest']['seed']}  "
          f"voices={result['manifest']['voices']}  "
          f"bars={result['manifest']['bars']}")
    if args.replay:
        if args.path.lower().endswith('.json'):
            with open(args.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            src = (data.get('compose') or {}).get('score_spec') or data
        else:
            src = musicxml_to_spec(args.path)
        regen = text_to_spec(result['description'])
        ok = specs_note_equal(src, regen)
        print('文字→乐谱 音符一致：' + ('✅' if ok else '❌'))
        try:
            import llm_compose as L
            xml_out = os.path.join(out, 'from_description.musicxml')
            L.spec_to_music21(regen).write('musicxml', fp=xml_out)
            print(f'已导出 {xml_out}')
        except Exception as e:
            print(f'MusicXML 导出跳过：{e}')
        return 0 if ok else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
