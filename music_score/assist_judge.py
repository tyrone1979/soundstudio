"""辅助候选听审：把多种铺满可能性交给 LLM，选出听感最好的一条。

无 API Key / 失败时回退到程序化 rank。不阻塞 UI——由调用方在后台线程跑。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .editor_core import Candidate, normalize_assist_event, pitch_label


def candidate_brief(cand: Candidate, index: int) -> Dict[str, Any]:
    """压缩成给 LLM 的符号摘要（音名 + 时值 + 休止/力度）。"""
    evs = [normalize_assist_event(e) for e in (cand.preview_events or [])]
    notes = []
    for e in evs[:24]:
        if e['kind'] == 'rest':
            notes.append(f"rest/{_dur_tag(e.get('duration'))}")
        elif e['kind'] == 'dynamic':
            notes.append(f"dyn:{e.get('dynamic')}")
        elif e.get('pitch') is not None:
            bit = pitch_label(e['pitch']) + '/' + _dur_tag(e.get('duration'))
            if e.get('marks'):
                bit += '+' + ','.join(str(m) for m in e['marks'][:2])
            notes.append(bit)
    return {
        'id': index + 1,
        'style': cand.reason,
        'n_events': len(evs),
        'notes': notes,
    }


def _dur_tag(dur) -> str:
    if dur is None:
        return '?'
    name = getattr(dur, 'name', None) or str(dur)
    return {
        'WHOLE': 'w', 'HALF': 'h', 'QUARTER': 'q',
        'EIGHTH': 'e', 'SIXTEENTH': 's', 'THIRTY_SECOND': 't',
    }.get(str(name), str(name)[:4].lower())


def heuristic_best(cands: Sequence[Candidate]) -> Tuple[int, str]:
    """无 LLM 时：取 rank 最高。"""
    if not cands:
        return 0, '无候选'
    best_i = max(range(len(cands)), key=lambda i: float(cands[i].rank or 0))
    return best_i, f'程序排序 · {cands[best_i].reason}'


def judge_variants(
    variants: Sequence[Candidate],
    *,
    prefix_labels: Optional[Sequence[str]] = None,
    key_hint: str = '',
    timeout: int = 25,
) -> Tuple[int, str]:
    """听审择优。返回 (0-based index, reason)。永远不抛到调用方外。"""
    if not variants:
        return 0, '无候选'
    if len(variants) == 1:
        return 0, '唯一候选'

    briefs = [candidate_brief(c, i) for i, c in enumerate(variants)]
    try:
        from llm_compose import chat_completion, load_llm_config
        cfg = load_llm_config() or {}
        if not cfg.get('api_key'):
            return heuristic_best(variants)
    except Exception:
        return heuristic_best(variants)

    payload = {
        'task': 'assist_listening_pick',
        'prefix': list(prefix_labels or []),
        'key': key_hint or '',
        'rubric': [
            '旋律可记性', '乐句呼吸', '与前缀连贯', '和声功能清晰', '不过度跳进',
        ],
        'candidates': briefs,
        'instruction': (
            '你是作曲听审。根据符号摘要（无音频）选出听感最好的一条续写。'
            '只返回 JSON：{"best": <候选id整数>, "reason": "<一句中文理由>"}。'
        ),
    }
    try:
        raw = chat_completion(
            [
                {'role': 'system', 'content':
                 '你是严肃的作曲听审助手。只输出 JSON，不要改谱。'},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ],
            timeout=timeout,
            json_mode=True,
        )
        data = _parse_json(raw)
        best_id = int(data.get('best') or 0)
        reason = str(data.get('reason') or 'LLM 听审').strip()
        # id 为 1-based
        if 1 <= best_id <= len(variants):
            return best_id - 1, reason or 'LLM 听审'
        # 也容忍 0-based
        if 0 <= best_id < len(variants):
            return best_id, reason or 'LLM 听审'
    except Exception as e:
        idx, why = heuristic_best(variants)
        return idx, f'听审回退：{why}（{type(e).__name__}）'
    return heuristic_best(variants)


def _parse_json(raw: str) -> dict:
    text = (raw or '').strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {}
