"""两级风格路由：文本 → 家族（family）→ 风格包（pack）。

此前 ``composition_skills.match_genre`` 是**单层关键词打分 + 阈值 50 + 失败静默
返回 None**，后果有三：

1. 用户写「来首安静的钢琴小品」匹配不到任何包，系统悄悄退回默认参数，
   用户以为「风格没起作用」；
2. 打分是整数硬编码（name 100 / name_en 90 / pack_id 80 / keyword 50），
   没有置信度概念，也无法比较「匹配得多好」；
3. 失败时无诊断信息，无法提示"你是不是想要 X"。

本模块改为：

- **第一级：家族路由** —— 先判定语汇家族（学院古典 / 舞曲体裁 / 叙事影像 /
  室内抒情 / 民俗民族 / 宗教仪式），家族命中会给同族的包加权；
- **第二级：包路由** —— 在包层面按 名称 / 英文名 / pack_id / 关键词 /
  风格标签 / 摘要 打分，归一化为 0–1 置信度；
- **失败可诊断** —— ``strict=True`` 时抛 :class:`StyleRoutingError`，
  错误里带「最接近的 3 个候选 + 全部家族 + 用法提示」，而不是静默 None。

用法::

    from style_routing import route, route_or_raise
    r = route('来首巴赫式的赋格')
    r.ok, r.confidence, r.pack['pack_id'], r.evidence
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import composition_skills as skill_lib

# ---------------------------------------------------------------- 家族层

#: 家族 id → (中文名, 额外同义触发词)。taxonomy.yaml 里的 label 会自动并入。
FAMILY_SYNONYMS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    'academic': ('学院古典', ('古典', '学院', '学术', '巴洛克', '古典主义',
                              '奏鸣', '协奏', '赋格', '对位', '巴赫')),
    'classical': ('古典管弦', ('管弦', '交响', 'symphony',
                               '海顿', '莫扎特', '舒伯特')),
    'dance': ('舞曲体裁', ('舞曲', '舞蹈', '圆舞', '华尔兹', '波尔卡',
                           '玛祖卡', '探戈', '小步舞', '舞会')),
    'cinematic': ('叙事影像', ('影视', '电影', '配乐', '影像', '史诗',
                               '悬疑', '追逐', '预告片', '游戏音乐')),
    'intimate': ('室内抒情', ('室内', '抒情', '小品', '夜曲', '浪漫',
                              '安静', '温柔', '沙龙')),
    'folk': ('民俗民族', ('民俗', '民族', '民谣', '民间', '乡愁',
                          '凯尔特', '吉格')),
    'sacred': ('宗教仪式', ('宗教', '教堂', '圣咏', '圣诗', '众赞歌',
                            '弥撒', '赞美诗', '格里高利')),
}


def family_label(fam: str) -> str:
    for f, (label, _syn) in FAMILY_SYNONYMS.items():
        if f == fam:
            return label
    try:
        tax = skill_lib.load_taxonomy() or {}
        vals = ((tax.get('dimensions') or {}).get('family') or {}).get('values') or {}
        return str((vals.get(fam) or {}).get('label') or fam)
    except Exception:
        return fam


def list_families() -> List[Dict[str, Any]]:
    """所有家族 + 每个家族下可用的包数量。"""
    counts: Dict[str, int] = {}
    for g in _enabled():
        fam = str((g.get('dimensions') or {}).get('family') or '')
        counts[fam] = counts.get(fam, 0) + 1
    out = []
    for fam, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        out.append({'id': fam, 'label': family_label(fam), 'packs': n})
    return out


def _enabled() -> List[dict]:
    try:
        return [g for g in skill_lib.list_genres() if g.get('enabled', True)]
    except Exception:
        return []


def score_families(text: str) -> Dict[str, float]:
    """第一级：文本对每个家族的得分（0–1，未命中不出现）。"""
    low = str(text or '').lower()
    if not low.strip():
        return {}
    out: Dict[str, float] = {}
    for fam, (label, syns) in FAMILY_SYNONYMS.items():
        s = 0.0
        if fam in low:
            s = max(s, 0.8)
        if label and label in str(text or ''):
            s = max(s, 0.9)
        for w in syns:
            if w and w in str(text or ''):
                # 词越长越特异，给越高分
                s = max(s, min(0.95, 0.45 + 0.06 * len(w)))
        if s > 0:
            out[fam] = round(s, 3)
    return out


# ---------------------------------------------------------------- 包层

#: (命中类型, 基础分)。分数即置信度上界，多命中取 max（不叠加，避免刷分）。
_HIT_SCORES = (
    ('name', 1.00),       # 中文名完全出现，如「赋格」
    ('name_en', 0.94),    # 英文名，如「fugue」
    ('pack_id', 0.88),    # 包 id，如「nocturne」
    ('keyword', 0.72),    # triggers.keywords
    ('style_tag', 0.60),  # triggers.style_tags
    ('summary', 0.34),    # summary 里的实词
    ('dimension', 0.40),  # dimensions 里的 form / mood 等取值命中
)

_FAMILY_BONUS = 0.12      # 家族命中后对同族包的加成


def score_pack(text: str, g: dict, family_scores: Optional[Dict[str, float]] = None
               ) -> Tuple[float, List[str]]:
    """第二级：单个包的置信度与命中证据。"""
    raw = str(text or '')
    low = raw.lower()
    if not low.strip():
        return 0.0, []
    fam = str((g.get('dimensions') or {}).get('family') or '')
    best, evi = 0.0, []

    def _hit(kind: str, tok: str, score: float):
        nonlocal best
        t = str(tok or '').strip()
        if not t:
            return
        tl = t.lower()
        matched = (t and t in raw) if re.search(r'[一-鿿]', t) else (tl in low)
        if matched and score > best:
            best = score
            evi.append(f'{kind}「{t}」')

    _hit('name', g.get('name'), 1.00)
    _hit('name_en', g.get('name_en'), 0.94)
    _hit('pack_id', g.get('pack_id'), 0.88)
    tr = g.get('triggers') or {}
    for kw in tr.get('keywords') or []:
        _hit('keyword', kw, 0.72 if not re.search(r'[一-鿿]', str(kw)) else 0.78)
    for tg in tr.get('style_tags') or []:
        _hit('style_tag', tg, 0.60)
    for dim_key in ('form', 'mood', 'medium', 'tradition', 'rhythmic_identity'):
        _hit('dimension', (g.get('dimensions') or {}).get(dim_key), 0.40)
    # 摘要里的实词（≥2 字）
    for w in re.findall(r'[一-鿿]{2,4}', str(g.get('summary') or '')):
        _hit('summary', w, 0.34)

    if best > 0 and family_scores and fam in family_scores:
        # 家族命中：同族包加权；家族分越高加成越多
        best = min(1.0, best + _FAMILY_BONUS * family_scores[fam])
        evi.append(f'家族「{family_label(fam)}」加权 +'
                   f'{round(_FAMILY_BONUS * family_scores[fam], 2)}')
    return round(best, 3), evi


# ---------------------------------------------------------------- 路由结果

class StyleRoutingError(Exception):
    """路由失败（无包达到阈值）。携带诊断信息，供 UI/聊天直接展示。"""

    def __init__(self, message: str, query: str = '',
                 suggestions: Optional[Sequence[str]] = None,
                 families: Optional[Sequence[str]] = None):
        super().__init__(message)
        self.query = query
        self.suggestions = list(suggestions or [])
        self.families = list(families or [])


class RoutingResult:
    """路由结果。"""

    __slots__ = ('query', 'pack', 'family', 'confidence', 'evidence',
                 'alternatives', 'family_scores', 'ok', 'threshold')

    def __init__(self, query: str, pack: Optional[dict], family: str,
                 confidence: float, evidence: List[str],
                 alternatives: List[Tuple[dict, float]],
                 family_scores: Dict[str, float], threshold: float):
        self.query = query
        self.pack = pack
        self.family = family
        self.confidence = confidence
        self.evidence = evidence
        self.alternatives = alternatives
        self.family_scores = family_scores
        self.threshold = threshold
        self.ok = pack is not None and confidence >= threshold

    @property
    def pack_id(self) -> str:
        return str((self.pack or {}).get('pack_id') or '')

    @property
    def genre_id(self) -> Optional[str]:
        return (self.pack or {}).get('id') if self.pack else None

    def describe(self) -> str:
        """给聊天/UI 的一行说明。"""
        if not self.ok:
            return f'未匹配到风格（最高置信度 {self.confidence:.2f} < 阈值 {self.threshold:.2f}）'
        name = (self.pack or {}).get('name') or self.pack_id
        return (f'{name}（{self.pack_id}）｜家族 {family_label(self.family)}'
                f'｜置信度 {self.confidence:.2f}｜依据 {"，".join(self.evidence[:3])}')

    def candidates_text(self, n: int = 3) -> str:
        return '、'.join(
            f'{p.get("name") or p.get("pack_id")}({s:.2f})'
            for p, s in self.alternatives[:n]
        )


DEFAULT_THRESHOLD = 0.45


def route(text: str, threshold: float = DEFAULT_THRESHOLD,
          strict: bool = False) -> RoutingResult:
    """两级路由。

    ``strict=True`` 且未达标时抛 :class:`StyleRoutingError`（含候选与家族提示）。
    """
    fam_scores = score_families(text)
    scored: List[Tuple[dict, float, List[str]]] = []
    for g in _enabled():
        s, evi = score_pack(text, g, fam_scores)
        if s > 0:
            scored.append((g, s, evi))
    scored.sort(key=lambda x: -x[1])
    top = scored[0] if scored else (None, 0.0, [])
    # 与 score_pack/list_families 一致的防御写法：dimensions 为 None 时也安全
    fam = str(((top[0] or {}).get('dimensions') or {}).get('family') or '') \
        if top[0] else ''
    res = RoutingResult(
        query=str(text or ''), pack=top[0], family=fam, confidence=top[1],
        evidence=top[2],
        alternatives=[(g, s) for g, s, _e in scored[1:5]],
        family_scores=fam_scores, threshold=threshold,
    )
    if strict and not res.ok:
        raise StyleRoutingError(
            _failure_message(res), query=res.query,
            suggestions=[str(p.get('name') or p.get('pack_id') or '')
                         for p, _s in res.alternatives[:3]],
            families=[f"{f['label']}" for f in list_families()],
        )
    return res


def route_or_raise(text: str, threshold: float = DEFAULT_THRESHOLD) -> RoutingResult:
    """等价于 ``route(..., strict=True)``。"""
    return route(text, threshold=threshold, strict=True)


def _failure_message(res: RoutingResult) -> str:
    q = res.query.strip()
    lines = [f'没能确定风格：{q[:40] or "（空）"}']
    if res.alternatives:
        lines.append(f'最接近的候选：{res.candidates_text(3)}（均未达阈值 {res.threshold:.2f}）')
    else:
        lines.append('没有任何风格包命中。')
    fams = '、'.join(f"{f['label']}" for f in list_families())
    lines.append(f'可用家族：{fams}')
    lines.append('提示：用 /style <风格名> 明确指定（右侧检查器的「风格包」下拉同源），'
                 '或输入 /style 后从弹出列表里选。')
    return '\n'.join(lines)


def best_match(text: str, threshold: float = DEFAULT_THRESHOLD) -> Optional[dict]:
    """向后兼容旧 ``match_genre`` 的调用点：拿不到就 None（不抛）。"""
    return route(text, threshold=threshold, strict=False).pack


def explain(text: str) -> str:
    """给 UI / 调试用的多行解释（两级判定全过程）。"""
    r = route(text)
    out = [f'查询：{text[:60]}']
    if r.family_scores:
        top_fams = sorted(r.family_scores.items(), key=lambda kv: -kv[1])[:3]
        out.append('① 家族层：' + '，'.join(
            f'{family_label(f)}={s:.2f}' for f, s in top_fams))
    else:
        out.append('① 家族层：未命中（不影响包级匹配）')
    if r.ok:
        out.append(f'② 包层：{r.pack.get("name")}（{r.pack_id}）'
                   f' 置信度 {r.confidence:.2f}')
        out.append('   依据：' + '；'.join(r.evidence[:5]))
    else:
        out.append(f'② 包层：未达标（最高 {r.confidence:.2f} < {r.threshold:.2f}）')
    if r.alternatives:
        out.append('   次选：' + r.candidates_text(4))
    return '\n'.join(out)


__all__ = [
    'FAMILY_SYNONYMS', 'StyleRoutingError', 'RoutingResult', 'DEFAULT_THRESHOLD',
    'route', 'route_or_raise', 'best_match', 'explain', 'list_families',
    'family_label', 'score_families', 'score_pack',
]
