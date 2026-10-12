# -*- coding: utf-8 -*-
"""和弦进行顾问（#8 竞品差距：对标 Hooktheory / Captain Chords）。

纯和声逻辑，零外部依赖，确定性、可审计、可复现——契合「确定性内核 + 规则校验
+ 可复现 + 可审计」护城河（不调 LLM、零训练数据、不改符号谱正确性）。

能力：
  - 罗马数字和弦解析（大小写=三和弦性质；°/+/7/M7/m7/ø 等七和弦与转位标记）
  - 常用进行库（按 风格/情绪 标签分类：流行 / 摇滚 / 爵士 / 古典 / 史诗 / 舞曲…）
  - 进行合法性校验：调内合法性、根音运动分类、功能圈、导音解决、终止式
  - 四声部（SATB）实现 + 平行五八度/隐伏五八度检测（规则引擎，可审计）
  - 转位与声部引导的确定性文字解释

所有函数均为确定性（无随机、无 LLM）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 基础常量
# --------------------------------------------------------------------------
SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
FLAT = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
# 字母 → 自然音级（半音，不含变音记号）
LETTER_PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
LETTERS = ['C', 'D', 'E', 'F', 'G', 'A', 'B']  # 音名圈（叠三度用）

# 大调 / 自然小调 / 和声小调 音阶（相对主音的半音）
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
NAT_MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]
HARM_MINOR_SCALE = [0, 2, 3, 5, 7, 8, 11]

# 调号变音记号数（升/降），按五度圈。index = 升降数；正=升，负=降。
KEY_SIG_SHARPS = {0: {}, 1: {'F'}, 2: {'F', 'C'}, 3: {'F', 'C', 'G'},
                  4: {'F', 'C', 'G', 'D'}, 5: {'F', 'C', 'G', 'D', 'A'},
                  6: {'F', 'C', 'G', 'D', 'A', 'E'}, 7: {'F', 'C', 'G', 'D', 'A', 'E', 'B'}}
KEY_SIG_FLATS = {-1: {'B'}, -2: {'B', 'E'}, -3: {'B', 'E', 'A'},
                 -4: {'B', 'E', 'A', 'D'}, -5: {'B', 'E', 'A', 'D', 'G'},
                 -6: {'B', 'E', 'A', 'D', 'G', 'C'}, -7: {'B', 'E', 'A', 'D', 'G', 'C', 'F'}}

# 罗马数字 → 音级（1..7）
_ROMAN_DEGREE = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7}

# SATB 合理音域（MIDI）
RANGE = {'S': (60, 79), 'A': (57, 74), 'T': (55, 69), 'B': (43, 64)}


# --------------------------------------------------------------------------
# 音名 / 调 解析
# --------------------------------------------------------------------------
def pitch_class(name: str) -> int:
    """音名（支持 #/b） → 音级数 0..11。"""
    n = name.strip().capitalize()
    m = re.match(r'^([A-G])([#b]*)$', n)
    if not m:
        raise ValueError(f'非法音名：{name}')
    base = LETTER_PC[m.group(1)]
    acc = m.group(2)
    alt = acc.count('#') - acc.count('b')
    return (base + alt) % 12


def _format_acc(acc: int) -> str:
    return '#' * acc if acc > 0 else 'b' * (-acc)


def _pc_to_letter(pc: int, letter: str) -> str:
    """把音级数 pc 用指定字母拼写（自动选变音记号，取 |acc| 最小）。"""
    nat = LETTER_PC[letter]
    diff = (pc - nat) % 12
    # 把 diff 归一到 [-6, +6]
    if diff > 6:
        diff -= 12
    return letter + _format_acc(diff)


def parse_key(arg: str) -> Tuple[int, str, Optional[str]]:
    """从自由文本推断 (主音pc, mode, 原始调名)。

    识别 C / Am / D major / bB / f#小调 / Dm 等。找不到主音 → 默认 ('C', 'major', None)。
    """
    if not arg:
        return 0, 'major', None
    s = arg.strip()
    mode = None
    note = None
    # 1) 显式后缀：<音名><maj|major|min|m>（音名须是词边界，避免误吞 jazz 里的 a）
    m = re.search(r'(?:^|\s)([A-Ga-g][#b]*)(maj|major|min|m)\b', s)
    if m:
        note = m.group(1).capitalize()
        suf = m.group(2)
        mode = 'minor' if suf in ('min', 'm') else 'major'
    else:
        # 2) 关键词（小调 / 大调 / minor / major）
        if re.search(r'小调|minor|小', s, re.I):
            mode = 'minor'
        elif re.search(r'大调|major|大', s, re.I):
            mode = 'major'
        m2 = re.search(r'(?:^|\s)([A-Ga-g][#b]*)\b', s)
        if m2:
            note = m2.group(1).capitalize()
    if mode is None:
        mode = 'major'
    if not note:
        return 0, mode, None
    try:
        pc = pitch_class(note)
    except ValueError:
        return 0, mode, None
    label = f'{note}{"" if mode == "major" else " 小调"}'
    return pc, mode, label


# --------------------------------------------------------------------------
# 罗马数字解析
# --------------------------------------------------------------------------
def _roman_to_degree(rn: str) -> int:
    key = rn.upper()
    # 去掉重复字母干扰：标准罗马数字只由 I V X L C D M 组成
    if key not in _ROMAN_DEGREE:
        # 容忍写法如 'II' 已覆盖；其余罕见写法按首字母映射
        key = re.sub(r'[^IVXLCDM]', '', key)
        key = key[:2] if key else 'I'
    return _ROMAN_DEGREE.get(key, 1)


def parse_roman(token: str) -> Dict:
    """解析一个罗马数字和弦标记 → 结构化字典。

    例：'V7' / 'ii°' / 'I64' / 'vii°7' / 'bII' / '#iv' / 'ii6/5' / 'V65'
    返回：{degree, lower, accid, quality, seventh, inversion_figure, bass_degree, raw}
      quality: 'maj' | 'min' | 'dim' | 'aug'
      seventh: None | 'dom' | 'maj' | 'min' | 'dim' | 'half'
      inversion_figure: '' | '6' | '64' | '65' | '43' | '42'（数字标记）
    """
    raw = token.strip()
    t = raw
    # 借用和弦变音记号（前导）
    accid = 0
    while t and t[0] in ('b', '#'):
        accid += 1 if t[0] == '#' else -1
        t = t[1:]
    if not t:
        raise ValueError(f'空和弦标记：{raw}')

    # 转位：显式 bass 写法 和弦/bass音（仅当 bass 端是合法音名时；
    # 否则 '/数字' 当作转位数字的一部分，如 6/5）
    explicit_bass = None
    if '/' in t:
        t, bpart = t.split('/', 1)
        bpart = bpart.strip()
        try:
            pitch_class(bpart)
            explicit_bass = bpart
        except ValueError:
            t = t + '/' + bpart  # 还原，并入转位数字（如 ii6/5）

    # 罗马数字核心（大小写敏感，但只取合法的罗马字母）
    core = re.match(r'[IVXLCDMivxlcdm]+', t)
    if not core:
        raise ValueError(f'无法解析罗马数字：{raw}')
    core_s = core.group(0)
    rest = t[core.end():]
    degree = _roman_to_degree(core_s)
    lower = core_s[0].islower()
    is_minor_case = lower

    # 七和弦 / 三和弦性质后缀
    seventh = None
    quality = 'maj' if not is_minor_case else 'min'
    # 处理 rest 中的符号
    r = rest
    # 完全/半减七、减三和弦
    if '°' in r or 'dim' in r or r.endswith('o'):
        quality = 'dim'
    if '+' in r or 'aug' in r:
        quality = 'aug'
    # 七和弦
    if 'maj7' in r or 'M7' in r:
        seventh = 'maj'; quality = 'maj'
    elif 'm7b5' in r or 'ø' in r:
        seventh = 'half'; quality = 'dim'
    elif 'dim7' in r or (quality == 'dim' and '7' in r):
        seventh = 'dim'; quality = 'dim'
    elif 'm7' in r:
        seventh = 'min'; quality = 'min'
    elif re.search(r'(?<![A-Za-z])7', r):
        # 普通 7：大三和弦上的属七 → dom；小三和弦上的小七 → min
        if quality == 'dim':
            seventh = 'dim'
        elif is_minor_case:
            seventh = 'min'; quality = 'min'
        else:
            seventh = 'dom'; quality = 'maj'

    # 转位数字标记（rest 中剩余的数字，忽略 '/' 如 6/5）
    fig_digits = re.findall(r'\d+', r)
    inversion_figure = ''.join(fig_digits) if fig_digits else ''
    # 7 和弦转位标记（65/43/42）：小三和弦上的小七 → min，其余按属七
    if inversion_figure in ('65', '43', '42') and seventh is None:
        seventh = 'min' if is_minor_case else 'dom'
        quality = 'min' if is_minor_case else 'maj'
    if explicit_bass:
        # 显式低音覆盖数字标记（实际 bass 唱名由 explicit_bass 决定）
        inversion_figure = ''

    # bass 音级（用于决定四声部 bass 唱名）
    bass_degree = degree
    return {
        'degree': degree, 'lower': is_minor_case, 'accid': accid,
        'quality': quality, 'seventh': seventh,
        'inversion_figure': inversion_figure,
        'explicit_bass': explicit_bass, 'bass_degree': bass_degree,
        'raw': raw,
    }


# --------------------------------------------------------------------------
# 和弦拼写
# --------------------------------------------------------------------------
def _scale_for(mode: str, use_harmonic: bool) -> List[int]:
    if mode == 'minor':
        return HARM_MINOR_SCALE if use_harmonic else NAT_MINOR_SCALE
    return MAJOR_SCALE


def spell_chord(tonic_pc: int, mode: str, parsed: Dict,
                harmonic: bool = True) -> List[Tuple[str, int]]:
    """为某个罗马数字和弦生成正确拼写（音名, 音级pc）。

    叠三度保证同度和弦用不同字母（避免 enharmonic 错误），再用半音距离
    反推每个音的变音记号 → 调内/借用和弦都能正确拼写。
    """
    d = parsed['degree']
    accid = parsed['accid']
    scale = _scale_for(mode, harmonic)
    tonic_letter = _canonical_tonic(tonic_pc, mode)
    tletter_idx = LETTERS.index(tonic_letter)

    root_int = scale[(d - 1) % 7]
    if mode == 'minor' and accid < 0:
        # 小调中降号前缀 → 平行大调借用（bVI/bIII/bVII/bII 等），
        # 而不是相对自然小调再降半音（那样会得到 Fb/Eb 等错误根音）。
        root_int = MAJOR_SCALE[(d - 1) % 7]
    root_pc = (tonic_pc + root_int + accid) % 12
    root_letter_idx = (tletter_idx + (d - 1)) % 7

    third_int = 3 if parsed['quality'] in ('min', 'dim', 'half') else 4
    fifth_int = 8 if parsed['quality'] == 'aug' else (6 if parsed['quality'] == 'dim' else 7)
    seventh_int = {None: None, 'dom': 10, 'maj': 11, 'min': 10,
                   'dim': 9, 'half': 10}[parsed['seventh']]

    tones: List[Tuple[int, int]] = []  # (letter_idx, target_pc)
    tones.append((root_letter_idx, root_pc))
    tones.append(((root_letter_idx + 2) % 7, (root_pc + third_int) % 12))
    tones.append(((root_letter_idx + 4) % 7, (root_pc + fifth_int) % 12))
    if seventh_int is not None:
        tones.append(((root_letter_idx + 6) % 7, (root_pc + seventh_int) % 12))

    out = [(_pc_to_letter(pc, LETTERS[lidx]), pc) for (lidx, pc) in tones]
    return out


def _canonical_major_tonic(pc: int) -> str:
    """按标准调号选主音字母（升/降），避免 G## 之类的丑拼写。"""
    sharp_map = {7: 'G', 2: 'D', 9: 'A', 4: 'E', 11: 'B', 6: 'F#', 1: 'C#'}
    flat_map = {5: 'F', 10: 'Bb', 3: 'Eb', 8: 'Ab', 1: 'Db', 6: 'Gb', 11: 'Cb'}
    if pc in sharp_map and pc in flat_map:
        # 同音异名：取更常用拼写（Db 优于 C#；F# 优于 Gb；B 优于 Cb）
        if pc == 1:
            return 'Db'
        if pc == 6:
            return 'F#'
        if pc == 11:
            return 'B'
    if pc in sharp_map:
        return sharp_map[pc]
    if pc in flat_map:
        return flat_map[pc]
    return 'C'


#: 标准小调调号拼写（Cm, C#m, Dm, Ebm, Em, Fm, F#m, Gm, G#m, Am, Bbm, Bm）
#: → 主音字母（本函数只关心字母，不关心升降号）
_MINOR_TONIC_LETTER = {0: 'C', 1: 'C', 2: 'D', 3: 'E', 4: 'E', 5: 'F',
                       6: 'F', 7: 'G', 8: 'G', 9: 'A', 10: 'B', 11: 'B'}


def _canonical_tonic(tonic_pc: int, mode: str) -> str:
    """主音字母：大调用标准调号拼写；小调用标准小调调号拼写。

    不能经由「关系大调名字回推字母」：异名偏好会把 Eb 小调的关系大调
    Gb 拼成 F#，回推后得到 D 而非 E。
    """
    if mode == 'minor':
        return _MINOR_TONIC_LETTER[tonic_pc % 12]
    return _canonical_major_tonic(tonic_pc)


# --------------------------------------------------------------------------
# 功能分析
# --------------------------------------------------------------------------
def function_label(parsed: Dict, mode: str) -> str:
    """功能和声标签：主(T) / 下属(SD) / 属(D)。"""
    d = parsed['degree']
    if mode == 'major':
        if d in (1, 3, 6):
            return '主(T)'
        if d in (2, 4):
            return '下属(SD)'
        if d in (5, 7):
            return '属(D)'
    else:  # minor
        if d in (1, 3, 6):
            return '主(T)'
        if d in (2, 4):
            return '下属(SD)'
        if d in (5, 7):
            return '属(D)'
    return '—'


# --------------------------------------------------------------------------
# 根音运动分类
# --------------------------------------------------------------------------
def root_motion_label(from_pc: int, to_pc: int) -> str:
    dist = (to_pc - from_pc) % 12
    table = {
        0: '同根（持续音）', 1: '半音上行', 11: '半音下行',
        2: '全音上行', 10: '全音下行',
        5: '下行五度 / 上行四度（最强）', 7: '上行五度 / 下行四度（强）',
        4: '上行大三度', 8: '下行大三度',
        3: '上行小三度', 9: '下行小三度',
        6: '三全音（需解决）',
    }
    return table.get(dist, f'{dist} 半音')


# --------------------------------------------------------------------------
# 常用进行库（按标签分类）
# --------------------------------------------------------------------------
PROGRESSIONS: Dict[str, List[Tuple[str, str, str]]] = {
    'pop': [
        ('万能轴式', 'I V vi IV', '当代流行最常用；明亮→稳定，循环感强'),
        ('五零年代', 'I vi IV V', '经典抒情；柔和下行'),
        ('vi 起头', 'vi IV I V', '以六级起头更忧郁、更有记忆点'),
        ('转折', 'I V vi iii IV I IV V', '主歌→预副歌→副歌的标准叙事弧'),
    ],
    'rock': [
        ('12 小节蓝调', 'I I I I IV IV I I V IV I V', '布鲁斯/摇滚根基'),
        ('力量进行', 'I bVII IV', '硬摇滚常见的下行小三度色彩'),
        ('小调摇滚', 'i bVI bIII bVII', '小调摇滚/金属的暗色进行'),
    ],
    'jazz': [
        ('ii–V–I', 'ii V I', '爵士最基石；属功能解决到主'),
        ('节奏变化 B', 'I vi ii V', 'Gershwin 式标准 turnaround'),
        ('iii–vi–ii–V', 'iii vi ii V I', '上行引导，smooth 收束'),
        ('循环', 'I IV iii vi ii V I', '一整圈五度循环，最顺滑'),
    ],
    'classical': [
        ('正格终止', 'I IV V I', '古典最基础；下属→属→主'),
        ('变格终止', 'IV I', '「阿门」式收束'),
        ('完全终止', 'ii V I', '属七解决，终止感最强'),
        ('阻碍终止', 'V vi', '属进行到六级（主替代），悬念'),
    ],
    'epic': [
        ('小调史诗', 'i bVI bIII bVII', '电影预告片/史诗感'),
        ('进行曲', 'i iv V i', '小调进行曲，坚定'),
        (' triumph', 'i VI III VII', '胜利主题常用'),
    ],
    'edm': [
        ('小调舞曲', 'i bVI bIII bVII', 'EDM/另类流行的暗色 drop'),
        ('副歌循环', 'VI III bVII i', '能量段循环'),
    ],
    'ballad': [
        ('抒情', 'I V vi IV', '慢歌万能'),
        ('叙事', 'I ii iii IV', '全上行，温暖推进'),
    ],
    'worship': [
        ('敬拜', 'I V vi IV', '当代敬拜标准'),
        ('展开', 'I ii IV V', '开阔、上行'),
    ],
}


def _tag_for_text(arg: str) -> str:
    a = (arg or '').lower()
    mapping = [
        ('jazz', 'jazz'), ('爵士', 'jazz'),
        ('rock', 'rock'), ('摇滚', 'rock'), ('蓝调', 'rock'), ('blues', 'rock'),
        ('古典', 'classical'), ('classical', 'classical'), ('巴洛克', 'classical'),
        ('史诗', 'epic'), ('epic', 'epic'), ('电影', 'epic'), ('游戏', 'epic'),
        ('edm', 'edm'), ('舞曲', 'edm'), ('电子', 'edm'),
        ('ballad', 'ballad'), ('抒情', 'ballad'), ('慢歌', 'ballad'),
        ('worship', 'worship'), ('敬拜', 'worship'), ('赞美', 'worship'),
        ('pop', 'pop'), ('流行', 'pop'), ('情歌', 'pop'), ('歌', 'pop'),
    ]
    for kw, tag in mapping:
        if kw in a:
            return tag
    return 'pop'


# --------------------------------------------------------------------------
# 进行解析 + 校验
# --------------------------------------------------------------------------
def _split_progression(text: str) -> List[str]:
    # 支持空格 / - / → / > 分隔
    parts = re.split(r'[\s\-→>]+', text.strip())
    return [p for p in parts if p]


_ROMAN_TOKEN_RE = re.compile(r'^[b#]?[IVXLCDMivxlcdm]+[°+oø]?'
                             r'(maj7|M7|m7b5|dim7|m7|7|maj|min|dim|aug)?'
                             r'(\d{1,2})?(/[A-Ga-g][#b]?)?$')


def looks_like_progression(text: str) -> bool:
    parts = _split_progression(text)
    if len(parts) < 2:
        return False
    hits = 0
    for p in parts:
        if _ROMAN_TOKEN_RE.match(p.strip()) and re.search(r'[IVXLCDMivxlcdm]', p):
            hits += 1
    return hits >= max(2, len(parts) - 1)


def analyze_progression(tokens: List[str], tonic_pc: int, mode: str) -> Dict:
    """对一串罗马数字和弦做完整分析（确定性）。"""
    parsed = []
    issues: List[Tuple[str, str]] = []  # (level, text) level: error|warn|info
    for tok in tokens:
        try:
            p = parse_roman(tok)
        except ValueError as e:
            issues.append(('error', f'「{tok}」无法解析：{e}'))
            continue
        # 借用和弦/调内合法性提示
        p['spelling'] = spell_chord(tonic_pc, mode, p, harmonic=True)
        p['function'] = function_label(p, mode)
        # 根音 pc
        root_letter, root_pc = p['spelling'][0]
        p['root_pc'] = root_pc
        parsed.append(p)

    # 根音运动序列
    motions = []
    for i in range(1, len(parsed)):
        m = root_motion_label(parsed[i - 1]['root_pc'], parsed[i]['root_pc'])
        motions.append(m)

    # 导音解决检查（大调 7 级 / 小调 7 级音符应上行到主）
    scale = _scale_for(mode, True)
    leading_pc = (tonic_pc + scale[6]) % 12
    for i in range(len(parsed) - 1):
        chord = parsed[i]
        has_leading = any(pc == leading_pc for (_n, pc) in chord['spelling'])
        if has_leading:
            nxt = parsed[i + 1]
            nxt_has_tonic = any(
                pc == tonic_pc for (_n, pc) in nxt['spelling'])
            if not nxt_has_tonic:
                issues.append(('warn',
                               f'{chord["raw"]} 含导音（{SHARP[leading_pc]}），'
                               f'下一和弦 {nxt["raw"]} 未回到主音，导音应上行解决'))

    # 终止式判断
    if parsed:
        last = parsed[-1]
        if last['degree'] == 1:
            if len(parsed) >= 2 and parsed[-2]['degree'] in (5, 7):
                issues.append(('info', '正格终止（属→主），终止感强 ✓'))
            elif len(parsed) >= 2 and parsed[-2]['degree'] == 4:
                issues.append(('info', '变格终止（下属→主），柔和 ✓'))
            else:
                issues.append(('info', '以主和弦收束 ✓'))
        else:
            issues.append(('warn', f'未以主和弦（I/{"i" if mode=="minor" else "I"}）'
                                   f'收束，终止感弱'))

    # 无属功能却以主收束 → 提示缺少属
    if parsed and parsed[-1]['degree'] == 1:
        has_dom = any(p['degree'] in (5, 7) for p in parsed[:-1])
        if not has_dom and len(parsed) >= 3:
            issues.append(('warn', '进行中缺少属功能（V/vii°），张力不足'))

    return {
        'parsed': parsed, 'motions': motions, 'issues': issues,
        'tonic_pc': tonic_pc, 'mode': mode,
    }


# --------------------------------------------------------------------------
# SATB 实现 + 平行检测
# --------------------------------------------------------------------------
def _choose_bass(parsed: Dict) -> Tuple[int, str]:
    """根据转位标记决定 bass 音（返回 bass_pc, bass音名）。"""
    if parsed.get('explicit_bass'):
        try:
            pc = pitch_class(parsed['explicit_bass'])
            return pc, parsed['explicit_bass']
        except ValueError:
            pass
    fig = parsed.get('inversion_figure', '') or ''
    spelling = parsed['spelling']  # [(name, pc), ...] root first
    root = spelling[0]
    third = spelling[1] if len(spelling) > 1 else root
    fifth = spelling[2] if len(spelling) > 2 else root
    sev = spelling[3] if len(spelling) > 3 else root
    if fig in ('6', '65'):
        # 第一转位（含属七 6/5）：三音在低声部
        return third[1], third[0]
    if fig in ('64', '43'):
        return fifth[1], fifth[0]
    if fig in ('42',):
        return sev[1], sev[0]
    # 原位 / 未知标记：根音在低声部
    return root[1], root[0]


def _oct_above(pc: int, lower_midi: int, voice: str) -> int:
    """取 >= lower_midi+1 且落在 voice 音域内、音级为 pc 的最低 MIDI。"""
    lo, hi = RANGE[voice]
    for k in range(0, 10):
        cand = pc + 12 * k
        if cand > lower_midi and lo <= cand <= hi:
            return cand
    # 兜底：放宽「高于 lower_midi」约束，取音域内该音级的最低八度
    cands = [pc + 12 * k for k in range(0, 10) if lo <= pc + 12 * k <= hi]
    if cands:
        return min(cands, key=lambda c: abs(c - lower_midi))
    return max(lo, min(hi, lo + ((pc - lo) % 12)))


def _voic_upper(upper_pcs: List[int], bass_midi: int) -> List[int]:
    """把 3 个和弦音分配到 S/A/T（确定性：在 bass 之上做紧凑 close voicing）。"""
    tones = sorted(upper_pcs)
    t_midi = _oct_above(tones[0], bass_midi, 'T')
    a_midi = _oct_above(tones[1], t_midi, 'A')
    s_midi = _oct_above(tones[2], a_midi, 'S')
    return [s_midi, a_midi, t_midi]


def realize_satb(analysis: Dict, tonic_pc: int, mode: str) -> Dict:
    """为分析中的进行生成确定性四声部（SATB）实现，并检测平行五八度。"""
    parsed = analysis['parsed']
    prev_bass = None
    prev_real = None
    realizations = []
    parallels = []

    for idx, p in enumerate(parsed):
        bass_pc, _bass_name = _choose_bass(p)
        pcs = [pc for (_n, pc) in p['spelling']]
        # 上三声部 = 全部和弦音去掉一个 bass 音（三和弦则重复根音凑满 3 个）
        upper = [pc for pc in pcs if pc != bass_pc]
        while len(upper) < 3:
            upper.append(bass_pc)
        upper = upper[:3]
        if prev_bass is None:
            b_midi = _place_in_range(bass_pc, 'B', None, prefer_low=True)
        else:
            b_midi = _place_in_range(bass_pc, 'B', prev_bass)
        sat = _voic_upper(upper, b_midi)
        voices = [sat[0], sat[1], sat[2], b_midi]  # S A T B
        if prev_bass is not None:
            prev_voices = prev_real['voices']
            _detect_parallels(prev_voices, voices, idx, p['raw'], parallels)
        realizations.append({'chord': p['raw'], 'voices': voices,
                             'names': _midi_names(voices)})
        prev_bass = b_midi
        prev_real = realizations[-1]

    return {'realizations': realizations, 'parallels': parallels}


def _place_in_range(pc: int, voice: str, prev_midi: Optional[int],
                    prefer_low: bool = False) -> int:
    lo, hi = RANGE[voice]
    # k 范围按音域推导（固定 -3..3 覆盖不了低音声部的低音级：
    # 如 pc=0–6 时 pc+36 仍低于 B 声部下限 43）
    k_lo = (lo - pc) // 12 - 1
    k_hi = (hi - pc) // 12 + 1
    candidates = [pc + 12 * k for k in range(k_lo, k_hi + 1)]
    cand = [c for c in candidates if lo <= c <= hi]
    if not cand:
        # 音域均 ≥ 14 半音，理论上不可达；兜底取音域内 ≥ 下限的最近同音级
        return max(lo, min(hi, lo + ((pc - lo) % 12)))
    if prev_midi is None:
        if prefer_low:
            return cand[0]  # 最低合法八度（低音声部用）
        # 取中部
        cand.sort(key=lambda c: abs(c - (lo + hi) / 2))
        return cand[0]
    cand.sort(key=lambda c: abs(c - prev_midi))
    return cand[0]


def _midi_names(midis: List[int]) -> List[str]:
    names = []
    for m in midis:
        names.append(f'{SHARP[m % 12]}{m // 12 - 1}')
    return names


def _detect_parallels(prev, cur, idx, chord, out):
    # 任两声部之间：检查平行 P5 / P8（前后音程相同且同向）
    labels = ['S', 'A', 'T', 'B']
    for i in range(4):
        for j in range(i + 1, 4):
            d1 = (prev[i] - prev[j]) % 12
            d2 = (cur[i] - cur[j]) % 12
            # 同向（都上行或都下行）
            same_dir = (cur[i] - prev[i]) * (cur[j] - prev[j]) > 0
            # 平行要求前后音程一致：纯五→纯八这类「同向但不平行」不算
            if same_dir and d1 == d2 and d1 in (7, 0):
                kind = '平行八度' if d2 == 0 else '平行五度'
                out.append({'bar': idx + 1, 'chord': chord,
                            'voices': f'{labels[i]}-{labels[j]}', 'kind': kind})


# --------------------------------------------------------------------------
# 报告生成
# --------------------------------------------------------------------------
def _format_chord_line(p: Dict) -> str:
    names = ' '.join(n for (n, _pc) in p['spelling'])
    fig = p.get('inversion_figure') or ''
    inv = {'6': '（第一转位）', '64': '（第二转位）', '65': '（属七一转）',
           '43': '（属七二转）', '42': '（属七三转）'}.get(fig, '（原位）')
    return f"{p['raw']}{inv}  [{names}]  {p['function']}"


def _strip_leading_key(arg: str) -> Tuple[str, bool]:
    """剥离前导调性标记（如 "Am i bVI..." 的 "Am" / "C major I..." 的 "C major"）。

    返回 (剩余文本, 是否检测到调性)。避免把调名当成和弦标记解析。
    """
    m = re.match(r'^\s*([A-Ga-g][#b]*)(maj|major|min|m)?(?=\s|$|,)', arg)
    if not m:
        return arg.strip(), False
    end = m.end()
    rest = arg[end:]
    rest = re.sub(r'^\s*(大调|小调|major|minor|maj|min|小|大)\b', '', rest,
                  flags=re.I).strip()
    return rest, True


def advisor(arg: str) -> str:
    """主入口：根据自由文本 arg 生成和弦顾问报告（markdown）。"""
    # 先剥离前导调性（若有），避免把调名当和弦解析
    prog_text, had_key = _strip_leading_key(arg)
    tonic_pc, mode, key_label = parse_key(arg)
    key_name = key_label or ('C' if mode == 'major' else 'A 小调')

    lines: List[str] = []
    lines.append(f'## 🎼 和弦进行顾问（确定性规则引擎 · 不调 LLM · 可审计）')
    lines.append(f'')
    lines.append(f'**调性**：{key_name}（主音 {SHARP[tonic_pc % 12]}）'
                 f'　**调式**：{"大调" if mode=="major" else "小调"}')
    lines.append('')

    # 判断是「校验进行」还是「建议进行」
    if looks_like_progression(prog_text):
        tokens = _split_progression(prog_text)
        lines.append(f'### 一、你提供的进行：`{" ".join(tokens)}`')
        lines.append('')
        analysis = analyze_progression(tokens, tonic_pc, mode)
        lines.append('| # | 和弦 | 拼写 | 功能 |')
        lines.append('|---|------|------|------|')
        for i, p in enumerate(analysis['parsed'], 1):
            names = ' '.join(n for (n, _pc) in p['spelling'])
            lines.append(f'| {i} | {p["raw"]} | {names} | {p["function"]} |')
        lines.append('')
        # 根音运动
        lines.append('### 二、根音运动')
        lines.append('')
        for i, m in enumerate(analysis['motions'], 1):
            lines.append(f'- 第 {i}→{i+1} 步：{m}')
        lines.append('')
        # 校验
        lines.append('### 三、规则校验')
        lines.append('')
        has_err = False
        for level, text in analysis['issues']:
            icon = {'error': '❌', 'warn': '⚠️', 'info': '✅'}.get(level, '•')
            if level == 'error':
                has_err = True
            lines.append(f'- {icon} {text}')
        if not analysis['issues']:
            lines.append('- ✅ 未发现明显问题')
        lines.append('')
        # SATB 实现 + 平行检测
        lines.append('### 四、四声部（SATB）实现与声部引导')
        lines.append('')
        if analysis['parsed']:
            real = realize_satb(analysis, tonic_pc, mode)
            lines.append('| 和弦 | S | A | T | B |')
            lines.append('|---|---|---|---|---|')
            for r in real['realizations']:
                s, a, t, b = r['names']
                lines.append(f'| {r["chord"]} | {s} | {a} | {t} | {b} |')
            lines.append('')
            if real['parallels']:
                lines.append('⚠️ **检测到声部引导问题**（规则引擎复检）：')
                for pr in real['parallels']:
                    lines.append(f'- 第{pr["bar"]}小节 {pr["chord"]}：'
                                 f'{pr["voices"]} 声部 {pr["kind"]}')
                lines.append('建议：调整内声部（保持一个声部不动 / 用级进代替跳进）'
                             '以消解平行。')
            else:
                lines.append('✅ 此实现未出现平行五度/八度（声部引导干净）。')
            lines.append('')
        # 转位/声部引导解释
        lines.append('### 五、转位与声部引导建议')
        lines.append('')
        lines.append(_voice_leading_explanation(analysis, mode))
    else:
        # 推荐进行
        tag = _tag_for_text(arg)
        lines.append(f'### 一、{tag.upper()} 风格常用进行（按标签推荐）')
        lines.append('')
        for label, prog, desc in PROGRESSIONS.get(tag, PROGRESSIONS['pop']):
            lines.append(f'- **{label}**：`{prog}` —— {desc}')
        lines.append('')
        lines.append('### 二、想深入？发一个进行让我校验')
        lines.append('')
        lines.append('例如：')
        lines.append('- `/chords I V vi IV` —— 校验这条流行轴式')
        lines.append('- `/chords ii V I` （爵士）')
        lines.append('- `/chords Am i bVI bIII bVII` —— 小调暗色进行')
        lines.append('')
        lines.append('我会给出：调内合法性、根音运动、功能圈、导音解决、'
                     '终止式，以及四声部实现 + 平行五八度复检。')
        lines.append('')
        # 顺手演示一个默认例子
        lines.append('### 三、默认示范：`I V vi IV`（C 大调）')
        lines.append('')
        demo = analyze_progression(['I', 'V', 'vi', 'IV'], tonic_pc, mode)
        lines.append('| # | 和弦 | 拼写 | 功能 |')
        lines.append('|---|------|------|------|')
        for i, p in enumerate(demo['parsed'], 1):
            names = ' '.join(n for (n, _pc) in p['spelling'])
            lines.append(f'| {i} | {p["raw"]} | {names} | {p["function"]} |')
        lines.append('')
        lines.append(_voice_leading_explanation(demo, mode))

    lines.append('')
    lines.append('---')
    lines.append('> 本顾问为**确定性规则引擎**：纯和声逻辑、零训练数据、不调 LLM，'
                 '结果可复现、可审计（每一项和弦拼写/功能/声部引导均可溯源）。')
    return '\n'.join(lines)


def _voice_leading_explanation(analysis: Dict, mode: str) -> str:
    out = []
    out.append('和弦的「原位/转位」决定低音唱名，直接影响声部引导的平滑度：')
    out.append('- **原位（5/3）**：低音=根音，音响最稳定，常用于强拍/终止；'
               '但连续原位易产生平行五度。')
    out.append('- **第一转位（6）**：低音=三音，柔和、弱功能，'
               '适合弱拍或连接两个原位和弦（如 I–V⁶–I 的 passing 用法）。')
    out.append('- **第二转位（6/4）**：低音=五音，不稳定，'
               '多用于「终止 6/4」（强拍）→ 属（弱拍）的装饰性过渡。')
    out.append('- **属七及转位（6⁵/4³/4²）**：属七天然含导音与下属音，'
               '解决到主和弦时：导音上行、七音下行级进，声部引导最自然。')
    out.append('')
    out.append('通用声部引导规则（校验依据）：')
    out.append('1. 避免**平行完全五度/八度**（两声部同向跳到纯五/纯八）——'
               '上方已实现自动复检。')
    out.append('2. **导音**（大调第 7 级 / 小调第 7 级音）应上行半音解决到主音。')
    out.append('3. **共同音保持**：相邻和弦尽量保留重复音，其余声部级进。')
    out.append('4. **声部独立**：避免声部交叉，内声部可在合理音域内自由填充。')
    return '\n'.join(out)


# --------------------------------------------------------------------------
# 自测 / 入口
# --------------------------------------------------------------------------
def check(name, cond, detail=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f'  ✅ {name}')
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {detail}')


_PASSED = 0
_FAILED = 0


def _selftest():
    print('[chord_advisor] 自测')
    # 音名
    check('pitch_class C', pitch_class('C') == 0)
    check('pitch_class Bb', pitch_class('Bb') == 10)
    check('pitch_class F#', pitch_class('F#') == 6)
    # 罗马解析
    p = parse_roman('V7')
    check('V7 degree=5', p['degree'] == 5)
    check('V7 seventh=dom', p['seventh'] == 'dom')
    p2 = parse_roman('ii')
    check('ii minor', p2['quality'] == 'min' and p2['degree'] == 2)
    p3 = parse_roman('vii°')
    check('vii° dim', p3['quality'] == 'dim' and p3['degree'] == 7)
    p4 = parse_roman('I64')
    check('I64 fig=64', p4['inversion_figure'] == '64')
    # 拼写：C 大调 V7 = G B D F
    a = analyze_progression(['V7'], 0, 'major')
    names = [n for (n, _pc) in a['parsed'][0]['spelling']]
    check('V7 拼写含 G B D F', set(names) == {'G', 'B', 'D', 'F'}, str(names))
    # I V vi IV 校验通过
    a2 = analyze_progression(['I', 'V', 'vi', 'IV'], 0, 'major')
    check('I V vi IV 解析出 4 个和弦', len(a2['parsed']) == 4)
    check('I 功能主', a2['parsed'][0]['function'] == '主(T)')
    check('V 功能属', a2['parsed'][1]['function'] == '属(D)')
    # I V vi IV 末和弦是 IV → 应提示「未以主和弦收束」
    warn_end = any('未以主和弦' in t for (_l, t) in a2['issues'])
    check('I V vi IV 提示未以主和弦收束', warn_end)
    # 真正的终止式检测：ii V I
    a_cad = analyze_progression(['ii', 'V', 'I'], 0, 'major')
    has_cad = any('正格终止' in t for (_l, t) in a_cad['issues'])
    check('ii V I 触发正格终止提示', has_cad)
    # SATB 实现无平行（I V vi IV 原位通常干净）
    real = realize_satb(a2, 0, 'major')
    check('SATB 生成 4 行', len(real['realizations']) == 4)
    # 小调降号前缀 → 平行大调借用（bVI/bIII/bVII 应为 F/C/G 大三和弦）
    m = analyze_progression(['i', 'bVI', 'bIII', 'bVII'], 9, 'minor')
    roots = [SHARP[p['root_pc']] for p in m['parsed']]
    check('Am bVI 根音=F', roots[1] == 'F', str(roots))
    check('Am bIII 根音=C', roots[2] == 'C', str(roots))
    check('Am bVII 根音=G', roots[3] == 'G', str(roots))
    # bVI 不应再误报「含导音」
    no_lead = not any('含导音' in t for (_l, t) in m['issues'])
    check('Am bVI 不再误报导音', no_lead)
    # 报告能生成且含关键词
    rep = advisor('I V vi IV')
    check('报告含「根音运动」', '根音运动' in rep)
    check('报告含「可审计」', '可审计' in rep)
    check('报告含「四声部」', '四声部' in rep)
    # 推荐模式
    rep2 = advisor('jazz')
    check('jazz 推荐含 ii V I', 'ii V I' in rep2)
    # 调解析
    tp, tm, _ = parse_key('Am')
    check('Am → minor', tm == 'minor' and tp == 9)
    tp2, tm2, _ = parse_key('D major')
    check('D major → major pc=2', tm2 == 'major' and tp2 == 2)


if __name__ == '__main__':
    _selftest()
    print(f'\n结果：{_PASSED} 通过 / {_FAILED} 失败')
    import sys
    sys.exit(1 if _FAILED else 0)
