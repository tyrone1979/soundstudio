"""记谱 / 演奏法 / 排版规则层。

报告（《符号音乐与器乐生成》第 4.3 节）指出符号生成的头号短板是
「产出的是一堆音符而非乐谱」——本模块就是补这一层。它把 spec/music21 分数
按**出版级排版惯例**规范化：

1. 等音拼写（调号一致、避免 `C#` 与 `Db` 混用）
2. 符干方向（多声部上符干/下符干；单声部以中线为准）
3. 连符尾分组（按拍号：4/4 按拍、6/8 按附点四分…）
4. 演奏法映射（profile 的 legato/tenuto/accent/staccato → 真实记号）
5. 可演奏性校验（音域、大跳、和弦跨度、密度）
6. 排版（系统换行、分页、排练记号）
7. 优先级仲裁 + 可解释日志（谁否掉了谁，全部留痕）

全部规则**可关**：`apply_notation(..., rules={'spelling':False, ...})`。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from music21 import articulations, beam, expressions, key as m21key
from music21 import layout, note as m21note, chord as m21chord, spanner
from music21 import pitch as m21pitch

# ---------------------------------------------------------------- 拼写规则

_LETTER_PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
_SHARP_ORDER = ['F', 'C', 'G', 'D', 'A', 'E', 'B']
_FLAT_ORDER = ['B', 'E', 'A', 'D', 'G', 'C', 'F']
#: (pc) → 两个常用拼法（letter, alter）
_PC_SPELLINGS: Dict[int, List[Tuple[str, int]]] = {
    1:  [('C', 1), ('D', -1)],
    3:  [('D', 1), ('E', -1)],
    6:  [('F', 1), ('G', -1)],
    8:  [('G', 1), ('A', -1)],
    10: [('A', 1), ('B', -1)],
}


def key_alter_for_letter(letter: str, sharps: int) -> int:
    """字母在给定调号里自带的变音记号（+1 升 / -1 降 / 0 本位）。"""
    if sharps > 0:
        return 1 if letter in _SHARP_ORDER[:sharps] else 0
    if sharps < 0:
        return -1 if letter in _FLAT_ORDER[:(-sharps)] else 0
    return 0


def tonic_pc_from_signature(sharps: int, mode: str = 'major') -> int:
    """调号 → 主音 pitch class（用于判断导音）。

    大调：主音 = sharps×7 半音；小调：再上移 9 半音（关系大调下方小三度）。
    """
    s = int(sharps) % 12
    base = (s * 7) % 12
    return (base + 9) % 12 if str(mode).lower().startswith('min') else base


def spell_name(pc: int, sharps: int, mode: str = 'major',
               tonic_pc: Optional[int] = None) -> str:
    """把 pitch class 拼成与调号一致、且变音记号最少的音名。

    判据（按优先级）：
    1. **导音**：距主音下方半音的音（小调里的 G#、大调里的 B→?）一律用升号拼写
       ——和声小调的导音拼成降号（Ab）会破坏其功能意义；
    2. 候选字母在调号里"自带"的变音与所需变音之差越小越好；
    3. 平局时倾向于调号的升/降方向。
    """
    pc = int(pc) % 12
    if pc not in _PC_SPELLINGS:
        for lt, v in _LETTER_PC.items():
            if v == pc:
                return lt
        return 'C'
    cands = _PC_SPELLINGS[pc]
    # 规则 1：导音（主音下方半音）强制升号拼写
    if tonic_pc is not None and pc == (int(tonic_pc) - 1) % 12:
        for letter, alter in cands:
            if alter > 0:
                return letter + '#' * alter
    return _spell_by_cost(cands, sharps, mode)


def _spell_by_cost(cands: List[Tuple[str, int]], sharps: int, mode: str) -> str:
    """按「调号自带变音差最小」挑拼法，平局时随调号升降方向。"""
    is_minor = str(mode).lower().startswith('min')
    best, best_cost = None, None
    for letter, alter in cands:
        cost = abs(alter - key_alter_for_letter(letter, int(sharps)))
        # 平局偏好：调号升号侧 → 升号拼法；降号侧 → 降号拼法
        if best_cost is not None and cost == best_cost:
            prefer_flat = sharps < 0 or (sharps == 0 and is_minor)
            if prefer_flat and alter < 0:
                best = (letter, alter)
            elif not prefer_flat and alter > 0:
                best = (letter, alter)
            continue
        if best_cost is None or cost < best_cost:
            best, best_cost = (letter, alter), cost
    letter, alter = best or cands[0]
    return letter + ('#' * alter if alter > 0 else 'b' * (-alter) if alter < 0 else '')


def midi_to_name(midi: int, sharps: int, mode: str = 'major',
                 tonic_pc: Optional[int] = None) -> str:
    """midi → 'Ab4' 形式的音名（含八度），按调号拼写。"""
    m = int(midi)
    return f'{spell_name(m % 12, sharps, mode, tonic_pc)}{m // 12 - 1}'


# ---------------------------------------------------------------- 符干规则

_CLEF_MIDLINE = {'treble': 71, 'bass': 50, 'alto': 60, 'tenor': 57}


def stem_direction(midi: int, clef: str = 'treble', voice: int = 0,
                   voices: int = 1) -> str:
    """符干方向。

    - 多声部（同一谱表两行）：高声部一律向上，低声部一律向下；
    - 单声部：以谱表中线为界，上方音向下、下方音向上（通用排版惯例）。
    """
    if voices >= 2:
        return 'up' if voice == 0 else 'down'
    mid = _CLEF_MIDLINE.get(str(clef).lower(), 71)
    return 'down' if int(midi) >= mid else 'up'


# ---------------------------------------------------------------- 符尾规则

def beat_group_size(ts: str) -> float:
    """连符尾的分组单位（以四分音符计）。

    4/4、3/4、2/4 → 每拍一组（1.0）；
    6/8、9/8、12/8 → 每附点四分一组（1.5）；
    2/2 → 每二分音符一组（2.0）。
    """
    try:
        num, den = str(ts or '4/4').split('/')
        num, den = int(num), int(den)
    except Exception:
        return 1.0
    if den <= 0:
        return 1.0
    # 复拍子（≥6 个八分音符且为 3 的倍数）按附点四分分组；3/8 是单拍子
    if den == 8 and num >= 6 and num % 3 == 0:
        return 1.5
    if den == 2:
        return 2.0
    return 4.0 / den


def beam_groups(durs_ql: Sequence[float], ts: str = '4/4') -> List[List[int]]:
    """给一小节的音符分连符尾组，返回索引分组。

    规则：组内总时值不超过一个分组单位；时值 ≥ 分组单位的音符不连符尾。
    """
    unit = beat_group_size(ts)
    groups: List[List[int]] = []
    cur: List[int] = []
    acc = 0.0
    for i, d in enumerate(durs_ql):
        d = float(d)
        if d >= unit - 1e-9:
            if cur:
                groups.append(cur)
                cur, acc = [], 0.0
            continue
        if acc + d > unit + 1e-9:
            if cur:
                groups.append(cur)
            cur, acc = [], 0.0
        cur.append(i)
        acc += d
    if len(cur) > 1:
        groups.append(cur)
    return groups


def apply_beams(notes: Sequence[Any], groups: Sequence[Sequence[int]]):
    """把分组结果写进 music21 音符的 beams。"""
    for gi, grp in enumerate(groups):
        if len(grp) < 2:
            continue
        # 单层符尾（八分音符组）；16 层往上需要按组内最短时值细分，
        # 当前内核最短时值为 0.25（16th），如需支持可在此扩展
        lv = 1
        for k, idx in enumerate(grp):
            n = notes[idx]
            if not isinstance(n, (m21note.Note,)):
                continue
            b = beam.Beams()
            t = 'start' if k == 0 else ('stop' if k == len(grp) - 1 else 'continue')
            bm = beam.Beam(type=t, number=lv)
            b.append(bm)
            n.beams = b


# ---------------------------------------------------------------- 演奏法

_ARTI_MAP = {
    'accent': lambda: articulations.Accent(),
    'tenuto': lambda: articulations.Tenuto(),
    'staccato': lambda: articulations.Staccato(),
    'marcato': lambda: articulations.StrongAccent(),
    'fermata': lambda: expressions.Fermata(),
}


def articulation_object(mark: str):
    mk = str(mark or '').strip().lower()
    f = _ARTI_MAP.get(mk)
    return f() if f else None


def apply_articulation(n, mark: str, log: Optional['RuleLog'] = None) -> bool:
    """给音符挂演奏法记号。legato 走连线（整小节），其余走 articulations。"""
    mk = str(mark or '').strip().lower()
    if not mk:
        return False
    if mk == 'legato':
        try:
            n.expressions.append(expressions.TextExpression('legato'))
            return True
        except Exception:
            return False
    obj = articulation_object(mk)
    if obj is None:
        return False
    try:
        n.articulations.append(obj)
        if log is not None:
            log.add('articulation', f'{mk}', f'挂 {type(obj).__name__}',
                    f'叙事/风格标记 {mk}', priority='style')
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 排版

def layout_plan(bars: int, ts: str = '4/4',
                sections: Optional[Sequence[Dict[str, Any]]] = None,
                bars_per_system: int = 4, systems_per_page: int = 4,
                measures_per_system_override: Optional[int] = None
                ) -> Dict[str, Any]:
    """系统换行 / 分页 / 排练记号。

    - 段落起点**必须**换行（乐句不能被切断）
    - 其余每 bars_per_system 小节换一行
    - 每 systems_per_page 行换一页
    - 排练记号 A/B/C… 打在每个段落起点
    """
    bars = max(1, int(bars))
    bps = max(1, int(measures_per_system_override or bars_per_system))
    sec_starts = []
    for s in (sections or []):
        try:
            st = int(s.get('start') or 0)
        except Exception:
            continue
        if 1 <= st <= bars:
            sec_starts.append(st)
    sec_starts = sorted(set(sec_starts))

    system_breaks = list(sec_starts)
    b = 1
    while b <= bars:
        if b not in system_breaks:
            system_breaks.append(b)
        b += bps
    system_breaks = sorted({x for x in system_breaks if 1 <= x <= bars})

    page_breaks: List[int] = []
    for i, sb in enumerate(system_breaks):
        if i > 0 and i % max(1, int(systems_per_page)) == 0:
            page_breaks.append(sb)

    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    rehearsal: Dict[int, str] = {}
    marks = sec_starts or [1]
    for i, sb in enumerate(marks):
        rehearsal[sb] = letters[i] if i < len(letters) else f'Z{i}'
    return {'system_breaks': system_breaks, 'page_breaks': page_breaks,
            'rehearsal_marks': rehearsal,
            'bars_per_system': bps, 'systems_per_page': systems_per_page}


# ---------------------------------------------------------------- 可演奏性

#: 时值名 → 四分音符数（与 llm_compose.DUR_QL 一致，此处自带一份以免循环导入）
_DUR_QL = {
    'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5,
    '16th': 0.25, '32nd': 0.125,
    'dotted-half': 3.0, 'dotted-quarter': 1.5, 'dotted-eighth': 0.75,
    'dotted-16th': 0.375,
    'half-dotted': 3.0, 'quarter-dotted': 1.5, 'eighth-dotted': 0.75,
}


def _ql_of(dur: str) -> float:
    """时值名 → 四分音符数；'6/4' 这类比值写法也支持，未知则按 1 拍。"""
    s = str(dur or '').strip()
    if s in _DUR_QL:
        return _DUR_QL[s]
    if '/' in s:
        try:
            nu, de = s.split('/')
            return float(nu) / float(de)
        except Exception:
            return 1.0
    try:
        return float(s)
    except Exception:
        return 1.0


#: 声部音域（midi）。未登记乐器用谱表默认。
_RANGE_BY_CLEF = {'treble': (40, 95), 'bass': (28, 67),
                  'alto': (45, 79), 'tenor': (40, 74)}
_RANGE_BY_NAME = {
    'violin': (55, 98), '小提琴': (55, 98),
    'viola': (48, 84), '中提琴': (48, 84), '中提': (48, 84),
    'cello': (36, 72), '大提琴': (36, 72), '大提': (36, 72),
    'flute': (60, 96), '长笛': (60, 96),
    'oboe': (58, 91), '双簧管': (58, 91),
    'clarinet': (50, 94), '单簧管': (50, 94),
    'bassoon': (34, 72), '巴松': (34, 72),
    'piccolo': (64, 103), '短笛': (64, 103),
    'trombone': (33, 74), '长号': (33, 74),
    'tuba': (28, 60), '大号': (28, 60),
    'harp': (23, 90), '竖琴': (23, 90),
    'horn': (34, 77), '圆号': (34, 77),
    'trumpet': (55, 88), '小号': (55, 88),
    'timpani': (36, 60), '定音鼓': (36, 60),
    'piano': (21, 108), '钢琴': (21, 108),
}


def part_range(name: str, clef: str) -> Tuple[int, int]:
    n = str(name or '').lower()
    for k, v in _RANGE_BY_NAME.items():
        if str(k).lower() in n:
            return v
    return _RANGE_BY_CLEF.get(str(clef or 'treble').lower(), (40, 95))


def has_named_range(name: str) -> bool:
    """该声部名是否命中了登记乐器的音域表（否则 part_range 走谱表默认）。"""
    n = str(name or '').lower()
    return any(str(k).lower() in n for k in _RANGE_BY_NAME)


def playability_issues(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """可演奏性校验：音域 / 大跳 / 和弦跨度 / 密度。"""
    out: List[Dict[str, Any]] = []
    if not spec:
        return out
    try:
        from music21 import pitch as m21pitch
    except Exception:
        return out

    def _midi(x):
        try:
            return m21pitch.Pitch(str(x)).midi
        except Exception:
            return None

    ts = str(spec.get('time_signature') or '4/4')
    bars = int(spec.get('bars') or 0)

    def _bar_len(tsx: str) -> float:
        try:
            nu, de = str(tsx or '4/4').split('/')
            return max(0.25, int(nu) * 4.0 / int(de))
        except Exception:
            return 4.0

    def _bar_grid() -> List[float]:
        """每小节起始的四分音符偏移（支持曲中换拍号）。"""
        changes: List[Tuple[int, str]] = []
        for c in spec.get('meter_changes') or []:
            try:
                if isinstance(c, (list, tuple)):
                    changes.append((int(c[0]), str(c[1])))
                elif isinstance(c, dict):
                    changes.append((int(c.get('bar') or c.get('start') or 0),
                                    str(c.get('ts') or c.get('time_signature') or '4/4')))
            except Exception:
                continue
        changes = sorted({(b, t) for b, t in changes if b >= 1})
        grid: List[float] = []
        t = 0.0
        cur = ts
        ci = 0
        n_bars = bars or 32
        for b in range(1, n_bars + 1):
            while ci < len(changes) and changes[ci][0] == b:
                cur = changes[ci][1]
                ci += 1
            grid.append(t)
            t += _bar_len(cur)
        grid.append(t)
        return grid

    grid = _bar_grid()

    for pi, p in enumerate(spec.get('parts') or []):
        name = str(p.get('name') or '')
        lo_r, hi_r = part_range(name, str(p.get('clef') or 'treble'))
        prev: Optional[int] = None
        t_cur = 0.0
        bar_counts: Dict[int, int] = {}
        for e in p.get('events') or []:
            if not isinstance(e, (list, tuple)) or len(e) < 2:
                continue
            dur = e[1]
            # 当前事件落在第几小节
            bi = 0
            for k in range(len(grid) - 1):
                if grid[k] <= t_cur + 1e-9 < grid[k + 1] - 1e-9:
                    bi = k
                    break
            else:
                bi = len(grid) - 2
            mids: List[int] = []
            raw = e[0]
            items = raw if isinstance(raw, (list, tuple)) else [raw]
            for it in items:
                m = _midi(it)
                if m is not None:
                    mids.append(m)
            if not mids:
                continue
            bar_counts[bi] = bar_counts.get(bi, 0) + 1
            top = max(mids)
            bot = min(mids)
            if bot < lo_r or top > hi_r:
                out.append({'level': 'MAJOR', 'part': pi + 1,
                            'kind': 'range',
                            'message': f'声部{pi + 1}（{name}）音 {top if top > hi_r else bot} '
                                       f'超出音域 {lo_r}–{hi_r}'})
            if len(mids) > 1 and (top - bot) > 12:
                out.append({'level': 'MINOR', 'part': pi + 1,
                            'kind': 'span',
                            'message': f'声部{pi + 1} 和弦跨度 {top - bot} > 八度，'
                                       f'单手/单声部不易演奏'})
            if prev is not None:
                leap = abs(mids[0] - prev)
                if leap > 24:
                    out.append({'level': 'MAJOR', 'part': pi + 1,
                                'kind': 'leap',
                                'message': f'声部{pi + 1} 连续大跳 {leap} 半音（>两个八度）'})
                elif leap > 12:
                    out.append({'level': 'MINOR', 'part': pi + 1,
                                'kind': 'leap',
                                'message': f'声部{pi + 1} 大跳 {leap} 半音（>八度）'})
            prev = mids[0]
            try:
                t_cur += _ql_of(str(dur))
            except Exception:
                pass
        # 密度：逐小节统计事件数（此前把整声部当一小节，误报）
        worst_bar, worst_n = -1, 0
        for b, c in bar_counts.items():
            if c > worst_n:
                worst_bar, worst_n = b, c
        if worst_n > 24:
            out.append({'level': 'MINOR', 'part': pi + 1, 'kind': 'density',
                        'message': f'声部{pi + 1} 第 {worst_bar + 1} 小节事件数 {worst_n}，'
                                   f'密度过高（>24）'})
    return out


# ---------------------------------------------------------------- 仲裁日志

_PRIORITY = {'correctness': 100, 'style': 60, 'layout': 30, 'decoration': 10}


class RuleLog:
    """可解释日志：每条规则做了什么、被谁覆盖。"""

    def __init__(self):
        self.entries: List[Dict[str, Any]] = []

    def add(self, rule: str, target: str, action: str, reason: str,
            priority: str = 'style', overridden: str = ''):
        self.entries.append({'rule': rule, 'target': str(target),
                             'action': action, 'reason': reason,
                             'priority': priority,
                             'rank': _PRIORITY.get(priority, 0),
                             'overridden': overridden})

    def arbitrate(self, target: str, candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """在同一目标上竞争的多条规则里选优先级最高的，并记录被否者。"""
        if not candidates:
            return {}
        ranked = sorted(candidates,
                        key=lambda c: _PRIORITY.get(c.get('priority', 'style'), 0),
                        reverse=True)
        win = ranked[0]
        for loser in ranked[1:]:
            self.add(str(loser.get('rule') or ''), target, '被否决',
                     f"优先级低于 {win.get('rule')}（{loser.get('priority')} < "
                     f"{win.get('priority')}）",
                     priority=str(loser.get('priority') or 'style'),
                     overridden=str(win.get('rule') or ''))
        self.add(str(win.get('rule') or ''), target,
                 str(win.get('action') or ''), str(win.get('reason') or ''),
                 priority=str(win.get('priority') or 'style'))
        return win

    @property
    def conflicts(self) -> int:
        return sum(1 for e in self.entries if e.get('overridden'))

    def render(self, limit: int = 40) -> str:
        if not self.entries:
            return '（无记谱规则生效）'
        lines = [f'共 {len(self.entries)} 条，其中仲裁 {self.conflicts} 次']
        for e in self.entries[:limit]:
            tag = f"[{e['rule']}/{e['priority']}]"
            if e['overridden']:
                lines.append(f'{tag} {e["target"]} — {e["action"]}'
                             f'（让位于 {e["overridden"]}）')
            else:
                lines.append(f'{tag} {e["target"]} — {e["action"]}：{e["reason"]}')
        if len(self.entries) > limit:
            lines.append(f'…另有 {len(self.entries) - limit} 条')
        return '\n'.join(lines)


# ---------------------------------------------------------------- 应用

_DEFAULT_RULES = {'spelling': True, 'stem': True, 'beams': True,
                  'articulation': True, 'layout': True, 'rehearsal': True}


def apply_notation(score, spec: Optional[Dict[str, Any]] = None,
                   rules: Optional[Dict[str, bool]] = None,
                   profile: Optional[Dict[str, Any]] = None,
                   log: Optional[RuleLog] = None) -> RuleLog:
    """把记谱规则应用到 music21 分数上（就地修改，返回日志）。"""
    log = log or RuleLog()
    r = dict(_DEFAULT_RULES)
    if rules:
        r.update({k: bool(v) for k, v in rules.items() if k in r})
    # 生成器已按和声功能拼写（如 g 小调 V/v 的 C#）时，调号式等音改写会破坏功能
    if (spec or {}).get('spelling_locked'):
        r['spelling'] = False

    try:
        sharps = 0
        mode = str((spec or {}).get('mode') or 'major').lower()
        if mode not in ('major', 'minor'):
            mode = 'major'
        for ks in score.getElementsByClass(m21key.KeySignature):
            sharps = int(ks.sharps)
            break
        # 主音：优先 spec 里的调名，退回调号推算（用于导音拼写）
        tonic_pc: Optional[int] = None
        try:
            kn = str((spec or {}).get('key') or '').strip()
            if kn:
                # 完整调名解析（如 'Bb'、'F#'、'Eb'）：只取首字母会丢变音记号
                tonic_pc = int(m21pitch.Pitch(kn).pitchClass)
        except Exception:
            tonic_pc = None
        if tonic_pc is None:
            tonic_pc = tonic_pc_from_signature(sharps, mode)
        leading_pc = (int(tonic_pc) - 1) % 12
        # 段落级转调：逐小节解析当前调号（spec['key_changes'] = [[bar,sharps,mode],…]），
        # 转调段之后的等音拼写按新调进行
        key_changes: List[Tuple[int, int, str]] = []
        for kc in ((spec or {}).get('key_changes') or []):
            try:
                kb, ks_, km_ = int(kc[0]), int(kc[1]), str(kc[2])
            except (TypeError, ValueError, IndexError):
                continue
            if km_ in ('major', 'minor'):
                key_changes.append((kb, ks_, km_))
        key_changes.sort()

        def _key_at(bar: int) -> Tuple[int, str]:
            sh, md = sharps, mode
            for b, s2, m2 in key_changes:
                if bar >= b:
                    sh, md = s2, m2
            return sh, md
        parts = list(score.parts) or [score]
        voices = len(parts)

        # 段落表：优先 spec['sections']（含 start/end/name）
        sections: List[Dict[str, Any]] = []
        if spec:
            sections = [s for s in (spec.get('sections') or [])
                        if isinstance(s, dict) and s.get('start')]
        bars = int((spec or {}).get('bars') or 0) or (
            len(parts[0].getElementsByClass('Measure')) if parts else 0)
        ts = str((spec or {}).get('time_signature') or '4/4')

        # 叙事演奏法：{bar: mark}
        nar_marks: Dict[int, str] = {}
        nar = ((spec or {}).get('narrative') or {})
        for k, v in (nar.get('marks') or {}).items():
            try:
                nar_marks[int(k)] = str(v)
            except Exception:
                continue

        # 每小节的偏移量（用于排练记号/换行）
        bar_offsets: Dict[int, float] = {}
        if parts:
            for mi, m in enumerate(parts[0].getElementsByClass('Measure'), start=1):
                try:
                    bar_offsets[mi] = float(m.getOffsetInHierarchy(parts[0]))
                except Exception:
                    bar_offsets[mi] = 0.0

        plan = layout_plan(bars, ts, sections)
        sys_breaks = set(plan['system_breaks'])
        page_breaks = set(plan['page_breaks'])
        rehearsal = plan['rehearsal_marks']

        #: 每条规则实际改动的次数（日志按规则汇总，避免逐音符刷屏）
        cnt = {'spelling': 0, 'stem': 0, 'beams': 0, 'articulation': 0,
               'layout': 0, 'rehearsal': 0}
        _SPELL_LOG_CAP = 12      # 拼写改动最多逐条列 12 条，其余计入汇总

        for pi, part in enumerate(parts):
            clef = 'treble'
            try:
                for c in part.getElementsByClass('Clef'):
                    clef = str(c.sign or 'G')
                    clef = {'G': 'treble', 'F': 'bass', 'C': 'alto'}.get(clef, 'treble')
                    break
            except Exception:
                pass
            part_name = ''
            try:
                part_name = str(part.partName or '')
            except Exception:
                pass

            for mi, m in enumerate(part.getElementsByClass('Measure'), start=1):
                notes = [n for n in m.recurse().notes
                         if isinstance(n, (m21note.Note, m21chord.Chord))]
                if not notes:
                    continue

                # --- 等音拼写 ---
                if r['spelling']:
                    # 本小节所在段落（可能已转调）的调号/主音
                    m_sharps, m_mode = _key_at(mi)
                    m_tonic = tonic_pc_from_signature(m_sharps, m_mode)
                    m_leading = (int(m_tonic) - 1) % 12
                    for n in notes:
                        pitches = (n.pitches if isinstance(n, m21chord.Chord)
                                   else [n.pitch])
                        for p in pitches:
                            try:
                                want = spell_name(int(p.pitchClass), m_sharps,
                                                  m_mode, m_tonic)
                                if p.name != want:
                                    old = p.name
                                    octv = p.octave
                                    p.name = want
                                    p.octave = octv
                                    cnt['spelling'] += 1
                                    if cnt['spelling'] <= _SPELL_LOG_CAP:
                                        why = ('导音：主音下方半音按功能拼升号'
                                               if int(p.pitchClass) == m_leading
                                               else f'调号 {m_sharps} 下变音记号更少')
                                        log.add('spelling', f'p{pi + 1}m{mi} {old}',
                                                f'→ {want}', why,
                                                priority='correctness')
                            except Exception:
                                continue

                # --- 符干 ---
                if r['stem']:
                    for n in notes:
                        try:
                            pitches = (n.pitches if isinstance(n, m21chord.Chord)
                                       else [n.pitch])
                            mid = sum(p.midi for p in pitches) / len(pitches)
                            want = stem_direction(mid, clef, pi, voices)
                            if n.stemDirection != want:
                                n.stemDirection = want
                                cnt['stem'] += 1
                        except Exception:
                            continue

                # --- 连符尾 ---
                if r['beams']:
                    durs = [float(n.duration.quarterLength) for n in notes]
                    plain = [n for n in notes if isinstance(n, m21note.Note)]
                    if len(plain) == len(notes) and len(notes) > 1:
                        grps = beam_groups(durs, ts)
                        if grps:
                            apply_beams(notes, grps)
                            cnt['beams'] += sum(len(g) for g in grps)

                # --- 演奏法 ---
                if r['articulation']:
                    mark = nar_marks.get(mi)
                    if mark and notes:
                        if apply_articulation(notes[0], mark, log):
                            cnt['articulation'] += 1

                # --- 排版：换行 / 分页 ---
                if r['layout'] and pi == 0:
                    if mi in sys_breaks:
                        try:
                            sl = layout.SystemLayout()
                            sl.isNew = True
                            m.insert(0, sl)
                            cnt['layout'] += 1
                        except Exception:
                            pass
                    if mi in page_breaks:
                        try:
                            pl = layout.PageLayout()
                            pl.isNew = True
                            m.insert(0, pl)
                        except Exception:
                            pass

                # --- 排练记号 ---
                if r['rehearsal'] and pi == 0 and mi in rehearsal:
                    try:
                        m.insert(0, expressions.TextExpression(rehearsal[mi]))
                        cnt['rehearsal'] += 1
                    except Exception:
                        pass

        # --- legato 连线（叙事标记）---
        if r['articulation']:
            for pi, part in enumerate(parts):
                for mi, m in enumerate(part.getElementsByClass('Measure'), start=1):
                    if nar_marks.get(mi) != 'legato':
                        continue
                    ns = [n for n in m.recurse().notes
                          if isinstance(n, (m21note.Note, m21chord.Chord))]
                    if len(ns) >= 2:
                        try:
                            sl = spanner.Slur()
                            for n in ns:
                                sl.addSpannedElements([n])
                            part.insert(0, sl)
                            cnt['articulation'] += 1
                        except Exception:
                            pass

        # --- 汇总：每条规则改了多少处（可解释性主体）---
        _RULE_DESC = {
            'spelling': '等音拼写统一到调号',
            'stem': '符干方向',
            'beams': '连符尾分组',
            'articulation': '演奏法记号',
            'layout': '系统换行',
            'rehearsal': '排练记号',
        }
        for rule, n in cnt.items():
            if n and r.get(rule):
                log.add(rule, f'{n} 处', '已应用', _RULE_DESC.get(rule, rule),
                        priority='correctness' if rule == 'spelling' else 'layout')
    except Exception:
        # 记谱层不能让出谱失败
        try:
            log.add('notation', 'score', '跳过', '记谱层异常（已忽略）',
                    priority='correctness')
        except Exception:
            pass
    return log


def notation_report(spec: Dict[str, Any], log: Optional[RuleLog] = None) -> str:
    """给报告面板用的记谱小节。"""
    issues = playability_issues(spec)
    lines = ['## 记谱与演奏法', '']
    if log is not None:
        rendered = log.render(limit=12).split('\n')
        lines.append(f'**规则日志**（{rendered[0]}）')
        lines.append('')
        for entry in rendered[1:]:
            lines.append(f'- {entry}')
        lines.append('')
    if not issues:
        lines.append('可演奏性：✅ 无音域/大跳/跨度/密度问题')
    else:
        major = [i for i in issues if i['level'] == 'MAJOR']
        lines.append(f'可演奏性：{len(major)} 项 MAJOR / {len(issues)} 项总计')
        for i in issues[:8]:
            lines.append(f"- [{i['level']}] {i['message']}")
        if len(issues) > 8:
            lines.append(f'- …其余 {len(issues) - 8} 项从略')
    return '\n'.join(lines)
