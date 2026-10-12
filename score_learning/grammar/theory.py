# -*- coding: utf-8 -*-
"""乐理基础：和弦模板识别、罗马数字显示、调内拼写。

所有音高在"调内坐标"里处理：rel = (pc - 主音 pc) % 12。和弦记为
(root_rel, quality, inversion)，与具体调无关，学到的规则因此可在任意调复用。
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

# 和弦性质 → 根音起的音程集合
QUALITIES: Dict[str, Tuple[int, ...]] = {
    'M': (0, 4, 7), 'm': (0, 3, 7), 'd': (0, 3, 6), 'A': (0, 4, 8),
    '7': (0, 4, 7, 10), 'm7': (0, 3, 7, 10), 'M7': (0, 4, 7, 11),
    'h7': (0, 3, 6, 10), 'o7': (0, 3, 6, 9),
}
SEVENTHS = ('7', 'm7', 'M7', 'h7', 'o7')
# 识别时的先验复杂度（越复杂越需要证据）
_Q_COST = {'M': 0.0, 'm': 0.0, 'd': 0.35, 'A': 0.9, '7': 0.25, 'm7': 0.45,
           'M7': 0.7, 'h7': 0.5, 'o7': 0.45}

MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)          # 自然小调；导音 11 视为调内变化音
LETTERS = 'CDEFGAB'
LETTER_PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
ROMAN = ('I', 'II', 'III', 'IV', 'V', 'VI', 'VII')
INV_FIG_TRIAD = ('', '6', '64')
INV_FIG_SEVENTH = ('7', '65', '43', '42')

Chord = Tuple[int, str, int]   # (root_rel, quality, inversion)


def chord_pcs(root: int, quality: str) -> List[int]:
    return [(root + i) % 12 for i in QUALITIES[quality]]


def chord_members(root: int, quality: str) -> Dict[int, str]:
    """rel pc → 角色（R/3/5/7）。"""
    names = ('R', '3', '5', '7')
    return {(root + iv) % 12: names[k] for k, iv in enumerate(QUALITIES[quality])}


def scale_of(mode: str) -> Tuple[int, ...]:
    return MAJOR_SCALE if mode == 'major' else MINOR_SCALE


def recognize_chord(onset: Dict[int, float], bass_rel: Optional[int], mode: str,
                    offbeat: Optional[Dict[int, float]] = None
                    ) -> Tuple[Optional[Chord], float]:
    """模板匹配和弦识别。

    onset：拍首发声的 rel pc → 权重（低音加倍）；offbeat：后半拍新起的音（经过音
    多在这里，证据弱）。七音必须在拍首出现，避免把经过音当七音。
    返回 ((root, quality, inversion), 置信度)。识别不出返回 (None, 0)。
    """
    if not onset:
        return None, 0.0
    offbeat = offbeat or {}
    total = sum(onset.values()) + sum(offbeat.values())
    best = None
    best_score = -1e9
    scale = scale_of(mode)
    for root in range(12):
        for q, ivs in QUALITIES.items():
            tpl = {(root + i) % 12 for i in ivs}
            if q in SEVENTHS and (root + ivs[3]) % 12 not in onset:
                continue
            cov_on = sum(w for pc, w in onset.items() if pc in tpl)
            cov_off = sum(w for pc, w in offbeat.items() if pc in tpl)
            unc_on = sum(onset.values()) - cov_on
            unc_off = sum(offbeat.values()) - cov_off
            score = cov_on + 0.5 * cov_off - 1.6 * unc_on - 0.4 * unc_off - _Q_COST[q]
            if root not in onset:
                score -= 1.5
            third = (root + ivs[1]) % 12
            if third not in onset:
                score -= 0.9
            if q in ('d', 'A', 'h7', 'o7') and (root + ivs[2]) % 12 not in onset:
                score -= 0.9
            if bass_rel is not None and bass_rel == root:
                score += 0.35
            # 调内和弦略优先
            if all(pc in scale or (mode == 'minor' and pc == 11) for pc in tpl):
                score += 0.15
            if score > best_score:
                best_score = score
                best = (root, q)
    if best is None:
        return None, 0.0
    root, q = best
    ivs = QUALITIES[q]
    inv = 0
    if bass_rel is not None:
        rel = (bass_rel - root) % 12
        if rel in ivs:
            inv = ivs.index(rel)
    conf = max(0.0, min(1.0, best_score / max(total, 1e-9)))
    return (root, q, inv), conf


def _degree_of(rel: int, mode: str) -> Tuple[int, int]:
    """rel pc → (音级 0–6, 升降 -1/0/+1)，尽量调内。"""
    scale = scale_of(mode)
    if rel in scale:
        return scale.index(rel), 0
    if mode == 'minor' and rel == 11:
        return 6, 1
    if mode == 'minor' and rel == 9:
        return 5, 1
    # 升高下方音级 or 降低上方音级：大调里 1/3/6/8/10 习惯写法
    prefer_flat = {1: 1, 3: 2, 8: 5, 10: 6} if mode == 'major' else {1: 1, 4: 2, 6: 4}
    if rel in prefer_flat:
        return prefer_flat[rel], -1
    for k, s in enumerate(scale):
        if (s + 1) % 12 == rel:
            return k, 1
    return 0, 0


def _is_diatonic_chord(root: int, quality: str, mode: str) -> bool:
    scale = set(scale_of(mode))
    if mode == 'minor':
        scale |= {11, 9}
    return all(pc in scale for pc in chord_pcs(root, quality))


def roman(ch: Chord, mode: str) -> str:
    """(root, quality, inv) → 罗马数字（含副属与转位数字），用于人读的规则。"""
    root, q, inv = ch
    fig = (INV_FIG_SEVENTH if q in SEVENTHS else INV_FIG_TRIAD)[min(inv, 3 if q in SEVENTHS else 2)]
    if q == '7' and inv == 0:
        fig = '7'
    if not _is_diatonic_chord(root, q, mode) and (root != 0 or q == '7'):
        if q in ('M', '7'):
            tgt = (root + 5) % 12
            if tgt in scale_of(mode) and tgt != 0 or (mode == 'minor' and tgt in (3, 8)):
                base = 'V' + (fig if fig else '')
                return f'{base}/{_plain_roman(tgt, mode)}'
        if q in ('d', 'o7', 'h7'):
            tgt = (root + 1) % 12
            if tgt in scale_of(mode):
                sfx = {'d': '°', 'o7': '°7', 'h7': 'ø7'}[q]
                f2 = fig if q == 'd' else (fig if fig != '7' else '')
                return f'vii{sfx}{f2}/{_plain_roman(tgt, mode)}'
    deg, alt = _degree_of(root, mode)
    num = ROMAN[deg]
    lower = q in ('m', 'd', 'm7', 'h7', 'o7')
    num = num.lower() if lower else num
    acc = {-1: 'b', 0: '', 1: '#'}[alt]
    sfx = {'M': '', 'm': '', 'd': '°', 'A': '+', '7': '', 'm7': '', 'M7': 'M',
           'h7': 'ø', 'o7': '°'}[q]
    if q in SEVENTHS and inv == 0 and q != '7':
        fig = '7'
    return f'{acc}{num}{sfx}{fig}'


def _plain_roman(rel: int, mode: str) -> str:
    scale = scale_of(mode)
    deg, alt = _degree_of(rel, mode)
    # 目标音级的调内三和弦性质决定大小写
    third = (scale[(deg + 2) % 7] - scale[deg]) % 12
    num = ROMAN[deg]
    return ('b' if alt < 0 else '#' if alt > 0 else '') + (num if third == 4 else num.lower())


def chord_key(ch: Chord) -> str:
    return f'{ch[0]}:{ch[1]}:{ch[2]}'


def parse_chord_key(s: str) -> Chord:
    a, b, c = s.split(':')
    return int(a), b, int(c)


# ------------------------------------------------------------------ 拼写
def tonic_letter_pc(tonic_name: str) -> Tuple[str, int]:
    """'G' / 'F#' / 'Bb' → (字母, 主音 pc)。"""
    t = tonic_name.strip().replace('♯', '#').replace('♭', 'b').replace('-', 'b')
    letter = t[0].upper()
    alt = t[1:].count('#') - t[1:].count('b')
    return letter, (LETTER_PC[letter] + alt) % 12


def _letter_shift(letter: str, steps: int) -> str:
    return LETTERS[(LETTERS.index(letter) + steps) % 7]


def spell(letter: str, pc: int) -> str:
    """字母 + 目标 pc → 音名（含升降），如 ('F', 6) → 'F#'。"""
    alt = (pc - LETTER_PC[letter]) % 12
    if alt > 6:
        alt -= 12
    return letter + ('#' * alt if alt > 0 else 'b' * (-alt))


def root_letter(ch_root: int, quality: str, tonic: str, mode: str) -> str:
    """和弦根音的字母：调内音级字母；副属/副导按目标音级推字母。"""
    t_letter, _ = tonic_letter_pc(tonic)
    if not _is_diatonic_chord(ch_root, quality, mode) and (ch_root != 0 or quality == '7'):
        if quality in ('M', '7'):
            tgt = (ch_root + 5) % 12
            if tgt in scale_of(mode):
                deg, _ = _degree_of(tgt, mode)
                return _letter_shift(t_letter, deg + 4)
        if quality in ('d', 'o7', 'h7'):
            tgt = (ch_root + 1) % 12
            if tgt in scale_of(mode):
                deg, _ = _degree_of(tgt, mode)
                return _letter_shift(t_letter, deg - 1)
    deg, _ = _degree_of(ch_root, mode)
    return _letter_shift(t_letter, deg)


def chord_tone_names(ch: Chord, tonic: str, mode: str) -> Dict[int, str]:
    """和弦各音 rel pc → 音名（按三度叠置推字母，保证 F# 不会写成 Gb）。"""
    root, q, _inv = ch
    _, tpc = tonic_letter_pc(tonic)
    rl = root_letter(root, q, tonic, mode)
    out = {}
    for k, iv in enumerate(QUALITIES[q]):
        rel = (root + iv) % 12
        out[rel] = spell(_letter_shift(rl, 2 * k), (rel + tpc) % 12)
    return out


def scale_names(tonic: str, mode: str) -> Dict[int, str]:
    """调内音级 rel pc → 音名（小调另含升 6、升 7）。"""
    t_letter, tpc = tonic_letter_pc(tonic)
    out = {}
    for k, rel in enumerate(scale_of(mode)):
        out[rel] = spell(_letter_shift(t_letter, k), (rel + tpc) % 12)
    if mode == 'minor':
        out[11] = spell(_letter_shift(t_letter, 6), (11 + tpc) % 12)
        out[9] = spell(_letter_shift(t_letter, 5), (9 + tpc) % 12)
    return out


def name_with_octave(name: str, midi: int) -> str:
    """音名 + MIDI → 带八度音名（B# / Cb 跨八度修正）。"""
    letter = name[0]
    alt = name[1:].count('#') - name[1:].count('b')
    octave = (midi - LETTER_PC[letter] - alt) // 12 - 1
    return f'{name}{octave}'


KEY_SHARPS_MAJOR = {'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5, 'F#': 6, 'C#': 7,
                    'F': -1, 'Bb': -2, 'Eb': -3, 'Ab': -4, 'Db': -5, 'Gb': -6, 'Cb': -7}
KEY_SHARPS_MINOR = {'A': 0, 'E': 1, 'B': 2, 'F#': 3, 'C#': 4, 'G#': 5, 'D#': 6, 'A#': 7,
                    'D': -1, 'G': -2, 'C': -3, 'F': -4, 'Bb': -5, 'Eb': -6, 'Ab': -7}


def tonic_from_sharps(sharps: int, mode: str) -> str:
    table = KEY_SHARPS_MAJOR if mode == 'major' else KEY_SHARPS_MINOR
    for k, v in table.items():
        if v == sharps:
            return k
    return 'C' if mode == 'major' else 'A'
