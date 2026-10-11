# -*- coding: utf-8 -*-
"""简单提示词 → 专业分段 brief（确定性扩写，不调 LLM）。

命中古典交响快板等短句时，扩成接近
`runs/专业提示词_单乐章交响曲_v2.md` 结构的可执行 brief，
供 planner/translator 与编制兜底使用。

弦乐小夜曲包已因语料不足（无独立大师级小夜曲全集，仅有四重奏代理）下线；
相关短句不再扩写为假小夜曲模板。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# (检测关键词, 模板 id)
_DETECT = [
    (('舒伯特', 'schubert', '交响曲快板', '交响快板', '早期交响'),
     'schubert_allegro'),
    (('交响曲', '交响乐', '交响', 'symphony', '管弦'),
     'symphony_allegro'),
]

_KEY_MAP = {
    'c': (0, 'major', 'C'), 'g': (1, 'major', 'G'), 'd': (2, 'major', 'D'),
    'a': (3, 'major', 'A'), 'f': (-1, 'major', 'F'), 'bb': (-2, 'major', 'Bb'),
    'am': (0, 'minor', 'A'), 'em': (1, 'minor', 'E'), 'dm': (-1, 'minor', 'D'),
    'c小': (-3, 'minor', 'C'), 'cm': (-3, 'minor', 'C'),
}


def _detect_key(text: str) -> Tuple[int, str, str]:
    low = text.lower().replace('大调', '').replace('小调', '')
    # 显式「G 大调」「g major」
    m = re.search(r'([A-Ga-g][b#]?\s*(?:大调|小调|major|minor))', text, re.I)
    if m:
        tok = m.group(1).lower().replace(' ', '')
        if '小' in tok or 'minor' in tok:
            letter = re.match(r'([a-g][b#]?)', tok)
            base = (letter.group(1) if letter else 'a') + 'm'
        else:
            letter = re.match(r'([a-g][b#]?)', tok)
            base = letter.group(1) if letter else 'c'
        if base in _KEY_MAP:
            return _KEY_MAP[base]
    for k, v in _KEY_MAP.items():
        if k in text.lower():
            return v
    # 交响默认 C 大调
    return (0, 'major', 'C')


def _detect_template(text: str) -> Optional[str]:
    low = text.lower()
    for keys, tid in _DETECT:
        if any(k in low for k in keys):
            return tid
    return None


def should_expand(text: str) -> bool:
    """短提示且命中古典模板时才扩写。"""
    t = (text or '').strip()
    if not t or len(t) > 220:
        return False
    # 已是专业分段提示词则不扩
    if '【作品】' in t and '【编制】' in t:
        return False
    tid = _detect_template(t)
    if tid is None:
        return False
    # 舒伯特等具名模板仍扩写；普通「交响 / 管弦」短句交给 /compose-grammar symphony
    if tid == 'schubert_allegro':
        return True
    if tid == 'symphony_allegro':
        return False
    # 小步舞曲 / 海顿 → learned_grammar
    if any(k in t.lower() for k in ('小步舞曲', '谐谑曲', 'minuet', 'scherzo', '海顿', 'haydn')):
        return False
    return True


def expand_brief(text: str) -> Dict[str, Any]:
    """返回 {expanded: str, template: str, key_sharps, mode, pack_hint}。"""
    t = (text or '').strip()
    tid = _detect_template(t) or 'symphony_allegro'
    sharps, mode, key_label = _detect_key(t)
    if tid == 'schubert_allegro' and not re.search(
            r'[A-Ga-g][b#]?\s*(大调|小调|major|minor)', t, re.I):
        sharps, mode, key_label = 2, 'major', 'D'
    mode_cn = '大调' if mode == 'major' else '小调'
    title = '早春交响快板' if tid == 'schubert_allegro' else '苍穹'

    if tid == 'schubert_allegro':
        expanded = f"""【作品】为完整交响乐团而作的单乐章交响曲快板《{title}》，{key_label} {mode_cn}
（key_sharps={sharps}, mode={mode}），4/4，共 32 小节，
奏鸣曲式：引子—主部—副部—展开—再现—尾声（舒伯特早期明朗气质）。

【编制】交响乐团（缩编双管 + 定音鼓 + 弦乐组）；
**主旋律由第一小提琴承担**（new_score 声部0必须是 violin）；
木管只做长音衬托，铜管做支撑长音，勿让全体木管/弦乐写满八分音抢戏。
旋律音区最高 high，不要 very_high。

【速度】引子 ♩=88 → 主部 ♩=112 → 副部 ♩=120 → 展开 ♩=126 → 再现 ♩=112 → 尾声 ♩=100。
六段小节数之和必须等于 32。

【旋律总则】六段 rhythm/contour 逐段不同；不要赋格；narrative(appearances=false)；
展开可转关系小调；再现换节奏勿原样照搬主部。
new_score 须带 style=classical/symphony。

【第 1 段 · 引子】4 小节 · Andante ♩=88 · p · 主调。伴奏 sustained；旋律 sparse。
【第 2 段 · 主部】6 小节 · Allegro ♩=112 · f · 主调。driving+arch，I–vi–IV–V7。
【第 3 段 · 副部】5 小节 · Allegro ♩=120 · mf · 属调。flowing+arch，分解和弦。
【第 4 段 · 展开】8 小节 · Agitato ♩=126 · ff · 关系小调。fragmented+wave。
【第 5 段 · 再现】5 小节 · Tempo I ♩=112 · f · 主调。flowing+descending。
【第 6 段 · 尾声】4 小节 · Maestoso ♩=100 · ff · 主调。driving 推进后 cadence(2)。

【不要】不要赋格，不要卡农，不要整曲 sparse，不要木管写密集跑动盖过小提琴。
"""
        pack_hint = 'symphony'
    else:
        expanded = f"""【作品】为完整交响乐团而作的单乐章交响曲《{title}》，{key_label} {mode_cn}
（key_sharps={sharps}, mode={mode}），4/4，共 32 小节，
奏鸣曲式：引子—主部—副部—展开—再现—尾声。

【编制】交响乐团（缩编双管 + 定音鼓 + 弦乐组）；
**主旋律由第一小提琴承担**；木管长音衬托，铜管支撑，勿全员密集八分。
音区最高 high。new_score 须带 style=classical/symphony。

【速度】引子 ♩=84 → 主部 ♩=104 → 副部 ♩=126 → 展开 ♩=132 → 再现 ♩=118 → 尾声 ♩=100。
六段小节 4+6+5+8+5+4=32。

【旋律总则】六段 rhythm/contour 不同；narrative(appearances=false)；不要赋格。

【第 1 段 · 引子】4 小节 · Maestoso ♩=84 · ff。I–V，sparse，伴奏 sustained。
【第 2 段 · 主部】6 小节 · Allegro ♩=104 · f。driving+arch，I–vi–IV–V7。
【第 3 段 · 副部】5 小节 · Allegro ♩=126 · mf · 属调。flowing+arch。
【第 4 段 · 展开】8 小节 · Agitato ♩=132 · ff · 关系小调。fragmented。
【第 5 段 · 再现】5 小节 · Tempo I ♩=118 · f。flowing+descending。
【第 6 段 · 尾声】4 小节 · Maestoso ♩=100 · ff。driving 后 cadence(2)。

【不要】不要赋格，不要卡农，不要木管密集跑动盖过小提琴。
"""
        pack_hint = 'symphony'

    return {
        'expanded': expanded.strip(),
        'template': tid,
        'key_sharps': sharps,
        'mode': mode,
        'pack_hint': pack_hint,
        'original': t,
    }


def maybe_expand_user_text(user_text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """若宜扩写则返回 (expanded, meta)，否则 (原文, None)。"""
    if not should_expand(user_text):
        return user_text, None
    meta = expand_brief(user_text)
    return meta['expanded'], meta
