# -*- coding: utf-8 -*-
"""风格骨架（Style Skeleton）——把「从真实乐谱学到的 style」确定性地变成乐谱骨架。

背景
----
score_learning 从语料里学习了每个曲风，产出 genre pack：
``skills/composition/genre/packs/<family>/<pack_id>/meta.yaml``
（profile 里记着拍号、速度、调性偏好、和声词汇、织体、音区、力度、曲式、
foundation_score 段落地图等）。过去这些只被当成 prompt 文本塞给 LLM，
风格是否生效全看模型心情。

本模块把 style 变成**确定性脚本**：同样的 style + 同样的覆盖参数 →
同样的内核调用序列（音符级可复现），完全不经过 LLM。

两层模型
--------
* **基调（style / genre pack）**：决定拍号、速度、调性、和声词汇、伴奏织体、
  旋律节奏性格、音区、力度区间、段落骨架。由本模块从 profile 读出。
* **提示词变奏**：只覆盖用户明确提到的维度（情绪 mood、小节数 bars、
  调性 key、速度 tempo、拍号 meter、种子 seed），其余一律继承基调。

典型用法
--------
    from style_skeleton import build_skeleton_calls, skeleton_spec
    calls = build_skeleton_calls('intimate/nocturne', mood='悲伤', bars=16)
    spec  = skeleton_spec('intimate/nocturne', mood='悲伤', bars=16)
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import composition_skills as skill_lib

#: contract_of 的 mtime 缓存（键：(genre_id, 契约文件 mtime)）
_CONTRACT_CACHE: Dict[Any, Any] = {}

try:  # 内核是本模块的唯一执行后端
    from compose_kernel import KernelError, run_program, _scale_names, parse_roman
except Exception:  # pragma: no cover
    KernelError = Exception
    run_program = None
    _scale_names = None
    parse_roman = None


# ============================================================
# 调性 / 速度 / 织体 的确定性映射表
# ============================================================

MAJOR_SHARPS = {
    'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5, 'F#': 6, 'C#': 7,
    'F': -1, 'Bb': -2, 'Eb': -3, 'Ab': -4, 'Db': -5, 'Gb': -6, 'Cb': -7,
}
MINOR_SHARPS = {
    'A': 0, 'E': 1, 'B': 2, 'F#': 3, 'C#': 4, 'G#': 5, 'D#': 6, 'A#': 7,
    'D': -1, 'G': -2, 'C': -3, 'F': -4, 'Bb': -5, 'Eb': -6, 'Ab': -7,
}

# 风格包里的织体名 → 内核伴奏 pattern
ACC_MAP = {
    # 风格包 profile.texture.primary（伴奏默认值缺失时的兜底）
    'polyphonic': 'sustained', 'figuration_driven': 'broken_arpeggio',
    'homophony_with_periodic_bass': 'alberti', 'full_orchestral': 'broken_arpeggio',
    'melody_plus_chordal': 'block_chords', 'homophonic': 'block_chords',
    'monophonic_tune': 'sustained', 'melody_plus_light_groove': 'block_chords',
    'melody_plus_chord_symbols': 'walking_bass', 'lead_vocal_plus_band': 'walking_bass',
    'homophonic_SATB': 'block_chords',
    # 通用内核 pattern 名（直通）
    'sustained': 'sustained', 'pad': 'sustained', 'drone': 'sustained',
    'long_chord': 'sustained', 'chorale': 'block_chords', 'hymn': 'block_chords',
    'block_chords': 'block_chords', 'block_chord': 'block_chords',
    'broken_chord': 'broken_arpeggio', 'broken_chord_nocturne': 'broken_arpeggio',
    'broken_arpeggio': 'broken_arpeggio', 'arpeggio': 'broken_arpeggio',
    'figuration': 'broken_arpeggio', 'running_eighths': 'broken_arpeggio',
    'alberti': 'alberti', 'alberti_bass': 'alberti', 'alberti_figuration': 'alberti',
    'waltz': 'waltz', 'waltz_bass': 'waltz', 'waltz_lh': 'waltz',
    'octave_pulse': 'octave_pulse', 'ostinato': 'octave_pulse',
    'march_bass': 'octave_pulse',
    'walking_bass': 'walking_bass', 'comping': 'comping',
    'chromatic_descent': 'chromatic_descent', 'chromatic': 'chromatic_descent',
    # 风格包 profile.rhythm.accompaniment.default → 内核 pattern（统一映射，不按体裁写死）
    'walking_bass_and_comping': 'walking_bass',
    'rhythm_section_comping': 'walking_bass',
    'oom_pah_pah': 'waltz', 'oom_pah': 'octave_pulse',
    'string_pulse_and_arpeggio': 'broken_arpeggio',
    'full_orchestra': 'broken_arpeggio',
    'alberti_or_broken': 'alberti',
    'alberti_or_chamber_homophony': 'alberti',
    'SATB_blocks': 'block_chords', 'chordal': 'block_chords',
    'soft_blocks_or_broken': 'block_chords',
    'held_chords_or_drone': 'sustained', 'drone_or_none': 'sustained',
    'heterophony_or_drone': 'sustained',
    'ostinato_figuration': 'broken_arpeggio',
    'heavy_pulse': 'octave_pulse', 'low_pulse': 'octave_pulse',
    'active_bass': 'octave_pulse', 'light_bass_harmony': 'block_chords',
    'light_bounce': 'broken_arpeggio', 'sway_pattern': 'waltz',
    'tango_bass': 'octave_pulse', 'boom_chuck_light': 'block_chords',
    'chordal_with_shifted_accent': 'mazurka',
    'mazurka': 'mazurka', 'mazurka_lh': 'mazurka',
    'brass_harmony': 'block_chords', 'string_pads_arps': 'broken_arpeggio',
    'romantic_piano': 'broken_arpeggio',
    'contrapuntal_entries': 'sustained',
}

# rhythmic_identity → (伴奏 pattern, 旋律 rhythm)
RHYTHM_IDENTITY_MAP = {
    'triple_meter_dance': ('waltz', 'flowing'),
    'duple_march': ('octave_pulse', 'driving'),
    'dotted_drive': ('octave_pulse', 'driving'),
    'compound_flow': ('broken_arpeggio', 'flowing'),
    'free_rubato': ('broken_arpeggio', 'sparse'),
    'pulse_ostinato': ('octave_pulse', 'flowing'),
    'syncopated_dance': ('block_chords', 'flowing'),
    'groove': ('block_chords', 'driving'),
    # 爵士：语料是 lead sheet（~1 音/小节），旋律必须 sparse+切分，不是密八分流畅
    'swing_eighths': ('walking_bass', 'sparse'),
}

# 旋律动机原型（级数 1-7 + 时值名），由 rhythmic_identity 确定性挑选
MOTIF_ARCHETYPES = {
    'free_rubato': [[5, 'quarter'], [4, 'eighth'], [3, 'eighth'], [2, 'half']],
    'compound_flow': [[5, 'quarter'], [6, 'eighth'], [5, 'eighth'], [4, 'half']],
    # 玛祖卡式：附点起句感（短短长），避免圆舞式每拍一音
    'triple_meter_dance': [[5, 'eighth'], [5, 'eighth'], [3, 'quarter'], [4, 'quarter']],
    'duple_march': [[1, 'eighth'], [1, 'eighth'], [3, 'quarter'], [5, 'quarter']],
    'dotted_drive': [[5, 'eighth'], [5, 'eighth'], [6, 'quarter'], [5, 'quarter']],
    'syncopated_dance': [[5, 'eighth'], [5, 'eighth'], [6, 'quarter'], [4, 'quarter']],
    'groove': [[3, 'eighth'], [5, 'eighth'], [6, 'quarter'], [5, 'quarter']],
    'pulse_ostinato': [[1, 'quarter'], [3, 'eighth'], [4, 'eighth'], [5, 'quarter']],
    # 爵士头段：长音主题（贴 Weimar lead-sheet 稀疏密度，勿八分音墙）
    'swing_eighths': [[5, 'half'], [3, 'half']],
}
MOTIF_DEFAULT = [[5, 'quarter'], [4, 'quarter'], [3, 'quarter'], [2, 'half']]

DYN_LADDER = ['pp', 'p', 'mp', 'mf', 'f', 'ff']

# 情绪词 → 变奏（只改它提到的维度）
# 注：激昂/激烈/高潮/epic 早先还带 'thicken': True（用于「只在 climax 段加八度」）。
# 加厚现已改为按力度峰值无条件施加（见 build_skeleton_calls），该键成了死配置
# —— 留着会让读者以为它还在生效，故移除；情绪对加厚的影响由 density 承担。
MOOD_OVERRIDES = {
    '悲伤': {'mode_bias': 'minor', 'melody_rhythm': 'sparse', 'density': 0.5},
    '忧伤': {'mode_bias': 'minor', 'melody_rhythm': 'sparse', 'density': 0.5},
    '忧郁': {'mode_bias': 'minor', 'melody_rhythm': 'sparse', 'density': 0.5},
    '安静': {'melody_rhythm': 'sparse', 'density': 0.5},
    '平静': {'melody_rhythm': 'sparse', 'density': 0.5},
    '激昂': {'melody_rhythm': 'driving', 'density': 2.0},
    '激烈': {'melody_rhythm': 'driving', 'density': 2.0},
    '高潮': {'melody_rhythm': 'driving', 'density': 2.0},
    '紧张': {'melody_rhythm': 'fragmented', 'density': 1.0},
    '不安': {'melody_rhythm': 'fragmented', 'density': 1.0},
    '欢快': {'mode_bias': 'major', 'melody_rhythm': 'flowing', 'density': 1.0},
    '轻快': {'mode_bias': 'major', 'melody_rhythm': 'flowing', 'density': 1.0},
    '庄严': {'melody_rhythm': 'flowing', 'density': 1.0},
    'sad': {'mode_bias': 'minor', 'melody_rhythm': 'sparse', 'density': 0.5},
    'calm': {'melody_rhythm': 'sparse', 'density': 0.5},
    'tense': {'melody_rhythm': 'fragmented', 'density': 1.0},
    'epic': {'melody_rhythm': 'driving', 'density': 2.0},
}


class SkeletonError(Exception):
    """骨架生成失败（风格包缺失、参数越界、内核拒绝）。"""


# ============================================================
# 风格解析
# ============================================================

def resolve_genre(genre: Any = None) -> Optional[dict]:
    """把 genre id / 中文名 / 英文串解析成 genre pack 字典。"""
    if genre is None:
        return None
    if isinstance(genre, dict) and (genre.get('profile') or genre.get('pack_id')):
        return genre
    s = str(genre).strip()
    if not s:
        return None
    g = skill_lib.get_genre(s)
    if g:
        return g
    # 两级路由（家族 → 包）；路由层不可用时退回旧的单层关键词匹配
    try:
        import style_routing
        g = style_routing.best_match(s)
    except Exception:
        g = None
    if not g:
        g = skill_lib.match_genre(s)
    if g:
        return g
    # 只给 pack_id（如 nocturne）时按 id 后缀匹配
    low = s.lower()
    for cand in skill_lib.list_genres():
        if str(cand.get('pack_id') or '').lower() == low or \
           str(cand.get('id') or '').lower().endswith('/' + low):
            return cand
    return None


#: 节奏身份 → 软权重（属性键，由内核 _rhythm_props 展开成逐模板权重）
_RHYTHM_SOFT_WEIGHTS: Dict[str, Dict[str, float]] = {
    # taxonomy.dimensions.rhythmic_identity 的实际取值
    'free_rubato': {'long_start': 1.4, 'sparse': 1.2, 'uniform': 0.7},
    'duple_march': {'uniform': 1.6, 'even_pairs': 1.4, 'dense': 1.2, 'rest': 0.6},
    # 玛祖卡/三拍舞：鼓励弱起与句末短音，降低「每拍一音」的圆舞感
    'triple_meter_dance': {'short_end': 1.5, 'rest': 1.35, 'even_pairs': 1.1,
                           'uniform': 0.65, 'dense': 0.7},
    'pulse_ostinato': {'uniform': 1.7, 'even_pairs': 1.3, 'dense': 1.2, 'rest': 0.6},
    'compound_flow': {'uniform': 1.3, 'dense': 1.2, 'long_start': 1.1},
    'syncopated_dance': {'rest': 1.3, 'uniform': 0.6, 'even_pairs': 0.8},
    'dotted_drive': {'short_end': 1.5, 'uniform': 0.6},
    # 爵士：休止/长起句优先，抑制密均匀八分
    'swing_eighths': {'rest': 1.6, 'long_start': 1.4, 'sparse': 1.35,
                      'uniform': 0.45, 'dense': 0.5, 'even_pairs': 0.7},
}

#: 情绪 → 软权重（与节奏身份相乘）
_MOOD_SOFT_WEIGHTS: Dict[str, Dict[str, float]] = {
    'lyrical': {'long_start': 1.3, 'sparse': 1.15},
    'playful': {'short_end': 1.25, 'dense': 1.15},
    'solemn': {'sparse': 1.2, 'long_start': 1.2, 'dense': 0.8},
    'heroic': {'dense': 1.3, 'uniform': 1.2},
    'tense': {'rest': 1.3, 'dense': 1.1},
    'melancholic': {'sparse': 1.25, 'long_start': 1.2, 'rest': 0.9},
    'triumphant': {'dense': 1.3, 'uniform': 1.25},
    'tender': {'sparse': 1.2, 'long_start': 1.2},
    'pastoral': {'sparse': 1.15, 'long_start': 1.15},
    'mysterious': {'rest': 1.2, 'sparse': 1.15},
    'adventurous': {'dense': 1.2, 'uniform': 1.1},
}


def soft_weights_for(prof: Optional[dict], dims: Optional[dict] = None) -> Dict[str, Dict[str, float]]:
    """风格包 profile/dimensions → 内核软权重（参与采样，不只是喂 prompt）。

    节奏身份（rhythmic_identity）与情绪（mood）各出一组属性权重，内核把它们
    展开成逐条节奏模板的采样权重。风格包因此能真正影响"选了哪条节奏"，
    而不只是影响提示词。没有可用指纹时返回空字典（等价等概率，行为不变）。
    """
    dims = dims or {}
    prof = prof or {}
    out: Dict[str, Dict[str, float]] = {}
    rw: Dict[str, float] = {}

    def _merge(src: Dict[str, float]):
        for k, v in src.items():
            rw[k] = round(rw.get(k, 1.0) * float(v), 4)

    merged: set = set()
    ident = str(dims.get('rhythmic_identity') or '').lower()
    if ident in _RHYTHM_SOFT_WEIGHTS:
        _merge(_RHYTHM_SOFT_WEIGHTS[ident])
        merged.add(ident)
    rhy = (prof.get('rhythm') or {}).get('identity') or ''
    for word, w in _RHYTHM_SOFT_WEIGHTS.items():
        # 已按精确 ident 合并过的词不再做子串二次合并（否则同一权重被平方）
        if word not in merged and word in str(rhy).lower():
            _merge(w)
    mood = str(dims.get('mood') or '').lower()
    if mood in _MOOD_SOFT_WEIGHTS:
        _merge(_MOOD_SOFT_WEIGHTS[mood])
    # 赋格：主题采样不该被 prelude 式 dens/uniform 拉成音阶练习曲
    if str(dims.get('form') or '').lower() == 'fugue':
        _merge({'dense': 0.55, 'uniform': 0.75, 'rest': 1.1, 'sparse': 1.15})
    if rw:
        out['rhythm'] = rw
    return out


def _unresolved_genre_message(genre: Any) -> str:
    """风格解析失败的诊断信息（两级路由：候选 + 家族 + 用法）。"""
    s = str(genre or '')
    base = f'找不到风格包：{s!r}' if s else '未指定风格包'
    try:
        import style_routing
        style_routing.route_or_raise(s)
    except Exception as e:
        msg = str(e)
        if msg:
            return f'{base}\n{msg}'
    try:
        names = '、'.join(str(x.get('name') or x.get('pack_id'))
                         for x in list_styles()[:8])
        return f'{base}\n可用风格：{names} …'
    except Exception:
        return base


def list_styles() -> List[dict]:
    """UI 用：所有风格包（id / 名称 / 家族 / 摘要）。"""
    out = []
    for g in skill_lib.list_genres():
        out.append({
            'id': g.get('id'),
            'pack_id': g.get('pack_id'),
            'name': g.get('name') or g.get('pack_id'),
            'name_en': g.get('name_en') or '',
            'family': (g.get('dimensions') or {}).get('family') or '',
            'form': (g.get('dimensions') or {}).get('form') or '',
            'summary': g.get('summary') or '',
            'enabled': bool(g.get('enabled', True)),
        })
    return sorted(out, key=lambda d: (d['family'], str(d['name'])))


def _norm_dyn(v: Any, default: str = 'mp') -> str:
    s = str(v or '').strip().lower()
    s = s.replace('ppp', 'pp').replace('fff', 'ff')
    return s if s in DYN_LADDER else default


QL_UNIT_NAME = {4.0: 'whole', 2.0: 'half', 1.0: 'quarter', 0.5: 'eighth', 0.25: '16th'}


def _meter_cap(meter: str) -> float:
    """拍号 → 每小节四分音符数（4/4=4、6/8=3、2/4=2）。"""
    try:
        num, den = str(meter).split('/')
        return 4.0 * int(num) / int(den)
    except Exception:
        return 4.0


def parse_key(text: Any) -> Optional[Tuple[int, str]]:
    """'Db' / 'F# minor' / 'f小调' / 'Eb 大调' / 'B- major' → (sharps, mode)。

    music21 / 语料里的降号常用连字符（B- = Bb、E- = Eb）；若不识别会落到
    同名升号调（B major），爵士会整曲跑到 5 个升号，听感立刻崩。
    """
    s = str(text or '').strip()
    if not s:
        return None
    s = s.replace('♭', 'b').replace('♯', '#').replace('−', '-')
    s = s.replace('大调', ' major ').replace('小调', ' minor ')
    # music21 式降号：字母后紧跟 '-'（B- major / E- minor）
    s = re.sub(r'\b([A-Ga-g])-(?=\s|$|[A-Za-z\u4e00-\u9fff])', r'\1b', s)
    m = re.search(r'([A-G])([#b]?)\s*(major|minor|m|maj|min)?', s, re.I)
    if not m:
        return None
    letter, acc, tail = m.group(1).upper(), m.group(2) or '', (m.group(3) or '').lower()
    name = letter + acc
    if tail in ('minor', 'min', 'm'):
        table = MINOR_SHARPS
        mode = 'minor'
    else:
        table = MAJOR_SHARPS
        mode = 'major'
    if name not in table:
        # 可能是 'F#m' 之类，尾部字母被正则吞掉
        return None
    return table[name], mode


def _tempo_text(bpm: float) -> str:
    """与 translator 规则一致的速度术语（文字与数字必须匹配）。"""
    b = float(bpm)
    if b < 66:
        return 'Adagio'
    if b < 76:
        return 'Andante'
    if b < 88:
        return 'Andantino'
    if b < 108:
        return 'Moderato'
    if b < 120:
        return 'Allegretto'
    if b < 140:
        return 'Allegro'
    return 'Presto'


def _valid_meter(meter: str) -> bool:
    """内核只接受每小节 2/3/4 个四分音符。"""
    try:
        num, den = str(meter).split('/')
        return 4.0 * int(num) / int(den) in (2.0, 3.0, 4.0)
    except Exception:
        return False


def _profile_of(g: dict) -> dict:
    return (g or {}).get('profile') or {}


def contract_of(g: Optional[dict]):
    """风格包 → 学到的实证契约（StyleContract）。

    有 grammar.yaml 的包返回 None（可解释语法已取代旧契约）。其余包若仍有
    style_contract.yaml 则照常加载。契约是**实测语料**的数值化结果；但已学
    语法包不再消费它。

    带 mtime 缓存：build_skeleton_calls 每次生成会调用多次（骨架 + 段落 +
    说明），不缓存就要重复解析同一个 YAML；用 mtime 做键，改了包立刻生效。
    """
    if not g:
        return None
    gid = g.get('id') or ''
    try:
        import composition_skills as _cs
        contract, path = _cs.genre_contract(gid)
    except Exception:
        return None
    key = (str(gid), _mtime(path))
    if key in _CONTRACT_CACHE:
        return _CONTRACT_CACHE[key]
    _CONTRACT_CACHE.clear()          # 只留最新一批（包数量级很小）
    _CONTRACT_CACHE[key] = contract
    return contract


def _mtime(path) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


def _contract_meter(c) -> Optional[str]:
    """契约给的体裁拍号（不可用/非法时 None）。"""
    try:
        m = getattr(c, 'meter', None)
        if m is not None and m.is_usable() and m.primary \
                and _valid_meter(str(m.primary)):
            return str(m.primary)
    except Exception:
        pass
    return None


def _contract_tempo(c) -> Optional[int]:
    """契约给的体裁速度（不可用/非法时 None）。"""
    try:
        t = getattr(c, 'tempo', None)
        if t is not None and t.is_usable() and t.default is not None:
            return int(t.default)
    except Exception:
        pass
    return None


def _split_progression(raw: str) -> List[str]:
    """'I–vi–IV–V' → ['I','vi','IV','V']。"""
    toks = [t.strip() for t in re.split(r'[–—\-→>,，/]+', str(raw or '')) if t.strip()]
    return toks


def _valid_roman(tok: str, sharps: int, mode: str) -> bool:
    if parse_roman is None or _scale_names is None:
        return bool(re.match(r'^[b#]?[IViv]+(7|maj7|M7|°7|o7|ø7|\+|aug|dim)?$', tok))
    try:
        parse_roman(tok, _scale_names(sharps, mode))
        return True
    except Exception:
        return False


def _harmony_pool(prof: dict, sharps: int, mode: str) -> List[List[str]]:
    """风格包里的和声进行 → 内核可解析的级数列表池。"""
    h = prof.get('harmony') or {}
    raws = list(h.get('progressions') or [])
    pool: List[List[str]] = []
    for r in raws:
        toks = _split_progression(r)
        toks = [t for t in toks if _valid_roman(t, sharps, mode)]
        if len(toks) >= 2:
            pool.append(toks)
    if not pool:
        pool = [['I', 'vi', 'IV', 'V7']] if mode == 'major' else [['i', 'VI', 'III', 'VII']]
    return pool


def _cadence_tokens(pool: List[List[str]], mode: str, n_bars: int) -> List[str]:
    """收束段：循环基调进行，末两小节强制 V7→I（小调同样用属七）。

    n_bars=1 时只有一小节，直接落主和弦（不能只有属七没有解决）。
    """
    base = list(pool[0])
    tonic = 'I' if mode == 'major' else 'i'
    if n_bars <= 1:
        return [tonic]
    seq = [base[i % len(base)] for i in range(n_bars)]
    seq[-1] = tonic
    seq[-2] = 'V7'
    return seq


def _register_choice(prof: dict, fallback: str = 'mid') -> str:
    """profile.register.melody 的音域 → low/mid/high/very_high。"""
    reg = (prof.get('register') or {}).get('melody') or {}
    centers = {'low': 62, 'mid': 71, 'high': 79, 'very_high': 84}

    def _midi(name: Any) -> Optional[int]:
        m = re.match(r'^([A-G])([#b]?)(-?\d)$', str(name or '').strip())
        if not m:
            return None
        pc = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[m.group(1)]
        acc = 1 if m.group(2) == '#' else (-1 if m.group(2) == 'b' else 0)
        return (int(m.group(3)) + 1) * 12 + pc + acc

    lo, hi = _midi(reg.get('low')), _midi(reg.get('high'))
    if lo is None or hi is None:
        return fallback
    center = (lo + hi) / 2.0
    return min(centers, key=lambda k: abs(centers[k] - center))


def _plan_sections(prof: dict, dims: dict, bars: int) -> List[dict]:
    """段落骨架：优先用 foundation_score.map（学习时从真实曲子量出来的段落地图）。"""
    fs = prof.get('foundation_score') or {}
    raw_map = fs.get('map') if isinstance(fs, dict) else None
    secs: List[dict] = []
    if isinstance(raw_map, dict) and raw_map:
        items = []
        for k, v in raw_map.items():
            m = re.match(r'^\s*(\d+)\s*(?:[-–—~]\s*(\d+))?\s*$', str(k))
            if not m:
                continue
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else a
            if b < a:
                a, b = b, a
            items.append((a, b, str(v or '').strip()))
        items.sort()
        if items:
            # 地图总长与 bars 不一致时按比例缩放到 bars
            total = sum(b - a + 1 for a, b, _ in items)
            scale = bars / float(total) if total else 1.0
            acc = 0
            for i, (a, b, label) in enumerate(items):
                n = max(1, int(round((b - a + 1) * scale)))
                if i == len(items) - 1:
                    n = max(1, bars - acc)
                acc += n
                secs.append({'name': label or f'段{i + 1}', 'bars': n})
                if acc >= bars:
                    break
            # 补齐/裁掉到 bars
            while sum(s['bars'] for s in secs) < bars:
                secs[-1]['bars'] += 1
    if not secs:
        shape = str((prof.get('form') or {}).get('full_shapes')
                    or (prof.get('form') or {}).get('shape') or '')
        letters = [t for t in re.split(r'[-–—\s]+', shape) if t] if shape else []
        if len(letters) >= 2:
            base, rest = divmod(bars, len(letters))
            for i, letter in enumerate(letters):
                secs.append({'name': letter if letter not in [s['name'] for s in secs]
                             else f"{letter}{i + 1}",
                             'bars': base + (1 if i < rest else 0)})
    if not secs:
        phrase = str((prof.get('phrase') or {}).get('preferred') or [''])
        m = re.search(r'(\d+)\s*\+\s*(\d+)', str((prof.get('phrase') or {}).get('preferred')))
        if not m:
            prefs = (prof.get('phrase') or {}).get('preferred') or []
            if prefs and isinstance(prefs[0], str):
                m = re.search(r'(\d+)\s*\+\s*(\d+)', prefs[0])
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            unit = a + b
            reps = max(1, bars // unit)
            for i in range(reps):
                secs.append({'name': f'A{i + 1}', 'bars': a})
                secs.append({'name': f'B{i + 1}', 'bars': b})
            tail = bars - sum(s['bars'] for s in secs)
            if tail > 0:
                secs.append({'name': 'Coda', 'bars': tail})
    if not secs:
        # 四段式：呈示 / 发展 / 高潮 / 收束
        if bars >= 16:
            quarters = [bars // 4, bars // 4, bars // 4, bars - 3 * (bars // 4)]
            names = ['呈示', '发展', '高潮', '收束']
        else:
            half = bars // 2
            quarters = [half, bars - half]
            names = ['呈示', '收束']
        for nm, n in zip(names, quarters):
            if n > 0:
                secs.append({'name': nm, 'bars': n})

    # 唯一化段落名（内核要求）
    seen = {}
    for s in secs:
        nm = s['name'] or '段'
        if nm in seen:
            seen[nm] += 1
            nm = f'{nm}{seen[nm]}'
        else:
            seen[nm] = 0
        s['name'] = nm

    # 角色：首段引子、末段收束、其余中段最后一个为高潮
    n = len(secs)
    for i, s in enumerate(secs):
        if i == 0:
            s['role'] = 'intro'
        elif i == n - 1:
            s['role'] = 'outro'
        elif n >= 3 and i == n - 2:
            s['role'] = 'climax'
        else:
            s['role'] = 'develop'
    if n == 1:
        secs[0]['role'] = 'outro'
    return [s for s in secs if s['bars'] > 0]


def _fit_sections(sections: List[dict], want_bars: int) -> List[dict]:
    """把段落总长精确对齐到 want_bars，且**每段至少 1 小节**。

    原先的实现把 `want_bars - sum(bars)` 的赤字整块丢给末段：请求 8 小节的
    「交响曲乐章」（段落地图是 8+8）会让末段变成 0 小节，内核直接拒绝
    「段落 B1 bars 必须 ≥1」。这里改成按比例重分配（最大余数法），
    段落数多于小节数时先裁段 —— 任何情况下不产生 0 小节段。
    """
    secs = [s for s in (sections or []) if int(s.get('bars') or 0) > 0]
    if not secs:
        return secs
    if len(secs) > want_bars:
        secs = secs[:max(1, want_bars)]
    total = sum(int(s['bars']) for s in secs)
    if total == want_bars:
        return secs
    shares = [int(s['bars']) * float(want_bars) / total for s in secs]
    base = [max(1, int(x)) for x in shares]
    # 先按小数余数从大到小补，再从最大的段里扣（扣不动就就地收敛）
    order = sorted(range(len(base)), key=lambda i: -(shares[i] - int(shares[i])))
    short = want_bars - sum(base)
    k = 0
    while short > 0:
        base[order[k % len(order)]] += 1
        short -= 1
        k += 1
    while short < 0:
        cands = [i for i in range(len(base)) if base[i] > 1]
        if not cands:
            break
        base[max(cands, key=lambda i: base[i])] -= 1
        short += 1
    for s, n in zip(secs, base):
        s['bars'] = int(n)
    return [s for s in secs if int(s['bars']) > 0]


# ============================================================
# 骨架生成（确定性）
# ============================================================

def build_skeleton_calls(genre: Any = None, *,
                         title: Optional[str] = None,
                         bars: Optional[int] = None,
                         key: Optional[str] = None,
                         tempo: Optional[int] = None,
                         meter: Optional[str] = None,
                         mode: Optional[str] = None,
                         seed: Optional[str] = None,
                         mood: Optional[str] = None,
                         use_motif: bool = True,
                         voices: Optional[int] = None,
                         register_override: Optional[str] = None,
                         density_override: Optional[float] = None,
                         fugue: str = 'auto') -> Dict[str, Any]:
    """风格 → 内核调用序列（确定性）。

    参数中给了的覆盖基调；没给的一律继承风格包。
    返回 {"calls": [...]}，可直接交给 compose_kernel.run_program。

    fugue：'auto'（默认）——骨架模板在合适的中段插入一段赋格（主题答题进入，
    2 声部即二声部创意曲；/skeleton 是显式请求，不受「brief 未提赋格」约束）；
    'full'——整首写成赋格；'off'——不要赋格。
    voices：2/3/4，不给时由风格织体推导（对位/赋格类风格=3，其余=2）。
    """
    g = resolve_genre(genre)
    if g is None:
        raise SkeletonError(_unresolved_genre_message(genre))
    prof, dims = _profile_of(g), (g.get('dimensions') or {})
    # 学到的实证契约（只有学过、产出 style_contract.yaml 的包才有）。
    # 优先级：显式参数 > 契约（实测） > meta.yaml profile（手写）。
    contract = contract_of(g)

    ov: Dict[str, Any] = {}
    if mood:
        low = str(mood).lower()
        for word, patch in MOOD_OVERRIDES.items():
            if word in str(mood) or word in low:
                ov.update(patch)

    # ---- 拍号 ----
    m = str(meter or _contract_meter(contract)
            or (prof.get('meter') or {}).get('primary') or '4/4').strip()
    if not _valid_meter(m):
        allowed = (prof.get('meter') or {}).get('allowed') or []
        m = next((str(x) for x in allowed if _valid_meter(str(x))), '4/4')

    # ---- 速度 ----
    # 默认仍走契约经验中位数；爵士 lead-sheet 语料常把中位数抬到 155+，
    # 听感发赶——该家族优先用人工 profile.default（meta 132）。
    tp = prof.get('tempo') or {}
    fam = str(dims.get('family') or (g.get('family') if isinstance(g, dict) else '')
              or '').lower()
    base_bpm = tempo
    prefer_profile_tempo = fam == 'jazz'
    if base_bpm is None and prefer_profile_tempo:
        for k in ('default', 'min', 'max'):
            if tp.get(k) is not None:
                try:
                    base_bpm = int(tp[k])
                    break
                except (TypeError, ValueError):
                    continue
    if base_bpm is None:
        base_bpm = _contract_tempo(contract)
    if base_bpm is None:
        for k in ('default', 'min', 'max'):
            if tp.get(k) is not None:
                try:
                    base_bpm = int(tp[k])
                    break
                except (TypeError, ValueError):
                    continue
    if base_bpm is None:
        base_bpm = 92
    base_bpm = max(40, min(200, int(base_bpm)))

    # ---- 调性 / 模式 ----
    if key is not None:
        parsed = parse_key(key)
    else:
        prefs = (prof.get('tonal_center') or {}).get('preferred') or []
        parsed = None
        for p in prefs:
            parsed = parse_key(p)
            if parsed:
                break
    if parsed is None:
        parsed = (0, 'major')
    sharps, key_mode = parsed
    if mode in ('major', 'minor'):
        key_mode = mode
    if ov.get('mode_bias') and not mode and key is None:
        key_mode = ov['mode_bias']

    # ---- 小节数 ----
    gen_f = prof.get('generation') or {}
    fd = (gen_f.get('foundation') or {}) if isinstance(gen_f.get('foundation'), dict) else {}
    # 显式传 bars=0 视为非法输入（后续有 8–64 钳制兜底），不用 or 短路掩盖
    want_bars = bars if bars else (
        fd.get('bars') or (prof.get('form') or {}).get('foundation_bars') or 16)
    try:
        want_bars = int(want_bars)
    except (TypeError, ValueError):
        want_bars = 16
    want_bars = max(8, min(64, want_bars))

    # ---- 织体 / 节奏性格 ----
    rid = (dims.get('rhythmic_identity') or '') or \
        ((prof.get('rhythm') or {}).get('identity') or '')
    acc_default = str((prof.get('rhythm') or {}).get('accompaniment', {}) and
                      ((prof.get('rhythm') or {}).get('accompaniment') or {}).get('default') or '')
    if acc_default:
        acc_pattern = ACC_MAP.get(acc_default)
    else:
        acc_pattern = None
    if not acc_pattern:
        tex = str((prof.get('texture') or {}).get('primary') or '')
        acc_pattern = ACC_MAP.get(tex)
    if not acc_pattern:
        acc_pattern = RHYTHM_IDENTITY_MAP.get(rid, ('broken_arpeggio', 'flowing'))[0]
    # 元数据声明「重音在二/三拍」→ 强制玛祖卡左手，勿退回圆舞 oom-pah
    accent = (prof.get('meter') or {}).get('accent') or {}
    if accent.get('often_beat_2_or_3') or acc_default == 'chordal_with_shifted_accent':
        acc_pattern = 'mazurka'
    mel_rhythm = RHYTHM_IDENTITY_MAP.get(rid, (None, 'flowing'))[1] or 'flowing'
    if ov.get('melody_rhythm'):
        mel_rhythm = ov['melody_rhythm']
    # 实证契约：lead-sheet 级稀疏旋律（爵士 Weimar 等）→ 禁止密八分 flowing
    try:
        ct = contract_of(g)
        dens = None
        if ct is not None and getattr(ct, 'rhythm', None) is not None:
            dens = getattr(ct.rhythm, 'density_notes_per_bar', None)
        if dens is not None and float(dens) < 2.0 and mel_rhythm == 'flowing':
            mel_rhythm = 'sparse'
        # 玛祖卡：旋律也要能弱起/切分，勿每拍填满
        if acc_pattern == 'mazurka' and mel_rhythm == 'flowing':
            mel_rhythm = 'sparse'
    except Exception:
        pass

    density = ov.get('density')
    if density is None:
        dens_word = str((prof.get('texture') or {}).get('density') or '')
        density = 0.5 if dens_word in (
            'transparent', 'thin', 'sparse', 'lead_sheet') else \
            (2.0 if dens_word in ('dense', 'thick') else 1.0)
    density = 0.5 if float(density) <= 0.5 else (2.0 if float(density) >= 2.0 else 1.0)
    # 显式 density 覆盖（refine_style 用来做确定性密度对齐；其余场景留空继承风格包）
    if density_override is not None:
        dv = float(density_override)
        density = 0.5 if dv <= 0.5 else (2.0 if dv >= 2.0 else 1.0)

    mel_register = _register_choice(prof, fallback='mid')
    tempo_energy = dims.get('tempo_energy') or ''
    if tempo_energy in ('allegro', 'presto') and mel_register in ('low', 'mid'):
        mel_register = 'high'
    # 显式 register 覆盖（refine_style 用来做确定性音域对齐；其余场景留空继承风格包）
    if register_override in ('low', 'mid', 'high', 'very_high'):
        mel_register = register_override

    # ---- 力度区间 ----
    dyn = prof.get('dynamics') or {}
    dmin = _norm_dyn((dyn.get('default') or {}).get('min'), 'p')
    dmax = _norm_dyn((dyn.get('default') or {}).get('max'), 'mf')
    # 钳制到表内且保证 hi > lo：下限为 ff 时 hi_i 会越界（DYN_LADDER 只有 6 档）
    lo_i = min(DYN_LADDER.index(dmin), len(DYN_LADDER) - 2)
    hi_i = max(DYN_LADDER.index(dmax), lo_i + 1)
    hi_i = min(hi_i, len(DYN_LADDER) - 1)

    # ---- 和声池 ----
    pool = _harmony_pool(prof, sharps, key_mode)

    # ---- 动机 ----
    motif = list(MOTIF_ARCHETYPES.get(rid, MOTIF_DEFAULT)) if use_motif else None
    if motif:
        # 动机总时值必须能塞进一小节（6/8、2/4 等小容量拍号要缩小）
        cap_beats = _meter_cap(m)
        units = {'whole': 4.0, 'half': 2.0, 'quarter': 1.0, 'eighth': 0.5, '16th': 0.25}
        total = sum(units.get(str(d), 1.0) for _deg, d in motif)
        if total > cap_beats + 1e-9:
            f = cap_beats / total
            scaled = []
            for deg, d in motif:
                v = units.get(str(d), 1.0) * f
                cand = [u for u in (4.0, 2.0, 1.0, 0.5, 0.25) if u <= v + 1e-9]
                scaled.append([deg, QL_UNIT_NAME[max(cand)] if cand else '16th'])
            if sum(units[str(d)] for _deg, d in scaled) <= cap_beats + 1e-9:
                motif = scaled
            else:
                motif = None

    # ---- 段落 ----
    sections = _plan_sections(prof, dims, want_bars)
    sections = _fit_sections(sections, want_bars)

    dyn_step = [lo_i, min(lo_i + 1, hi_i), hi_i, lo_i]
    calls: List[Dict[str, Any]] = [{
        'script': 'new_score',
        'args': {
            'title': str(title or f"{g.get('name') or g.get('pack_id')}小品"),
            'key_sharps': int(sharps),
            'mode': key_mode,
            'meter': m,
            'tempo': int(base_bpm),
            'bars': int(want_bars),
        },
    }]
    # 始终写入 style= 包路径，便于追溯 / 评估；旧 style_contract 已在有 grammar
    # 的包上作废，内核 resolve_contract 会返回 None，不会再改写和声。
    ref = f"{dims.get('family') or ''}/{g.get('pack_id') or ''}".strip('/')
    calls[0]['args']['style'] = ref or str(g.get('id') or '')
    if seed:
        calls[0]['args']['seed'] = str(seed)
    # 软权重：风格指纹参与采样（此前只是喂给 prompt，不影响内核选择）
    try:
        sw = soft_weights_for(prof, dims)
    except Exception:
        sw = {}
    if sw:
        calls[0]['args']['soft_weights'] = sw

    sec_args = []
    prev_bpm = float(base_bpm)
    for i, s in enumerate(sections):
        role = s['role']
        if i == 0:
            # 首段必须是全曲速度本身：此前首段也按 intro 系数 ×0.95，于是
            # `new_score(tempo=X)` 声明的速度（含风格契约学到的速度）只在 spec
            # 里存在，谱面第 1 小节被写成 X×0.95 的另一个值 —— 分析报告按谱面
            # 读出的是后者，声明的速度从头到尾没有被听到，也没被验证到。
            # 相对系数（0.95/1.10/0.90）只在「相对前一段」时才有意义。
            bpm = int(round(prev_bpm))
        elif role == 'intro':
            bpm = int(round(prev_bpm * 0.95))
        elif role == 'climax':
            bpm = int(round(prev_bpm * 1.10))
        elif role == 'outro':
            bpm = int(round(prev_bpm * 0.90))
        else:
            bpm = int(round(prev_bpm))
        bpm = max(30, min(220, bpm))
        prev_bpm = float(bpm)
        di = dyn_step[min(i, len(dyn_step) - 1)]
        di = max(lo_i, min(hi_i, di))
        sec_args.append({
            'name': s['name'],
            'bars': int(s['bars']),
            'meter': m,
            'dynamic': DYN_LADDER[di],
            'tempo_text': _tempo_text(bpm),
            'bpm': bpm,
        })
    calls.append({'script': 'set_sections', 'args': {'sections': sec_args}})

    # ---- 声部数 + 赋格段（基调决定织体，赋格是硬需求） ----
    tex_density = str((prof.get('texture') or {}).get('density') or '')
    tex_primary = str((prof.get('texture') or {}).get('primary') or '')
    inst_default = []
    for bag in (fd if isinstance(fd, dict) else {},
                gen_f if isinstance(gen_f, dict) else {}):
        raw_inst = bag.get('instruments_default') or []
        if isinstance(raw_inst, list) and raw_inst:
            inst_default = [str(x) for x in raw_inst if str(x).strip()]
            break
    try:
        from llm_compose import (_normalize_instrument_list,
                                 _expand_piano_to_grand)
        inst_default = _expand_piano_to_grand(
            _normalize_instrument_list(inst_default))
    except Exception:
        pass
    voices_explicit = voices is not None
    if voices is None and inst_default:
        voices = len(inst_default)
    elif voices is None:
        voices = 3 if ('2_to_4' in tex_density or tex_primary == 'polyphonic'
                       or dims.get('form') == 'fugue') else 2
    try:
        voices = int(voices)
    except (TypeError, ValueError):
        voices = 2
    voices = max(1, min(24, voices))
    calls[0]['args']['voices'] = voices
    if inst_default:
        # 钢琴大谱表：[piano,piano] 优先于织体推出来的 3/4 声部，但不覆盖用户显式声部数
        if ((inst_default == ['piano', 'piano'] and not voices_explicit)
                or len(inst_default) == voices):
            calls[0]['args']['instruments'] = list(inst_default)
            calls[0]['args']['voices'] = len(inst_default)
            voices = len(inst_default)

    fugue_mode = str(fugue or 'auto').lower()
    # 风格包 foundation.fugue 可强制 off/full（YAML 里 off/false 都会落到关）
    pack_fugue = None
    for bag in (fd if isinstance(fd, dict) else {},
                gen_f if isinstance(gen_f, dict) else {}):
        if 'fugue' in bag and bag.get('fugue') is not None:
            raw = bag.get('fugue')
            if raw is False or str(raw).lower() in ('off', 'false', 'no', '0'):
                pack_fugue = 'off'
            elif raw is True or str(raw).lower() in ('full', 'true', 'yes', '1'):
                pack_fugue = 'full'
            else:
                pack_fugue = str(raw).lower()
            break
    if pack_fugue in ('off', 'full', 'auto') and fugue_mode == 'auto':
        fugue_mode = pack_fugue
    if fugue_mode not in ('auto', 'full', 'off'):
        fugue_mode = 'auto'
    fugue_idx = -1
    if fugue_mode == 'full':
        calls.append({'script': 'fugue', 'args': {}})
    elif fugue_mode == 'auto':
        # 段落太碎（没有 ≥4 小节的段）时整首写成赋格；实在太短就不赋格
        if not any(s['bars'] >= 4 for s in sections) and want_bars >= 8:
            fugue_mode = 'full'
            calls.append({'script': 'fugue', 'args': {}})
    if fugue_mode == 'auto':
        # 选中段（优先 climax/develop，其次最中间的一段）写成赋格
        cand = [i for i, s in enumerate(sections)
                if s['role'] in ('climax', 'develop') and s['bars'] >= 4]
        if not cand:
            cand = [i for i in range(1, len(sections) - 1) if sections[i]['bars'] >= 4]
        if cand:
            fugue_idx = max(cand, key=lambda i: (sections[i]['role'] == 'climax',
                                                 sections[i]['bars']))
        elif len(sections) >= 1 and want_bars >= 8:
            # 兜底：选最长的一段。中间段可能都 < 4 小节（如 intro(5)+develop(2)+outro(5)），
            # 内核要求赋格区间 ≥ 4 小节；最长段仍不足时改为整首赋格
            fugue_idx = max(range(len(sections)), key=lambda i: sections[i]['bars'])
            if sections[fugue_idx]['bars'] < 4:
                fugue_idx = -1
                fugue_mode = 'full'
                calls.append({'script': 'fugue', 'args': {}})
        if fugue_idx >= 0:
            calls.append({'script': 'fugue',
                          'args': {'section': sec_args[fugue_idx]['name']}})

    # 力度最高的一段就是检查器认定的「高潮段」（它按 DYNAMIC_VELOCITIES 取最大）。
    # 两边必须指同一段，否则「加了厚但检查器看的是另一段」＝没加。
    _dyn_idx = [max(lo_i, min(hi_i, dyn_step[min(k, len(dyn_step) - 1)]))
                for k in range(len(sec_args))]
    peak_idx = max(range(len(_dyn_idx)), key=lambda k: _dyn_idx[k]) if _dyn_idx else -1

    for i, (s, sa) in enumerate(zip(sections, sec_args)):
        if i == fugue_idx or fugue_mode == 'full':
            continue        # 赋格段由 fugue 脚本整体写，不要再叠主调织体
        role = s['role']
        n_bars = int(sa['bars'])
        if role == 'outro':
            prog = _cadence_tokens(pool, key_mode, n_bars)
        else:
            base = pool[i % len(pool)]
            prog = [base[j % len(base)] for j in range(n_bars)] if n_bars <= 8 else base
        prog = [t for t in prog if _valid_roman(t, sharps, key_mode)] or \
            (['I', 'V7', 'I'] if key_mode == 'major' else ['i', 'V7', 'i'])
        calls.append({'script': 'set_harmony',
                      'args': {'section': sa['name'], 'progression': prog}})

        pat = acc_pattern
        if role == 'intro' and pat in ('octave_pulse', 'chromatic_descent'):
            pat = 'broken_arpeggio'
        if role == 'outro' and pat in ('octave_pulse', 'chromatic_descent'):
            pat = 'sustained'
        dens = density
        if role == 'intro':
            dens = min(dens, 1.0)
        if role == 'climax':
            dens = max(dens, 1.0)
        if role == 'outro':
            dens = min(dens, 1.0)
        calls.append({'script': 'accompaniment', 'args': {
            'section': sa['name'], 'pattern': pat,
            'register': 'low', 'density': float(dens),
        }})

        rhythm = mel_rhythm
        contour = 'arch'
        if role == 'outro':
            rhythm, contour = 'sparse', 'descending'
        elif role == 'climax':
            # lead-sheet / 玛祖卡稀疏旋律：高潮只抬音区轮廓，不改成 dense driving
            if mel_rhythm == 'sparse':
                rhythm, contour = 'sparse', 'ascending'
            else:
                rhythm, contour = (
                    ('driving' if not ov.get('melody_rhythm') else rhythm),
                    'ascending')
        reg = mel_register
        if role == 'intro' and reg in ('very_high', 'high'):
            reg = 'mid'
        margs = {'section': sa['name'], 'register': reg,
                 'rhythm': rhythm, 'contour': contour}
        if motif and role == 'intro':
            margs['motif'] = motif
        calls.append({'script': 'melody', 'args': margs})

        # 高潮段加厚：检查器把「高潮段无八度加厚」当作 CRITICAL 织体问题，
        # 而此前加厚只挂在 tempo_energy ∈ {allegro, presto} 上 —— 于是 11/31 个
        # 包的骨架曲在高潮段仍是单音旋律，生成即被判缺陷（skeleton_spec 无法靠
        # 换种子规避，只能走兜底返回）。这里按力度峰值判定，与检查器同源；
        # 取「≥ 次高力度」而不是「= 最高」：叙事层会在多个段写出同一个最高力度
        # （爵士的 f 落在第 5–6 与 9–10 小节），而检查器按最大力度取段时会选中
        # 靠前那一个 —— 只加厚「最高」那一段会与它错位，等于没加。
        # 爵士 combo：八度加厚破坏 lead-sheet 线条与指纹密度，改由 walking/comping 撑织体。
        jazz_combo = acc_pattern in ('walking_bass', 'comping')
        if (not jazz_combo) and (
                i == peak_idx or role == 'climax' or _dyn_idx[i] >= hi_i - 1):
            calls.append({'script': 'thicken', 'args': {'section': sa['name']}})

    # 赋格已写到末小节时它自带终止，不要再覆盖
    if not (fugue_mode == 'full' or fugue_idx == len(sec_args) - 1):
        calls.append({'script': 'cadence', 'args': {'bars': 1}})
    calls.append({'script': 'finalize', 'args': {}})
    return {'calls': calls}


def skeleton_spec(genre: Any = None, *, tries: int = 4, trace=None, **kw) -> Dict[str, Any]:
    """骨架 → spec（跑确定性内核）。

    同一风格包在不同种子下旋律细节不同；这里最多试 tries 次，
    挑第一个没有 CRITICAL/MAJOR 级问题（时值、声部进行）的实现。
    trace: 传入列表时，把逐脚本「输入+输出」记录进去（作曲过程透明化）。
    """
    if run_program is None:
        raise SkeletonError('compose_kernel 不可用')
    last_err = None
    best = None
    best_trace = None
    for i in range(max(1, int(tries))):
        kw_i = dict(kw)
        if i:
            kw_i['seed'] = f"{kw.get('seed') or 'sk'}-{i}"
        # 逐脚本记录只在**最终选中的那一次**回填：失败的尝试会调用 new_score，
        # 直接复用同一个 trace 会让报告 4.3 出现两遍脚本链。
        local_trace: list = [] if trace is not None else None
        try:
            spec = run_program(build_skeleton_calls(genre, **kw_i), trace=local_trace)
        except SkeletonError:
            raise
        except KernelError as e:
            last_err = e
            continue
        try:
            from llm_compose import validate_score_spec, spec_to_music21
            from llm_compose import voice_leading_issues_from_m21
            bad = [x for x in validate_score_spec(spec)
                   if x.get('level') in ('CRITICAL', 'MAJOR')]
            bad += [x for x in voice_leading_issues_from_m21(spec_to_music21(spec))
                    if x.get('level') in ('CRITICAL', 'MAJOR')]
        except Exception:
            bad = []
        if not bad:
            if trace is not None:
                trace.extend(local_trace or [])
            return _attach_sections(spec, dict(kw_i, genre=genre))
        if best is None:
            # 兜底返回的是「第 1 次尝试」的谱，过程记录必须跟着同一次尝试。
            # 此前只有「存在一条零问题的实现」时才回填 trace，于是报告第 4 节
            # 「作曲过程透明化」在恰恰最需要它的场合（曲子带着 CRITICAL）是空的，
            # 而完全正常的曲子反倒有记录 —— 记录的存在性与「有没有问题」正相关错了。
            best, best_trace = spec, local_trace
    if best is not None:
        if trace is not None and best_trace:
            trace.extend(best_trace)
        return _attach_sections(best, dict(kw, genre=genre))
    raise SkeletonError(f'内核拒绝骨架：{last_err}')


def _attach_sections(spec: Dict[str, Any], kw: dict) -> Dict[str, Any]:
    """把段落信息（名称/起止小节）挂到 spec 上，供 UI 的编排视图显示。"""
    try:
        prog = build_skeleton_calls(kw.get('genre'), **{k: v for k, v in kw.items()
                                                        if k != 'genre'})
    except Exception:
        return spec
    start = 1
    out = []
    for c in prog.get('calls') or []:
        if c.get('script') != 'set_sections':
            continue
        for s in ((c.get('args') or {}).get('sections') or []):
            n = int(s.get('bars') or 0)
            out.append({'name': str(s.get('name') or ''), 'start': start,
                        'bars': n,
                        'fugue': str(s.get('name') or '') in _fugue_names(prog)})
            start += n
    if out:
        spec['sections'] = out
    # 记录风格包 id：归档/评估/复现都要用它（内核 spec 本身不带这个字段）
    if not spec.get('genre_id'):
        try:
            g = resolve_genre(kw.get('genre'))
            if g:
                spec['genre_id'] = g.get('id')
                spec['pack_id'] = g.get('pack_id')
        except Exception:
            pass
    return spec


def _fugue_names(prog: Dict[str, Any]) -> set:
    return {str((c.get('args') or {}).get('section') or '')
            for c in (prog.get('calls') or [])
            if c.get('script') == 'fugue' and (c.get('args') or {}).get('section')}


def skeleton_score(genre: Any = None, **kw):
    """骨架 → music21 Score（需要 llm_compose.spec_to_music21）。"""
    from llm_compose import spec_to_music21
    return spec_to_music21(skeleton_spec(genre, **kw))


def skeleton_report(genre: Any = None, **kw) -> str:
    """人读的骨架说明（UI / 对话里展示基调怎么落到参数上）。"""
    calls = build_skeleton_calls(genre, **kw)
    lines = ['**风格骨架（确定性生成，未经 LLM）**', '']
    for c in calls['calls']:
        a = c.get('args') or {}
        if c['script'] == 'new_score':
            lines.append(f"- 基调：`{a['title']}` {a['meter']} ♩={a['tempo']} "
                         f"sharps={a['key_sharps']} {a['mode']} · {a['bars']} 小节")
        elif c['script'] == 'set_sections':
            for s in a['sections']:
                lines.append(f"- 段落 {s['name']}：{s['bars']} 小节 · "
                             f"{s['dynamic']} · {s['tempo_text']} ♩={s['bpm']}")
        elif c['script'] == 'set_harmony':
            lines.append(f"- {a['section']} 和声：{'–'.join(a['progression'])}")
        elif c['script'] == 'accompaniment':
            lines.append(f"- {a['section']} 伴奏：{a['pattern']}（density {a['density']}）")
        elif c['script'] == 'melody':
            extra = ' + 动机' if a.get('motif') else ''
            lines.append(f"- {a['section']} 旋律：{a['register']}/{a['rhythm']}/"
                         f"{a['contour']}{extra}")
    return '\n'.join(lines)


def describe_style(genre: Any = None) -> Dict[str, Any]:
    """UI 检查器用：把风格包的关键坐标抽出来展示。"""
    g = resolve_genre(genre)
    if g is None:
        return {}
    prof, dims = _profile_of(g), (g.get('dimensions') or {})
    tp = prof.get('tempo') or {}
    mt = prof.get('meter') or {}
    return {
        'id': g.get('id'),
        'name': g.get('name'),
        'name_en': g.get('name_en') or '',
        'family': dims.get('family') or '',
        'form': dims.get('form') or '',
        'mood': dims.get('mood') or '',
        'tempo_energy': dims.get('tempo_energy') or '',
        'rhythmic_identity': dims.get('rhythmic_identity') or '',
        'summary': g.get('summary') or '',
        'meter': mt.get('primary') or '4/4',
        'tempo': tp.get('default') or tp.get('min') or 92,
        'tempo_range': [tp.get('min'), tp.get('max')],
        'keys': list((prof.get('tonal_center') or {}).get('preferred') or []),
        'texture': (prof.get('texture') or {}).get('primary') or '',
        'accompaniment': ((prof.get('rhythm') or {}).get('accompaniment') or {}).get('default') or '',
        'harmony': list((prof.get('harmony') or {}).get('progressions') or []),
        'dynamics': (prof.get('dynamics') or {}).get('default') or {},
        'register': (prof.get('register') or {}).get('melody') or {},
        'form_bars': (prof.get('form') or {}),
        'foundation': ((prof.get('generation') or {}).get('foundation') or {}),
        'constraints': prof.get('constraints') or {},
    }


if __name__ == '__main__':
    import json
    import sys
    gid = sys.argv[1] if len(sys.argv) > 1 else None
    if not gid:
        print('可用风格包：')
        for s in list_styles():
            print(f"  {s['id']:<34} {s['name']}")
        sys.exit(0)
    prog = build_skeleton_calls(gid)
    print(json.dumps(prog, ensure_ascii=False, indent=1))
    print(skeleton_report(gid))
