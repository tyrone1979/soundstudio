# -*- coding: utf-8 -*-
"""确定性作曲内核：LLM 只翻译（输出脚本调用序列），音符全部由脚本生成。

设计契约：
- run_program(program) 执行 {"calls": [{"script": ..., "args": {...}}]}
- 每个脚本对输入做严格校验，违反即抛 KernelError（可回喂翻译器修正）
- 生成器保证：逐小节时值精确填满、调内记谱（除明确的离调参数）、
  每段必有力度与织体参数、无休止塌陷、终止落在主音
- kernel_docs() 自动生成脚本文档 → 注入翻译器提示词，知识与实现永不脱节
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from music21 import key as m21key
from music21 import pitch as m21pitch
from music21 import scale as m21scale
from notation import part_range

# 全量时值（含附点）：供 notes 显式音符级控制（验证 token 合法性 + 正向映射）。
DUR_QL = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5,
          '16th': 0.25, '32nd': 0.125, 'dotted_quarter': 1.5, 'dotted_eighth': 0.75}
# 自动织体/填充只认「基础」时值（不含附点）：保证既有指纹与风格复现不被附点时值
# 渗入自动生成（附点只出现在用户用 notes 显式指定的音符上）。QL_DUR 是反向映射，
# 仅用于自动填充的 cap/hits 整除查找与 QL→名 还原，故取基础集。
_QL_DUR_BASE = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5, '16th': 0.25}
# 反向映射须覆盖 DUR_QL 全部键 + _LEGAL_DURS 可能出现的附点，
# 否则 melody 槽位抽到 1.5/0.75/3.0 会 KeyError。
QL_DUR = {v: k for k, v in DUR_QL.items()}
for _n, _v in _QL_DUR_BASE.items():
    QL_DUR[_v] = _n
QL_DUR.setdefault(3.0, 'dotted_half')
DUR_QL.setdefault('dotted_half', 3.0)

REGISTER_CENTERS = {'low': 62, 'mid': 71, 'high': 79, 'very_high': 84}
BASS_OCTS = {'low': 2, 'mid': 3}
ACC_PATTERNS = ('sustained', 'broken_arpeggio', 'alberti', 'block_chords',
                'octave_pulse', 'chromatic_descent', 'waltz', 'mazurka',
                'walking_bass', 'comping')
RHY_STYLES = ('sparse', 'flowing', 'fragmented', 'driving')
CONTOURS = ('arch', 'wave', 'descending', 'ascending')

# 声部布局（自上而下）：2 声部=钢琴 RH/LH，也是二声部创意曲；3/4 声部=赋格
_VOICE_PARTS = {
    2: [('Piano RH', 'treble'), ('Piano LH', 'bass')],
    3: [('Soprano', 'treble'), ('Alto', 'treble'), ('Bass', 'bass')],
    4: [('Soprano', 'treble'), ('Alto', 'treble'), ('Tenor', 'treble'),
        ('Bass', 'bass')],
}

# 乐器 → 谱号提示（与 llm_compose.INSTRUMENT_FACTORY 的乐器名对应）。
# new_score 传 instruments 时按此配谱号；未列出的乐器沿用声部位置默认谱号。
_INSTR_CLEF = {
    'flute': 'treble', 'oboe': 'treble', 'piccolo': 'treble',
    'clarinet': 'treble', 'horn': 'treble', 'trumpet': 'treble',
    'violin': 'treble', 'viola': 'alto',
    'cello': 'bass', 'violoncello': 'bass', 'contrabass': 'bass',
    'bassoon': 'bass', 'trombone': 'bass', 'tuba': 'bass',
    'timpani': 'bass', 'harp': 'treble', 'piano': 'treble',
    'soprano': 'treble', 'alto': 'treble', 'tenor': 'treble', 'bass': 'bass',
}


class KernelError(Exception):
    """脚本调用违约（参数错误/顺序错误/完整性错误），可回喂 LLM 修正。"""


def _rng(*key_parts) -> random.Random:
    seed = hashlib.md5('|'.join(str(k) for k in key_parts).encode('utf-8')).hexdigest()
    return random.Random(seed)


#: 种子分枝的维度：主 seed 派生出各自独立的子种子，
#: 于是「换旋律但保留和声」「只换织体」这类局部重生成成为可能。
SUBSEED_DIMS = ('melody', 'harmony', 'texture', 'narrative', 'decor',
                'rhythm', 'fugue')


def derive_subseeds(seed: str, dims: Sequence[str] = SUBSEED_DIMS,
                    override: Optional[Dict[str, str]] = None
                    ) -> Dict[str, str]:
    """主种子 → 各维度子种子（确定性；override 可钉住某一维）。

    当前**真正在采样中使用**的维度：``melody``（旋律节奏/音高走向）、
    ``fugue``（赋格主题原型）、``narrative``（戏剧弧线/演奏法/惊喜）。
    ``harmony``/``texture``/``decor`` 已预留但尚无可采样素材（和声与织体
    目前由风格包直接指定，非随机），保留以便后续接入时不影响既有复现性。

    注意：末段的平行五八度自动修正会读外声部，因此改 melody 也可能让
    低声部个别音被动调整——这是有意的（宁可修正确，不要维度僵化）。
    """
    s = str(seed or '')
    out = {}
    for d in dims:
        v = (override or {}).get(d)
        out[d] = str(v).strip() if v else hashlib.md5(
            f'{s}|{d}'.encode('utf-8')).hexdigest()[:12]
    return out


def _weighted_choice(rng: random.Random, items: Sequence[Any],
                     weights: Optional[Dict[str, float]] = None,
                     keyfn=None):
    """按软权重采样。

    ``weights`` 是 {素材键: 权重倍数}；未登记的素材权重 = 1.0（中性）。
    权重为 0 的素材永不被选中，但**表内若无任何可选素材则退回等概率**——
    软权重只能"偏置"确定性素材表，不能让生成失败。
    """
    if not items:
        raise KernelError('_weighted_choice：候选为空')
    if not weights:
        return rng.choice(list(items))
    keyfn = keyfn or (lambda x: str(x))
    ws = []
    for it in items:
        try:
            w = float(weights.get(keyfn(it), 1.0))
        except Exception:
            w = 1.0
        ws.append(w if w > 0 else 0.0)
    total = sum(ws)
    if total <= 0:                       # 全被压到 0：退回等概率，不硬失败
        return rng.choice(list(items))
    r = rng.random() * total
    acc = 0.0
    for it, w in zip(items, ws):
        acc += w
        if r <= acc:
            return it
    return list(items)[-1]


def _rhythm_key(tpl: Sequence[Optional[float]]) -> str:
    """节奏模板 → 稳定键（软权重用）：'1|0.5|0.5|1'（休止记 r）。"""
    return '|'.join('r' if x is None else f'{float(x):g}' for x in tpl)


def _rhythm_props(tpl: Sequence[Optional[float]]) -> Dict[str, bool]:
    """节奏模板的**可解释属性**，供风格软权重按属性加权（不必列举模板）。"""
    vals = [float(x) for x in tpl if x is not None]
    n = len(vals)
    distinct = len({round(v, 6) for v in vals})
    return {
        'uniform': n > 0 and distinct == 1,      # 全等时值（脉搏/固定音型）
        'rest': any(x is None for x in tpl),     # 含休止（碎片化）
        'long_start': bool(vals) and vals[0] >= 1.0,   # 起于长音（歌唱性）
        'short_end': bool(vals) and vals[-1] <= 0.5,   # 收于短音（舞曲感）
        'dense': n >= 4,
        'sparse': n <= 3,
        'even_pairs': n >= 2 and all(
            abs(vals[i] - vals[i + 1]) < 1e-9 for i in range(0, n - 1, 2)),
    }


def _rhythm_weights_for(table: Sequence[Sequence[Optional[float]]],
                        weights: Optional[Dict[str, float]]
                        ) -> Optional[Dict[str, float]]:
    """把「属性软权重」展开成这张模板表的逐条权重。

    权重可写两种键：模板稳定键（'1|0.5|0.5|1'）或属性名（uniform / rest /
    long_start / short_end / dense / sparse / even_pairs）。同一模板命中的
    多个权重**相乘**，因此可以给"均匀 + 密集"这类组合偏好。
    """
    if not weights:
        return None
    out: Dict[str, float] = {}
    for tpl in table:
        w = 1.0
        key = _rhythm_key(tpl)
        if key in weights:
            w *= max(0.0, float(weights[key]))
        for prop, ok in _rhythm_props(tpl).items():
            if ok and prop in weights:
                w *= max(0.0, float(weights[prop]))
        out[key] = w
    return out


def _sp(name: str) -> str:
    """music21 的 'A-' 降号风格 → 本项目通用的 'Ab' 风格（Chord 字符串解析只认后者）。"""
    return str(name).replace('-', 'b')


def _scale_names(sharps: int, mode: str) -> List[str]:
    """调内音名（'Ab' 拼写风格），**以主音开头**（级数 = 索引+1）。"""
    mode = 'minor' if mode == 'minor' else 'major'
    k = m21key.KeySignature(int(sharps)).asKey(mode)
    sc = m21scale.MinorScale(k.tonic) if mode == 'minor' else m21scale.MajorScale(k.tonic)
    names = [_sp(p.name) for p in sc.getPitches('C0', 'B0')]
    tonic = _sp(k.tonic.name)
    i = names.index(tonic) if tonic in names else 0
    return names[i:] + names[:i]


# ---------- 和声：罗马数字 → 和弦音名 ----------

_ROMAN_VAL = {'I': 0, 'II': 1, 'III': 2, 'IV': 3, 'V': 4, 'VI': 5, 'VII': 6}
_ROMAN_RE = re.compile(r'^([b#]*)([IViv]+)(.*)$')


def parse_roman(token: str, scale_names: List[str]) -> List[str]:
    """'V7' / 'vii°7' / 'bII' → 和弦音名列表（无八度）。"""
    m = _ROMAN_RE.match(str(token).strip())
    if not m:
        raise KernelError(f'无法解析和声标记 {token!r}（应为罗马数字如 I / V7 / vii°7 / bII）')
    accs, roman, suffix = m.groups()
    up = roman.upper()
    if up not in _ROMAN_VAL:
        raise KernelError(f'和声级数超范围 {token!r}（I–VII）')
    deg = _ROMAN_VAL[up]
    if deg >= len(scale_names):
        raise KernelError(f'和声级数超范围 {token!r}')
    root = m21pitch.Pitch(scale_names[deg])
    for a in accs:
        root = root.transpose(1 if a == '#' else -1)
    is_major = roman[0].isupper()
    s = suffix.replace(' ', '')
    if s == '':
        ivs = [0, 4, 7] if is_major else [0, 3, 7]
    elif s == '7':
        ivs = [0, 4, 7, 10] if is_major else [0, 3, 7, 10]
    elif s in ('°7', 'o7', 'dim7'):
        ivs = [0, 3, 6, 9]
    elif s in ('°', 'o', 'dim'):
        ivs = [0, 3, 6]
    elif s in ('ø7', 'ø', 'half-dim'):
        ivs = [0, 3, 6, 10]
    elif s in ('+', 'aug'):
        ivs = [0, 4, 8]
    elif s in ('maj7', 'M7'):
        ivs = [0, 4, 7, 11]
    elif s == '6':
        # 加六和弦：大调 I6/IV6、小调 i6/iv6（交响/室内乐常见色彩，
        # 给旋律多一个可用音，且不像 I7 那样把主和弦变成属七性质）
        ivs = [0, 4, 7, 9] if is_major else [0, 3, 7, 9]
    elif s == '9':
        # 九和弦：属九（V9）/ 小九（ii9）——五音和弦，素材面最宽的一种
        ivs = [0, 4, 7, 10, 14] if is_major else [0, 3, 7, 10, 14]
    elif s in ('maj9', 'M9'):
        ivs = [0, 4, 7, 11, 14]
    elif s in ('sus4', 'sus'):
        ivs = [0, 5, 7]
    else:
        raise KernelError(
            f'未知和弦后缀 {suffix!r}'
            f'（支持 "" 7 ° °7 ø7 + maj7 6 9 maj9 sus4）')
    # 注意：music21 的 transpose(0) 会做等音重拼，根音直接取原名；
    # 其余音若与调内音同音高则优先调内拼写（V 的五音 Ab 而非 G#）
    out = [_sp(root.name)]
    for iv in ivs[1:]:
        n = _sp(root.transpose(iv).name)
        sc_name = next((s for s in scale_names if _pc(s) == _pc(n)), None)
        out.append(sc_name if sc_name is not None else n)
    return out


_CHORD_SYM_RE = re.compile(
    r'^\s*([A-Ga-g])([#b]?)\s*'
    r'((?:m|maj|min|dim|aug|\+|°|o|7|9|11|13|6|5|sus[24]|add\d+|'
    r'maj7|M7|m7|dim7|°7|ø7|ø|o7|m7b5|m7-5)*)'
    r'(?:/[A-Ga-g][#b]?)?\s*$'
)


def _parse_chord_symbol(sym: str, scale_names: List[str]) -> List[str]:
    """绝对和弦符号 → 和弦音名列表（无八度），如 'Dm7'/'G7'/'Cmaj7'/
    'C#dim7'/'Am7b5'/'Ebmaj7'。与 parse_roman 同款：能落到调内的音优先用调内拼写。
    """
    m = _CHORD_SYM_RE.match(str(sym).strip())
    if not m:
        raise KernelError(
            f'无法解析和弦符号 {sym!r}（支持如 Dm7 / G7 / Cmaj7 / C#dim7 / Am7b5）')
    letter, acc, qual = m.groups()
    root = m21pitch.Pitch(letter + (acc or ''))
    q = (qual or '').replace(' ', '')
    # 品质 → 音程（半音）
    if q in ('m', 'min'):
        ivs = [0, 3, 7]
    elif q in ('m7', 'min7'):
        ivs = [0, 3, 7, 10]
    elif q == 'm7b5' or q == 'm7-5':
        ivs = [0, 3, 6, 10]
    elif q in ('maj7', 'M7'):
        ivs = [0, 4, 7, 11]
    elif q == '7':
        ivs = [0, 4, 7, 10]
    elif q == 'dim' or q == '°' or q == 'o':
        ivs = [0, 3, 6]
    elif q in ('dim7', '°7', 'o7'):
        ivs = [0, 3, 6, 9]
    elif q == 'ø7' or q == 'ø':
        ivs = [0, 3, 6, 10]
    elif q == '+':
        ivs = [0, 4, 8]
    elif q == '6':
        ivs = [0, 4, 7, 9]
    elif q == '5':
        ivs = [0, 7]
    elif q == '9':
        # 修：原本 9/11/13 一律退化成属七 [0,4,7,10]，九音被丢掉 —— 写了
        # 'G9' 却拿不到 A。现在真正返回五音，旋律素材面才是 5 个音。
        ivs = [0, 4, 7, 10, 14]
    elif q == 'm9':
        ivs = [0, 3, 7, 10, 14]
    elif q in ('maj9',):
        ivs = [0, 4, 7, 11, 14]
    elif q in ('11', '13'):
        ivs = [0, 4, 7, 10, 14]
    elif q.startswith('sus'):
        ivs = [0, 5 if '4' in q else 2, 7]
    elif q.startswith('add'):
        ivs = [0, 4, 7]
    else:
        ivs = [0, 4, 7]
    out = [_sp(root.name)]
    for iv in ivs[1:]:
        n = _sp(root.transpose(iv).name)
        sc_name = next((s for s in scale_names if _pc(s) == _pc(n)), None)
        out.append(sc_name if sc_name is not None else n)
    return out


def _is_chord_symbol(token: str) -> bool:
    """token 是否像绝对和弦符号（以 A–G 音名开头），而非罗马数字。

    关键点：带前置临时记号的罗马数字（bII、#iv、bVII）必须以罗马规则优先识别，
    否则 'b'/'#' 会被当成和弦根音的升降号，把 bII 误判成「B 大三和弦」并跳过风格契约。
    所以先过罗马正则，命中即判为罗马；否则再看是否以音名 A–G（含 #/b）开头。
    """
    t = str(token).strip()
    if _ROMAN_TOKEN_RE.match(t):
        return False
    return bool(re.match(r'^[A-Ga-g][#b]?', t))


# ---------- 内核状态 ----------

class _State:
    def __init__(self):
        self.title = ''
        self.sharps = 0
        self.mode = 'major'
        self.ts = '4/4'
        self.tempo = 90
        self.bars = 0
        self.sections: List[Dict[str, Any]] = []
        self.harmony: Dict[int, List[str]] = {}
        self.voices = 2
        self.parts = [
            {'name': 'Piano RH', 'clef': 'treble', 'bars': {}},
            {'name': 'Piano LH', 'clef': 'bass', 'bars': {}},
        ]
        self.dynamics: List[List] = []
        self.meter_changes: List[List] = []
        self.tempo_marks: List[List] = []
        # 段落级转调：[[起始小节, 调号数, mode], …]（set_harmony 的 key_sharps/mode 产生）
        self.key_changes: List[List] = []
        self.called: List[str] = []
        self.seed = ''   # 运行种子：new_score 时设定（未提供则随机）
        self.fugue_final = False   # fugue 是否已写到全曲末小节（免重复终止）
        self.fugue_called = False  # 本曲含赋格段（对位织体，豁免织体对比校验）
        self.fugue_roles: List[List] = []   # [[小节, 声部idx, 角色], …] 角色分档
        # 种子分枝：从主 seed 派生，各维度可独立复现
        self.subseeds: Dict[str, str] = {}
        # 软权重：风格指纹对"可选素材"的偏好（参与采样，不只是喂 prompt）
        self.soft_weights: Dict[str, Dict[str, float]] = {}
        self.narrative: Optional[Dict[str, Any]] = None   # 叙事计划（drama）
        # 终止式小节（cadence 写入）：叙事层的动机变形/惊喜和弦/留白必须避开，
        # 否则自动叙事会把「末两小节主和弦长音」重写成变形动机，破坏终止。
        self.cadence_bars: set = set()
        # 旋律路由：承载旋律（独奏）的乐器名（new_score 的 solo_instrument 指定）。
        # None = 未指定路由，旋律仍写在声部 0 并沿用 register 默认音区。
        self.melody_instrument: Optional[str] = None
        # 确定性风格契约（new_score 的 style 参数）——机器可读的风格参数。
        # 与 soft_weights 的区别：soft_weights 只影响「可选素材」的采样权重，
        # 而 style 契约会**强制**约束和弦品质、旋律形态等可测量维度。
        # None = 无风格约束（与历史行为一致）。
        self.style: Any = None
        #: new_score 传入的 style 原样字符串。契约解析可能失败（返回 None），
        #: 故另存一份供 narrative 层做体裁判定，避免保护逻辑静默失效。
        self.style_id: str = ''
        self.style_notes: List[str] = []   # 契约实际施加了哪些约束（可追溯）
        # 弱起拍数：>0 时第 0 小节是只有这么多拍的不完整小节（第 1..bars 小节照常）
        self.pickup = 0.0

    def rng_for(self, dim: str, *parts) -> random.Random:
        """取某一维度的随机源（种子分枝：该维度可独立复现/单独更换）。"""
        return _rng(self.subseeds.get(dim) or self.seed, dim, *parts)

    @property
    def bass_idx(self) -> int:
        """最低声部索引（2 声部=1；3 声部=2；4 声部=3）。"""
        return len(self.parts) - 1

    def section(self, name) -> Dict[str, Any]:
        for s in self.sections:
            if s['name'] == name:
                return s
        raise KernelError(f'未知段落 {name!r}（先在 set_sections 里定义）')

    def bar_cap(self, bar: int) -> float:
        if bar == 0 and self.pickup:
            return self.pickup
        for s in self.sections:
            if s['start'] <= bar <= s['end']:
                num, den = s['meter'].split('/')
                return 4.0 * int(num) / int(den)
        num, den = self.ts.split('/')
        return 4.0 * int(num) / int(den)

    def scale(self) -> List[str]:
        return _scale_names(self.sharps, self.mode)

    def scale_for(self, sec: Optional[Dict[str, Any]]) -> List[str]:
        """段落级音阶：段落带 key_sharps/mode（转调）时用段落调，否则全曲调。"""
        if sec and ('key_sharps' in sec or 'mode' in sec):
            return _scale_names(int(sec.get('key_sharps', self.sharps)),
                                str(sec.get('mode', self.mode)))
        return self.scale()

    def scale_at(self, bar: int) -> List[str]:
        """第 bar 小节所在段落的音阶（供叙事惊喜和弦等按小节取调用）。"""
        for s in self.sections:
            if s['start'] <= bar <= s['end']:
                return self.scale_for(s)
        return self.scale()

    def tonic_name(self) -> str:
        return _scale_names(self.sharps, self.mode)[0]

    def assert_bar_free(self, part_idx: int, bar: int, script: str):
        if bar in self.parts[part_idx]['bars']:
            raise KernelError(
                f'{script}：第 {bar} 小节（声部{part_idx + 1}）已写过，禁止覆盖')

    def put_bar(self, part_idx: int, bar: int, events: List[List],
                force: bool = False):
        cap = self.bar_cap(bar)
        for e in events:
            if str(e[1]) not in DUR_QL:
                raise KernelError(f'内部校验失败：非法时值 {e[1]!r}')
        total = sum(DUR_QL[str(e[1])] for e in events)
        if abs(total - cap) > 1e-6:
            raise KernelError(
                f'内部校验失败：第 {bar} 小节（声部{part_idx + 1}）时值 {total} ≠ {cap}')
        self.parts[part_idx]['bars'][bar] = events


# ---------- 音高工具 ----------

def _pc(name: str) -> int:
    return m21pitch.Pitch(str(name)).pitchClass


def _name_oct(midi: int, prefer: List[str]) -> str:
    octave = midi // 12 - 1
    for n in prefer:
        if _pc(n) == midi % 12:
            return f'{n}{octave}'
    # 兜底：音高不在调内/和弦内（半音经过音、离调音等）时用标准等音拼写
    try:
        p = m21pitch.Pitch(midi)
        return f'{_sp(p.name)}{p.octave}'
    except Exception:
        raise KernelError(f'无法拼写 midi {midi}')


def _nearest_pc(target: int, pc: int, lo: int, hi: int) -> int:
    m = pc + 12 * round((target - pc) / 12)
    while m < lo:
        m += 12
    while m > hi:
        m -= 12
    return m


def _fit_register_window(lo: int, hi: int, rlo: int, rhi: int) -> Tuple[int, int]:
    """把旋律音区窗口 [lo,hi] 收进乐器可奏音域 [rlo,rhi]（保宽度，必要时才压缩）。

    旋律路由把主题交给中低音乐器时，register 默认窗口会越出该乐器音域——
    例如 mid 窗口是 62..81，而大提琴上限只有 72。这里把窗口整体平移进音域
    （而非逐音截断），因此 `_nearest_pc` 选出的每个旋律音自然落在可奏范围内，
    同时保留原窗口的宽度（乐句起伏的音区空间不丢失）。
    """
    if rhi <= rlo:
        return lo, max(hi, rlo)
    if hi - lo >= rhi - rlo:        # 窗口比音域还宽：直接用满整个音域
        return rlo, rhi
    if lo < rlo:                    # 先贴下界整体上移
        hi += rlo - lo
        lo = rlo
    if hi > rhi:                    # 仍超出上界：整体下移贴上界
        lo -= hi - rhi
        hi = rhi
    if lo < rlo:                    # 极端窄音域（如定音鼓）：贴下界
        lo = rlo
    return max(lo, rlo), min(hi, rhi)


def _melody_range(st: '_State') -> Optional[Tuple[int, int]]:
    """旋律声部承载乐器的可奏音域；未指定路由时返回 None（各处沿用默认音区）。

    旋律路由后，凡对旋律声部做**八度运算**的地方（加厚、终止八度、叙事变奏动机）
    都必须先照这个音域兜住，否则会把大提琴/圆号这类乐器写出超音域的音。
    """
    mi = getattr(st, 'melody_instrument', None)
    if not mi:
        return None
    try:
        return part_range(mi, st.parts[0].get('clef', 'treble'))
    except Exception:
        return None


def _scale_step_to(prev_midi: int, direction: int, lo: int, hi: int,
                   scale_pcs: List[int]) -> int:
    """从 prev 出发沿 direction 走的最近调内音（窗口内）。

    距离平局时偏向行进方向一侧（prev+direction 两侧等距时取前方音），
    避免返回 prev 触发「走不动→反向」的原地钟摆。
    """
    desired = prev_midi + direction
    best, best_d = None, 10 ** 9
    for octv in range(lo // 12 - 1, hi // 12 + 2):
        for pc in scale_pcs:
            m = pc + 12 * octv
            if lo <= m <= hi and abs(m - desired) < best_d:
                best, best_d = m, abs(m - desired)
    if best == prev_midi:  # 平局可能落在 prev 上：改取行进方向一侧
        for octv in range(lo // 12 - 1, hi // 12 + 2):
            for pc in scale_pcs:
                m = pc + 12 * octv
                if lo <= m <= hi and m != prev_midi and (
                        (direction > 0) == (m > prev_midi)):
                    if abs(m - desired) <= best_d + 1:
                        return m
    return best if best is not None else prev_midi


# 半音经过音的调性侧拼写（避免 music21 transpose 的等音乱拼：C#/D-/F# 混入降号调）
_FLAT_NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
_SHARP_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def _key_name(st: '_State', midi: int, prefer: Optional[List[str]] = None) -> str:
    """midi → 音名：优先 prefer（和弦音/级数）拼写，其次调内音，最后按调号侧。"""
    pc = midi % 12
    for n in (prefer or []):
        if _pc(n) == pc:
            return f'{n}{midi // 12 - 1}'
    for n in st.scale():
        if _pc(n) == pc:
            return f'{n}{midi // 12 - 1}'
    table = _FLAT_NAMES if int(st.sharps) <= 0 else _SHARP_NAMES
    return f'{table[pc]}{midi // 12 - 1}'


# ---------- 节奏模板（(风格, 小节容量) → 候选模板） ----------
# None = 休止；模板时值之和恒等于容量。

_RHYTHMS: Dict[Tuple[str, float], List[List[Optional[float]]]] = {
    # sparse：None=0.5 拍休止；供爵士 lead-sheet 与玛祖卡弱起
    ('sparse', 4.0): [
        [4.0], [2.0, 2.0], [1.0, 1.0, 2.0], [2.0, 1.0, 1.0], [1.0, 2.0, 1.0],
        [None, 0.5, 1.0, 2.0], [None, 1.0, 2.0, 0.5], [None, 2.0, 1.0, 0.5],
        [1.0, None, 0.5, 2.0], [2.0, None, 0.5, 1.0],
        [None, 1.0, None, 1.0, 1.0], [1.0, 1.0, None, 0.5, 1.0],
        # 爵士 head：弱起切分 + 句末留白（None=0.5 拍休止；总和须 = 4）
        [None, 0.5, None, 0.5, 1.0, 1.0],
        [None, 1.0, 1.0, None, 1.0],
        [1.0, None, 0.5, None, 0.5, 1.0],
        [1.0, None, 1.0, None, 1.0],
    ],
    # 3.0 拍：整小节的 3 拍音（附点二分）内核不支持，用 2+1 代替
    ('sparse', 3.0): [
        [2.0, 1.0], [1.0, 2.0], [1.0, 1.0, 1.0],
        [None, 0.5, 2.0], [None, 2.0, 0.5], [None, 1.0, 0.5, 1.0],
        [1.0, None, 0.5, 1.0], [2.0, None, 0.5],
    ],
    ('flowing', 4.0): [[1.0, 1.0, 1.0, 1.0], [0.5, 0.5, 1.0, 1.0, 1.0],
                       [1.0, 0.5, 0.5, 1.0, 1.0], [1.0, 1.0, 0.5, 0.5, 1.0],
                       [0.5, 0.5, 1.0, 2.0]],
    ('flowing', 3.0): [[1.0, 1.0, 1.0], [0.5, 0.5, 1.0, 1.0], [1.0, 0.5, 0.5, 1.0]],
    ('flowing', 2.0): [[1.0, 1.0], [0.5, 0.5, 1.0], [1.0, 0.5, 0.5]],
    ('sparse', 2.0): [[2.0], [1.0, 1.0], [1.0, 0.5, 0.5]],
    ('fragmented', 2.0): [[0.5, 0.5, 1.0], [1.0, 0.5, 0.5], [0.5, 0.5, 0.5, 0.5]],
    ('driving', 2.0): [[1.0, 1.0], [0.5, 0.5, 1.0], [0.5, 0.5, 0.5, 0.5]],
    ('fragmented', 4.0): [[0.5, 0.5, 1.0, 0.5, 0.5, 1.0],
                          [0.5, 0.5, 0.5, 0.5, 1.0, 1.0],
                          [1.0, 0.5, 0.5, 1.0, 1.0],
                          [1.0, 1.0, None, 1.0, 0.5],
                          [None, 1.0, 1.0, 0.5, 1.0]],
    ('fragmented', 3.0): [[0.5, 0.5, 1.0, 1.0], [1.0, 0.5, 0.5, 1.0],
                          [0.5, 0.5, 0.5, 0.5, 1.0], [1.0, None, 1.0, 0.5]],
    ('driving', 4.0): [[1.0, 1.0, 1.0, 1.0], [1.0, 1.0, 0.5, 0.5, 1.0],
                       [0.5, 0.5, 1.0, 1.0, 1.0]],
    ('driving', 3.0): [[1.0, 1.0, 1.0], [0.5, 0.5, 1.0, 1.0]],
}


#: **自由创作**（无风格契约）时附加的节奏模板池。
#:
#: 风格包通过软权重锁定自己的「节奏身份」，因此不并入 `_RHYTHMS`——
#: 否则会稀释风格指纹、让各包的可区分度下降。但当创作一个不属于任何风格包的
#: 自由作品时（`soft_weights` 为空，例如用户直接写「写一首命运交响曲」），
#: 原来的模板池每种风格只有 2–5 条，32 小节里同一节奏型会重复 8 次，
#: 听感上就是「旋律很单一」。这里补充一批同风格、同容量的模板，扩大可选面。
#: 只使用基础时值（4/2/1/0.5/0.25），因为槽位→时值名要查 `QL_DUR`。
_RHYTHMS_EXTRA: Dict[Tuple[str, float], List[List[Optional[float]]]] = {
    ('flowing', 4.0): [
        [1.0, 2.0, 1.0], [2.0, 1.0, 1.0], [1.0, 1.0, 0.5, 0.5, 1.0],
        [0.5, 0.5, 0.5, 0.5, 2.0], [0.5, 0.5, 1.0, 0.5, 0.5, 1.0],
        [1.0, 0.5, 0.5, 2.0], [1.0, 0.5, 0.5, 1.0, 1.0],
        [0.25, 0.25, 1.0, 1.0, 0.5, 1.0], [0.5, 0.5, 2.0, 1.0],
        [1.0, 1.0, 1.0, 0.5, 0.5],
    ],
    ('driving', 4.0): [
        [1.0, 1.0, 1.0, 0.5, 0.5], [0.5, 0.5, 0.5, 0.5, 1.0, 1.0],
        [1.0, 0.5, 0.5, 0.5, 0.5, 1.0], [1.0, 0.5, 0.5, 1.0, 1.0],
        [1.0, 1.0, 2.0], [0.5, 0.5, 2.0, 1.0], [2.0, 1.0, 1.0],
        [0.5, 0.5, 0.5, 0.5, 2.0], [1.0, 0.5, 0.5, 2.0],
        [0.25, 0.25, 0.5, 1.0, 1.0, 1.0],
    ],
    ('fragmented', 4.0): [
        [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 1.0],
        [None, 1.0, 0.5, 0.5, 0.5, 1.0],
        [1.0, None, 0.5, 1.0, 1.0],
        [None, 0.5, 0.5, 1.0, 0.5, 1.0],
        [0.5, 0.5, None, 1.0, 0.5, 1.0],
        [1.0, 0.5, 0.5, None, 0.5, 1.0],
        [0.5, 1.0, 0.5, 1.0, 1.0],
    ],
    ('flowing', 3.0): [
        [1.0, 2.0], [2.0, 1.0], [0.5, 0.5, 2.0], [1.0, 0.5, 0.5, 1.0],
        [0.5, 0.5, 0.5, 0.5, 1.0],
    ],
    ('driving', 3.0): [
        [1.0, 2.0], [2.0, 1.0], [1.0, 0.5, 0.5, 1.0],
        [0.5, 0.5, 0.5, 0.5, 1.0],
    ],
    ('fragmented', 3.0): [
        [1.0, 1.0, 0.5, 0.5], [0.5, 1.0, 0.5, 1.0],
        [None, 1.0, 1.0, 0.5], [0.5, 0.5, None, 1.0, 0.5],
    ],
}


def _pick_rhythm(rng: random.Random, style: str, cap: float,
                 weights: Optional[Dict[str, float]] = None,
                 avoid: Optional[Sequence[Optional[float]]] = None
                 ) -> List[Optional[float]]:
    """挑一条节奏模板；``weights`` 为软权重（风格指纹对节奏型的偏好）。

    ``avoid`` 给最近几小节已用的模板：能换就换一条，避免短期内节奏雷同
    ——这是「旋律听起来很单一」最常见的来源。有风格契约时不扩池（保风格身份）。
    """
    base = _RHYTHMS.get((style, cap))
    if not base:
        raise KernelError(f'节奏风格 {style!r} 不支持 {cap:g} 拍的小节')
    table = list(base)
    if not weights:
        table += list(_RHYTHMS_EXTRA.get((style, cap)) or [])
    w = _rhythm_weights_for(table, weights)
    pick = _weighted_choice(rng, table, w, keyfn=_rhythm_key)
    if avoid:
        rec = {tuple(x) for x in avoid}
        # 最多重抽 6 次；每次都按软权重抽，因此风格偏好仍然占主导
        for _ in range(6):
            if tuple(pick) not in rec:
                break
            pick = _weighted_choice(rng, table, w, keyfn=_rhythm_key)
    return list(pick)


# ---------- 动机节奏变形池（修复方向 2：动机贯穿） ----------
#
# 「三短一长无处不在」不是每小节照抄动机，而是每小节用动机的**一个变形**。
# 原实现只有乐句首小节用动机原型，其余小节从通用节奏模板池抽 —— 动机于是
# 只在句头露一次，后面的填充节奏跟它毫无关系，全曲听起来就是散的。
# 这里给出动机的变形算子（增时 / 减时 / 截断 / 逆行 / 循环移位），每小节
# 从池里抽，原型权重最高 → 主题贯穿但不复制粘贴。

#: 合法时值（必须都能在 QL_DUR 里查回时值名），重配节奏时用。
#: 注意 **不含 3.0**：附点二分（dotted_half）没进 QL_DUR（历史遗留：
#: 3 拍小节用 2+1 表示），混进来会让 QL_DUR[s] 抛 KeyError。
_LEGAL_DURS = (4.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.25)


def _normalize_slots(slots, cap: float) -> List[Optional[float]]:
    """把任意时值序列重配到小节容量：裁长补短后时值之和 == cap。

    动机变形（增时 ×2 / 减时 ÷2）后总时值几乎必然不等于小节容量，不重配
    就会写出 6 拍或 2 拍的小节，整首曲子从小节线开始全部错位。
    每个时值取自 `_LEGAL_DURS`，保证后续能查回时值名；``None`` 为休止，
    按 0.5 拍计（与 `_RHYTHMS` 模板同口径）。
    """
    def _tot(lst) -> float:
        return sum((x if x is not None else 0.5) for x in lst)

    out: List[Optional[float]] = [
        None if s is None else float(s) for s in (slots or [])]
    while len(out) > 1 and _tot(out) > cap + 1e-9:
        out.pop()
    if not out or _tot(out) > cap + 1e-9:
        out = [max([d for d in _LEGAL_DURS if d <= cap + 1e-9]
                   or [min(_LEGAL_DURS)])]
    rest = cap - _tot(out)
    while rest > 1e-9:
        step = max([d for d in _LEGAL_DURS if d <= rest + 1e-9]
                   or [min(_LEGAL_DURS)])
        out.append(step)
        rest -= step
    return out


def _motif_rhythm_variants(motif_durs, cap: float) -> List[List[Optional[float]]]:
    """动机节奏的变形池。

    算子：原型 / 增时 / 减时 / 逆行 / 截断 / 循环移位 / 附点化 / 拆分 /
    留白（换成休止）/ 弱起（前置休止）。

    前六个是「把动机整体搬过来」的变换；后四个是「在动机内部做文章」——
    只有前者时池子只有 6–7 条，32 小节里节奏型仍会明显重复（实测 distinct
    从 18 掉回 12），必须再补上动机内部的变形才够用。
    全部经 `_normalize_slots` 适配到容量，去重后返回。
    """
    base = [float(d) for d in (motif_durs or []) if d]
    if not base:
        return []
    vs: List[List[Optional[float]]] = [list(base),
                                       [d * 2 for d in base],
                                       [d / 2 for d in base],
                                       list(reversed(base))]
    for k in range(2, len(base)):        # 截断：只奏动机头
        vs.append(list(base[:k]))
    for k in range(1, len(base)):        # 循环移位：动机从后半开始
        vs.append(list(base[k:]) + list(base[:k]))
    if len(base) >= 2:
        kmax = max(range(len(base)), key=lambda j: base[j])
        dotted = list(base)              # 附点化：把最长音拉成附点
        dotted[kmax] = dotted[kmax] * 1.5
        vs.append(dotted)
        split = list(base)               # 拆分：把最长音拆成两个等长音
        split[kmax:kmax + 1] = [split[kmax] / 2, split[kmax] / 2]
        vs.append(split)
    for k in range(1, len(base)):        # 留白：中间换成休止，制造气口
        gap = list(base)
        gap[k] = None
        vs.append(gap)
    vs.append([None] + list(base))       # 弱起：休止 + 动机
    out: List[List[Optional[float]]] = []
    seen = set()
    for v in vs:
        norm = tuple(_normalize_slots(v, cap))
        if norm not in seen:
            seen.add(norm)
            out.append(list(norm))
    return out


def _pick_motif_rhythm(rng: random.Random, motif_durs, cap: float,
                       avoid: Optional[Sequence[Optional[float]]] = None,
                       identity_bias: float = 2.5) -> Optional[List[float]]:
    """从动机节奏变形池抽一条：原型权重最高（主题贯穿），变形穿插。

    ``avoid`` 为最近小节用过的模板 —— 与 `_pick_rhythm` 同款，防止连续
    两三小节节奏一模一样。
    """
    vs = _motif_rhythm_variants(motif_durs, cap)
    if not vs:
        return None
    proto = tuple(_normalize_slots(motif_durs, cap))
    w = [identity_bias if tuple(v) == proto else 1.0 for v in vs]
    pick = _weighted_choice(rng, vs, w, keyfn=_rhythm_key)
    if avoid:
        rec = {tuple(x) for x in avoid}
        for _ in range(4):
            if tuple(pick) not in rec:
                break
            pick = _weighted_choice(rng, vs, w, keyfn=_rhythm_key)
    return list(pick)


def _verify_templates():
    """模块导入时自检：所有节奏模板（含 0.5 拍休止）时值之和必须等于容量。"""
    for src in (_RHYTHMS, _RHYTHMS_EXTRA):
        for (style, cap), table in src.items():
            for t in table:
                total = sum((x if x is not None else 0.5) for x in t)
                if abs(total - cap) > 1e-9:
                    raise RuntimeError(
                        f'节奏模板违约 {style}/{cap}: {t} 时值 {total}')
                for x in t:
                    if x is not None and x not in QL_DUR:
                        raise RuntimeError(
                            f'节奏模板 {style}/{cap} 用了 QL_DUR 外时值 {x}')


_verify_templates()


# ---------- 脚本实现 ----------

def _s_new_score(st: _State, args: Dict[str, Any]):
    if st.sections or st.bars:
        raise KernelError('new_score 必须是第一个调用（且只调用一次）')
    st.title = str(args.get('title') or 'Untitled').strip() or 'Untitled'
    st.sharps = int(args.get('key_sharps') or 0)
    if not (-7 <= st.sharps <= 7):
        raise KernelError(f'key_sharps 需在 -7–7，收到 {st.sharps}')
    st.mode = str(args.get('mode') or 'major')
    if st.mode not in ('major', 'minor'):
        raise KernelError(f"mode 必须是 'major' 或 'minor'，收到 {st.mode!r}")
    # 确定性风格契约（可选）：机器可读的风格参数，它会真实约束和弦品质等
    # 可测量维度 —— 区别于只被序列化成 prompt 的 meta.yaml profile。
    # style_id 无论契约能否解析都会保留：它是 narrative 层判定体裁的兜底依据
    # （契约解析在有grammar.yaml 时会返回 None，见 resolve_contract）。
    st.style_id = str(args.get('style') or '')
    st.style = _resolve_style_arg(args.get('style'))
    st.ts = str(args.get('meter') or _style_default_meter(st) or '4/4')
    try:
        num, den = st.ts.split('/')
        if 4.0 * int(num) / int(den) not in (2.0, 3.0, 4.0):
            raise ValueError
    except Exception:
        raise KernelError(f'拍号 {st.ts!r} 暂只支持 4/4、3/4、2/4')
    st.tempo = int(args.get('tempo') or _style_default_tempo(st) or 90)
    if not (30 <= st.tempo <= 240):
        raise KernelError(f'tempo 需在 30–240，收到 {st.tempo}')
    st.bars = int(args.get('bars') or 16)
    # allow_long：逆向复现路径可写到 256 小节（莫扎特小夜曲乐章等）
    max_bars = 256 if args.get('allow_long') else 64
    if not (8 <= st.bars <= max_bars):
        raise KernelError(f'bars 需在 8–{max_bars}，收到 {st.bars}')
    # 运行种子：不传则随机生成 → 同一提示词每次生成不同的节奏/旋律实现；
    # 传入上一次 spec.seed 可逐音符复现
    st.seed = str(args.get('seed') or '').strip() or random.randbytes(4).hex()
    # 种子分枝：各维度独立子种子（可逐维覆盖 → "只换旋律"这类局部重生成）
    ov_sub = args.get('subseeds') if isinstance(args.get('subseeds'), dict) else None
    st.subseeds = derive_subseeds(st.seed, override=ov_sub)
    # 软权重：风格指纹对可选素材的偏好（参与采样，不只是喂 prompt）
    sw = args.get('soft_weights')
    st.soft_weights = {str(k): {str(kk): float(vv) for kk, vv in v.items()}
                       for k, v in sw.items()} if isinstance(sw, dict) else {}
    # 声部数：2=钢琴 RH/LH（也是二声部创意曲）；3/4=赋格用的 SAT/B 四部骨架
    voices = int(args.get('voices') or 2)
    if voices < 1 or voices > 24:
        raise KernelError(f'voices 需为 1–24，收到 {voices}')
    st.voices = voices
    # 默认声部布局：2/3/4 用预置（钢琴/合唱/赋格骨架）；>4 声部（交响/室内乐编制）
    # 必须由 instruments 明确指定，否则给通用占位命名（谱号随后由 instruments 决定）。
    if voices in _VOICE_PARTS:
        parts_def = list(_VOICE_PARTS[voices])
    else:
        parts_def = [(f'声部 {i + 1}', 'treble' if i < voices // 2 else 'bass')
                     for i in range(voices)]
    # 可选 instruments：为各声部指定乐器（英文名，与记谱层乐器表一致，
    # 如 ['violin', 'violin', 'viola', 'cello']，顺序须从高音到低音）。
    # 缺省时 2 声部 = Piano RH/LH，3/4 声部 = SATB（合唱/赋格骨架）。
    insts = args.get('instruments')
    # 旋律路由：solo_instrument 指定「由某乐器独奏（承载旋律）」，内核负责把该乐器
    # 提到声部 0（旋律声部）并把旋律音区收进它的可奏音域（见 _s_melody）。
    solo = args.get('solo_instrument')
    if insts is None:
        if solo is not None:
            raise KernelError(
                'solo_instrument 需与 instruments 同时给出——独奏乐器必须是'
                '编制成员（先在 instruments 里列出该乐器）')
    else:
        if (not isinstance(insts, list) or len(insts) != voices
                or any(not str(x or '').strip() for x in insts)):
            raise KernelError(
                f'instruments 需为长度 {voices} 的非空字符串列表（与 voices 对应），'
                f'收到 {insts!r}')
        insts = [str(x).strip() for x in insts]
        if solo is not None:
            snm = str(solo).strip()
            if not snm:
                raise KernelError('solo_instrument 需为非空乐器名（收到空串）')
            idx = next((i for i, x in enumerate(insts)
                        if x.lower() == snm.lower()), None)
            if idx is None:
                raise KernelError(
                    f'solo_instrument {snm!r} 不在 instruments 名单内（当前名单 '
                    f'{insts}）；独奏乐器必须是编制成员')
            # 提到声部 0（旋律声部），其余声部保持原有「高→低」相对次序整体让位
            insts = [insts[idx]] + [x for i, x in enumerate(insts) if i != idx]
            st.melody_instrument = snm
        if voices == 2 and all(x.lower() == 'piano' for x in insts):
            insts = ['Piano RH', 'Piano LH']     # 钢琴对：保留大谱表命名
        else:
            insts = [x[0].upper() + x[1:] for x in insts]
    st.parts = []
    for i, (nm, cl) in enumerate(parts_def):
        if insts is not None:
            nm = insts[i]
            # 乐器决定谱号（低音乐器给低音谱号），未知乐器沿用位置默认谱号
            cl = _INSTR_CLEF.get(nm.lower(), cl)
        st.parts.append({'name': nm, 'clef': cl, 'bars': {}})


def _s_set_sections(st: _State, args: Dict[str, Any]):
    secs = args.get('sections')
    if not isinstance(secs, list) or not secs:
        raise KernelError('set_sections 需要 sections 非空列表')
    total, seen = 0, set()
    for s in secs:
        if not isinstance(s, dict):
            raise KernelError(
                f'set_sections 元素须为对象 {{name, bars, ...}}，收到 {type(s).__name__}')
        name = str(s.get('name') or '').strip()
        n = int(s.get('bars') or 0)
        if not name or name in seen:
            raise KernelError(f'段落名必须唯一非空，收到 {name!r}')
        if n < 1:
            raise KernelError(f'段落 {name} bars 必须 ≥1')
        seen.add(name)
        total += n
    if total != st.bars:
        raise KernelError(f'各段小节之和 {total} ≠ 总小节数 {st.bars}（new_score）')
    start, prev_meter = 1, st.ts
    meter_changes: List[List] = []
    tempo_marks: List[List] = []
    dyn_raw: List[Tuple[int, str]] = []
    prev_bpm = st.tempo  # 相邻段速度平滑的锚点
    for s in secs:
        # 首轮已校验 isinstance(dict)；此处仍用 .get 保持 KernelError 契约
        name, n = str(s.get('name') or '').strip(), int(s.get('bars') or 0)
        meter = str(s.get('meter') or prev_meter)
        try:
            num, den = meter.split('/')
            if 4.0 * int(num) / int(den) not in (2.0, 3.0, 4.0):
                raise ValueError
        except Exception:
            raise KernelError(f'段落 {name} 拍号 {meter!r} 非法（如 "4/4" "3/4" "2/4"）')
        dynamic = str(s.get('dynamic') or 'mp').strip().lower()
        if dynamic not in ('pp', 'p', 'mp', 'mf', 'f', 'ff'):
            raise KernelError(f'段落 {name} 力度 {dynamic!r} 非法（pp–ff）')
        bpm = s.get('bpm')
        bpm = int(bpm) if bpm else None
        if bpm is not None and not (30 <= bpm <= 240):
            raise KernelError(f'段落 {name} bpm 需在 30–240')
        # 速度平滑：相邻段 BPM 钳制在上一段的 70%–125%，
        # 「略微加快」不会被翻成 72→120 的突跳（多段递进仍可达目标速度）
        if bpm is None:
            eff = prev_bpm
        else:
            eff = max(30, min(240, max(round(prev_bpm * 0.70),
                                       min(round(prev_bpm * 1.25), bpm))))
        st.sections.append({
            'name': name, 'start': start, 'end': start + n - 1, 'bars': n,
            'meter': meter, 'dynamic': dynamic,
            'tempo_text': str(s.get('tempo_text') or '').strip(), 'bpm': eff,
        })
        if meter != prev_meter:
            meter_changes.append([start, meter])
        if bpm or st.sections[-1]['tempo_text']:
            tempo_marks.append([start, st.sections[-1]['tempo_text']
                                or f'♩={eff}', eff])
        dyn_raw.append((start, dynamic))
        prev_meter = meter
        prev_bpm = eff
        start += n
    if not tempo_marks or tempo_marks[0][0] != 1:
        tempo_marks.insert(0, [1, f'♩={st.tempo}', st.tempo])
    st.meter_changes = meter_changes
    st.tempo_marks = tempo_marks
    dyn = [list(dyn_raw[0])]
    for b, v in dyn_raw[1:]:
        if v != dyn[-1][1]:
            dyn.append([b, v])
    st.dynamics = dyn


def _resolve_style_arg(style):
    """解析 new_score 的 style 参数 → StyleContract。

    接受 StyleContract 对象 / 内联 dict / 'family/pack_id' 或 pack_id。
    路径写错时返回 None 而不是抛错：风格是增强项，不该因为找不到风格包
    就让整次创作失败。
    """
    if style is None:
        return None
    try:
        from style_contract import (
            StyleContract, contract_from_dict, resolve_contract,
        )
    except Exception:
        return None
    if isinstance(style, StyleContract):
        return style
    if isinstance(style, dict):
        return contract_from_dict(style)
    if isinstance(style, str) and style.strip():
        return resolve_contract(style)
    return None


def _style_default_tempo(st: _State):
    """契约给出的体裁默认速度（仅在调用方未显式传 tempo 时兜底）。"""
    c = getattr(st, 'style', None)
    if c is None:
        return None
    t = getattr(c, 'tempo', None)
    try:
        if t is not None and t.is_usable() and t.default is not None:
            st.style_notes.append(
                f'风格契约给定速度 ♩={int(t.default)}（契约未显式指定时的体裁缺省）')
            return int(t.default)
    except Exception:
        return None
    return None


def _style_default_meter(st: _State):
    """契约给出的体裁默认拍号（仅在调用方未显式传 meter 时兜底）。"""
    c = getattr(st, 'style', None)
    if c is None:
        return None
    m = getattr(c, 'meter', None)
    try:
        if m is not None and m.is_usable() and m.primary:
            num, den = str(m.primary).split('/')
            if 4.0 * int(num) / int(den) in (2.0, 3.0, 4.0):
                st.style_notes.append(
                    f'风格契约给定拍号 {m.primary}（契约未显式指定时的体裁缺省）')
                return str(m.primary)
    except Exception:
        return None
    return None


_STYLE_QUALITY_MAJOR = (
    'triad', 'dominant7', 'major7', 'augmented', 'diminished7',
    'half_diminished7',
)
# 小写（小三和弦）级数：major7 会引入大三+大七而破坏小调色彩，故排除；
# augmented（增三）在小调级数上也极少作为风格统计的有效取样
_STYLE_QUALITY_MINOR = (
    'triad', 'minor7', 'diminished7', 'half_diminished7',
)
_STYLE_SUFFIX_FOR = {
    'triad': '',
    'dominant7': '7',
    'major7': 'maj7',
    'minor7': '7',
    'diminished7': '°7',
    'half_diminished7': 'ø7',
    'augmented': '+',
    'other': '',
}
# 按**级数**进一步收窄可用品质 —— 光按「大调/小调级数」分还不够，
# 会给属和弦戴上大七（Vmaj7 含导音上方大七，破坏属功能，是配和声法错误）。
# ii–V–I 里 V 必须是属七(V7)而非 Vmaj7；大七留给 I / IV（Imaj7、IVmaj7 常见）。
_STYLE_QUALITY_BY_DEGREE = {
    0: ('triad', 'major7', 'dominant7'),                  # I / i
    1: ('triad', 'minor7', 'half_diminished7'),           # ii
    2: ('triad', 'minor7'),                               # iii
    3: ('triad', 'major7', 'dominant7'),                  # IV / iv
    4: ('triad', 'dominant7'),                            # V —— 不给 maj7
    5: ('triad', 'minor7'),                               # vi
    6: ('triad', 'diminished7', 'half_diminished7'),      # vii°
}
_ROMAN_TOKEN_RE = re.compile(r'^([b#]*)([IViv]+)\s*(.*)$')


def apply_style_quality(st: _State, prog_tokens, section_name: str):
    """按风格契约确定性地给和弦加上品质后缀。

    **这是体裁差异的核心落实点。** 内核 `parse_roman` 早就支持
    `7 / maj7 / ø7 / °7 / +`，但此前没有任何机器可读的风格信号告诉它去用，
    于是所有体裁都用同样的三和弦库存，听起来必然雷同 —— 而 `meta.yaml`
    里那些关于和声的描述只被序列化成 prompt 交给 LLM 自行领会。

    两个必须守住的边界：
      1. 用户**显式**写了后缀的（如 'V7'）一律不改 —— 显式优先于风格。
      2. 只对合同 multipart valid 的品质取样（大写级数不给 maj7 之外的
         破坏性品质，小写级数不给大七/增三）。

    返回 (新的 token 列表, 说明列表)。
    """
    contract = getattr(st, 'style', None)
    if contract is None:
        return list(prog_tokens), []
    harmony = getattr(contract, 'harmony', None)
    if harmony is None or not harmony.is_usable():
        return list(prog_tokens), []

    rng = st.rng_for('harmony-quality', section_name or 'all')
    out: List[str] = []
    applied: List[str] = []
    for tok in prog_tokens:
        t = str(tok).strip()
        m = _ROMAN_TOKEN_RE.match(t)
        if not m:
            out.append(t)
            continue
        _accs, roman, suffix = m.groups()
        if suffix.strip():          # 用户显式指定 → 尊重原样
            out.append(t)
            continue
        allowed = (_STYLE_QUALITY_MAJOR if roman[0].isupper()
                   else _STYLE_QUALITY_MINOR)
        # 级数功能过滤：V 不得取 maj7，小三级数不得取大七/增三……
        deg = _ROMAN_VAL.get(roman.upper())
        if deg is not None and deg in _STYLE_QUALITY_BY_DEGREE:
            functionally_ok = [q for q in _STYLE_QUALITY_BY_DEGREE[deg]
                               if q in allowed]
            if functionally_ok:
                allowed = functionally_ok
        q = harmony.pick_quality(rng, allowed=allowed)
        sf = _STYLE_SUFFIX_FOR.get(q, '')
        if sf:
            out.append(t + sf)
            applied.append(f'{t}→{t}{sf}')
        else:
            out.append(t)
    return out, applied


def apply_style_defaults(st: _State, tempo, meter):
    """把契约里的速度/拍号缺省**确定性地**落到 After generating 的全局参数。

    注意只补「调用方没给」的字段 —— 显式参数永远优先。
    """
    contract = getattr(st, 'style', None)
    if contract is None:
        return tempo, meter, []
    notes: List[str] = []
    tspec = getattr(contract, 'tempo', None)
    if tspec is not None and getattr(tspec, 'is_usable', None) \
            and tspec.is_usable() and tspec.default is not None:
        tempo = int(tspec.default)
        notes.append(f'风格契约给定速度 ♩={int(tspec.default)}')
    mspec = getattr(contract, 'meter', None)
    if mspec is not None and getattr(mspec, 'is_usable', None) \
            and mspec.is_usable() and mspec.primary:
        meter = str(mspec.primary)
        notes.append(f'风格契约给定拍号 {mspec.primary}')
    return tempo, meter, notes


def _extend_chord_tone(chord: List[str],
                       scale_names: Optional[List[str]] = None) -> List[str]:
    """给三和弦补出第四个可用音（七音 / 加六），已是四音及以上则原样返回。

    用于「和声多样性」：progression 短于段落时要循环铺满，循环接缝处会出现
    连续两小节完全相同的和弦 —— 那一小节的旋律素材因此和前一节一模一样，
    听感上就是「复制粘贴」。这里只**加音**、不改根音也不改级数，功能和声
    语义完全保留，但和弦音从 3 个变 4 个（旋律素材面 +1）。

    加哪个音：
      * 属音级（根音 = 主音上方纯五度）→ 小七（10），得属七，正当；
      * 小三和弦 → 小七（10），i7 / ii7 / iv7 / vi7 都常见；
      * 其余大三和弦 → 加六（9），得 I6 / IV6 —— 不用 I7，否则主和弦
        被改成属七性质，功能就乱了。
    """
    if len(chord) >= 4:
        return list(chord)
    try:
        root = m21pitch.Pitch(chord[0])
        third = m21pitch.Pitch(chord[1])
    except Exception:
        return list(chord)
    iv3 = (third.pitchClass - root.pitchClass) % 12
    is_dom = False
    if scale_names:
        try:
            tonic_pc = _pc(scale_names[0])
            is_dom = (root.pitchClass - tonic_pc) % 12 == 7
        except Exception:
            is_dom = False
    if is_dom or iv3 == 3:
        add = 10
    elif iv3 == 4:
        add = 9
    else:
        return list(chord)          # 减三/增三等：不加音，保持原样
    return list(chord) + [_sp(root.transpose(add).name)]


def _s_set_harmony(st: _State, args: Dict[str, Any]):
    sec = st.section(args.get('section'))
    prog = args.get('progression')
    if not isinstance(prog, list) or not prog:
        raise KernelError('set_harmony 需要 progression 非空列表（罗马数字 或 和弦符号）')
    # 段落级转调（可选）：给该段设新调号/调式，罗马数字按**新调**解析，
    # 记入 key_changes 并在 MusicXML 对应小节写出显式调号变化
    new_sharps = args.get('key_sharps')
    new_mode = args.get('mode')
    if new_sharps is not None:
        try:
            new_sharps = int(new_sharps)
        except (TypeError, ValueError):
            raise KernelError(f'key_sharps 需为 int（-7–7），收到 {args.get("key_sharps")!r}')
        if not (-7 <= new_sharps <= 7):
            raise KernelError(f'key_sharps 需在 -7–7，收到 {new_sharps}')
    if new_mode is not None:
        new_mode = str(new_mode)
        if new_mode not in ('major', 'minor'):
            raise KernelError(f"mode 必须是 'major' 或 'minor'，收到 {new_mode!r}")
    if new_sharps is not None or new_mode is not None:
        sec['key_sharps'] = st.sharps if new_sharps is None else new_sharps
        sec['mode'] = st.mode if new_mode is None else new_mode
        if (sec['key_sharps'], sec['mode']) != (st.sharps, st.mode) and \
                not any(int(k[0]) == sec['start'] for k in st.key_changes):
            st.key_changes.append([sec['start'], sec['key_sharps'], sec['mode']])
            st.key_changes.sort(key=lambda k: int(k[0]))
    scale = st.scale_for(sec)
    # 显式和弦符号（Dm7/G7/Cmaj7/C#dim7…）是用户明确意图，跳过风格契约改写；
    # 纯罗马数字才走 apply_style_quality 的语料品质分布。
    explicit = any(_is_chord_symbol(t) for t in prog)
    # 风格契约质量改写只能作用在「罗马 token 字符串」上；和弦符号是用户显式
    # 意图，跳过改写。先对 token 做质量改写，随后**统一解析成和弦**，避免把
    # chord-list 误当成 token 喂给 apply_style_quality（会导致 list(str) 字符化）。
    if not explicit:
        prog, _qapplied = apply_style_quality(st, list(prog), str(sec.get('name') or ''))
        if _qapplied:
            st.style_notes.extend(
                [f'段落 {sec["name"]} 按风格契约加和弦品质：{x}' for x in _qapplied])
    try:
        chords = [(_parse_chord_symbol(t, scale) if _is_chord_symbol(t)
                   else parse_roman(t, scale)) for t in prog]
    except KernelError as e:
        raise KernelError(f'段落 {sec["name"]}：{e}')
    for i, bar in enumerate(range(sec['start'], sec['end'] + 1)):
        st.harmony[bar] = list(chords[i % len(chords)])
    # ---- 和声多样性（修复方向 5）----
    # progression 短于段落时 `chords[i % len(chords)]` 会循环铺满，接缝处
    # （以及作者自己写的 `i i`）出现连续两小节**完全相同的和弦**：那两小节的
    # 旋律素材面逐音相同，是「复制粘贴」最直接的和声侧来源。
    # 这里把后一个小节升级为七/六和弦（只加音，级数与功能不变），素材面
    # 3→4。终止式小节不动（cadence 需要干净的主/属长音）。
    # `variation: false` 可显式关闭（例如要严格复现某个进行）。
    if args.get('variation', True):
        for bar in range(sec['start'], sec['end']):
            if bar in st.cadence_bars or (bar + 1) in st.cadence_bars:
                continue
            a = st.harmony.get(bar)
            b = st.harmony.get(bar + 1)
            if a and b and len(a) == len(b) and all(
                    _pc(x) == _pc(y) for x, y in zip(a, b)):
                st.harmony[bar + 1] = _extend_chord_tone(b, scale)
    # 逐小节覆盖：bar_overrides 用绝对小节号（从曲首第 1 小节起）指向某个
    # 和弦符号/罗马数字，实现「第 N 小节用 X 和弦」式精确命中。
    bar_overrides = args.get('bar_overrides') or {}
    if isinstance(bar_overrides, dict):
        for bar_str, tok in bar_overrides.items():
            try:
                bar_no = int(bar_str)
            except (TypeError, ValueError):
                raise KernelError(f'bar_overrides 的键需为小节号(int)，收到 {bar_str!r}')
            if not (sec['start'] <= bar_no <= sec['end']):
                raise KernelError(
                    f'bar_overrides 小节 {bar_no} 不在段落 {sec["name"]}'
                    f'（{sec["start"]}–{sec["end"]}）范围内')
            try:
                ch = (_parse_chord_symbol(tok, scale) if _is_chord_symbol(tok)
                      else parse_roman(tok, scale))
            except KernelError as e:
                raise KernelError(f'段落 {sec["name"]} bar_overrides[{bar_no}]：{e}')
            st.harmony[bar_no] = list(ch)


# ---- 伴奏 ----

# 伴奏低音跨小节最大跳进（半音）：优先五度内，必要时放宽到八度
BASS_MAX_LEAP = 7


def _lowest_midi_of_ev(ev: List) -> Optional[int]:
    """事件最低音 MIDI；休止/空返回 None。"""
    if not ev:
        return None
    p = ev[0]
    if p is None:
        return None
    if isinstance(p, str) and p.strip().lower() in ('r', 'rest', ''):
        return None
    try:
        if isinstance(p, (list, tuple)):
            ms = [_midi(str(x)) for x in p]
            return min(ms) if ms else None
        return _midi(str(p))
    except Exception:
        return None


def _last_bass_midi(evs: List[List]) -> Optional[int]:
    for e in reversed(evs or []):
        m = _lowest_midi_of_ev(e)
        if m is not None:
            return m
    return None


def _shift_pitch_token(tok, shift: int, prefer: List[str]):
    """音名或音名列表整体移 shift 半音（应为 12 的倍数）。"""
    if tok is None:
        return None
    if isinstance(tok, (list, tuple)):
        return [_name_oct(_midi(str(x)) + shift, prefer) for x in tok]
    if isinstance(tok, str) and tok.strip().lower() in ('r', 'rest', ''):
        return tok
    return _name_oct(_midi(str(tok)) + shift, prefer)


def _align_acc_bar_to_prev(ev: List[List], bass_prev: int, chord: List[str],
                          lo: int, hi: int,
                          max_leap: int = BASS_MAX_LEAP) -> Tuple[List[List], int]:
    """整小节伴奏按八度平移，使低音根音贴近上一小节末低音（收紧跨小节跳进）。

    音型内部相对关系不变；仅在跨小节根音运动上做最近八度选择。
    优先锚定和弦根音（圆舞/玛祖卡首拍可能是柱式而非根音）。
    """
    root_pc = _pc(chord[0]) if chord else None
    first = None
    root_anchor = None
    for e in ev:
        p = e[0] if e else None
        if p is None:
            continue
        if isinstance(p, str) and p.strip().lower() in ('r', 'rest', ''):
            continue
        try:
            if isinstance(p, (list, tuple)):
                ms = [_midi(str(x)) for x in p]
            else:
                ms = [_midi(str(p))]
        except Exception:
            continue
        if not ms:
            continue
        if first is None:
            first = min(ms)
        if root_pc is not None and root_anchor is None:
            for m in ms:
                if m % 12 == root_pc:
                    root_anchor = m
                    break
    anchor = root_anchor if root_anchor is not None else first
    if anchor is None:
        return ev, bass_prev
    pc = anchor % 12
    prefer = list(chord) if chord else []
    target = None
    leap = int(max_leap)
    while leap <= 24:
        wlo = max(lo, bass_prev - leap)
        whi = min(hi, bass_prev + leap)
        if wlo <= whi:
            t = _nearest_pc(bass_prev, pc, wlo, whi)
            if lo <= t <= hi:
                target = t
                break
        leap += 5
    if target is None:
        target = _nearest_pc(bass_prev, pc, lo, hi)
    shift = int(target) - int(anchor)
    if shift == 0:
        return ev, int(anchor)
    out: List[List] = []
    for e in ev:
        if not e:
            out.append(e)
            continue
        new_p = _shift_pitch_token(e[0], shift, prefer)
        out.append([new_p] + list(e[1:]))
    return out, int(anchor) + shift


def _bass_register_window(oct_: int) -> Tuple[int, int]:
    """伴奏低音可游荡音域（以 register 八度为中心，约两八度）。"""
    lo = 12 * int(oct_)          # 该八度的 C
    hi = 12 * (int(oct_) + 2) + 5  # 上两八度的 F
    return max(24, lo - 5), min(67, hi)  # 约 C1..G4


def _contract_tones(st: _State, chord: List[str], oct_: int) -> Optional[List[str]]:
    """带实证契约时的伴奏音型：奏出和弦自身的音（根 / 三 / 五 / 七）。

    默认（无契约）的 broken_arpeggio / block_chords 只弹根音与五音 —— 空五度
    既听不出大调小调，也听不出七和弦。于是「从 426 首爵士学到的 82% 七和弦」
    只存在于和声层，在谱面上既不可听也不可测（风格验收测试无从判定）。

    有契约且契约和声分区可用时，按和弦实际音高管发声，学到的品质才可听可测。
    没有契约 / 契约不可用 / 和弦不足 3 音 → 返回 None，走历史音型（零侵入）。
    """
    c = getattr(st, 'style', None)
    if c is None or len(chord) < 3:
        return None
    try:
        h = getattr(c, 'harmony', None)
        if h is None or not h.is_usable():
            return None
    except Exception:
        return None
    tones = [f'{chord[0]}{oct_}', f'{chord[1]}{oct_ + 1}', f'{chord[2]}{oct_ + 1}']
    for extra in chord[3:]:
        tones.append(f'{extra}{oct_ + 1}')
    return tones


def _rest_bar(cap: float) -> List[List]:
    """整小节休止（精确填满）。"""
    return [[None, QL_DUR[d]] for d in _cad_durs(cap)]


def _pitched_bass_idx(st: _State) -> int:
    """伴奏低音声部：优先弦乐/键盘低音，跳过打击（避免鼓组占 bass_idx）。"""
    for i, p in enumerate(st.parts):
        n = p['name'].lower()
        if any(k in n for k in ('contrabass', 'doublebass', 'cello', 'bassoon',
                                'tuba', 'trombone')):
            if _family(n) != 'percussion':
                return i
    for i in range(len(st.parts) - 1, 0, -1):
        if _family(st.parts[i]['name']) != 'percussion':
            return i
    return st.bass_idx


def _comping_stack(st: _State, chord: List[str], lo: int, hi: int) -> List[str]:
    """Comping 柱式：三/五/七（根音留给低音线条）；音高来自当前小节和弦。"""
    tones = []
    for idx in (1, 2, 3) if len(chord) >= 4 else (1, 2, 0):
        if idx >= len(chord):
            continue
        m = _tone_midi_in_range(chord[idx], lo, hi)
        if m is None:
            continue
        tones.append(_key_name(st, m, chord))
    if not tones:
        m = max(lo, min(hi, (lo + hi) // 2))
        tones = [_key_name(st, m, chord)]
    return tones


def _walking_bass_events(st: _State, chord: List[str], nxt: List[str],
                         lo: int, hi: int, cap: float,
                         prev_m: Optional[int]) -> Tuple[List[List], int]:
    """走动低音 pattern：四分和弦音 + 末拍半音逼近下一根（音级来自和声层）。"""
    n = max(1, int(round(cap)))
    order = [0, 2, 1, 3] if len(chord) >= 4 else [0, 2, 1, 0]
    midis: List[int] = []
    for i in range(n):
        if i == n - 1 and nxt:
            tgt = _tone_midi_in_range(nxt[0], lo, hi)
            if tgt is None:
                tgt = max(lo, min(hi, (lo + hi) // 2))
            approach = tgt - 1
            if approach < lo:
                approach = tgt + 1 if tgt + 1 <= hi else tgt
            midis.append(approach)
            continue
        t = chord[order[i % len(order)] % len(chord)]
        m = _tone_midi_in_range(t, lo, hi)
        if m is None:
            m = max(lo, min(hi, (lo + hi) // 2))
        ref = midis[-1] if midis else prev_m
        if ref is not None:
            while m - ref > 7 and m - 12 >= lo:
                m -= 12
            while ref - m > 7 and m + 12 <= hi:
                m += 12
        midis.append(m)
    ev = [[_key_name(st, m, chord), 'quarter'] for m in midis]
    return ev, midis[-1]


def _comping_events(st: _State, chord: List[str], lo: int, hi: int,
                    cap: float, density: float = 1.0) -> List[List]:
    """Comping pattern：4/4 走 Charleston / 2&4；其它拍号按小节容量交替空拍/柱式。"""
    stack = _comping_stack(st, chord, lo, hi)
    # 4/4：疏密度走 Charleston（更摇摆）；中等 2&4；密则加后半拍填空
    if abs(cap - 4.0) < 1e-9:
        if density >= 2.0:
            return [
                [None, 'eighth'], [list(stack), 'eighth'],
                [list(stack), 'quarter'],
                [None, 'eighth'], [list(stack), 'eighth'],
                [list(stack), 'quarter'],
            ]
        if density <= 0.5:
            # Charleston：1& 起击 + 延到 2，3 空，4 击 —— 比死板 2&4 更像标准曲
            return [
                [None, 'eighth'], [list(stack), 'eighth'],
                [list(stack), 'quarter'],
                [None, 'quarter'], [list(stack), 'quarter'],
            ]
        return [
            [None, 'quarter'], [list(stack), 'quarter'],
            [None, 'quarter'], [list(stack), 'quarter'],
        ]
    # 3/4：空拍 + 两下柱式（合计 3 拍）
    if abs(cap - 3.0) < 1e-9:
        if density >= 2.0:
            return [
                [None, 'eighth'], [list(stack), 'eighth'],
                [list(stack), 'quarter'],
                [list(stack), 'quarter'],
            ]
        return [
            [None, 'quarter'], [list(stack), 'quarter'], [list(stack), 'quarter'],
        ]
    # 2/4 及更短：尽量 rest+hit，不足则单柱
    if cap >= 2.0 - 1e-9:
        return [[None, 'quarter'], [list(stack), 'quarter']]
    return [[list(stack), 'quarter']]


def _s_accompaniment(st: _State, args: Dict[str, Any]):
    sec = st.section(args.get('section'))
    pattern = str(args.get('pattern') or 'broken_arpeggio')
    if pattern not in ACC_PATTERNS:
        raise KernelError(f'pattern {pattern!r} 未知（{" / ".join(ACC_PATTERNS)}）')
    oct_ = BASS_OCTS.get(str(args.get('register') or 'low'))
    if oct_ is None:
        raise KernelError(f"register 需为 'low'/'mid'，收到 {args.get('register')!r}")
    density = float(args.get('density') or 1.0)
    if density not in (0.5, 1.0, 2.0):
        raise KernelError('density 需为 0.5 / 1 / 2')
    # walking_bass：低音走动 + 键盘/内声部走 comping 织体（由风格包选中此 pattern）
    st._acc_pattern = pattern
    # 音符级控制：notes 显式给出低声部（第二声部）线条，原样保留绝对音高。
    notes_arg = args.get('notes')
    if notes_arg is not None:
        if not isinstance(notes_arg, list) or not notes_arg:
            raise KernelError('notes 需为 [[token,时值名], …] 非空列表')
        for ntok, ndur in notes_arg:
            if not (isinstance(ntok, (str, int, type(None)))
                    and str(ndur) in DUR_QL):
                raise KernelError(
                    f'notes 项 {[ntok, ndur]!r} 非法（[token, 时值名]，'
                    f'token∈级数1-7/^和弦音/音名C4/rest）')
        bcenter = oct_ * 12 + 7
        blo, bhi = bcenter - 9, bcenter + 10
        scale = st.scale_for(sec)
        idx = 0
        for bar in range(sec['start'], sec['end'] + 1):
            cap = st.bar_cap(bar)
            chord = st.harmony.get(bar)
            if not chord:
                raise KernelError(f'第 {bar} 小节无和声（先对段落 {sec["name"]} set_harmony）')
            bar_events: List[List] = []
            used = 0.0
            while idx < len(notes_arg) and used < cap - 1e-9:
                ntok, ndur = notes_arg[idx]
                dq = DUR_QL[str(ndur)]
                if used + dq > cap + 1e-9:
                    raise KernelError(
                        f'第 {bar} 小节 notes 越界/跨小节（需精确填满或小于小节容量）')
                m = _resolve_melody_token(ntok, chord, scale, blo, bhi, bcenter,
                                          clamp=False)
                bar_events.append(
                    [None if m is None else _name_oct(m, scale + chord),
                     str(ndur), None])
                used += dq
                idx += 1
            if cap - used > 1e-6:
                cont, _ = _fill_continuation(cap - used, chord, bcenter, None,
                                             blo, bhi, scale)
                bar_events.extend(cont)
            st.assert_bar_free(st.bass_idx, bar, 'accompaniment')
            st.put_bar(st.bass_idx, bar, bar_events)
        if idx < len(notes_arg):
            raise KernelError(
                f'notes 总时值超过段落 {sec["name"]} 容量（剩余 {len(notes_arg) - idx} 个音无处安放）')
        return
    first_chord = st.harmony.get(sec['start'])
    bass_prev = _midi(f'{first_chord[0]}{oct_}') if first_chord else _midi('C2')
    bass_lo, bass_hi = _bass_register_window(oct_)
    # walking/comping：低音写到有音高的低音声部（跳过打击）
    bass_pi = (_pitched_bass_idx(st)
               if pattern in ('walking_bass', 'comping') else st.bass_idx)
    walk_prev: Optional[int] = None
    for bar in range(sec['start'], sec['end'] + 1):
        chord = st.harmony.get(bar)
        if not chord:
            raise KernelError(f'第 {bar} 小节无和声（先对段落 {sec["name"]} set_harmony）')
        st.assert_bar_free(bass_pi, bar, 'accompaniment')
        cap = st.bar_cap(bar)
        root = f'{chord[0]}{oct_}'
        fifth = f'{chord[2]}{oct_}'
        root_up = f'{chord[0]}{oct_ + 1}'
        third = f'{chord[1]}{oct_ + 1}'
        fifth_up = f'{chord[2]}{oct_ + 1}'
        ev: List[List] = []
        if pattern == 'walking_bass':
            bname = st.parts[bass_pi]['name']
            bclef = st.parts[bass_pi].get('clef', 'bass')
            blo, bhi = part_range(bname, bclef)
            nxt = st.harmony.get(bar + 1) or chord
            ev, walk_prev = _walking_bass_events(
                st, chord, nxt, blo, bhi, cap, walk_prev)
            st.put_bar(bass_pi, bar, _guard_part_range(st, bass_pi, ev))
            if st.bass_idx != bass_pi and bar not in st.parts[st.bass_idx]['bars']:
                if _family(st.parts[st.bass_idx]['name']) == 'percussion':
                    st.put_bar(st.bass_idx, bar, _rest_bar(cap))
            _render_texture(st, bar, chord, cap, density=density)
            continue
        if pattern == 'comping':
            bname = st.parts[bass_pi]['name']
            bclef = st.parts[bass_pi].get('clef', 'bass')
            blo, bhi = part_range(bname, bclef)
            ev = _comping_events(st, chord, blo, bhi, cap, density)
            st.put_bar(bass_pi, bar, _guard_part_range(st, bass_pi, ev))
            _render_texture(st, bar, chord, cap, density=density)
            continue
        if pattern == 'sustained':
            if cap == 4.0:
                ev = [[root, 'half'], [fifth, 'half']]
            elif cap == 3.0:
                ev = [[root, 'half'], [fifth, 'quarter']]
            elif cap == 2.0:
                ev = [[root, 'half']]
            else:
                ev = [[root, 'whole']]
        elif pattern == 'block_chords':
            ctones = _contract_tones(st, chord, oct_)
            if ctones:
                stack = list(ctones)
            else:
                stack = [root, third, fifth_up]
                if len(chord) >= 4:
                    stack.append(f'{chord[3]}{oct_ + 1}')
            if cap == 4.0:
                ev = [[list(stack), 'half'], [list(stack), 'half']]
            elif cap == 3.0:
                ev = [[list(stack), 'half'], [root, 'quarter']]
            elif cap == 2.0:
                ev = [[list(stack), 'half']]
            else:
                ev = [[list(stack), 'whole']]
        elif pattern == 'broken_arpeggio':
            ctones = _contract_tones(st, chord, oct_)
            if density == 2.0:
                tones = ctones or [root, third, fifth_up, root_up,
                                   fifth_up, third, root, third]
                n = int(cap * 2)
                seq = (list(tones) * (n // len(tones) + 1))[:n]
                ev = [[p, 'eighth'] for p in seq]
            else:
                tones = ctones or [root, third, fifth_up, root_up]
                n = int(cap)
                seq = (list(tones) * (n // len(tones) + 1))[:n]
                ev = [[p, 'quarter'] for p in seq]
        elif pattern == 'alberti':
            seq = [root, fifth_up, third, fifth_up]
            if cap == 3.0:
                seq = [root, fifth_up, third]
            ev = [[p, 'quarter'] for p in seq[: int(cap)]]
        elif pattern == 'octave_pulse':
            hits = max(1, int(cap * density))
            while cap / hits not in QL_DUR and hits < 32:
                hits += 1
            dur = QL_DUR[cap / hits]
            for _ in range(hits):
                ev.append([[root, root_up], dur])
        elif pattern == 'chromatic_descent':
            nxt = st.harmony.get(bar + 1) or chord
            hits = max(1, int(cap * (1 if density <= 1 else 2)))
            while cap / hits not in QL_DUR and hits < 32:
                hits += 1
            dur = QL_DUR[cap / hits]
            bass_lo_cd = 12 * (oct_ + 1)
            bass_hi_cd = bass_lo_cd + 12
            start_m = _nearest_pc(bass_prev, _pc(chord[0]),
                                  bass_prev - 6, bass_prev + 6)
            while start_m < bass_lo_cd:
                start_m += 12
            while start_m > bass_hi_cd:
                start_m -= 12
            tgt_m = _nearest_pc(start_m, _pc(nxt[0]), start_m - 6, start_m + 6)
            while tgt_m < bass_lo_cd:
                tgt_m += 12
            while tgt_m > bass_hi_cd:
                tgt_m -= 12
            if hits <= 1:
                seq = [start_m]
            elif tgt_m == start_m:
                fifth_m = _nearest_pc(start_m, _pc(chord[2]),
                                      start_m - 2, start_m + 7)
                seq = [start_m if i % 2 == 0 else fifth_m
                       for i in range(hits)]
            else:
                seq = [start_m + round((tgt_m - start_m) * i / (hits - 1))
                       for i in range(hits)]
            for m in seq:
                ev.append([_key_name(st, m, chord), dur])
            bass_prev = seq[-1]
        elif pattern == 'waltz':
            tri = [third, fifth_up]
            if cap == 3.0:
                ev = [[root, 'quarter'], [list(tri), 'quarter'], [list(tri), 'quarter']]
            elif cap == 2.0:
                ev = [[root, 'quarter'], [list(tri), 'quarter']]
            else:
                ev = [[root, 'half'], [list(tri), 'half']]
        elif pattern == 'mazurka':
            # 3/4 重音在二或三拍：弱柱 → 根音重拍 → 柱式（区别于圆舞「强-弱-弱」）
            tri = [third, fifth_up]
            if cap == 3.0:
                # 偶数小节偏重第三拍，奇数小节偏重第二拍
                if bar % 2 == 0:
                    ev = [[list(tri), 'quarter'], [list(tri), 'quarter'],
                          [root, 'quarter']]
                else:
                    ev = [[list(tri), 'quarter'], [root, 'quarter'],
                          [list(tri), 'quarter']]
            elif cap == 2.0:
                ev = [[list(tri), 'quarter'], [root, 'quarter']]
            else:
                ev = [[list(tri), 'half'], [root, 'half']]
        # 跨小节低音最近八度：收紧根音跳进（chromatic_descent 已自管 bass_prev）
        if pattern != 'chromatic_descent' and ev:
            ev, bass_prev = _align_acc_bar_to_prev(
                ev, bass_prev, chord, bass_lo, bass_hi)
        st.put_bar(bass_pi, bar, _guard_part_range(st, bass_pi, ev))
        if bass_pi != st.bass_idx and bar not in st.parts[st.bass_idx]['bars']:
            if _family(st.parts[st.bass_idx]['name']) == 'percussion':
                st.put_bar(st.bass_idx, bar, _rest_bar(cap))
        _render_texture(st, bar, chord, cap, density=density)


def _family(name: str) -> str:
    """乐器名 → 乐器族（决定默认织体角色）。"""
    n = (name or '').lower()
    if any(k in n for k in ('piccolo', 'flute', 'oboe', 'clarinet', 'bassoon')):
        return 'woodwind'
    if any(k in n for k in ('horn', 'trumpet', 'trombone', 'tuba')):
        return 'brass'
    if any(k in n for k in ('timpani', 'percussion', 'cymbal', 'snare',
                            'bassdrum', 'gong', 'drum')):
        return 'percussion'
    if any(k in n for k in ('harp', 'piano', 'celesta', 'keyboard')):
        return 'keyboard'
    return 'string'


def _tone_midi_in_range(tone: str, lo: int, hi: int) -> Optional[int]:
    """把和弦音名放到 [lo,hi] 内最接近音域中段的八度；无可行八度返回 None。"""
    best = None
    for o in range(0, 9):
        try:
            m = _midi(f'{tone}{o}')
        except Exception:
            continue
        if lo <= m <= hi:
            k = abs(m - (lo + hi) / 2.0)
            if best is None or k < best[0]:
                best = (k, m)
    return best[1] if best else None


def _brass_support(st, chord, lo, hi, cap):
    """铜管支撑：长音和弦音（根或五音），低密度，不抢节奏。"""
    m = _tone_midi_in_range(chord[0], lo, hi) or _tone_midi_in_range(chord[2], lo, hi)
    if m is None:
        m = max(lo, min(hi, (lo + hi) // 2))
    name = _key_name(st, m, chord)
    if cap >= 4.0 - 1e-9:
        return [[name, 'half'], [name, 'half']]
    if cap >= 3.0 - 1e-9:
        return [[name, 'half'], [name, 'quarter']]
    return [[name, 'whole']]


def _ww_sustain(st, chord, lo, hi, cap):
    """木管衬托：长音和弦音（与铜管同密度），不抢旋律节奏。"""
    # 优先三音，其次五音——与旋律根音线条错开
    prefer = []
    if len(chord) > 1:
        prefer.append(chord[1])
    if len(chord) > 2:
        prefer.append(chord[2])
    prefer.append(chord[0])
    m = None
    for t in prefer:
        m = _tone_midi_in_range(t, lo, hi)
        if m is not None:
            break
    if m is None:
        m = max(lo, min(hi, (lo + hi) // 2))
    name = _key_name(st, m, chord)
    if cap >= 4.0 - 1e-9:
        return [[name, 'whole']]
    if cap >= 3.0 - 1e-9:
        return [[name, 'half'], [name, 'quarter']]
    if cap >= 2.0 - 1e-9:
        return [[name, 'half']]
    return [[name, 'quarter']]


def _string_arp(st, chord, lo, hi, cap, offset: float = 0.5,
                *, sparse: bool = False):
    """弦乐/竖琴琶音：分解和弦；sparse=True 时稀疏四分（管弦伴奏层，精确填满小节）。"""
    arp = []
    for t in (chord[0], chord[1], chord[2]):
        m = _tone_midi_in_range(t, lo, hi)
        arp.append(m if m is not None else max(lo, min(hi, (lo + hi) // 2)))
    arp.append(min(arp[0] + 12, hi) if arp[0] + 12 <= hi else arp[0])
    if sparse:
        # 前半休止 + 后半四分分解，总时值 = cap
        if cap >= 4.0 - 1e-9:
            tones = [arp[i % len(arp)] for i in range(2)]
            return ([[None, 'half']]
                    + [[_key_name(st, m, chord), 'quarter'] for m in tones])
        if cap >= 3.0 - 1e-9:
            tones = [arp[i % len(arp)] for i in range(2)]
            return ([[None, 'quarter']]
                    + [[_key_name(st, m, chord), 'quarter'] for m in tones])
        # 短小节：一个四分休止 + 一音符
        return [[None, 'quarter'],
                [_key_name(st, arp[0], chord), 'quarter']]
    n = max(1, int(round((cap - offset) / 0.5)))
    seq = [arp[i % len(arp)] for i in range(n)]
    ev = [[None, QL_DUR[offset]]] if offset > 1e-6 else []
    ev += [[_key_name(st, m, chord), 'eighth'] for m in seq]
    return ev


def _vn2_harmony_rhythm(st, chord, lo, hi, cap):
    """VnII：八分和声节奏，优先三音，根/三/五/(七) 轮转——非长音垫。"""
    midis: List[int] = []
    for t in chord[: min(4, len(chord))]:
        m = _tone_midi_in_range(t, lo, hi)
        if m is not None:
            midis.append(m)
    if not midis:
        midis = [max(lo, min(hi, (lo + hi) // 2))]
    # 优先从三音起（有则把 index 1 提前）
    order = list(range(len(midis)))
    if len(midis) >= 2:
        order = [1, 0] + list(range(2, len(midis)))
    n = max(1, int(round(cap / 0.5)))
    seq = [midis[order[i % len(order)]] for i in range(n)]
    return [[_key_name(st, m, chord), 'eighth'] for m in seq]


def _va_inner_motion(st, chord, lo, hi, cap):
    """中提琴：无休止起拍的分解八分运动。"""
    return _string_arp(st, chord, lo, hi, cap, offset=0.0)


def _vc_walking_bass(st, chord, lo, hi, cap):
    """大提琴（五部时低音之上）：四分根-五交替，禁止整小节长音垫。"""
    root_m = _tone_midi_in_range(chord[0], lo, hi)
    fifth_m = _tone_midi_in_range(chord[2], lo, hi) if len(chord) > 2 else None
    if root_m is None:
        root_m = max(lo, min(hi, (lo + hi) // 2))
    if fifth_m is None:
        fifth_m = root_m
    n = max(1, int(round(cap)))
    seq = [root_m if i % 2 == 0 else fifth_m for i in range(n)]
    return [[_key_name(st, m, chord), 'quarter'] for m in seq]


def _is_string_ensemble(st: _State) -> bool:
    """纯弦乐四部及以上（无木管/铜管）→ 启用角色织体，禁止 pad。"""
    names = [p['name'].lower() for p in st.parts]
    if any(_family(n) in ('woodwind', 'brass') for n in names):
        return False
    n_str = sum(1 for n in names if _family(n) == 'string')
    return n_str >= 4 and st.voices >= 4


def _perc_accent(st, chord, lo, hi, cap, bar):
    """打击点描：仅在第 1（及 3）拍落音，其余休止。"""
    m = _tone_midi_in_range(chord[0], lo, hi)
    if m is None:
        m = max(lo, min(hi, lo + 12))
    name = _key_name(st, m, chord)
    beats = max(1, int(round(cap)))
    each = cap / beats
    hits = {1}
    if beats >= 3:
        hits.add(3)
    return [[name if b in hits else None, QL_DUR[each]] for b in range(1, beats + 1)]

def _render_texture(st: _State, bar: int, chord: List[str], cap: float,
                    density: float = 1.0):
    """内声部织体引擎。

    - **walking_bass / comping pattern**：键盘 comps，打击休止，低音已由伴奏写好
    - **管弦编制**（≥6 且含木管/铜管）：按乐器族分层，禁止全员八分抢戏
    - **纯弦乐四部+**：VnII 和声节奏 / Va 内声 / Vc 走动
    - 其余：经典长音 pad
    编制来自风格包 instruments；此处只按「实际有哪些乐器」选织体算法。
    """
    if st.voices <= 2:
        return
    names = [p['name'].lower() for p in st.parts]
    acc_pat = getattr(st, '_acc_pattern', None)
    walk_mode = acc_pat in ('walking_bass', 'comping')
    is_orch = (not walk_mode
               and any(_family(n) in ('woodwind', 'brass') for n in names)
               and st.voices >= 6)
    is_str_ens = (not walk_mode) and _is_string_ensemble(st)
    bass_written = _pitched_bass_idx(st) if walk_mode else st.bass_idx
    inners = list(range(1, st.bass_idx))
    string_inners = [pi for pi in inners
                     if _family(st.parts[pi]['name']) == 'string']
    last_pi = st.bass_idx if walk_mode else st.bass_idx - 1
    for pi in range(1, last_pi + 1):
        if walk_mode and pi == bass_written:
            continue
        if bar in st.parts[pi]['bars']:
            continue
        nm = st.parts[pi]['name']
        clef = st.parts[pi].get('clef', 'treble')
        fam = _family(nm)
        lo, hi = part_range(nm, clef)
        if walk_mode:
            if fam == 'keyboard':
                raw = _comping_events(st, chord, lo, hi, cap, density)
                st.put_bar(pi, bar, _guard_part_range(st, pi, raw))
            else:
                st.put_bar(pi, bar, _rest_bar(cap))
            continue
        if is_str_ens:
            role_i = inners.index(pi) if pi in inners else 0
            if role_i == 0:
                raw = _vn2_harmony_rhythm(st, chord, lo, hi, cap)
            elif role_i == len(inners) - 1 and len(inners) >= 2:
                raw = _vc_walking_bass(st, chord, lo, hi, cap)
            else:
                raw = _va_inner_motion(st, chord, lo, hi, cap)
            st.put_bar(pi, bar, _guard_part_range(st, pi, raw))
            continue
        if not is_orch:
            tone = chord[pi % max(1, len(chord))]
            m = _tone_midi_in_range(tone, lo, hi)
            if m is None:
                m = max(lo, min(hi, _midi(f'{tone}4')))
            ev = [[_key_name(st, m, chord), QL_DUR[d]] for d in _cad_durs(cap)]
            st.put_bar(pi, bar, ev)
            continue
        if fam == 'percussion':
            st.put_bar(pi, bar, _perc_accent(st, chord, lo, hi, cap, bar))
        elif fam == 'brass':
            st.put_bar(pi, bar, _brass_support(st, chord, lo, hi, cap))
        elif fam == 'woodwind':
            st.put_bar(pi, bar, _ww_sustain(st, chord, lo, hi, cap))
        else:
            if string_inners and pi == string_inners[0]:
                raw = _vn2_harmony_rhythm(st, chord, lo, hi, cap)
            elif string_inners and pi == string_inners[-1] and len(string_inners) >= 2:
                raw = _vc_walking_bass(st, chord, lo, hi, cap)
            else:
                raw = _string_arp(st, chord, lo, hi, cap, 0.5, sparse=True)
            st.put_bar(pi, bar, _guard_part_range(st, pi, raw))
    if is_orch or is_str_ens:
        _resolve_inner_crossings(st, bar)



def _resolve_inner_crossings(st: _State, bar: int):
    """声部写作 voice-leading：把内声部按真实音高重排，逐拍用八度移位消除交叉。

    分层织体让每个家族（木管线条/铜管支撑/弦乐琶音/打击点描）在各自音域内取音，但
    编制按「乐谱次序」排列（木管→铜管→打击→弦乐），相邻乐谱声部未必纵向相邻，且各
    声部独立取音会彼此越过。本函数：
      1) 按「实际音高跨度中心」确定真正的纵向叠放（旋律恒最上、低音恒最下、内声部按
         实际跨度中心高→低排在中间）。注意不能用乐器名义音域中心——大管名义音域向低
         音区延伸，但实际只在中高音区取音，按名义中心会把它错排到低音号之下反造交叉。
      2) 逐拍让下方声部不越过上方声部：越界则把该音下行八度（仍保持音级、且不越出
         乐器音域），下行空间不足则把上方声部上行八度。旋律与低音为锚点，绝不移动。
    八度移位保持音级不变（同和弦音/调内音换八度），不破坏和声与角色。
    """
    if st.bass_idx <= 2:
        return
    voices = list(range(0, st.bass_idx + 1))
    ranges: Dict[int, Tuple[int, int]] = {}
    for v in voices:
        nm = st.parts[v]['name']
        clef = st.parts[v].get('clef', 'treble')
        ranges[v] = part_range(nm, clef)
    anchors = {0, st.bass_idx}

    bar_events: Dict[int, List[Tuple[int, List]]] = {}
    for v in voices:
        b = st.parts[v]['bars'].get(bar)
        bar_events[v] = list(enumerate(b)) if b else []

    def _ev_midis(e0):
        if e0 is None:
            return []
        if isinstance(e0, (list, tuple)):
            out = []
            for x in e0:
                if x is None:
                    continue
                try:
                    out.append(m21pitch.Pitch(str(x)).midi)
                except Exception:
                    pass
            return out
        try:
            return [m21pitch.Pitch(str(e0)).midi]
        except Exception:
            return []

    # 每个声部本小节的音高跨度（休止声部不参与重排）
    span: Dict[int, Optional[Tuple[int, int]]] = {}
    for v in voices:
        ms = []
        for _, e in bar_events[v]:
            ms.extend(_ev_midis(e[0]))
        span[v] = (min(ms), max(ms)) if ms else None

    # 纵向叠放顺序：旋律(0) 最上、低音(bass_idx) 最下、内声部按实际跨度中心高→低
    def _vcenter(v: int) -> float:
        if span.get(v):
            return (span[v][0] + span[v][1]) / 2.0
        return 0.0
    order = [0] + sorted([v for v in voices if v not in anchors],
                         key=lambda v: -_vcenter(v)) + [st.bass_idx]
    order = [v for v in order if span.get(v) is not None]

    # 收集拍点
    onsets: set = set()
    for v in voices:
        evs = bar_events[v]
        if not evs:
            continue
        t = 0.0
        for _, e in evs:
            onsets.add(round(t, 4))
            t += DUR_QL[str(e[1])]
    onsets = sorted(onsets)

    # 每个事件累计的八度移位（仅作用于内声部，锚点恒 0）；同事件跨拍点共享
    delta: Dict[Tuple[int, int], int] = {}

    def _sounding(v, t):
        evs = bar_events[v]
        if not evs:
            return None
        cur = 0.0
        for ei, e in evs:
            if abs(cur - t) < 1e-6 and e[0] is not None:
                return ei, e
            cur += DUR_QL[str(e[1])]
        return None

    def _shift_name(name, d):
        try:
            p = m21pitch.Pitch(str(name))
            p.octave = (p.octave or 4) + d // 12
            return f'{p.name}{p.octave}'
        except Exception:
            return name

    # 逐拍、按纵向顺序处理相邻声部；越界则下行/上行八度，更新后继续级联
    for t in onsets:
        items = []  # (voice, ev_idx, midis)
        for v in order:
            hit = _sounding(v, t)
            if hit is None:
                continue
            ei, e = hit
            base = _ev_midis(e[0])
            if not base:
                continue
            d = delta.get((v, ei), 0)
            items.append((v, ei, [m + d for m in base]))
        items.sort(key=lambda x: -_vcenter(x[0]))
        for k in range(1, len(items)):
            a = items[k - 1]   # 上方
            b = items[k]       # 下方
            a_max = max(a[2])
            b_min = min(b[2])
            if b_min <= a_max:
                continue
            bv, bei, bmidis = b
            av, aei, amidis = a
            lo_b, hi_b = ranges[bv]
            lo_a, hi_a = ranges[av]
            # 优先把下方声部 b 下行八度（保持上方声部稳定）
            if bv not in anchors:
                new_m = [m - 12 for m in bmidis]
                if all(lo_b <= m <= hi_b for m in new_m):
                    delta[(bv, bei)] = delta.get((bv, bei), 0) - 12
                    items[k] = (bv, bei, new_m)
                    continue
            # 否则把上方声部 a 上行八度
            if av not in anchors:
                new_m = [m + 12 for m in amidis]
                if all(lo_a <= m <= hi_a for m in new_m):
                    delta[(av, aei)] = delta.get((av, aei), 0) + 12
                    items[k - 1] = (av, aei, new_m)
                    continue
            # 两端都无法移动 → 残留交叉（尽量而为）

    # 应用八度移位（保持原拼写，只改八度数字）；锚点不移动
    for (v, ei), d in delta.items():
        if d == 0 or v in anchors:
            continue
        e = st.parts[v]['bars'][bar][ei]
        e0 = e[0]
        if isinstance(e0, (list, tuple)):
            e[0] = [_shift_name(x, d) if x is not None else x for x in e0]
        else:
            e[0] = _shift_name(e0, d)


# ---- 旋律 ----

def _fill_continuation(remain: float, chord: List[str], target: int,
                       prev_midi: Optional[int], lo: int, hi: int,
                       scale: List[str]) -> Tuple[List[List], Optional[int]]:
    """动机之后按剩余时值补和弦音（优先长时值）。返回 (事件, 新的 prev_midi)。"""
    ev: List[List] = []
    while remain > 1e-6:
        d = 2.0 if remain >= 2.0 else (1.0 if remain >= 1.0
                                       else (0.5 if remain >= 0.5 else 0.25))
        pcs = [_pc(n) for n in chord]
        base = prev_midi if prev_midi is not None else target
        # 平滑且有方向：兼顾「离前一音近」与「朝乐句目标走」；
        # 重复音小罚分——近目标时保持，离开目标时移动
        best, best_sc = None, None
        for c in set(pcs):
            mc = _nearest_pc(base, c, lo, hi)
            sc = (0.5 * abs(mc - base) + abs(mc - target)
                  + (2.5 if mc == prev_midi else 0))
            if best_sc is None or sc < best_sc:
                best, best_sc = mc, sc
        m = best
        ev.append([_name_oct(m, scale + chord), QL_DUR[d], None])
        prev_midi = m
        remain -= d
    return ev, prev_midi


# ---------- 旋律素材面（修复方向 1：扩素材面） ----------

#: 邻音在锚点打分里的罚分：和弦音仍然优先，但窄和弦（3 音）时邻音能补位
_NEIGHBOR_PENALTY = 3.0
#: 回音式装饰（turn）的出现概率：弱拍先朝反方向绕一步再进入锚点。
#: 走**调内**音，因此不破坏「全曲无调外音」的硬保证
#: （`tests/test_narrative_key_safety` 逐音断言这一点）。
_TURN_P = 0.22


def _melody_pitch_pool(chord_pcs, scale_pcs):
    """旋律素材面：``(和弦音集合, 调内邻音集合)``。

    原实现锚点候选只有本小节 `chord` 的那 3–4 个音（因为 `parse_roman` 的
    音程表写死：三和弦 3 音、七和弦 4 音）。实测命运交响曲 32 小节的素材面
    只有 8/12 个半音，而旋律把 8 个**全部用尽**（未用音级 = 空集）——
    说明不是「选得不好」，是**可选池就这么大**，再调打分函数也变不出新音。

    这里把素材面扩成两层：

      * **和弦音**（tier0）：骨架，保证和声清晰；
      * **调内邻音**（tier1）：与某个和弦音相距 1–2 个半音、且落在本段
        音阶上的音——旋律学上的邻音 / 经过音，放在强拍上就是 6th、9th
        这类色彩音。

    只取调内音，因此**不引入调外音**。
    """
    chord_set = {int(c) % 12 for c in chord_pcs}
    nb = set()
    for c in chord_set:
        for d in (-2, -1, 1, 2):
            p = (c + d) % 12
            if p in scale_pcs and p not in chord_set:
                nb.add(p)
    return chord_set, nb


# ---------- 乐句句法（方向 3）与目标导向解决（方向 4） ----------

#: 乐句算子：第 n 个乐句套用第 n 个算子，让乐句之间产生**句法关系**
#: （模进 / 倒影 / 紧缩），而不是像原来那样只有曲线相位差一格
_PHRASE_OPS = ('identity', 'sequence_up2', 'sequence_dn3', 'invert', 'compress')


def _melodic_goal(st: '_State', bar: int, sec: Dict[str, Any], center: int,
                  curves: List[int], phrase_no: int, bi: int,
                  lo: int, hi: int) -> int:
    """本小节的旋律目标音高 = 轮廓曲线 ⊕ 乐句算子 ⊕ 终止解决。

    三层叠加，取代原先单一的 ``center + curves[i % 8]``：

      1. **轮廓曲线**：保留 contour 语义（arch / wave / ascending …）；
      2. **乐句句法**（方向 3）：第 n 个乐句套用 `_PHRASE_OPS[n]` ——
         模进上行二度 / 模进下行三度 / 倒影 / 紧缩。原来第二乐句和第一乐句
         只有曲线相位差一格，彼此毫无关系，这是「四个乐句像同一句抄四遍」
         的直接原因；
      3. **目标导向解决**（方向 4）：距段落终止（或最近的 cadence 小节）
         两小节以内时，把目标锁到终止和弦的音上 —— 旋律于是**朝终止点
         解决**（导音→主音的倾向性自然包含在内），而不是各走各的。
    """
    base = curves[(phrase_no + bi) % len(curves)]
    op = _PHRASE_OPS[phrase_no % len(_PHRASE_OPS)]
    if op == 'sequence_up2':
        base += 2
    elif op == 'sequence_dn3':
        base -= 3
    elif op == 'invert':
        base = -base
    elif op == 'compress':
        base = int(round(base * 0.5))
    base = max(-11, min(11, base))
    target = center + base
    # ---- 方向 4：终止解决 ----
    goal_bar = sec['end']
    for cb in sorted(st.cadence_bars or ()):
        if bar <= cb <= goal_bar:
            goal_bar = cb
            break
    if goal_bar - bar <= 2:
        gch = st.harmony.get(goal_bar) or st.harmony.get(sec['end'])
        if gch:
            cand = []
            for n in gch:
                c = _pc(n)
                cand.append(_nearest_pc(target, c, lo, hi))
            if cand:
                # 同距时取较高音：终止前上行解决更有收束感
                best_d = min(abs(m - target) for m in cand)
                target = max(m for m in cand if abs(m - target) == best_d)
    return target


def _resolve_melody_token(token, chord: List[str], scale: List[str],
                          lo: int, hi: int, center: int,
                          clamp: bool = True) -> Optional[int]:
    """把 notes 里的一个 token 解析成 MIDI（None=休止）。

    token 形式：
      - None / "rest"            → 休止
      - "1".."7"（或 int）        → 调内音阶级数（按 center 取最近八度）
      - "^1"/"^3"/"^5"/"^7"      → 当前小节和弦音（根/三/五/七）
      - "C4"/"Bb3"/"F#5"         → 绝对音名
    clamp=True（默认，旋律）：绝对音名落在窗口外则整八度收进 [lo,hi]。
    clamp=False（伴奏显式线条）：绝对音名原样保留（用户指定即演奏该音）。
    """
    if token is None:
        return None
    s = str(token).strip()
    if s == '' or s.lower() == 'rest':
        return None
    # 和弦音 ^1/^3/^5/^7（索引 0/1/2/3 对应 根/三/五/七）
    if s.startswith('^'):
        try:
            idx = int(s[1:]) - 1
        except ValueError:
            idx = -1
        if 0 <= idx < len(chord):
            return _nearest_pc(center, _pc(chord[idx]), lo, hi)
        s = s[1:]  # 不支持的 ^n 退化为音阶级数
    # 调内音阶级数
    if s.isdigit() and 1 <= int(s) <= 7:
        deg = int(s) - 1
        scale_pcs = [_pc(n) for n in scale]
        return _nearest_pc(center, scale_pcs[deg % 7], lo, hi)
    # 绝对音名
    try:
        m = _midi(s)
    except Exception:
        raise KernelError(
            f'notes 音符 {token!r} 非法（需为 级数1-7 / ^和弦音 / 音名如 C4 / rest）')
    if clamp:
        return _nearest_pc(m, _pc(s), lo, hi)
    return m


def _s_melody(st: _State, args: Dict[str, Any]):
    sec = st.section(args.get('section'))
    register = str(args.get('register') or 'mid')
    if register not in REGISTER_CENTERS:
        raise KernelError(f"register 需为 low/mid/high/very_high，收到 {register!r}")
    rhythm = str(args.get('rhythm') or 'flowing')
    if rhythm not in RHY_STYLES:
        raise KernelError(f"rhythm 需为 {'/'.join(RHY_STYLES)}，收到 {rhythm!r}")
    contour = str(args.get('contour') or 'arch')
    if contour not in CONTOURS:
        raise KernelError(f"contour 需为 {'/'.join(CONTOURS)}，收到 {contour!r}")
    motif = args.get('motif')
    if motif is not None:
        if (not isinstance(motif, list) or not motif
                or not all(isinstance(m, (list, tuple)) and len(m) == 2
                           for m in motif)):
            raise KernelError('motif 需为 [[级数1-7, 时值名], …]，'
                              "如 [[5,'quarter'],[4,'eighth'],[3,'eighth'],[2,'half']]")
        for d, dur in motif:
            if not (1 <= int(d) <= 7) or str(dur) not in DUR_QL:
                raise KernelError(f'motif 音对 {[d, dur]!r} 非法（级数1-7 + 时值名）')
    center = REGISTER_CENTERS[register]
    lo, hi = center - 9, center + 10
    # 旋律路由：指定了独奏乐器时，把默认音区窗口收进该乐器的可奏音域——
    # 否则把主题交给大提琴/圆号/长号这类中低音乐器会写出超音域的音
    # （例如 mid 窗口 62..81 越出大提琴上限 72）。_nearest_pc 会把每个
    # 旋律音钳在窗口内，所以这一步即保证整个旋律落在该乐器音域里。
    minst = getattr(st, 'melody_instrument', None)
    if minst:
        try:
            rlo, rhi = part_range(minst, st.parts[0].get('clef', 'treble'))
        except Exception:
            rlo, rhi = 40, 90
        lo, hi = _fit_register_window(lo, hi, rlo, rhi)
    scale = st.scale_for(sec)   # 段落级转调：旋律经过音/动机按本段调性走
    scale_pcs = [_pc(n) for n in scale]
    # ---- 音符级控制：notes 显式给出本段旋律音高序列 ----
    # token：级数1-7 / ^和弦音(^1^3^5^7) / 绝对音名(C4) / rest；时值为 DUR_QL 键。
    # 逐小节铺满：每小节按容量截取，余量用和弦音续写，越界（跨小节）报错。
    notes_arg = args.get('notes')
    if notes_arg is not None:
        if not isinstance(notes_arg, list) or not notes_arg:
            raise KernelError('notes 需为 [[token,时值名], …] 非空列表')
        for ntok, ndur in notes_arg:
            if not (isinstance(ntok, (str, int, type(None)))
                    and str(ndur) in DUR_QL):
                raise KernelError(
                    f'notes 项 {[ntok, ndur]!r} 非法（[token, 时值名]，'
                    f'token∈级数1-7/^和弦音/音名C4/rest）')
        minst = getattr(st, 'melody_instrument', None)
        if minst:
            try:
                rlo, rhi = part_range(minst, st.parts[0].get('clef', 'treble'))
            except Exception:
                rlo, rhi = 40, 90
            lo, hi = _fit_register_window(lo, hi, rlo, rhi)
        idx = 0
        prev: Optional[int] = None
        for bar in range(sec['start'], sec['end'] + 1):
            cap = st.bar_cap(bar)
            chord = st.harmony.get(bar)
            if not chord:
                raise KernelError(f'第 {bar} 小节无和声（先对段落 {sec["name"]} set_harmony）')
            bar_events: List[List] = []
            used = 0.0
            while idx < len(notes_arg) and used < cap - 1e-9:
                ntok, ndur = notes_arg[idx]
                dq = DUR_QL[str(ndur)]
                if used + dq > cap + 1e-9:
                    raise KernelError(
                        f'第 {bar} 小节 notes 越界/跨小节（需精确填满或小于小节容量；'
                        f'当前已用 {used:g}，下一个音 {dq:g} > 余量 {cap - used:g}）')
                m = _resolve_melody_token(ntok, chord, scale, lo, hi, center)
                bar_events.append(
                    [None if m is None else _name_oct(m, scale + chord),
                     str(ndur), None])
                prev = m
                used += dq
                idx += 1
            if cap - used > 1e-6:
                cont, prev = _fill_continuation(
                    cap - used, chord, center, prev, lo, hi, scale)
                bar_events.extend(cont)
            st.assert_bar_free(0, bar, 'melody')
            st.put_bar(0, bar, bar_events)
        if idx < len(notes_arg):
            raise KernelError(
                f'notes 总时值超过段落 {sec["name"]} 容量（剩余 {len(notes_arg) - idx} 个音无处安放）')
        return
    rng = st.rng_for('melody', st.title, sec['name'])
    rw = st.soft_weights.get('rhythm')
    bars = list(range(sec['start'], sec['end'] + 1))
    is_last = sec is st.sections[-1]
    curves = {
        'arch': [0, 2, 4, 1, 0, 2, 4, 2],
        'wave': [0, 3, 0, -3, 0, 3, 0, -3],
        'descending': [5, 4, 3, 2, 1, 0, -1, -2],
        'ascending': [-4, -2, 0, 2, 4, 6, 8, 10],
    }[contour]

    prev_midi: Optional[int] = None  # 跨小节延续，旋律才有乐句方向
    back1: Optional[int] = None      # 两音前的音高，用于防钟摆
    recent_rhy: List[tuple] = []     # 最近 2 小节的节奏模板（防短期节奏雷同）
    for phrase_i in range(0, len(bars), 4):
        phrase = bars[phrase_i:phrase_i + 4]
        for bi, bar in enumerate(phrase):
            st.assert_bar_free(0, bar, 'melody')
            chord = st.harmony.get(bar)
            if not chord:
                raise KernelError(f'第 {bar} 小节无和声（先对段落 {sec["name"]} set_harmony）')
            cap = st.bar_cap(bar)
            # 方向 3 + 方向 4：目标 = 轮廓曲线 ⊕ 乐句句法算子 ⊕ 终止解决
            target = _melodic_goal(st, bar, sec, center, curves,
                                   phrase_i // 4, bi, lo, hi)
            bar_events: List[List] = []
            # prev_midi / back1 跨小节延续：乐句有方向感，不逐小节重新锚定
            # 动机（每个乐句首小节，二度模进；按音阶级数走，保持级进轮廓）
            used = 0.0
            if motif and bi == 0:
                shift = [0, 1, 0, -1][(phrase_i // 4) % 4]
                used = sum(DUR_QL[str(d)] for _d, d in motif)
                if used > cap + 1e-6:
                    raise KernelError(f'motif 总时值 {used:g} 超过小节容量 {cap:g}')
                # 窗口内的调音阶（升序），用于按级数走
                scale_walk: List[int] = []
                for octv in range(lo // 12 - 1, hi // 12 + 2):
                    for pc in scale_pcs:
                        m = pc + 12 * octv
                        if lo <= m <= hi:
                            scale_walk.append(m)
                degs = [((int(dg) - 1 + shift) % 7) for dg, _du in motif]
                m0 = _nearest_pc(target, scale_pcs[degs[0]], lo, hi)
                try:
                    idx0 = scale_walk.index(m0)
                except ValueError:
                    idx0 = min(range(len(scale_walk)),
                               key=lambda k: abs(scale_walk[k] - m0))
                # 动机整体可能超出音阶窗口上下界：把起点收进来，避免越界
                d_hi = max(degs) - degs[0]
                d_lo = min(degs) - degs[0]
                idx0 = min(idx0, len(scale_walk) - 1 - d_hi)
                idx0 = max(idx0, -d_lo)
                idx0 = max(0, min(idx0, len(scale_walk) - 1))
                for k, (d, dur) in enumerate(motif):
                    m = scale_walk[max(0, min(len(scale_walk) - 1,
                                              idx0 + (degs[k] - degs[0])))]
                    bar_events.append([_name_oct(m, scale + chord), str(dur), None])
                    back1, prev_midi = prev_midi, m
                if cap - used > 1e-6:
                    cont, prev_midi = _fill_continuation(
                        cap - used, chord, target, prev_midi, lo, hi, scale)
                    bar_events.extend(cont)
                recent_rhy.append(tuple(DUR_QL[str(d)] for _d, d in motif))
                del recent_rhy[:-2]
            else:
                # 方向 2：有动机时，节奏从动机的变形池抽（而不是通用模板池）
                # ——这才是「三短一长无处不在」的写法：动机贯穿全曲，
                # 但以增时/减时/截断/逆行/移位的形态出现，不是逐小节照抄。
                slots = None
                if motif:
                    slots = _pick_motif_rhythm(
                        rng, [DUR_QL[str(d)] for _dg, d in motif],
                        cap, avoid=recent_rhy)
                if slots is None:
                    slots = _pick_rhythm(rng, rhythm, cap, rw,
                                         avoid=recent_rhy)
                recent_rhy.append(tuple(slots))
                del recent_rhy[:-2]
                # —— 锚点式旋律，分两遍：
                # 第一遍只定强拍锚点（和弦音 + 乐句曲线 + 声部平滑，
                #   锚点间不回头、禁止三连同音）；
                # 第二遍弱拍一律作为「朝下一锚点级进的经过音」。
                # 旋律因此有方向、有线条，不产生原地钟摆。
                strong_idx = [i for i, s in enumerate(slots)
                              if s is not None and s >= 1.0]
                anchors: Dict[int, int] = {}
                a_prev: Optional[int] = prev_midi   # 上一锚点（跨小节延续）
                a_back: Optional[int] = back1
                # 方向 1：素材面 = 和弦音 ∪ 和弦音的调内二度邻音。
                # 原来只有和弦音（3–4 个），实测 63 次锚点决策里 31.7%
                # 过滤后「只剩 1 个可选」——不是选得像，是没得选。
                chord_set, nb_set = _melody_pitch_pool(
                    [_pc(n) for n in chord], scale_pcs)
                for i in strong_idx:
                    if i > 0 and slots[i - 1] is None:   # 锚点前是休止：重起
                        a_prev, a_back = None, None
                    if a_prev is None:
                        cand = sorted(chord_set) + sorted(nb_set)
                        pc = min(cand, key=lambda c: abs(
                            _nearest_pc(target, c, lo, hi) - target)
                            + (_NEIGHBOR_PENALTY if c in nb_set else 0.0))
                        m = _nearest_pc(target, pc, lo, hi)
                    else:
                        # 硬性分层过滤：先禁「重复/回头/大跳」，逐层放宽。
                        # 软罚分在窄和弦上会退化为同音持续音墙。
                        cands = [_nearest_pc(a_prev, c, lo, hi)
                                 for c in sorted(chord_set)]
                        ext = [_nearest_pc(a_prev, c, lo, hi)
                               for c in sorted(nb_set)]
                        ext_set = set(ext)

                        def _ok(mc: int) -> bool:
                            return (mc != a_prev and mc != a_back
                                    and abs(mc - a_prev) <= 7)

                        tier1 = [mc for mc in cands if _ok(mc)]
                        tier2 = [mc for mc in cands if mc != a_prev]
                        # 窄和弦补位：候选少于 2 个时并入调内邻音，避免
                        # 「只剩 1 个可选」被迫走同一条路
                        if len(tier1) < 2:
                            tier1 += [mc for mc in ext if _ok(mc)]
                        if len(tier2) < 2:
                            tier2 += [mc for mc in ext if mc != a_prev]
                        pool = tier1 or tier2 or (cands + ext)

                        def _score(mc: int) -> float:
                            sc = (0.6 * abs(mc - a_prev) + abs(mc - target))
                            if mc in ext_set:
                                sc += _NEIGHBOR_PENALTY   # 邻音需「有理由」
                            if target - a_prev > 2 and mc < a_prev:
                                sc += 4      # 目标在上，别倒退
                            if a_prev - target > 2 and mc > a_prev:
                                sc += 4      # 目标在下，别上冲
                            return sc

                        m = min(pool, key=_score)
                    anchors[i] = m
                    a_back, a_prev = a_prev, m
                # 每个槽位的「下一锚点」（弱拍定向用）
                next_a: List[Optional[int]] = [None] * len(slots)
                nxt: Optional[int] = None
                for i in range(len(slots) - 1, -1, -1):
                    next_a[i] = nxt
                    if i in anchors:
                        nxt = anchors[i]
                cur: Optional[int] = prev_midi   # 上一实际发音（跨小节延续）
                momentum: Optional[int] = None
                run = (2 if prev_midi is not None and back1 == prev_midi
                       else 1)                     # 当前音已连续出现的次数
                for i, s in enumerate(slots):
                    if s is None:
                        bar_events.append([None, 'eighth', None])
                        cur, momentum, run = None, None, 1
                        continue
                    dur = QL_DUR[s]
                    if i in anchors:
                        m = anchors[i]
                    elif cur is None:
                        pc = _pc(chord[0])
                        m = _nearest_pc(target, pc, lo, hi)
                    else:
                        na = next_a[i]
                        if na is not None and na != cur:
                            # 经过音：朝下一锚点级进
                            direction = 1 if na > cur else -1
                            # 方向 1（续）：回音式装饰 —— 弱拍上先朝反方向
                            # 绕一步（调内邻音），再从另一侧进入锚点，而不是
                            # 永远单调地「一步一级进」。原先弱拍只有一种走法
                            # （朝锚点走最近调内音），这正是「每个音都能猜到
                            # 下一个音」的来源。走调内音，故不引入调外音。
                            # 终止小节与段落末小节禁用（那里要干净解决）。
                            if (rng.random() < _TURN_P
                                    and abs(na - cur) >= 3
                                    and bar not in st.cadence_bars
                                    and bar != sec['end']):
                                m = _scale_step_to(cur, -direction, lo, hi,
                                                   scale_pcs)
                                if m == cur:
                                    m = _scale_step_to(cur, direction, lo, hi,
                                                       scale_pcs)
                            else:
                                m = _scale_step_to(cur, direction, lo, hi,
                                                   scale_pcs)
                            if direction * (m - na) > 0:
                                m = na        # 走过头：直接落到锚点上
                            if m == cur:      # 窗口边缘走不动：反向一步
                                m = _scale_step_to(cur, -direction, lo, hi,
                                                   scale_pcs)
                        elif na is not None:
                            m = cur           # 已到锚点：保持（预备）
                        else:
                            # 末尾弱拍（本小节已无后续锚点）：朝乐句目标级进
                            if momentum is None:
                                momentum = 1 if target >= cur else -1
                            m = _scale_step_to(cur, momentum, lo, hi,
                                               scale_pcs)
                            if m == cur:
                                momentum = -momentum
                                m = _scale_step_to(cur, momentum, lo, hi,
                                                   scale_pcs)
                    if m == cur and run >= 2:
                        # 同音持续封顶：第三次必动（朝乐句目标一侧），
                        # 否则冲突段小节数秒的持续音墙
                        step_dir = 1 if target >= cur else -1
                        alt = _scale_step_to(cur, step_dir, lo, hi, scale_pcs)
                        if alt == cur:
                            alt = _scale_step_to(cur, -step_dir, lo, hi,
                                                 scale_pcs)
                        if alt != cur:
                            m = alt
                    run = run + 1 if m == cur else 1
                    bar_events.append([_name_oct(m, scale + chord), dur, None])
                    back1, prev_midi = prev_midi, m
                    cur = m
            # 末段末小节：旋律落在主音（cadence 之前的主音预备）
            if is_last and bar == st.bars:
                tonic_pc = _pc(scale[0])   # 本段（可能已转调）的主音
                for e in reversed(bar_events):
                    if e[0] is not None:
                        m = _nearest_pc(_midi(e[0]), tonic_pc, lo, hi)
                        e[0] = _name_oct(m, scale + chord)
                        break
            # 连音线：乐句首小节首音 slur-start；末小节末音 slur-end
            note_idx = [i for i, e in enumerate(bar_events) if e[0] is not None]
            if note_idx:
                if bi == 0:
                    bar_events[note_idx[0]][2] = 'slur-start'
                if bi == len(phrase) - 1:
                    bar_events[note_idx[-1]][2] = 'slur-end'
            st.put_bar(0, bar, bar_events)


def _midi(pitch_str: str) -> int:
    try:
        return m21pitch.Pitch(str(pitch_str)).midi
    except Exception:
        return 71


# ---- 高潮加厚 / 终止 ----

def _oct_up(name: str) -> str:
    """同名高八度（保持拼写，避免 music21 等音重拼）。"""
    p = m21pitch.Pitch(str(name))
    return f'{_sp(p.name)}{(p.octave or 4) + 1}'


def _fit_note_in_range(name: str, rlo: int, rhi: int) -> str:
    """把单个音名按整八度移进 [rlo,rhi]，保持拼写（等音不变）。

    低音/织体里的音有时是直接拼音名写出的（如 `{根音}{oct_}`、`{根音}2`），
    不经过 part_range 守门——低音声部恰好落在音域窄的乐器（定音鼓 36–60）
    上时就会写出超音域的音。音级与拼写都不变，只是换八度。
    """
    try:
        p = m21pitch.Pitch(str(name))
        m = p.midi
    except Exception:
        return str(name)
    oct0 = p.octave if p.octave is not None else 4
    if m < rlo:
        oct0 += (rlo - m + 11) // 12
    elif m > rhi:
        oct0 -= (m - rhi + 11) // 12
    return f'{_sp(p.name)}{oct0}'


def _guard_part_range(st: _State, idx: int, ev: List[List]) -> List[List]:
    """把一小节的事件列表整体兜进该声部的乐器音域（逐音八度移位）。"""
    try:
        rlo, rhi = part_range(st.parts[idx]['name'],
                              st.parts[idx].get('clef', 'treble'))
    except Exception:
        return ev
    out: List[List] = []
    for e in ev:
        if not e:
            out.append(e)
            continue
        pitch = e[0]
        if isinstance(pitch, (list, tuple)):
            new_p = [_fit_note_in_range(str(x), rlo, rhi) for x in pitch]
        elif isinstance(pitch, str) and not pitch.strip().lower().startswith('r'):
            new_p = _fit_note_in_range(pitch, rlo, rhi)
        else:
            new_p = pitch
        out.append([new_p] + list(e[1:]))
    return out


def _s_thicken(st: _State, args: Dict[str, Any]):
    sec = st.section(args.get('section'))
    _mr = _melody_range(st)   # 路由后：八度加厚需兜住承载乐器的音域
    doubled = 0
    seen = 0                  # 候选音数（区分「没有音」与被音域挡住）
    for bar in range(sec['start'], sec['end'] + 1):
        evs = st.parts[0]['bars'].get(bar)
        if not evs:
            continue
        for i, e in enumerate(evs):
            if e[0] is None or isinstance(e[0], list):
                continue
            try:
                hi = _oct_up(e[0])
                hi_m = m21pitch.Pitch(hi).midi
            except Exception:
                continue
            seen += 1
            # 旋律路由：八度加厚不得越出承载乐器音域，否则该音保持单音
            if _mr is not None and not (_mr[0] <= hi_m <= _mr[1]):
                continue
            e[0] = [str(e[0]), hi]
            if i == 0 and not e[2]:
                e[2] = 'accent'
            doubled += 1
    if not doubled and not seen:
        raise KernelError(f'thicken：段落 {sec["name"]} 高音声部无可加厚音符（先 melody）')


# ============================================================
# 叙事层（drama）内核接口
#   把 narrative.py 的"戏剧计划"落到音符上：力度弧线、演奏法、
#   动机出场（变形重写高音声部）、惊喜和弦、峰值后留白。
#   在 melody/accompaniment/fugue 之后、finalize 之前调用。
# ============================================================

_NARRATIVE_MIN_BARS = 6

#: 这些体裁默认**不**加叙事惊喜和弦 / 不做动机变形出场。
#: 爵士与赋格的功能进行（ii–V–I、模进）靠和声骨架承载，插入 bVI 借用和弦
#: 或变形动机会直接破坏进行感。原实现只判``family == 'jazz'``，而契约解析
#: 在同目录有 grammar.yaml 时返回 None（见 ``style_contract.resolve_contract``），
#: 该判断因此静默失效；现改为按 family 名判定，且 family 取不到时回落到
#: ``st.style_id`` 的第一段。
_NARRATIVE_GUARD_FAMILIES = frozenset(
    {'jazz', 'classical', 'fugue', 'baroque', 'chorale', 'madrigal'})


def _nar_bar_features(st: _State, bar: int) -> Dict[str, float]:
    """第 bar 小节的客观张力素材：密度 / 音区 / 和声不稳定 / 节奏活跃。"""
    cap = st.bar_cap(bar)
    onsets = 0
    pitches: List[int] = []
    min_dur = 4.0
    for pi in range(len(st.parts)):
        for e in (st.parts[pi]['bars'].get(bar) or []):
            if e[0] is None:
                continue
            onsets += 1
            min_dur = min(min_dur, DUR_QL.get(str(e[1]), 1.0))
            try:
                if isinstance(e[0], (list, tuple)):
                    pitches.append(_midi(str(e[0][-1])))
                else:
                    pitches.append(_midi(str(e[0])))
            except Exception:
                pass
    density = onsets / max(1.0, cap) / 2.0
    if pitches:
        mean = sum(pitches) / len(pitches)
        register = min(1.0, abs(mean - 72.0) / 24.0)
    else:
        register = 0.0
    activity = min(1.0, (1.0 / max(0.25, min_dur)) / 4.0)

    # 和声不稳定度：七和弦 / 三全音 / 调外音
    inst = 0.0
    chord = st.harmony.get(bar) or []
    if len(chord) >= 4:
        inst += 0.35
    try:
        pcs = [_pc(str(c)) for c in chord]
        for i in range(len(pcs)):
            for j in range(i + 1, len(pcs)):
                if (pcs[i] - pcs[j]) % 12 == 6:
                    inst += 0.30
                    break
        scale_pcs = {_pc(n) for n in st.scale()}
        if any(p not in scale_pcs for p in pcs):
            inst += 0.50
    except Exception:
        pass
    return {'density': min(1.0, density), 'register': register,
            'instability': min(1.0, inst), 'activity': activity}


def _nar_extract_motif(st: _State, bar: int) -> Optional[Any]:
    """从高音声部某小节抽出动机（midi + 时值）。"""
    evs = st.parts[0]['bars'].get(bar) or []
    midis: List[int] = []
    durs: List[float] = []
    for e in evs:
        if e[0] is None:
            continue
        try:
            m = (_midi(str(e[0][-1])) if isinstance(e[0], (list, tuple))
                 else _midi(str(e[0])))
        except Exception:
            continue
        midis.append(m)
        durs.append(DUR_QL.get(str(e[1]), 1.0))
    if len(midis) < 2:
        return None
    try:
        import narrative as _nv
        return _nv.Motif.from_midis(midis, durs, 'subject')
    except Exception:
        return None


def _nar_fit_durs(durs: List[float], cap: float) -> List[str]:
    """把动机时值缩放/截断到恰好填满一小节，并转成合法时值名。"""
    total = sum(durs)
    if total <= 0:
        return [QL_DUR[cap]] if cap in QL_DUR else ['quarter']
    k = cap / total
    scaled = [max(0.25, round(d * k / 0.25) * 0.25) for d in durs]
    # 逐步修正到总和 == cap（网格化带来的误差补在最后一个音上）
    guard = 0
    while abs(sum(scaled) - cap) > 1e-9 and guard < 64:
        guard += 1
        diff = round(cap - sum(scaled), 4)
        if abs(diff) < 1e-9:
            break
        if diff > 0:
            scaled[-1] = min(4.0, scaled[-1] + 0.25)
        else:
            idx = max(range(len(scaled)), key=lambda i: scaled[i])
            if scaled[idx] <= 0.25:
                break
            scaled[idx] -= 0.25
    if abs(sum(scaled) - cap) > 1e-9:
        # 兜底：等分
        n = max(1, int(round(cap / 0.5)))
        scaled = [0.5] * n
    return [QL_DUR.get(d, 'quarter') for d in scaled]


def _s_narrative(st: _State, args: Dict[str, Any]):
    """叙事脚本：给整曲套上戏剧弧线。"""
    import narrative as _nv

    if not st.sections:
        raise KernelError('narrative 需要先 set_sections')
    if st.bars < _NARRATIVE_MIN_BARS:
        st.narrative = {'skipped': f'不足 {_NARRATIVE_MIN_BARS} 小节，跳过叙事层'}
        return
    filled = all((st.parts[pi]['bars'].get(b) or [])
                 for pi in range(len(st.parts)) for b in range(1, st.bars + 1))
    if not filled:
        raise KernelError('narrative 需要所有声部所有小节已有音符'
                          '（放在 melody/accompaniment/fugue 之后）')

    intensity = float(args.get('intensity') or 1.0)
    # 叙事走自己的子种子：换叙事不必重生成旋律，反之亦然
    seed = str(args.get('seed') or st.subseeds.get('narrative') or st.seed)
    # 爵士 ii–V–I / 赋格对位：默认「惊喜和弦 + 动机变形出场」会毁掉功能进行与主题线
    # （finalize 自动补 narrative 时尤其致命）。显式参数仍优先。
    #
    # 体裁判定**不能只依赖 st.style**：契约解析在同目录存在 grammar.yaml 时会
    # 返回 None（见 style_contract.resolve_contract），而 /learn-all 现已为
    # 多数包写入了 grammar.yaml，于是 st.style is None、family 读成空串，
    # 爵士的惊喜和弦保护**静默失效**（实测 jazz/swing-standard 被塞入 bVI）。
    # 故增加 st.style_id 这个一定存在的字符串作为兜底。
    fam = ''
    try:
        fam = str(getattr(getattr(st, 'style', None), 'family', '') or '').lower()
    except Exception:
        fam = ''
    if not fam:
        # 兜底：从风格 id 的第一段推断 family（'jazz/swing-standard' → 'jazz'）
        sid = str(getattr(st, 'style_id', '') or '').strip().lower()
        sid = sid.split(':', 1)[-1]          # 去掉 'genre:' 前缀
        if sid.startswith('genre/'):
            sid = sid[len('genre/'):]
        if '/' in sid:
            fam = sid.split('/', 1)[0]
        else:
            fam = sid
    style_guard = fam in _NARRATIVE_GUARD_FAMILIES or bool(
        getattr(st, 'fugue_called', False))
    if args.get('surprises') is not None:
        n_sup = int(args.get('surprises'))
    else:
        n_sup = 0 if style_guard else 1
    if args.get('silence') is not None:
        want_silence = bool(args.get('silence'))
    else:
        want_silence = True
    # 是否允许「动机变形出场」重写高音声部。默认开（历史行为）；
    # 明确「不要赋格/复调模仿/卡农」的作品应关掉，旋律才保持作曲家写下的形态。
    if args.get('appearances') is not None:
        want_apps = bool(args.get('appearances'))
    else:
        want_apps = not style_guard

    # ---- 客观张力 ----
    feats = [_nar_bar_features(st, b) for b in range(1, st.bars + 1)]
    actual = _nv.tension_series([f['density'] for f in feats],
                                [f['register'] for f in feats],
                                [f['instability'] for f in feats],
                                [f['activity'] for f in feats])
    actual = _nv.smooth(actual, 3)

    # ---- 主导动机：优先赋格主题，其次首段高音声部 ----
    motif = None
    src_bar = 1
    if st.fugue_called:
        motif = _nar_extract_motif(st, 1)
        src_bar = 1
    if motif is None:
        for b in (1, 2, 3):
            motif = _nar_extract_motif(st, b)
            if motif is not None:
                src_bar = b
                break

    scale = [_midi(f'{n}4') for n in st.scale()]
    base_dyn = {}
    for s in st.sections:
        for b in range(s['start'], s['end'] + 1):
            if s.get('dynamic'):
                base_dyn[b] = str(s['dynamic'])

    plan = _nv.build_plan(bars=st.bars, sections=st.sections, actual=actual,
                          seed=seed, intensity=intensity, motif=motif,
                          surprises=n_sup, silence=want_silence,
                          scale=scale, base_dynamics=base_dyn,
                          appearances=want_apps)

    # ---- 1) 力度弧线 ----
    dyn: List[List] = []
    prev = None
    for b in range(1, st.bars + 1):
        v = plan['dynamics'].get(b)
        if v and v != prev:
            dyn.append([b, v])
            prev = v
    if dyn:
        st.dynamics = dyn

    # ---- 2) 演奏法 ----
    for b, mk in (plan.get('marks') or {}).items():
        if not mk:
            continue
        for pi in range(len(st.parts)):
            evs = st.parts[pi]['bars'].get(b) or []
            for i, e in enumerate(evs):
                if e[0] is None:
                    continue
                if len(e) < 3:
                    e.append(None)
                # 只在小节第一个发音上打记号，避免满谱重音
                e[2] = mk if i == 0 else e[2]

    # ---- 3) 动机出场：变形重写高音声部 ----
    applied_apps = []
    for app in (plan.get('appearances') or []):
        b = int(app['bar'])
        if b <= src_bar and app['transform'] == 'original':
            # 原型就在原位，不必重写（保留赋格/主题的原始陈述）
            applied_apps.append(dict(app, applied=False))
            continue
        if b > st.bars - 1 or b in st.cadence_bars:
            # 末小节 & 终止式小节都不许动机变形覆盖：终止是硬保证
            applied_apps.append(dict(app, applied=False))
            continue
        m = _nv.transform_motif(motif, app['transform'],
                                role=app['role'], scale=scale, degrees=4)
        cap = st.bar_cap(b)
        chord = st.harmony.get(b) or []
        try:
            start_lo, start_hi = (67, 88)
            # 旋律路由：叙事的「动机出场」会把变形动机重写进声部 0，默认音区 67..88
            # 对中低音乐器（大提琴上限 72）会写出超音域的音——同样收进可奏音域。
            _mr = _melody_range(st)
            if _mr:
                start_lo, start_hi = _fit_register_window(
                    start_lo, start_hi, _mr[0], _mr[1])
            cur = (st.parts[0]['bars'].get(b) or [{}])[0]
            base_m = 72
            # cur 是事件列表 [pitch, dur, ...]；pitch 可能是音名或和弦列表
            if isinstance(cur, (list, tuple)) and cur:
                p0 = cur[0][-1] if isinstance(cur[0], (list, tuple)) else cur[0]
                base_m = _midi(str(p0))
        except Exception:
            base_m = 72
        notes = m.notes_from(base_m)
        # 夹进音域，并吸附回「该小节调内音 ∪ 和弦音」。
        # 动机变形算子里 invert（半音倒影）与 ornament（上邻音 = +1 半音）
        # 本质是半音级运算，会产生调外音——C 大调里 G 的上邻音被写成 Ab、
        # 大三度倒影成小二度（Db/Gb）。原实现只把首音吸附到和弦音，其余照抄，
        # 于是调外音直接进谱。这里对所有音统一吸附。
        seg_scale = st.scale_at(b)
        allow_pcs = {_pc(n) for n in seg_scale} | {_pc(n) for n in chord}
        pref = list(chord) + list(seg_scale)
        fixed: List[int] = []
        for i, (mm, _d) in enumerate(notes):
            while mm < start_lo:
                mm += 12
            while mm > start_hi:
                mm -= 12
            if i == 0 and chord:
                cand = min(chord, key=lambda c: abs(_pc_round(c, mm) - mm))
                mm = _pc_round(cand, mm)
            elif allow_pcs and (mm % 12) not in allow_pcs:
                mm = _snap_pc(mm, allow_pcs)
            fixed.append(mm)
        try:
            names = [_key_name(st, mm, pref) for mm in fixed]
            durs = _nar_fit_durs(m.durs, cap)
            evs = [[nm, d, None] for nm, d in zip(names, durs)]
            st.put_bar(0, b, evs, force=True)
            applied_apps.append(dict(app, applied=True))
        except Exception:
            applied_apps.append(dict(app, applied=False))
    plan['appearances'] = applied_apps

    # ---- 4) 惊喜和弦 ----
    applied_sup = []
    for sp in (plan.get('surprises') or []):
        b = int(sp['bar'])
        if b < 1 or b > st.bars - 1 or b in st.cadence_bars:
            applied_sup.append(dict(sp, applied=False))
            continue
        try:
            new_chord = parse_roman(sp['roman'], st.scale_at(b))
            st.harmony[b] = list(new_chord)
            applied_sup.append(dict(sp, applied=True,
                                    notes=list(new_chord)))
        except Exception:
            applied_sup.append(dict(sp, applied=False))
    plan['surprises'] = applied_sup

    # ---- 5) 峰值后静默（留白）----
    applied_sil = []
    for sl in (plan.get('silences') or []):
        b = int(sl['bar'])
        beats = float(sl.get('beats') or 1.0)
        evs = st.parts[0]['bars'].get(b)
        if not evs or b >= st.bars or b in st.cadence_bars:
            applied_sil.append(dict(sl, applied=False))
            continue
        try:
            # 把高音声部开头 beats 拍换成休止（时值保持，谱面留白）
            out: List[List] = []
            rem = beats
            for e in evs:
                d = DUR_QL.get(str(e[1]), 1.0)
                if rem > 1e-9 and d <= rem + 1e-9 and len(out) < len(evs) - 1:
                    out.append([None, e[1], None])
                    rem -= d
                else:
                    out.append(list(e))
            if out and all(e[0] is None for e in out):
                out[-1] = list(evs[-1])
            st.put_bar(0, b, out, force=True)
            applied_sil.append(dict(sl, applied=True))
        except Exception:
            applied_sil.append(dict(sl, applied=False))
    plan['silences'] = applied_sil

    plan['source_motif_bar'] = src_bar
    st.narrative = plan


def _snap_pc(midi: int, pcs) -> int:
    """把 midi 吸附到最近的允许音级（pcs = 0–11 音级集合），保持就近八度。

    用于叙事层动机变形：变形算子按半音运算，结果常落在调外；
    吸附到「调内音 ∪ 和弦音」可保证变形旋律仍在调内。
    """
    best, best_d = int(midi), None
    for pc in pcs:
        base = int(pc) + 12 * ((int(midi) - int(pc)) // 12)
        for cand in (base, base + 12):
            d = abs(cand - int(midi))
            if best_d is None or d < best_d:
                best_d, best = d, cand
    return best


def _pc_round(name: str, target: int) -> int:
    """把音名移到离 target 最近的八度。"""
    try:
        pc = _pc(name)
    except Exception:
        return target
    return pc + 12 * int(round((target - pc) / 12.0))


def _cad_durs(cap: float) -> List[float]:
    """把小节容量拆成可用时值（3 拍 → [2,1]）。"""
    out, rem = [], cap
    while rem > 1e-6:
        if rem >= 4.0 - 1e-9:
            out.append(4.0); rem -= 4.0
        elif rem >= 2.0 - 1e-9:
            out.append(2.0); rem -= 2.0
        elif rem >= 1.0 - 1e-9:
            out.append(1.0); rem -= 1.0
        else:
            out.append(0.5); rem -= 0.5
    return out


def _s_cadence(st: _State, args: Dict[str, Any]):
    if not st.sections:
        raise KernelError('cadence 需要先 set_sections')
    sec = st.section(args['section']) if args.get('section') else st.sections[-1]
    n = int(args.get('bars') or 1)
    if n not in (1, 2):
        raise KernelError('cadence bars 需为 1 或 2')
    if sec['end'] != st.bars:
        raise KernelError(f'cadence 只能用于最后一个段落（{sec["name"]} 不是末段）')
    _mr = _melody_range(st)   # 路由后：终止八度加厚需兜住承载乐器的音域
    scale = st.scale_for(sec)   # 末段若已转调，终止按新调的主/属
    tonic = scale[0]
    triad = parse_roman('I', scale)          # 主三和弦（根音/三音/五音）
    dominant = parse_roman('V', scale)[0]
    names = [p['name'].lower() for p in st.parts]
    is_orch = any(_family(x) in ('woodwind', 'brass') for x in names)
    # 登记终止式小节：自动叙事层（finalize 里 cadence 之后自动补）据此避让，
    # 不再把终止的属七/主和弦长音覆盖成变形动机（曾导致末段旋律调外+终止失效）。
    st.cadence_bars.update(range(st.bars - n + 1, st.bars + 1))
    for bar in range(st.bars - n + 1, st.bars + 1):
        is_final = bar == st.bars
        root_name = tonic if is_final else dominant
        cap = st.bar_cap(bar)
        durs = _cad_durs(cap)
        # 低声部：根音（+ 五音交替），相对前一小节末低音选最近八度（避免终止硬掉到 C2）
        prev_evs = st.parts[st.bass_idx]['bars'].get(bar - 1) or []
        bass_prev_m = _last_bass_midi(prev_evs) or _midi(f'{root_name}2')
        bname = st.parts[st.bass_idx]['name']
        bclef = st.parts[st.bass_idx].get('clef', 'bass')
        try:
            blo, bhi = part_range(bname, bclef)
        except Exception:
            blo, bhi = 28, 60
        bass = []
        cur_b = bass_prev_m
        for i, d in enumerate(durs):
            nm = root_name if i % 2 == 0 else triad[2]
            pc = _pc(nm)
            # 优先五度内，否则放宽到八度
            tgt = None
            for leap in (7, 12, 19):
                wlo, whi = max(blo, cur_b - leap), min(bhi, cur_b + leap)
                if wlo <= whi:
                    t = _nearest_pc(cur_b, pc, wlo, whi)
                    if blo <= t <= bhi:
                        tgt = t
                        break
            if tgt is None:
                tgt = _nearest_pc(cur_b, pc, blo, bhi)
            bass.append([_key_name(st, tgt, triad), QL_DUR[d]])
            cur_b = tgt
        st.put_bar(st.bass_idx, bar,
                   _guard_part_range(st, st.bass_idx, bass), force=True)
        # 上方各声部：主和弦音（最高声部做八度加倍）。八度按各乐器音域中段选取，
        # 不再硬编码 C3–C4，避免高音乐器在终止处掉到低音区（如双簧管 C3）。
        for pi in range(0, st.bass_idx):
            vname = st.parts[pi]['name']
            vclef = st.parts[pi].get('clef', 'treble')
            # 打击乐在终止式只做点状强击（不做长音 pad，否则定音鼓被写成 4 拍长音）
            if pi != 0 and _family(vname) == 'percussion':
                lo, hi = part_range(vname, vclef)
                st.put_bar(pi, bar, _perc_accent(st, triad, lo, hi, cap, bar),
                            force=True)
                continue
            tone = triad[pi % len(triad)]
            lo, hi = part_range(vname, vclef)
            mid = (lo + hi) / 2.0
            best = None
            for o in range(0, 9):
                try:
                    m = _midi(f'{tone}{o}')
                except Exception:
                    continue
                if lo <= m <= hi:
                    k = abs(m - mid)
                    if best is None or k < best[0]:
                        best = (k, m)
            target_m = best[1] if best else max(lo, min(hi, _midi(f'{tone}4')))
            nm = _key_name(st, target_m, triad)
            if pi == 0:
                # 旋律路由：终止八度加厚不得越出承载乐器音域，否则保持单音
                if _mr is not None and not (_mr[0] <= target_m + 12 <= _mr[1]):
                    ev = [[nm, QL_DUR[d], None] for d in durs]
                else:
                    ev = [[[nm, _oct_up(nm)], QL_DUR[d], None] for d in durs]
            else:
                ev = [[nm, QL_DUR[d], None] for d in durs]
            st.put_bar(pi, bar, ev, force=True)
        # 管弦终止式同样做声部重排，消除内声部交叉
        if is_orch:
            _resolve_inner_crossings(st, bar)


# ---------- 汇总 & 平行五八度修正 ----------

_DYN_ORDER = ('pp', 'p', 'mp', 'mf', 'f', 'ff')


def _shift_dynamic(mark: str, delta: int) -> str:
    """力度记号在 pp–ff 阶梯上平移（钳制两端）。"""
    m = str(mark or 'mf').strip().lower()
    try:
        i = _DYN_ORDER.index(m)
    except ValueError:
        return m
    return _DYN_ORDER[max(0, min(len(_DYN_ORDER) - 1, i + int(delta)))]


def _part_dynamic_delta(st: _State, pi: int) -> int:
    """多声部主次：旋律突出，伴奏/衬托退后。

    2 声部（钢琴等）不改——左右手共用段落力度更自然。
    管弦（≥6 且含木管/铜管）：小提琴旋律抬两档；木管/打击再退后，避免「全员一样响」。
    """
    if st.voices <= 2:
        return 0
    names = [p['name'].lower() for p in st.parts]
    is_orch = (any(_family(n) in ('woodwind', 'brass') for n in names)
               and st.voices >= 6)
    if pi == 0:
        return 2 if is_orch else 1
    fam = _family(st.parts[pi]['name'])
    if pi == st.bass_idx:
        return -3 if is_orch else -2
    if not is_orch:
        return -2
    if fam == 'percussion':
        return -3
    if fam == 'woodwind':
        return -3
    if fam == 'brass':
        return -2
    if fam == 'string':
        string_idxs = [
            i for i in range(1, st.bass_idx)
            if _family(st.parts[i]['name']) == 'string'
        ]
        if string_idxs and pi == string_idxs[0]:
            return -1  # VnII 稍靠前
        return -2
    return -2


def _dynamics_for_part(st: _State, pi: int) -> List[List]:
    delta = _part_dynamic_delta(st, pi)
    return [[int(bar), _shift_dynamic(str(d), delta)] for bar, d in st.dynamics]


def _assemble(st: _State) -> Dict[str, Any]:
    first = 0 if st.pickup else 1
    for pi, p in enumerate(st.parts):
        for bar in range(first, st.bars + 1):
            if bar not in p['bars']:
                raise KernelError(
                    f'声部{pi + 1} 第 {bar} 小节没有音符——每个段落都要调用 '
                    f'melody 与 accompaniment（末段还要 cadence）')
    parts = []
    for pi, p in enumerate(st.parts):
        events: List[List] = []
        for bar in range(first, st.bars + 1):
            events.extend([list(e) for e in p['bars'][bar]])
        # program=None：导出层按乐器名查默认 GM 音色（写死 0 会把所有声部
        # 都变成大钢琴——管弦乐编制全灭的根因）
        parts.append({
            'name': p['name'], 'program': None, 'clef': p['clef'],
            'events': events,
            # 按声部角色分化力度，避免五部同强度听不出主次
            'dynamics': _dynamics_for_part(st, pi),
        })
    out = {
        'title': st.title, 'key_sharps': st.sharps, 'mode': st.mode,
        'time_signature': st.ts, 'tempo': st.tempo, 'bars': st.bars,
        'seed': st.seed, 'voices': len(st.parts), 'fugue': st.fugue_called,
        'tempo_marks': st.tempo_marks, 'meter_changes': st.meter_changes,
        'key_changes': [list(k) for k in st.key_changes],
        'parts': parts,
        # 每小节和声（音名列表），供风格验收 / 分析报告 / 音符级命中测试判定
        # 「第 N 小节是否确实是某和弦」。键=小节号，值=该小节和弦音名。
        'harmony': {int(b): list(ch) for b, ch in sorted(st.harmony.items())},
        'narrative': st.narrative,
        'subseeds': dict(st.subseeds),
        'soft_weights': {k: dict(v) for k, v in st.soft_weights.items()},
        'fugue_roles': [list(r) for r in st.fugue_roles],
        # 契约实际施加了哪些约束（可追溯）：风格验收 / 分析报告据此判定
        # 「学到的风格到底进没进谱面」，而不是只看提示词里写了什么。
        'style_notes': list(getattr(st, 'style_notes', []) or []),
    }
    if getattr(st, 'grammar', None):
        out['grammar'] = st.grammar
        out['spelling_locked'] = True
    if st.pickup:
        out['pickup_ql'] = st.pickup
    # 钢琴大谱表：2 声部（RH/LH）= 单件乐器的两行谱表（花括号组合），
    # 导出时合并成一个 MusicXML part（<staves>2</staves>），而非两个独立声部
    if len(parts) == 2 and all(p['name'].lower().startswith('piano')
                               for p in parts):
        parts[0]['staff'] = 1
        parts[1]['staff'] = 2
        out['staff_groups'] = [{'parts': [0, 1], 'symbol': 'brace', 'name': 'Piano'}]
    elif len(parts) == 3 and [p['name'] for p in parts[1:]] == ['Piano RH', 'Piano LH']:
        # 旋律 + 钢琴伴奏（语法作曲的旋律曲）：后两行合成钢琴大谱表
        parts[1]['staff'] = 1
        parts[2]['staff'] = 2
        out['staff_groups'] = [{'parts': [1, 2], 'symbol': 'brace', 'name': 'Piano'}]
    return out


def _top_pitch(ev) -> Optional[int]:
    p = ev[0]
    if p is None:
        return None
    if isinstance(p, (list, tuple)):
        try:
            return max(m21pitch.Pitch(str(x)).midi for x in p)
        except Exception:
            return None
    try:
        return m21pitch.Pitch(str(p)).midi
    except Exception:
        return None


def _bottom_pitch(ev) -> Optional[int]:
    p = ev[0]
    if p is None:
        return None
    if isinstance(p, (list, tuple)):
        try:
            return min(m21pitch.Pitch(str(x)).midi for x in p)
        except Exception:
            return None
    try:
        return m21pitch.Pitch(str(p)).midi
    except Exception:
        return None


def _fix_parallel_outer(st: _State):
    """外声部平行纯五/纯八度：旋律音换和弦音规避（确定性，尽力而为）。"""
    # prev 跨小节保留：小节线两侧的平行五八度同样要查
    prev = None  # (interval_class, top, bottom)
    for bar in range(1, st.bars + 1):
        # 拼写优先级按本小节所在段落（可能已转调）的调性取
        scale = st.scale_at(bar)
        rh = st.parts[0]['bars'].get(bar) or []
        lh = st.parts[st.bass_idx]['bars'].get(bar) or []
        chord = st.harmony.get(bar) or []
        if not rh or not lh:
            continue

        def walk(evs):
            out, t = [], 0.0
            for e in evs:
                out.append((round(t, 4), e))
                t += DUR_QL[str(e[1])]
            return out

        lmap = {}
        for t, e in walk(lh):
            if e[0] is not None:
                lmap.setdefault(t, e)
        for t, e in walk(rh):
            cands = [lt for lt in lmap if lt <= t]
            le = lmap[max(cands)] if cands else None
            top = _top_pitch(e)
            bot = _bottom_pitch(le) if le is not None else None
            if top is None or bot is None:
                continue
            iv = (top - bot) % 12
            # 赋格段没有和声标记时，用调内音做规避候选
            pool = chord or scale
            if (prev is not None and iv in (0, 7) and prev[0] == iv
                    and (top - prev[1]) * (bot - prev[2]) > 0 and pool):
                # 规避音从「和弦音（或调内音）」里挑：离原旋律音最近、
                # 且与当前低音不构成新的纯五/纯八度。只换三音会在
                # 窄旋律位置上砸出 ±8 半音的大跳，比平行五度更伤旋律。
                best_d, new_m = None, None
                for c in pool:
                    pc = _pc(c)
                    cand = pc + 12 * round((top - pc) / 12)
                    if not (55 <= cand <= 96):
                        continue
                    if (cand - bot) % 12 in (0, 7):
                        continue
                    d = abs(cand - top)
                    if best_d is None or d < best_d:
                        best_d, new_m = d, cand
                if new_m is not None:
                    # 保持原有织体结构：和弦（八度对）仍保持和弦，单音仍是单音
                    try:
                        if isinstance(e[0], list) and new_m - 12 >= 50:
                            e[0] = [_name_oct(new_m - 12, scale + pool),
                                    _name_oct(new_m, scale + pool)]
                        else:
                            e[0] = _name_oct(new_m, scale + pool)
                        top = new_m
                    except Exception:
                        pass
            prev = ((top - bot) % 12, top, bot)


_VOCAL_KEYS = ('soprano', 'alto', 'tenor', 'bass', '女高', '女低', '男高', '男低')


def _is_chorale_texture(st: _State) -> bool:
    """开谱 SATB（声部名全为人声）：四部和声口径，全部声部对禁平行五八度。"""
    if len(st.parts) < 3:
        return False
    return all(any(k in p['name'].lower() for k in _VOCAL_KEYS) for p in st.parts)


def _ev_midis(ev) -> List[int]:
    p = ev[0] if ev else None
    if p is None:
        return []
    try:
        if isinstance(p, (list, tuple)):
            return [_midi(str(x)) for x in p]
        if isinstance(p, str) and p.strip().lower() in ('r', 'rest', ''):
            return []
        return [_midi(str(p))]
    except Exception:
        return []


def _part_timeline(st: _State, pi: int) -> List[Tuple[float, float, int, List]]:
    """声部事件 → [(全曲起拍, 时值, 小节, 事件引用)]，事件引用可原地改音。"""
    out, t0 = [], 0.0
    for bar in range(1, st.bars + 1):
        t = t0
        for e in st.parts[pi]['bars'].get(bar) or []:
            d = DUR_QL.get(str(e[1]), 0.0)
            out.append((round(t, 4), d, bar, e))
            t += d
        t0 += st.bar_cap(bar)
    return out


def _sounding_at(tl, t: float) -> List[int]:
    for on, d, _bar, e in tl:
        if on <= t + 1e-9 < on + d:
            return _ev_midis(e)
    return []


def _starting_at(tl, t: float):
    for item in tl:
        if abs(item[0] - t) < 1e-6:
            return item
    return None


def _pair_moves(a0: List[int], a1: List[int]) -> List[Tuple[int, int]]:
    """就近配对（与报告检测口径一致）。"""
    remaining = list(a1)
    pairs = []
    for src in sorted(a0):
        if not remaining:
            break
        dst = min(remaining, key=lambda n: (abs(n - src), n))
        remaining.remove(dst)
        pairs.append((src, dst))
    return pairs


def _parallel_hits(u0, u1, l0, l1) -> List[Tuple[int, int]]:
    """返回构成平行纯五/纯八的 (上方原音, 上方新音) 列表。"""
    hits = []
    for a0, a1 in _pair_moves(u0, u1):
        for b0, b1 in _pair_moves(l0, l1):
            if a1 == a0 or b1 == b0 or (a1 - a0) * (b1 - b0) <= 0:
                continue
            ic0, ic1 = (a0 - b0) % 12, (a1 - b1) % 12
            if ic0 == ic1 and ic0 in (0, 7):
                hits.append((a0, a1))
    return hits


def _fix_parallel_all_pairs(st: _State, passes: int = 3):
    """SATB 开谱：任意两声部的平行纯五/纯八度 → 改内声部（无内声部时改旋律）。

    替换音取本小节和弦音（无和声标记时取调内音），离原音最近，且与所有其它
    声部都不形成新的平行五八度、不越出该乐器音域。确定性、尽力而为。
    """
    if not _is_chorale_texture(st):
        return
    n = len(st.parts)
    for _ in range(max(1, passes)):
        changed = False
        tls = [_part_timeline(st, pi) for pi in range(n)]
        onsets = sorted({0.0} | {it[0] for tl in tls for it in tl})
        for i in range(n):
            for j in range(i + 1, n):
                target = next((k for k in (j, i) if k not in (0, st.bass_idx)), 0)
                for t0, t1 in zip(onsets, onsets[1:]):
                    cur = [(_sounding_at(tls[k], t0), [m for it in tls[k]
                            if abs(it[0] - t1) < 1e-6 for m in _ev_midis(it[3])])
                           for k in range(n)]
                    (u0, u1), (l0, l1) = cur[i], cur[j]
                    if not u1 or not l1 or not _parallel_hits(u0, u1, l0, l1):
                        continue
                    item = _starting_at(tls[target], t1)
                    if item is None:
                        continue
                    _on, _d, bar, ev = item
                    scale = st.scale_at(bar)
                    pool = st.harmony.get(bar) or scale
                    name = st.parts[target]['name']
                    try:
                        lo, hi = part_range(name, st.parts[target].get('clef', 'treble'))
                    except Exception:
                        lo, hi = 36, 84
                    orig = cur[target][1]
                    if not orig:
                        continue
                    o_top = max(orig)
                    best = None
                    for c in pool:
                        pc = _pc(c)
                        for octv in (-1, 0, 1):
                            cand = pc + 12 * (round((o_top - pc) / 12) + octv)
                            if cand == o_top or not (lo <= cand <= hi):
                                continue
                            if abs(cand - o_top) > 5:
                                continue
                            delta = cand - o_top
                            new_set = [m + delta for m in orig]
                            ok = True
                            for k in range(n):
                                if k == target:
                                    continue
                                k0, k1 = cur[k]
                                if not k1:
                                    continue
                                if target < k:
                                    bad = _parallel_hits(cur[target][0], new_set, k0, k1)
                                else:
                                    bad = _parallel_hits(k0, k1, cur[target][0], new_set)
                                if bad:
                                    ok = False
                                    break
                            if not ok:
                                continue
                            # 不与相邻声部交叉
                            if target > 0 and cur[target - 1][1] and cand > max(cur[target - 1][1]):
                                continue
                            if target < n - 1 and cur[target + 1][1] and cand < min(cur[target + 1][1]):
                                continue
                            score = (abs(delta), 0 if c in (st.harmony.get(bar) or []) else 1)
                            if best is None or score < best[0]:
                                best = (score, delta)
                    if best is None:
                        continue
                    delta = best[1]
                    prefer = list(pool) + list(scale)
                    if isinstance(ev[0], (list, tuple)):
                        ev[0] = [_name_oct(_midi(str(x)) + delta, prefer) for x in ev[0]]
                    else:
                        ev[0] = _name_oct(_midi(str(ev[0])) + delta, prefer)
                    changed = True
                    tls[target] = _part_timeline(st, target)
        if not changed:
            break


def _s_write_part(st: _State, args: Dict[str, Any]):
    """按声部写入绝对音符事件（逆向复现 / 音符级精确控制）。

    notes 为整曲扁平事件列表 ``[[pitch|null|list, 时值名, mark?], …]``，
    按各小节容量切分；也可用 ``bars`` 字典 ``{小节号: events}``。
    """
    if not st.parts:
        raise KernelError('write_part 需要先 new_score')
    try:
        pi = int(args.get('part', 0))
    except (TypeError, ValueError):
        raise KernelError(f"write_part.part 需为整数，收到 {args.get('part')!r}")
    if not (0 <= pi < len(st.parts)):
        raise KernelError(f'write_part.part={pi} 超出声部范围 0–{len(st.parts) - 1}')
    bars_map = args.get('bars')
    if isinstance(bars_map, dict) and bars_map:
        for bar_str, evs in bars_map.items():
            try:
                bar = int(bar_str)
            except (TypeError, ValueError):
                raise KernelError(f'write_part.bars 键需为小节号，收到 {bar_str!r}')
            if not (1 <= bar <= st.bars):
                raise KernelError(f'write_part 小节 {bar} 超出 1–{st.bars}')
            if not isinstance(evs, list) or not evs:
                raise KernelError(f'write_part 第 {bar} 小节 events 须为非空列表')
            norm = [_normalize_write_event(e) for e in evs]
            st.put_bar(pi, bar, norm, force=True)
        return
    notes = args.get('notes')
    if not isinstance(notes, list) or not notes:
        raise KernelError('write_part 需要 notes 非空列表或 bars 字典')
    idx = 0
    for bar in range(1, st.bars + 1):
        cap = st.bar_cap(bar)
        evs: List[List] = []
        used = 0.0
        while idx < len(notes) and used < cap - 1e-9:
            e = _normalize_write_event(notes[idx])
            dq = DUR_QL[str(e[1])]
            if used + dq > cap + 1e-9:
                raise KernelError(
                    f'write_part 声部{pi + 1} 第 {bar} 小节 notes 越界/跨小节'
                    f'（已用 {used:g}，下一音 {dq:g} > 余量 {cap - used:g}）')
            evs.append(e)
            used += dq
            idx += 1
        if abs(used - cap) > 1e-6:
            raise KernelError(
                f'write_part 声部{pi + 1} 第 {bar} 小节时值 {used:g} ≠ 容量 {cap:g}')
        st.put_bar(pi, bar, evs, force=True)
    if idx < len(notes):
        raise KernelError(
            f'write_part 声部{pi + 1} notes 总时值超过全曲容量'
            f'（剩余 {len(notes) - idx} 个事件）')


def _normalize_write_event(e: Any) -> List:
    if not isinstance(e, (list, tuple)) or len(e) < 2:
        raise KernelError(f'write_part 事件须为 [pitch, dur, …]，收到 {e!r}')
    pitch, dur = e[0], e[1]
    if str(dur) not in DUR_QL:
        raise KernelError(f'write_part 非法时值 {dur!r}')
    mark = e[2] if len(e) > 2 else None
    # 允许 pitch=None（休止）、str 音名、list 和弦音名
    if pitch is not None and not isinstance(pitch, (str, list, tuple)):
        raise KernelError(f'write_part 非法 pitch {pitch!r}')
    if isinstance(pitch, tuple):
        pitch = list(pitch)
    return [pitch, str(dur), mark]


def _s_finalize(st: _State, args: Dict[str, Any]):
    if not st.sections:
        raise KernelError('finalize 前需要 new_score + set_sections')
    # preserve=True：逆向复现路径——谱面已由 write_part 写满，跳过自动
    # cadence / narrative / 平行修正，保证逐音符与源谱一致。
    # 标记 sticky：run_program 会再调一次 finalize，避免第二次空参冲掉。
    if args.get('preserve') or getattr(st, '_preserve_finalize', False):
        st._preserve_finalize = True
        return _assemble(st)
    # 幂等保护：run_program 循环里会执行一次 finalize，收尾又统一执行一次。
    # 这里通过向 st.called 写标记，保证 cadence/narrative 只被自动补一次。
    # 终止式是硬保证：翻译器忘了调 cadence 也自动补（末小节主和弦长音）
    # 赋格已写到末小节时它自己带终止，不要再覆盖对位织体
    if 'cadence' not in st.called and not st.fugue_final:
        st.called.append('cadence')
        _s_cadence(st, {})
    # 叙事层是硬保证：翻译器/骨架忘了调 narrative 也自动补（戏剧弧线）。
    # 放在 cadence 之后：先有稳定的终止，再套弧线，避免把结尾重写掉。
    if 'narrative' not in st.called:
        st.called.append('narrative')
        try:
            _s_narrative(st, {})
        except Exception:
            st.narrative = {'skipped': '自动叙事失败（忽略，不影响出谱）'}
    _fix_parallel_outer(st)
    _fix_parallel_all_pairs(st)
    return _assemble(st)


# ============================================================
# 赋格引擎（确定性）
#   主题 → 答题（真实/守调）→ 对题 → 间插段（模进）→ 中部进入
#   → 密接和应（stretto）→ 持续音 + 终止
# 全部由种子与调性推导，不经过 LLM：同一 seed 逐音符复现。
# ============================================================

# 各声部音域（自上而下：S/A/T/B；2 声部=创意曲 RH/LH）
_FUGUE_RANGES = {
    2: [(57, 77), (45, 64)],
    3: [(64, 79), (55, 72), (43, 62)],
    4: [(67, 81), (60, 74), (53, 67), (40, 58)],
}
# 呈示部进入顺序（巴赫式：先中声部，再最高，最后低声部）
_FUGUE_ENTRY_ORDER = {2: [0, 1], 3: [1, 0, 2], 4: [2, 0, 1, 3]}
# 协和音程（半音 mod 12）：三/五/六/八度及其转位
_CONSONANT_IV = (0, 3, 4, 5, 7, 8, 9)

# 主题原型（scale 索引 0=主音；时值单位=四分音符），按时长分档。
# 忌纯音阶上下行：间插段会模进主题头，音阶主题会把整曲听成练习曲。
# 每条至少含一个 ≥4 度跳进（音级差 ≥3），形成可辨动机头。
_SUBJECT_TEMPLATES = {
    8.0: [
        # 主→属跳进后级进回落（巴赫式答题友好）
        ([0, 4, 3, 2, 1, 2, 3, 1, 0],
         [1.0, .5, .5, 1.0, 1.0, .5, .5, 1.0, 2.0]),
        # 属音反复后下行，末尾主音长时值
        ([4, 4, 3, 2, 1, 0, 1, 2, 0],
         [.5, .5, 1.0, 1.0, 1.0, .5, .5, 1.0, 2.0]),
        # 主–下属跳进 + 邻音迂回
        ([0, 3, 2, 4, 3, 1, 2, 1, 0],
         [1.0, .5, .5, 1.0, .5, .5, 1.0, 1.0, 2.0]),
        # 上行四度动机头，再折回
        ([0, 3, 4, 2, 1, 3, 2, 1, 0],
         [.5, .5, 1.0, 1.0, .5, .5, 1.0, 1.0, 2.0]),
    ],
    6.0: [
        ([0, 4, 3, 2, 1, 2, 0], [1.0, .5, .5, 1.0, 1.0, 1.0, 1.0]),
        ([4, 3, 2, 0, 1, 2, 0], [.5, .5, 1.0, 1.0, 1.0, 1.0, 1.0]),
        ([0, 3, 4, 2, 1, 2, 0], [1.0, .5, .5, 1.0, 1.0, 1.0, 1.0]),
        ([2, 0, 4, 3, 1, 2, 0], [.5, .5, 1.0, 1.0, 1.0, 1.0, 1.0]),
    ],
    4.0: [
        ([0, 4, 3, 2, 1, 0], [1.0, .5, .5, 1.0, .5, .5]),
        ([4, 3, 2, 0, 1, 0], [.5, .5, 1.0, 1.0, .5, .5]),
        ([0, 3, 4, 2, 1, 0], [.5, .5, 1.0, .5, .5, 1.0]),
        ([0, 4, 2, 3, 1, 0], [1.0, .5, .5, .5, .5, 1.0]),
    ],
    3.0: [
        ([0, 4, 3, 2, 1], [1.0, .5, .5, .5, .5]),
        ([4, 3, 0, 1, 0], [.5, .5, 1.0, .5, .5]),
        ([0, 3, 4, 2, 0], [.5, .5, 1.0, .5, .5]),
    ],
    2.0: [
        ([0, 4, 3, 2], [.5, .5, .5, .5]),
        ([4, 0, 2, 1], [.5, .5, .5, .5]),
        ([0, 3, 4, 2], [.5, .5, .5, .5]),
    ],
}


def _fugue_split_dur(d: float) -> List[float]:
    """把任意时值拆成内核可用时值（4/2/1/0.5/0.25）之和。"""
    out, rem = [], round(float(d), 6)
    for unit in (4.0, 2.0, 1.0, 0.5, 0.25):
        while rem >= unit - 1e-9:
            out.append(unit)
            rem -= unit
    return out or [0.25]


def _fugue_deg_midi(scale: List[str], deg: int, octv: int) -> int:
    return _midi(f'{scale[int(deg) % 7]}{octv + int(deg) // 7}')


def _fugue_fit_oct(scale: List[str], degs: List[int], lo: int, hi: int) -> int:
    """为整条旋律挑一个八度，使所有音尽量落在该声部音域内。"""
    best, best_over = 3, None
    for octv in range(1, 8):
        over = 0
        for d in degs:
            m = _fugue_deg_midi(scale, d, octv)
            over += max(0, lo - m) + max(0, m - hi)
        if best_over is None or over < best_over:
            best, best_over = octv, over
        if over == 0:
            return octv
    return best


def _nearest_scale_tone(scale_pcs: List[int], target: int, lo: int, hi: int) -> int:
    best, best_d = None, None
    for octv in range(0, 9):
        for pc in scale_pcs:
            c = pc + 12 * octv
            if not (lo <= c <= hi):
                continue
            d = abs(c - target)
            if best_d is None or d < best_d:
                best, best_d = c, d
    return best if best is not None else min(max(target, lo), hi)


def _scale_tone_step(scale_pcs: List[int], m: int, direction: int,
                     lo: int, hi: int) -> Optional[int]:
    """从 m 沿 direction 走一个调内音级（窗口内最近的那一侧音）。"""
    best, best_d = None, None
    for octv in range(0, 9):
        for pc in scale_pcs:
            c = pc + 12 * octv
            if not (lo <= c <= hi):
                continue
            if direction > 0 and c <= m:
                continue
            if direction < 0 and c >= m:
                continue
            d = abs(c - m)
            if best_d is None or d < best_d:
                best, best_d = c, d
    return best


def _fugue_statement(scale: List[str], degs: List[int], durs: List[float],
                     shift: int, lo: int, hi: int, t0: float,
                     is_subject: bool = True,
                     tonal_adjust: Optional[int] = None) -> List[tuple]:
    """一次主题/答题陈述 → [(t, dur, midi, is_subject), …]。"""
    degs2 = [d + shift for d in degs]
    if tonal_adjust is not None and len(degs2) > 1:
        degs2[1] = tonal_adjust
    octv = _fugue_fit_oct(scale, degs2, lo, hi)
    out, t = [], float(t0)
    for d, dur in zip(degs2, durs):
        m = _fugue_deg_midi(scale, d, octv)
        while m < lo:
            m += 12
        while m > hi:
            m -= 12
        out.append((round(t, 6), float(dur), m, is_subject))
        t += dur
    return out


def _fugue_counterpoint(scale_pcs: List[int], other: List[tuple],
                        lo: int, hi: int, above: bool = True) -> List[tuple]:
    """【已过期】对题：与另一声部反向级进；强拍协和、规避平行五八度。

    无调用方——赋格引擎的对题/间插已改由 ``_fugue_sequence``（主题头模进）生成。
    保留仅供参考，后续版本删除。
    """
    import warnings
    warnings.warn('_fugue_counterpoint 已过期，改用 _fugue_sequence',
                  DeprecationWarning, stacklevel=2)
    out: List[tuple] = []
    prev_cp: Optional[int] = None
    prev_ot: Optional[int] = None
    for i, (t, dur, om, _f) in enumerate(other):
        on_beat = abs(t - round(t)) < 1e-9
        if i == 0:
            cand = _nearest_scale_tone(scale_pcs,
                                       om + (7 if above else -7), lo, hi)
        else:
            if om > (prev_ot or om):
                dirn = -1
            elif om < (prev_ot or om):
                dirn = 1
            else:
                dirn = -1 if (prev_cp or 0) > om else 1
            nxt = _scale_tone_step(scale_pcs, prev_cp if prev_cp is not None else om,
                                   dirn, lo, hi)
            cand = nxt if nxt is not None else (prev_cp if prev_cp is not None else om)
            # 平行五/八度：反向走一级避开
            if prev_cp is not None and prev_ot is not None:
                iv0 = (prev_cp - prev_ot) % 12
                if iv0 in (0, 7) and (cand - om) % 12 == iv0:
                    alt = _scale_tone_step(scale_pcs, cand, -dirn, lo, hi)
                    if alt is not None:
                        cand = alt
        # 强拍不协和 → 就近调整（最多 4 次）
        tries = 0
        while on_beat and ((cand - om) % 12) not in _CONSONANT_IV and tries < 4:
            alt = _scale_tone_step(scale_pcs, cand,
                                   -1 if tries % 2 == 0 else 1, lo, hi)
            if alt is None:
                break
            cand = alt
            tries += 1
        out.append((round(t, 6), float(dur), cand, False))
        prev_cp, prev_ot = cand, om
    return out


def _fugue_sequence(scale: List[str], scale_pcs: List[int], degs: List[int],
                    durs: List[float], n_bars: int, cap: float,
                    shift_start: int, step: int, lo: int, hi: int,
                    t0: float = 0.0) -> List[tuple]:
    """间插段：主题头按级进模进（逐小节移位），用于连接与转调暗示。"""
    out: List[tuple] = []
    for b in range(max(0, n_bars)):
        shift = shift_start + step * b
        degs2 = [d + shift for d in degs]
        total = sum(durs) or 1.0
        d2 = [max(0.25, round(d * cap / total * 2) / 2) for d in durs]
        diff = round(cap - sum(d2), 6)
        if abs(diff) > 1e-9:
            d2[-1] = round(d2[-1] + diff, 6)
            if d2[-1] <= 0:
                d2[-1] = 0.5
        octv = _fugue_fit_oct(scale, degs2, lo, hi)
        t = t0 + b * cap
        for d, dur in zip(degs2, d2):
            m = _fugue_deg_midi(scale, d, octv)
            while m < lo:
                m += 12
            while m > hi:
                m -= 12
            out.append((round(t, 6), float(dur), m, False))
            t += dur
    return out


def _fugue_head_fit(degs: List[int], durs: List[float],
                    max_beats: float) -> Tuple[List[int], List[float]]:
    """主题头裁剪到不超过 max_beats（避免溢出小节/段落）。"""
    out_d, out_u, t = [], [], 0.0
    for d, u in zip(degs, durs):
        if t + u > max_beats + 1e-9:
            break
        out_d.append(d)
        out_u.append(u)
        t += u
        if abs(t - max_beats) < 1e-9:
            break
    if not out_d:
        return [degs[0]], [min(durs[0], max_beats)]
    return out_d, out_u


def _fugue_smooth(lines: List[List[tuple]], scale_pcs: List[int],
                  ranges: List[Tuple[int, int]]):
    """拍点上的刺耳音程（小二/大二/三全音/七度）→ 挪动非主题声部到协和音。"""
    times = sorted({round(t, 6) for line in lines for (t, _d, _m, _f) in line})
    for t in times:
        if abs(t - round(t)) > 1e-9:      # 只在拍点上做协和检查
            continue
        sounding = []
        for vi, line in enumerate(lines):
            for (nt, nd, nm, flag) in line:
                if nt <= t < nt + nd - 1e-9:
                    sounding.append((vi, nm, flag))
                    break
        for i in range(len(sounding)):
            for j in range(i + 1, len(sounding)):
                vi, mi, fi = sounding[i]
                vj, mj, fj = sounding[j]
                if (mi - mj) % 12 in (1, 2, 6, 10, 11):
                    # 优先挪非主题声部；都是主题/都不是则挪下方声部
                    tgt = vj if (not fj and fi) else (vi if (not fi and fj) else vj)
                    lo, hi = ranges[tgt]
                    m_old = mj if tgt == vj else mi
                    other = mi if tgt == vj else mj
                    cand, tries = m_old, 0
                    while ((cand - other) % 12) not in _CONSONANT_IV and tries < 4:
                        alt = _scale_tone_step(scale_pcs, cand,
                                               -1 if tries % 2 == 0 else 1, lo, hi)
                        if alt is None:
                            break
                        cand = alt
                        tries += 1
                    if cand != m_old:
                        for k, (nt, nd, nm, flag) in enumerate(lines[tgt]):
                            if nt <= t < nt + nd - 1e-9:
                                lines[tgt][k] = (nt, nd, cand, flag)
                                sounding[j if tgt == vj else i] = \
                                    (tgt, cand, fj if tgt == vj else fi)
                                break


def _fugue_layout(n_bars: int, voices: int, L: int,
                  episodes: bool, stretto: bool) -> List[Tuple[str, int]]:
    """段落分配：呈示 / 间插 / 中部 / 密接 / 终止，自动适配总小节数。

    硬性约束：主题进入类段落（exposition / middle）长度必须是 L 的倍数，
    否则一次陈述会溢出到下一个段落。
    """
    segs: List[List] = [['exposition', L * voices]]
    if episodes:
        segs += [['episode1', L], ['middle', L * max(1, voices - 1)],
                 ['episode2', L]]
    if stretto:
        segs.append(['stretto', max(2, voices)])
    segs.append(['final', 2])

    def _total():
        return sum(b for _k, b in segs)

    # 1) 缩减：先砍间插 → 密接 → 中部 → 呈示进入数 → 终止
    guard = 0
    while _total() > n_bars and guard < 300:
        guard += 1
        done = False
        for kind in ('episode2', 'episode1'):
            for i, (k, _b) in enumerate(segs):
                if k == kind:
                    segs.pop(i)
                    done = True
                    break
            if done:
                break
        if done:
            continue
        for kind, floor in (('stretto', 2), ('middle', L), ('final', 1)):
            for i, (k, b) in enumerate(segs):
                if k == kind and b > floor:
                    segs[i][1] = b - 1
                    done = True
                    break
            if done:
                break
        if done:
            continue
        for i, (k, b) in enumerate(segs):
            if k == 'exposition' and b > 2 * L:
                segs[i][1] = b - L
                done = True
                break
        if not done:
            break
    # 2) 仍超长：删段（保留呈示与终止）
    while _total() > n_bars and len(segs) > 2:
        segs.pop(len(segs) - 2)
    # 3) 补足到 n_bars
    guard = 0
    while _total() < n_bars and guard < 300:
        guard += 1
        for kind in ('episode2', 'episode1', 'stretto', 'final', 'exposition'):
            hit = [i for i, (k, _b) in enumerate(segs) if k == kind]
            if hit:
                segs[hit[-1]][1] += 1
                break
        else:
            segs.append(['final', 1])
    return [(k, b) for k, b in segs if b > 0]


def _s_fugue(st: _State, args: Dict[str, Any]):
    """赋格（确定性）：把目标小节区间写成完整的赋格织体，覆盖所有声部。"""
    if not st.sections:
        raise KernelError('fugue 需要先 set_sections')
    sec = st.section(args['section']) if args.get('section') else None
    bar_from = sec['start'] if sec else 1
    bar_to = sec['end'] if sec else st.bars
    n_bars = bar_to - bar_from + 1
    if n_bars < 4:
        raise KernelError(f'fugue 需要至少 4 小节，当前区间只有 {n_bars} 小节')
    voices = int(args.get('voices') or st.voices)
    if voices != st.voices:
        raise KernelError(f'fugue voices={voices} 与 new_score voices={st.voices} '
                          '不一致（声部数在 new_score 里定）')
    cap = st.bar_cap(bar_from)
    for b in range(bar_from, bar_to + 1):
        if abs(st.bar_cap(b) - cap) > 1e-9:
            raise KernelError('fugue 区间内拍号必须一致（不要跨换拍段落）')

    if voices not in _FUGUE_RANGES:
        raise KernelError(f'fugue 只支持 2–4 声部（当前 new_score voices={voices}）；'
                          '大编制请先写赋格缩谱，或不要调用 fugue')
    scale = st.scale_for(sec)
    sec_mode = str(sec.get('mode', st.mode)) if sec else st.mode
    scale_pcs = [_pc(n) for n in scale]
    ranges = _FUGUE_RANGES[voices]
    rng = st.rng_for('fugue', str(bar_from), str(voices))

    # 赋格主题的软权重：复用风格包的节奏属性权重（rhythmic_identity/mood），
    # 按主题时值序列的属性展开。历史上取 'subject' 键永远为空（该键从不产出），
    # 等于未接线；现在让风格真正影响主题模板的选择。
    def _subj_weights(tab):
        rw = st.soft_weights.get('rhythm') or {}
        out: Dict[str, float] = {}
        for t_degs, t_durs in tab:
            w = 1.0
            for prop, ok in _rhythm_props(t_durs).items():
                if ok and prop in rw:
                    w *= max(0.0, float(rw[prop]))
            # 轮廓：至少一处跳进才像赋格主题；纯级进/音阶头权重压低
            gaps = [abs(a - b) for a, b in zip(t_degs, t_degs[1:])]
            leaps = sum(1 for g in gaps if g >= 3)
            if leaps == 0 and len(t_degs) >= 4:
                w *= 0.12
            elif leaps >= 1:
                w *= 1.35
            out[f'{list(t_degs)}|{list(t_durs)}'] = w
        return out or None

    # ---- 主题 ----
    L = int(args.get('entry_bars') or (2 if n_bars >= 4 * voices else 1))
    if L not in (1, 2):
        raise KernelError('entry_bars 需为 1 或 2')
    beats = cap * L
    subj_arg = args.get('subject')
    if isinstance(subj_arg, list) and subj_arg:
        try:
            degs = [int(p[0]) - 1 for p in subj_arg]
            durs = [float(DUR_QL[str(p[1])]) for p in subj_arg]
        except Exception as e:
            raise KernelError(f'subject 需为 [[级数1-14, 时值名], …]：{e}')
        if any(not (0 <= d <= 13) for d in degs) or any(d <= 0 for d in durs):
            raise KernelError('subject 级数需在 1–14（8–14=高八度）、时值名需合法')
        total = sum(durs)
        if abs(total - beats) > 1e-6:      # 按时值比例归一到进入长度
            f = beats / total
            durs = [max(0.25, round(d * f * 4) / 4) for d in durs]
            durs[-1] = round(beats - sum(durs[:-1]), 6)
            if durs[-1] <= 0:
                raise KernelError('subject 时值无法归一，请检查时值名')
    else:
        table = _SUBJECT_TEMPLATES.get(round(beats, 6))
        if not table:
            key = min(_SUBJECT_TEMPLATES, key=lambda k: abs(k - beats))
            table = _SUBJECT_TEMPLATES[key]
            degs, durs = list(_weighted_choice(rng, table, _subj_weights(table),
                                               keyfn=lambda t: f'{list(t[0])}|{list(t[1])}'))
            f = beats / sum(durs)
            durs = [max(0.25, round(d * f * 4) / 4) for d in durs]
            durs[-1] = round(beats - sum(durs[:-1]), 6)
        else:
            degs, durs = list(_weighted_choice(
                rng, table, _subj_weights(table),
                keyfn=lambda t: f'{list(t[0])}|{list(t[1])}'))

    answer = str(args.get('answer') or 'tonal').lower()
    if answer not in ('real', 'tonal'):
        raise KernelError("answer 需为 'real' 或 'tonal'")
    episodes = bool(args.get('episodes', True))
    stretto_on = bool(args.get('stretto', True))
    pedal_on = bool(args.get('pedal', True))

    # 守调答题：主题开头若是主→属的五度跳进，答题改为属→主（四度下行）。
    # 答题第二音回主音级（= 主题首音的音级，通常为 0）；
    # 注意 tonal_adjust 是绝对音级（_fugue_statement 里不做 shift 叠加）
    tonal_adj = None
    if answer == 'tonal' and len(degs) > 1 and abs(degs[1] - degs[0]) == 4:
        tonal_adj = degs[0]

    head_n = max(3, min(len(degs), len(degs) // 2 + 2))
    head_degs, head_durs = _fugue_head_fit(degs[:head_n], durs[:head_n], cap)

    lines: List[List[tuple]] = [[] for _ in range(voices)]
    order = _FUGUE_ENTRY_ORDER[voices]
    segs = _fugue_layout(n_bars, voices, L, episodes, stretto_on)
    cursor = 0.0

    # ---- 角色分档：每小节每声部在赋格结构里"扮演什么" ----
    # 主题 / 答题 / 对题 / 间插 / 中部主题 / 密接和应 / 属持续音 / 终止
    roles: Dict[int, Dict[int, str]] = {}

    def _mark(off_bars: int, n: int, voice: int, role: str,
              overwrite: bool = False):
        b0 = bar_from + int(round(cursor / cap)) + off_bars
        for k in range(max(0, n)):
            b = b0 + k
            if b > bar_to:
                break
            roles.setdefault(b, {})
            if overwrite or voice not in roles[b]:
                roles[b][voice] = role

    for kind, nb in segs:
        if kind == 'exposition':
            n_ent = nb // L
            if n_ent == 0:      # 段太短：退化成主题头模进
                lo, hi = ranges[order[0]]
                lines[order[0]].extend(_fugue_sequence(
                    scale, scale_pcs, head_degs, head_durs, nb, cap, 0, -1,
                    lo, hi, cursor))
            for i in range(n_ent):
                v = order[i % len(order)]
                lo, hi = ranges[v]
                shift = 0 if i % 2 == 0 else 4
                t0 = cursor + i * L * cap
                entry = _fugue_statement(scale, degs, durs, shift, lo, hi, t0,
                                         True, tonal_adj if shift else None)
                lines[v].extend(entry)
                _mark(i * L, L, v, 'subject' if shift == 0 else 'answer',
                      overwrite=True)
                # 已进入的声部在其后继续对题/模进，直到呈示段结束
                tail_bars = n_ent * L - (i + 1) * L
                if tail_bars > 0:
                    _mark((i + 1) * L, tail_bars, v, 'countersubject')
                    t1 = t0 + L * cap
                    fill = _fugue_sequence(scale, scale_pcs, head_degs, head_durs,
                                           tail_bars, cap, 0, -1, lo, hi, t1)
                    lines[v].extend(fill)
            # 未进入的声部休止（保持静默）
        elif kind in ('episode1', 'episode2'):
            step = -1 if kind == 'episode1' else 1
            va = 0
            vb = 1 if voices > 1 else 0
            lines[va].extend(_fugue_sequence(
                scale, scale_pcs, head_degs, head_durs, nb, cap, 0, step,
                *ranges[va], cursor))
            _mark(0, nb, va, 'episode')
            if vb != va:
                rest = max(0, nb - 1)
                if rest:
                    _mark(1, rest, vb, 'episode')
                    lines[vb].extend(_fugue_sequence(
                        scale, scale_pcs, head_degs, head_durs, rest, cap,
                        0, step, *ranges[vb], cursor + cap))
        elif kind == 'middle':
            n_ent = nb // L
            rel = 5 if sec_mode == 'major' else 2     # 关系调移位
            if n_ent == 0:      # 段太短：退化成主题头模进
                lo, hi = ranges[order[1 % len(order)]]
                lines[order[1 % len(order)]].extend(_fugue_sequence(
                    scale, scale_pcs, head_degs, head_durs, nb, cap, 0, 1,
                    lo, hi, cursor))
            for i in range(n_ent):
                v = order[(i + 1) % len(order)]
                lo, hi = ranges[v]
                shift = (4 if i % 2 == 0 else rel)
                t0 = cursor + i * L * cap
                lines[v].extend(_fugue_statement(
                    scale, degs, durs, shift, lo, hi, t0, True))
                _mark(i * L, L, v,
                      'middle-subject' if shift == 0 else 'middle-answer',
                      overwrite=True)
                tail_bars = n_ent * L - (i + 1) * L
                if tail_bars > 0:
                    lines[v].extend(_fugue_sequence(
                        scale, scale_pcs, head_degs, head_durs, tail_bars, cap,
                        0, -1, lo, hi, t0 + L * cap))
                    _mark((i + 1) * L, tail_bars, v, 'countersubject')
        elif kind == 'stretto':
            # 密接和应：主题头（≤1 小节）每隔 1 小节依次进入
            for v in order:
                start_bar = v
                if start_bar >= nb:
                    continue
                lo, hi = ranges[v]
                shift = 0 if v % 2 == 0 else 4
                t0 = cursor + start_bar * cap
                lines[v].extend(_fugue_statement(
                    scale, head_degs, head_durs, shift, lo, hi, t0, True))
                _mark(start_bar, 1, v, 'stretto', overwrite=True)
                rest = nb - start_bar - 1
                if rest > 0:
                    lines[v].extend(_fugue_sequence(
                        scale, scale_pcs, head_degs, head_durs, rest, cap,
                        0, -1, lo, hi, t0 + cap))
                    _mark(start_bar + 1, rest, v, 'episode')
        elif kind == 'final':
            # 末段：属持续音（末小节回主）+ 上方声部主题头/和弦音收束
            bass_v = voices - 1
            lo_b, hi_b = ranges[bass_v]
            dom_m = _nearest_scale_tone(scale_pcs, _fugue_deg_midi(scale, 4, 3),
                                        lo_b, hi_b)
            ton_m = _nearest_scale_tone(scale_pcs, _fugue_deg_midi(scale, 0, 3),
                                        lo_b, hi_b)
            for b in range(nb):
                is_last = (b == nb - 1)
                tb = cursor + b * cap
                if pedal_on:
                    # 属持续音：低音整小节悬在属音上，末小节回主音收束
                    lines[bass_v].append((round(tb, 6), cap,
                                          ton_m if is_last else dom_m, False))
                else:
                    # 无持续音：低音在属/主之间交替走动，末小节回主音
                    alt = ton_m if b % 2 == 1 else dom_m
                    lines[bass_v].append((round(tb, 6), cap,
                                          ton_m if is_last else alt, False))
            _mark(0, nb, bass_v, 'pedal' if pedal_on else 'bass-line')
            for v in range(voices - 1):
                lo, hi = ranges[v]
                for b in range(nb):
                    is_last = (b == nb - 1)
                    tb = cursor + b * cap
                    _mark(b, 1, v,
                          'cadence' if is_last else
                          ('subject-head' if b == 0 else 'episode'))
                    if is_last:
                        idx = (0, 2, 4)[v % 3]
                        m = _nearest_scale_tone(
                            scale_pcs, _fugue_deg_midi(scale, idx, 4), lo, hi)
                        lines[v].append((round(tb, 6), cap, m, True))
                    elif b == 0:
                        # 主题头进入（时值缩放到本小节容量，避免溢出）
                        lines[v].extend(_fugue_sequence(
                            scale, scale_pcs, head_degs, head_durs, 1, cap,
                            0, -1, lo, hi, tb))
                    else:
                        lines[v].extend(_fugue_sequence(
                            scale, scale_pcs, head_degs, head_durs, 1, cap,
                            0, -1, lo, hi, tb))
        cursor += nb * cap

    _fugue_smooth(lines, scale_pcs, ranges)

    # ---- 写入各声部（按小节切分，跨小节长音拆成可用时值） ----
    for vi in range(voices):
        line = sorted(lines[vi], key=lambda x: x[0])
        for b in range(n_bars):
            bs, be = b * cap, (b + 1) * cap
            ev: List[List] = []
            t = bs
            for (nt, nd, nm, _flag) in line:
                if nt >= be - 1e-9:
                    break
                e = min(nt + nd, be)
                s = max(nt, bs)
                if e <= s + 1e-9:
                    continue
                if s > t + 1e-9:
                    for d in _fugue_split_dur(s - t):
                        ev.append([None, QL_DUR[d], None])
                    t = s
                name = _key_name(st, nm, scale)
                for d in _fugue_split_dur(e - t):
                    ev.append([name, QL_DUR[d], None])
                    t += d
            if t < be - 1e-9:
                for d in _fugue_split_dur(be - t):
                    ev.append([None, QL_DUR[d], None])
            st.put_bar(vi, bar_from + b, ev, force=True)

    # 角色表收进 spec（供分析/评估/教学展示：谁在哪小节唱主题）
    if roles:
        flat = st.fugue_roles
        for b in sorted(roles):
            for v in sorted(roles[b]):
                flat.append([b, v, roles[b][v]])
    st.fugue_called = True
    if bar_to >= st.bars:
        st.fugue_final = True


def _s_chorale_grammar(st: _State, args: Dict[str, Any]):
    """学到的众赞歌语法（确定性）：用 grammar.yaml 一次写满 SATB 四个声部。"""
    if len(st.parts) != 4:
        raise KernelError('chorale_grammar 需要 new_score voices=4（SATB 四部）')
    if st.ts != '4/4' or st.meter_changes:
        raise KernelError('chorale_grammar 目前只支持全曲 4/4')
    from score_learning.grammar import script as gscript
    from score_learning.grammar.compose import compose as grammar_compose
    path = str(args.get('grammar') or gscript.DEFAULT_YAML)
    try:
        sc = gscript.load(path)
    except FileNotFoundError:
        raise KernelError(f'找不到规则脚本 {path}，先运行 /learn-grammar')
    if st.mode not in sc.get('vocab', {}):
        raise KernelError(f'规则脚本里没有 {st.mode} 调式的语法')
    bf = args.get('barform')
    layout = str(args.get('layout') or 'auto').lower()
    if layout not in ('auto', 'pickup', 'downbeat'):
        raise KernelError(f'chorale_grammar：layout 只能是 auto / pickup / downbeat，收到 {layout!r}')
    try:
        res = grammar_compose(
            sc, st.sharps, st.mode, seed=str(args.get('seed') or st.rng_for('harmony').random()),
            barform=None if bf is None else bool(bf),
            temperature=float(args.get('temperature', 1.0)), total_bars=st.bars,
            title=st.title, tempo=st.tempo, layout=layout)
    except (RuntimeError, ValueError) as e:
        raise KernelError(f'chorale_grammar：{e}')
    st.pickup = float(res['spec'].get('pickup_ql') or 0.0)
    for vi, part in enumerate(res['spec']['parts']):
        evs, idx = part['events'], 0
        for bar in range(0 if st.pickup else 1, st.bars + 1):
            cap, used, cur = st.bar_cap(bar), 0.0, []
            while idx < len(evs) and used < cap - 1e-9:
                cur.append([evs[idx][0], evs[idx][1], evs[idx][2] if len(evs[idx]) > 2 else None])
                used += DUR_QL[str(evs[idx][1])]
                idx += 1
            st.put_bar(vi, bar, cur, force=True)
        st.parts[vi]['clef'] = part['clef']
    for bar, names in res['spec']['harmony'].items():
        st.harmony[int(bar)] = list(names)
    st.grammar = {
        'grammar': sc.get('grammar'), 'seed': res['seed_used'],
        'layout': res['plan']['layout'],
        'phrases': res['phrases'],
        'beats': [{'bar': b['bar'], 'beat': b['beat'], 'chord': b['roman'], 'rule': b['rule']}
                  for b in res['beats']],
    }
    lay_say = '弱起（一拍弱起小节，句终落第 3 拍）' if st.pickup else '强拍起'
    st.style_notes.append(f"grammar:{sc.get('grammar')} {lay_say}，乐句 {len(res['phrases'])} 句，"
                          f"硬规则违例 0")
    st._preserve_finalize = True


def _s_leadsheet_grammar(st: _State, args: Dict[str, Any]):
    """学到的旋律 + 和弦语法（民间舞曲 / 爵士 / 流行 / 牧歌 / 古典奏鸣曲 / 弦乐四重奏 /
    海顿交响曲 / 肖邦，确定性）：按织体一次写满全部声部——旋律 + 钢琴左右手（3 声部）、
    钢琴独奏（2 声部）、弦乐四重奏（4 声部）或古典管弦乐（9 声部）。"""
    from score_learning.leadsheet import script as lscript
    from score_learning.leadsheet.analyze import STYLES as LS_STYLES
    from score_learning.leadsheet.compose import INSTRUMENTS, compose as ls_compose
    style = str(args.get('style') or '').strip()
    if style not in LS_STYLES:
        raise KernelError(f'leadsheet_grammar：style 只能是 {" / ".join(LS_STYLES)}，收到 {style!r}')
    texture = LS_STYLES[style].get('texture', 'melody_piano')
    need = {'melody_piano': (3, '旋律 + Piano RH + Piano LH'), 'piano': (2, 'Piano RH + Piano LH'),
            'quartet': (4, 'Violin I + Violin II + Viola + Violoncello'),
            'orchestra': (9, 'Flute + Oboe + Horn + Bassoon + Timpani + Violin I + Violin II + '
                             'Viola + Violoncello')}[texture]
    if len(st.parts) != need[0]:
        raise KernelError(f'leadsheet_grammar：{style} 需要 new_score voices={need[0]}（{need[1]}）')
    if st.meter_changes:
        raise KernelError('leadsheet_grammar 不支持曲中换拍号')
    if st.bars % 8:
        raise KernelError(f'leadsheet_grammar：小节数须为 8 的倍数（8 小节一段），收到 {st.bars}')
    try:
        sc = lscript.load(style)
    except FileNotFoundError:
        raise KernelError(f'找不到 {style} 的规则脚本，先运行 /learn-grammar {style}')
    sub = str(args.get('sub') or next(iter(sc['subs'])))
    if sub not in sc['subs']:
        raise KernelError(f'leadsheet_grammar：{style} 没有子风格 {sub}'
                          f'（可用：{"、".join(sc["subs"])}）')
    if st.ts != sc['subs'][sub]['meter']:
        raise KernelError(f'leadsheet_grammar：{sub} 学到的拍号是 {sc["subs"][sub]["meter"]}，'
                          f'new_score 却是 {st.ts}')
    layout = str(args.get('layout') or 'auto').lower()
    if layout not in ('auto', 'pickup', 'downbeat'):
        raise KernelError(f'leadsheet_grammar：layout 只能是 auto / pickup / downbeat，收到 {layout!r}')
    inst = st.parts[0]['name'].lower() if texture == 'melody_piano' else ''
    try:
        res = ls_compose(
            sc, sub, st.mode, st.sharps, st.bars,
            seed=str(args.get('seed') or st.rng_for('melody').random()),
            temperature=float(args.get('temperature', 1.0)), layout=layout,
            title=st.title, tempo=st.tempo,
            instrument=inst if inst in INSTRUMENTS else None)
    except (RuntimeError, ValueError) as e:
        raise KernelError(f'leadsheet_grammar：{e}')
    spec = res['spec']
    st.pickup = float(spec.get('pickup_ql') or 0.0)
    for vi, part in enumerate(spec['parts']):
        evs, idx = part['events'], 0
        for bar in range(0 if st.pickup else 1, st.bars + 1):
            cap, used, cur = st.bar_cap(bar), 0.0, []
            while idx < len(evs) and used < cap - 1e-9:
                cur.append([evs[idx][0], evs[idx][1], evs[idx][2] if len(evs[idx]) > 2 else None])
                used += DUR_QL[str(evs[idx][1])]
                idx += 1
            st.put_bar(vi, bar, cur, force=True)
        st.parts[vi]['clef'] = part['clef']
        st.parts[vi]['name'] = part['name']
    for bar, names in spec['harmony'].items():
        st.harmony[int(bar)] = list(names)
    plan = res['plan']
    st.grammar = {
        'grammar': f'{style}/{sub}', 'seed': res['seed_used'], 'layout': plan['layout'],
        'form': plan['form'], 'form_rule': plan['form_rule'], 'units': res['units'],
        'beats': [{'bar': s['bar'], 'beat': s['beat'], 'chord': s['roman'],
                   'symbol': s['chord'], 'rule': s['rule']} for s in res['slots']],
    }
    lay_say = '弱起' if st.pickup else '强拍起'
    st.style_notes.append(f'grammar:{style}/{sub} 曲式 {plan["form"]}，{lay_say}，'
                          f'{len(res["units"])} 段，硬规则违例 0')
    st._preserve_finalize = True


# ---------- 注册表（文档唯一事实来源） ----------

SCRIPT_IMPL = {
    'new_score': _s_new_score,
    'set_sections': _s_set_sections,
    'set_harmony': _s_set_harmony,
    'accompaniment': _s_accompaniment,
    'melody': _s_melody,
    'thicken': _s_thicken,
    'cadence': _s_cadence,
    'fugue': _s_fugue,
    'chorale_grammar': _s_chorale_grammar,
    'leadsheet_grammar': _s_leadsheet_grammar,
    'narrative': _s_narrative,
    'write_part': _s_write_part,
    'finalize': _s_finalize,
}

SCRIPT_DOCS = [
    {
        'script': 'new_score',
        'desc': '初始化空白总谱（钢琴：右手高音声部 + 左手低音声部）。必须是第一个调用。',
        'args': (
            'title(str 必填) 曲名；key_sharps(int 必填) 调号升降数（Db大调=-5，f小调=-4）；'
            'mode(str 必填) "major"/"minor"；meter(str 必填) 基础拍号如 "4/4"；'
            'tempo(int 必填) 基础 BPM；bars(int 必填) 总小节数 8–64；'
            'seed(str 可选) 运行种子——不传则内核随机（同一提示词每次生成不同的'
            '节奏/旋律实现），传上一次 spec.seed 可逐音符复现；'
            'voices(int 可选) 1–24 声部（2=钢琴/创意曲，3/4=合唱/赋格，>4=室内乐/管弦编制）；'
            'instruments(list 可选) 各声部乐器英文名（长度=voices，顺序从高音到低音，'
            '低音乐器如 cello/timpani 放最后一位）：flute/oboe/horn/trumpet/violin/'
            'viola/cello/contrabass/timpani/piano/soprano/alto/tenor/bass。'
            '**brief 提到管弦乐/弦乐/铜管/木管/具体乐器时必须传**——否则声部会用'
            '默认音色（钢琴/人声）。例：弦乐四重奏 voices=4 + '
            '["violin","violin","viola","cello"]；'
            'solo_instrument(str 可选) 旋律路由——brief 指定某乐器独奏/主奏（如'
            '"大提琴独奏"、"flute solo"）传该乐器英文名；内核会把它提到声部 0'
            '承载旋律，并把旋律音区收进它的可奏音域（低音乐器不会被写成超音域的'
            '音）。该乐器必须在 instruments 名单内，例：solo_instrument="cello"；'
            'subseeds(obj 可选) 只覆盖某几维的子种子（键：melody/harmony/texture/'
            'narrative/decor/rhythm/fugue）——例如只改 melody 即"换旋律保留和声"；'
            'soft_weights(obj 可选) 风格软权重，如 {"rhythm": {"1|0.5|0.5|1": 2.0}}，'
            '键是素材稳定键（节奏模板形如 "1|0.5|0.5|1"，休止记 r），未登记素材权重=1，'
            '权重只偏置采样、不会让生成失败'
        ),
    },
    {
        'script': 'set_sections',
        'desc': '划分段落（名称/小节数/拍号/力度/速度）。各段小节之和必须等于总小节数。'
                '自动生成力度阶梯、tempo_marks、meter_changes。'
                '相邻段 BPM 自动钳制在上一段的 70%–125%（段落边界平滑变速，'
                '「略微加快」按 +8~+15 BPM 给值，不要给翻倍式跳变）。',
        'args': (
            'sections(list 必填) 每项 {name(str), bars(int), meter(str 默认继承前段), '
            'dynamic(str pp–ff 默认mp), tempo_text(str 如 "Andante"/"Agitato"), bpm(int)}'
        ),
    },
    {
        'script': 'set_harmony',
        'desc': '为段落设定和声进行（罗马数字或绝对和弦符号，逐小节循环）。必须先于该段 '
                '的 melody/accompaniment。可选 key_sharps/mode 做**段落级转调**：'
                '该段罗马数字按新调解析，并在段首小节写出显式调号变化（MusicXML key）。',
        'args': (
            'section(str 必填) 段落名；progression(list 必填) 罗马数字 或 和弦符号列表，'
            '如 ["I","vi","IV","V7"]、爵士七和弦 ["ii7","V7","Imaj7"]、'
            '冲突段 ["bII","vii°7","V7"]、收束 ["IV","I","ii","V7"]；'
            '**绝对和弦符号**也支持：["Dm7","G7","Cmaj7","C#dim7","Am7b5"]（用户明确指定'
            '某小节和弦时用）；'
            'key_sharps(int 可选) 该段新调号 -7–7（如转到关系小调不换调号则不传）；'
            'mode(str 可选) 该段调式 "major"/"minor"；'
            'bar_overrides(dict 可选) 逐小节精确覆盖：键=绝对小节号(从曲首第1小节起)，'
            '值=和弦符号或罗马数字，实现「第 N 小节用 X 和弦」；'
            '转调段应使用新调的调内和声词汇（如转 A 小调后用 i–iv–V7–i）。'
            '注意：progression 一旦出现绝对和弦符号，该段跳过风格契约的品质改写（显式意图优先）'
        ),
    },
    {
        'script': 'accompaniment',
        'desc': '为段落生成左手/低声部伴奏（由和声驱动，时值精确填满）。',
        'args': (
            'section(str 必填)；pattern(str 必填) sustained(长音)/broken_arpeggio(分解和弦)/'
            'alberti(阿尔贝蒂低音)/block_chords(柱式和弦)/octave_pulse(八度脉冲,冲突)/'
            'chromatic_descent(半音下行低音,冲突)/waltz(圆舞曲)/mazurka(玛祖卡,第2拍重音)/'
            'walking_bass(走动低音+键盘comping)/comping(切分柱式)；'
            "register(str 'low'/'mid' 默认low)；density(0.5/1/2 默认1)；"
            'notes(list 可选) **音符级控制**：显式低声部线条 [[token,时值名],…]，'
            '原样保留绝对音高——实现「第二声部用 C4 到 G4 的半音经过音」之类需求。'
            'token∈ 级数1-7 / ^和弦音(^1^3^5^7) / 音名C4 / rest；时值为 DUR_QL 键。'
            '提供 notes 时忽略 pattern（整段用显式线条）'
        ),
    },
    {
        'script': 'melody',
        'desc': '为段落生成右手旋律：和弦音骨架 + 调内经过音 + 乐句拱形 + 连音线。'
                '旋律跨小节连贯、朝乐句目标级进、不原地钟摆；'
                'finalize 时自动规避外声部平行五/八度。',
        'args': (
            "section(str 必填)；register(str low/mid/high/very_high 默认mid)；"
            "rhythm(str sparse(安静长音)/flowing(流畅)/fragmented(碎片+休止,冲突)/"
            "driving(推进,高潮) 默认flowing)；"
            "contour(str arch/wave/descending/ascending 默认arch)；"
            "motif(list 可选) 动机 [[级数1-7,时值名],…] 如 [[5,'quarter'],[4,'eighth'],"
            "[3,'eighth'],[2,'half']]，每个乐句首小节呈现并做二度模进；"
            "notes(list 可选) **音符级控制**：显式旋律音高序列 [[token,时值名],…]，"
            "精确控制到音符——实现「旋律从 C4 开始级进上行到 G4」「用 C-E-G 琶音」"
            "「附点八分音符节奏型」之类需求。token∈ 级数1-7 / ^和弦音(^1^3^5^7) / "
            "音名C4 / rest；时值支持 whole/half/quarter/eighth/16th/32nd/"
            "dotted_half(3)/dotted_quarter(1.5)/dotted_eighth(0.75)。提供 notes 时忽略 rhythm/contour"
            "（整段用显式音高）；绝对音名落在音区窗口外时整八度收进窗口"
        ),
    },
    {
        'script': 'thicken',
        'desc': '段落内右手旋律八度加厚（高潮段用），每小节首音加重音记号。',
        'args': 'section(str 必填)',
    },
    {
        'script': 'cadence',
        'desc': '终止式：替换最后 1–2 小节为主和弦长音（右手主音双音、左手主音+五度），'
                '只能用于最后一个段落。',
        'args': 'bars(int 1或2 默认1)；section(str 可选，默认最后一段)',
    },
    {
        'script': 'fugue',
        'desc': '赋格（确定性）：把目标区间写成完整赋格织体——主题依次进入（主/属答题）→ '
                '已进入声部以主题头模进作对题 → 模进间插段 → 中部关系调进入 → 密接和应 → '
                '属持续音 + 主和弦终止。'
                '会覆盖该区间的所有声部，因此区间内不要再调 melody/accompaniment。'
                '**可选编排手段，不是默认**：仅当用户需求/plan 明确提到赋格'
                '（赋格/fugue/卡农/创意曲）或 plan.form=="fugue" 时才调用；'
                '「对位/复调」本身不算赋格；brief 未点名不得自加（越权会被摘除）。'
                '声部数由 new_score 的 voices 决定，2 声部=二声部创意曲，3/4 声部=标准赋格。',
        'args': (
            'section(str 可选，默认整首) 目标段落名；voices(int 可选) 必须与 new_score 一致；'
            'subject(list 可选) 主题 [[级数1-14,时值名],…]（8–14=高八度），不给则按种子从原型库确定性生成；'
            'answer(str "real"/"tonal" 默认tonal) 真实/守调答题；'
            'entry_bars(int 1或2 默认自动) 每次主题进入占几小节；'
            'stretto(bool 默认true) 是否密接和应；episodes(bool 默认true) 是否间插段；'
            'pedal(bool 默认true) 是否属持续音'
        ),
    },
    {
        'script': 'chorale_grammar',
        'desc': '巴赫众赞歌语法（确定性、可解释）：用从 350 首巴赫众赞歌学到的规则脚本 '
                '(grammar.yaml) 写满 SATB 四个声部——句终规划 → 每句终止公式 → 和声+女高音 '
                '联合搜索 → 低音 → 内声部 → 经过音；硬规则（平行五八度/交叉/导音/七音等）'
                '违例为 0，每拍可追溯到引用的规则编号。会覆盖所有声部并跳过自动 '
                'cadence/narrative/平行修正，因此只需 new_score(voices=4, meter="4/4", '
                'instruments=["soprano","alto","tenor","bass"]) → set_sections → '
                'chorale_grammar → finalize。**仅当 brief 明确要众赞歌 / 四部合唱 / '
                '巴赫风格圣咏时使用**。',
        'args': ('seed(str 可选) 默认取运行种子的 harmony 分枝；'
                 'temperature(float 0–3 默认1) 越高越多样，0 = 每步取最可能；'
                 'barform(bool 可选) 是否让第 3–4 句重复第 1–2 句（不传则按语料概率）；'
                 'layout(str "auto"/"pickup"/"downbeat" 默认auto) 弱起或强拍起——pickup = '
                 '巴赫最常见的写法：一拍弱起小节 + bars 个完整小节，句终落第 3 拍、第 4 拍'
                 '是下一句弱起；auto 按语料比例（约 72% 弱起）；'
                 'grammar(str 可选) 规则脚本路径，默认 sacred/chorale/grammar.yaml'),
    },
    {
        'script': 'leadsheet_grammar',
        'desc': '旋律 + 和弦语法（确定性、可解释）：用从带和弦标记的语料学到的规则脚本写满'
                '旋律声部与钢琴左右手——folk = 诺丁汉英伦民间舞曲（jig/reel/hornpipe/waltz），'
                'jazz = 魏玛爵士独奏（swing/bebop/cool/hardbop/postbop/traditional），'
                'pop = Billboard 流行歌，madrigal = 蒙特威尔第牧歌；和声由分析器逐拍识别的'
                '多声部语料：sonata = 贝多芬/莫扎特/海顿钢琴奏鸣曲、strings = 弦乐四重奏'
                '（子风格 allegro/minuet/duple/compound 按拍号）、symphony = 海顿交响曲'
                '（allegro/minuet/duple），chopin = 肖邦玛祖卡。'
                '曲式（AABB/AABA…）→ 每段和声（H 转移 + C 终止公式）→ 每小节节奏模板 / 按拍'
                '节奏单元 → 旋律动态规划 → 织体；硬规则（强拍和弦音、非和弦音级进、跳进上限、'
                '音域、末音主音、和弦转移须在语料出现过、各声部两两无平行五八度等）违例为 0。'
                '旋律 + 钢琴（folk/jazz/pop/madrigal）：new_score(voices=3, meter=<该子风格学到的'
                '拍号>, bars=8 的倍数, instruments=["violin"|"trumpet"|"voice"|"soprano"|"flute"|'
                '"clarinet", "Piano RH", "Piano LH"])；钢琴独奏（sonata/chopin，右手旋律 + 左手'
                '阿尔贝蒂 / 低音-和弦-和弦 / 分解和弦音型）：voices=2, instruments=["piano","piano"]；'
                '弦乐四重奏（strings）：voices=4, instruments=["violin","violin","viola","cello"]；'
                '交响曲（symphony）：voices=9, instruments=["flute","oboe","horn","bassoon",'
                '"timpani","violin","violin","viola","cello"]。'
                '之后 set_sections → leadsheet_grammar → finalize。'
                '**translator 不要调用**：由 Planner 的 learned_grammar 路由或 /compose-grammar '
                '直接执行，translator 程序里出现会被摘除。',
        'args': ('style(str 必填) folk/jazz/pop/madrigal/sonata/strings/chopin；'
                 'sub(str 可选) 子风格，默认第一个；'
                 'seed(str 可选) 默认取运行种子的 melody 分枝；'
                 'temperature(float 0–3 默认1)；'
                 'layout(str "auto"/"pickup"/"downbeat" 默认auto) 弱起或强拍起，auto 按语料比例'),
    },
    {
        'script': 'narrative',
        'desc': '叙事层：给整曲套上戏剧弧线——张力曲线与高潮定位、力度弧线、'
                '演奏法、动机变形出场表、惊喜和弦、峰值后留白。'
                '必须在所有声部都写完之后调用（finalize 之前）。缺省也会自动补。',
        'args': ('intensity(float 0.4–1.6 默认1) 戏剧强度；'
                 'surprises(int 0–3 默认1) 惊喜和弦数量；'
                 'silence(bool 默认true) 高潮后是否留白；'
                 'appearances(bool 默认true) 是否用「动机变形出场」重写高音声部'
                 '（倒影/逆行/增时/减时/加花）；明确「不要赋格/复调模仿/卡农」'
                 '的作品应设 false，旋律才严格保持 melody 写下的形态；'
                 'seed(str 可选) 叙事种子，默认用运行种子'),
    },
    {
        'script': 'write_part',
        'desc': '按声部写入绝对音符（逆向复现权威路径）。可代替 melody/accompaniment；'
                '与 finalize(preserve=true) 联用可逐音符复现源谱。',
        'args': (
            'part(int 必填) 声部索引（0=最高）；'
            'notes(list 可选) 整曲扁平事件 [[pitch|null|list, 时值名, mark?], …]；'
            'bars(obj 可选) {小节号: events}，与 notes 二选一'
        ),
    },
    {
        'script': 'finalize',
        'desc': '汇总成总谱 spec（校验每小节每声部都有音符、修正外声部平行五八度）。'
                '必须是最后一个调用。',
        'args': (
            'preserve(bool 可选) 为 true 时跳过自动 cadence/narrative/平行修正——'
            '用于 write_part 逆向复现，保证音符级一致'
        ),
    },
]


SCRIPT_CALL_ORDER = (
    '调用顺序模板：new_score → set_sections → 对每个段落 { set_harmony → '
    'accompaniment → melody } →（高潮段）thicken →（末段）cadence → finalize'
)

SCRIPT_TEXTURE_MAP = (
    '段落织体映射参考：安静段 melody(rhythm=sparse, register=mid/low) + '
    'accompaniment(sustained 或 broken_arpeggio, density 0.5–1)；冲突段 '
    'melody(rhythm=fragmented, register=high) + accompaniment(octave_pulse 或 '
    'chromatic_descent)；高潮段 melody(rhythm=driving, register=high/very_high) + '
    'thicken + accompaniment(broken_arpeggio, density 2)；收束段 '
    'melody(rhythm=sparse, contour=descending) + accompaniment(sustained) + cadence。'
)


def kernel_docs() -> str:
    lines = ['# 可用脚本（唯一事实来源）', '']
    for d in SCRIPT_DOCS:
        lines.append(f"## {d['script']}\n{d['desc']}\n参数：{d['args']}\n")
    lines.append(SCRIPT_CALL_ORDER + '\n\n' + SCRIPT_TEXTURE_MAP)
    return '\n'.join(lines)


def _snapshot_state(st: "_State") -> Dict[str, Any]:
    """截取 _State 的关键状态（用于脚本输入输出追溯）。

    声部的音符实际存放在 p['bars']（小节号 → 事件列表）里，这里按
    小节顺序摊平成「每个声部一条事件列表」，便于与上一快照做差得到
    「本次脚本新增了哪些音符」。
    """
    events: List[List] = []
    for p in st.parts:
        bars = p.get('bars') or {}
        part_evs: List[Any] = []
        for b in sorted(k for k in bars.keys() if isinstance(k, int)):
            for e in bars[b]:
                part_evs.append(list(e) if isinstance(e, (list, tuple)) else e)
        events.append(part_evs)
    return {
        'events': events,
        'ts': st.ts, 'tempo': st.tempo, 'bars': st.bars,
        'voices': st.voices, 'sections': len(st.sections),
        'harmony': len(st.harmony), 'seed': st.seed,
    }


def _ev_token(e) -> str:
    """把一个 event 压成可读的「音高:时值」token。"""
    if not isinstance(e, (list, tuple)) or not e:
        return '?'
    p = e[0]
    d = e[1] if len(e) > 1 else '?'
    if isinstance(p, (list, tuple)):
        p = '+'.join(str(x) for x in p)
    return f'{p}:{d}'


def _script_note(name: str, args: Dict[str, Any], before: Dict[str, Any],
                 after: Dict[str, Any]) -> str:
    """每个脚本的人类可读输出摘要。"""
    a = args or {}
    if name == 'new_score':
        return (f"建立乐曲：{a.get('meter')} ♩={a.get('tempo')} "
                f"{a.get('voices')}声部 {a.get('mode')}")
    if name == 'set_sections':
        secs = a.get('sections') or []
        return '分段：' + '，'.join(
            f"{s.get('name')}({s.get('bars')}小节)" for s in secs)
    if name == 'set_harmony':
        return f"和声：段落{a.get('section')} 进行{a.get('progression')}"
    if name == 'accompaniment':
        return (f"伴奏：段落{a.get('section')} pattern={a.get('pattern')} "
                f"register={a.get('register')} density={a.get('density')}")
    if name == 'melody':
        return f"旋律：段落{a.get('section')} rhythm={a.get('rhythm')}"
    if name == 'fugue':
        return f"赋格：段落{a.get('section') or '整首'}"
    if name == 'chorale_grammar':
        return f"众赞歌语法：写满 SATB 四声部（{after['bars']} 小节）"
    if name == 'leadsheet_grammar':
        return f"旋律曲语法 {a.get('style')}/{a.get('sub') or ''}：{after['voices']} 声部（{after['bars']} 小节）"
    if name == 'cadence':
        return f"终止：bars={a.get('bars')}"
    if name == 'thicken':
        return f"加厚：段落{a.get('section')}"
    if name == 'finalize':
        return f"收束装配：{after['bars']}小节 / {after['voices']}声部"
    return name


def _script_trace_entry(name: str, args: Dict[str, Any], before: Dict[str, Any],
                        after: Dict[str, Any]) -> Dict[str, Any]:
    """构造单条脚本的「输入 + 输出」追溯记录。"""
    ev_before = before['events']
    ev_after = after['events']
    added_counts: List[int] = []
    added_samples: List[List[str]] = []
    for i in range(len(ev_after)):
        b = ev_before[i] if i < len(ev_before) else []
        new = ev_after[i][len(b):]
        added_counts.append(len(new))
        added_samples.append([_ev_token(e) for e in new[:8]])
    return {
        'script': name,
        'input': args,
        'output': {
            'added_counts': added_counts,
            'added_sample': added_samples,
            'state_after': {
                'ts': after['ts'], 'tempo': after['tempo'],
                'bars': after['bars'], 'voices': after['voices'],
                'sections': after['sections'], 'harmony': after['harmony'],
            },
            'note': _script_note(name, args, before, after),
        },
    }


def run_program(program: Any,
                trace: Optional[List[Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
    """执行翻译器输出的调用序列，返回 spec。任何违约抛 KernelError。

    trace: 传入一个列表时，逐脚本记录「输入(args) + 输出(新增音符/状态变更)」，
    用于作曲过程透明化（分析报告第 4 节）。
    """
    if not isinstance(program, dict) or not isinstance(program.get('calls'), list):
        raise KernelError('输出必须是 {"calls": [{"script": ..., "args": {...}}]}')
    calls = program['calls']
    if not calls:
        raise KernelError('calls 为空')
    st = _State()
    names: List[str] = []
    for c in calls:
        if not isinstance(c, dict) or 'script' not in c:
            raise KernelError(f'调用缺少 script 字段：{c!r}')
        name = str(c['script'])
        fn = SCRIPT_IMPL.get(name)
        if fn is None:
            raise KernelError(f'未知脚本 {name!r}（可用：{", ".join(SCRIPT_IMPL)}）')
        if not st.bars and name != 'new_score':
            raise KernelError('第一个调用必须是 new_score')
        args = c.get('args') or {}
        if not isinstance(args, dict):
            raise KernelError(f'{name}: args 必须是对象')
        names.append(name)
        st.called.append(name)
        before = _snapshot_state(st)
        fn(st, args)
        after = _snapshot_state(st)
        if trace is not None:
            trace.append(_script_trace_entry(name, args, before, after))
    if names.count('new_score') != 1 or names[0] != 'new_score':
        raise KernelError('new_score 必须恰好调用一次且在最前')
    if 'set_sections' not in names:
        raise KernelError('缺少 set_sections')
    if names[-1] != 'finalize':
        raise KernelError('finalize 必须是最后一个调用')
    # 收尾再跑一次 finalize（幂等）；必须沿用调用里的 args，
    # 否则 write_part + preserve=true 会被第二次空参 finalize 冲掉。
    final_args = calls[-1].get('args') or {}
    if not isinstance(final_args, dict):
        final_args = {}
    return _s_finalize(st, final_args)
