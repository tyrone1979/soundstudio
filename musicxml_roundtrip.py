# -*- coding: utf-8 -*-
"""MusicXML 往返导入 + 再校验（P2，对标 AIVA 的 DAW 内编辑闭环）。

护城河契合
--------
导入外部（例如 DAW 编辑过的）MusicXML → 用我们的**确定性规则引擎**再校验：
外声部平行五八度、小节时值完整性、音域、风格重叠率（对标包的 fingerprint）；
再重新导出为**规范化** MusicXML + MIDI（往返）。全程可审计、零训练数据、规则驱动。

主入口
------
  import_musicxml(path)              → music21 Score
  revalidate(score, pack_id=None)    → 结构化校验报告（voice_leading / measure / range / style）
  revalidate_report(res)             → markdown（可审计）
  export_canonical(score, xml, mid)  → 重新导出规范化文件（往返）
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple


def import_musicxml(path: str):
    """解析 MusicXML（或任何 music21 支持的格式）→ music21 Score。"""
    from music21 import converter
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return converter.parse(path)


# ---------------------------------------------------------------- 校验项

def _measure_completeness(score) -> List[Dict[str, Any]]:
    """逐声部、逐小节检查时值完整性（实际 quarterLength vs 拍号容量）。"""
    out: List[Dict[str, Any]] = []
    for p in score.parts:
        name = p.partName or p.id
        for m in p.getElementsByClass('Measure'):
            if m.number is None:
                continue
            try:
                bar_dur = float(m.barDuration.quarterLength)
                actual = float(m.duration.quarterLength)
            except Exception:
                continue
            diff = actual - bar_dur
            if abs(diff) > 0.02:
                out.append({'part': str(name), 'measure': int(m.number),
                            'expected': round(bar_dur, 2),
                            'actual': round(actual, 2),
                            'deficit': round(-diff, 2)})
    return out


def _range_issues(score) -> List[Dict[str, Any]]:
    """检查各声部音域（过宽 / 越界），仅作注意。"""
    out: List[Dict[str, Any]] = []
    for p in score.parts:
        name = p.partName or p.id
        midis = [n.pitch.midi for n in p.recurse().notes
                 if n.isNote and n.pitch is not None]
        if not midis:
            continue
        lo, hi = min(midis), max(midis)
        span = hi - lo
        if span > 36:  # 超过 3 个八度
            out.append({'part': str(name), 'low': lo, 'high': hi,
                        'span': span, 'level': 'MINOR',
                        'message': f'音域过宽（{span} 半音 ≈ {span / 12:.1f} 八度）'})
        if lo < 24 or hi > 100:  # C1 以下 / C7 以上
            out.append({'part': str(name), 'low': lo, 'high': hi,
                        'level': 'MINOR', 'message': '存在极端音高（超出常规音区）'})
    return out


def _style_compare(score, pack_id: Optional[str], title: str = '') -> Optional[Dict[str, Any]]:
    """把导入谱与风格包指纹比较（标准化余弦，与 evaluate 同口径）。"""
    if not pack_id:
        return None
    try:
        from evaluate import (analyze_m21, style_vector, pack_fingerprint,
                              pack_stats, zscore, cosine)
        vec = style_vector(analyze_m21(score, source=title))
        fp = pack_fingerprint(pack_id)
        pv = fp.get('vector') or []
        if not pv:
            return None
        mean, std = pack_stats()
        a, b = (zscore(vec, mean, std), zscore(pv, mean, std)) if mean else (vec, pv)
        ov = cosine(a, b)
        return {'pack_id': pack_id,
                'overlap': round(ov, 4) if ov is not None else None,
                'vector': [round(x, 4) for x in vec],
                'target_vector': [round(x, 4) for x in pv]}
    except Exception:
        return {'pack_id': pack_id, 'overlap': None, 'error': '指纹不可用'}


def revalidate(score, pack_id: Optional[str] = None, title: str = '') -> Dict[str, Any]:
    """对导入谱做全套规则再校验，返回结构化报告（可审计）。"""
    from llm_compose import voice_leading_issues_from_m21

    vl = voice_leading_issues_from_m21(score)
    measures = _measure_completeness(score)
    ranges = _range_issues(score)
    style = _style_compare(score, pack_id, title=title)

    # 元信息
    meta: Dict[str, Any] = {'title': title or ''}
    try:
        md = getattr(score, 'metadata', None)
        if md and getattr(md, 'title', None):
            meta['title'] = str(md.title)
        ts = score.recurse().getElementsByClass('TimeSignature').first()
        meta['meter'] = ts.ratioString if ts else None
        mm = score.recurse().getElementsByClass('MetronomeMark').first()
        meta['tempo'] = int(round(float(mm.number))) if (mm and mm.number) else None
        ks = score.recurse().getElementsByClass('KeySignature').first()
        meta['key_sharps'] = int(ks.sharps) if ks else None
        meta['parts'] = [p.partName or p.id for p in score.parts]
        meta['bars'] = max((len(p.getElementsByClass('Measure')) for p in score.parts),
                           default=0)
    except Exception:
        pass

    counts = {'CRITICAL': 0, 'MAJOR': 0, 'MINOR': 0}
    for it in vl:
        lv = str(it.get('level') or 'MINOR').upper()
        counts[lv] = counts.get(lv, 0) + 1

    return {
        'meta': meta,
        'voice_leading': {'counts': counts, 'issues': vl[:20]},
        'measures': {'incomplete': measures},
        'range': {'issues': ranges},
        'style': style,
    }


def revalidate_report(res: Dict[str, Any]) -> str:
    """校验报告（markdown），契合「可审计」护城河。"""
    m = res.get('meta') or {}
    lines = ['## MusicXML 再校验（确定性规则引擎）', '']
    lines.append(f"- 文件：`{m.get('title') or '(未命名)'}` · "
                 f"{m.get('meter') or '?'} · ♩={m.get('tempo') or '?'} · "
                 f"{m.get('bars') or '?'} 小节 · 声部 {len(m.get('parts') or [])}")
    vl = res.get('voice_leading', {})
    c = vl.get('counts', {})
    lines.append(f"- **外声部声部进行**：CRITICAL {c.get('CRITICAL', 0)} / "
                 f"MAJOR {c.get('MAJOR', 0)} / MINOR {c.get('MINOR', 0)}")
    for it in vl.get('issues', [])[:6]:
        lines.append(f"  - ❌ {it.get('message')}")
    meas = res.get('measures', {}).get('incomplete') or []
    lines.append(f"- **小节时值完整性**：{'✅ 全部完整' if not meas else f'⚠️ {len(meas)} 个小节时值不满'}")
    for x in meas[:6]:
        lines.append(f"  - ⚠️ {x.get('part')} 第 {x.get('measure')} 小节："
                     f"应有 {x.get('expected')} 实际 {x.get('actual')}")
    rng = res.get('range', {}).get('issues') or []
    lines.append(f"- **音域**：{'✅ 正常' if not rng else f'⚠️ {len(rng)} 处注意'}")
    for x in rng[:4]:
        lines.append(f"  - ⚠️ {x.get('part')}：{x.get('message')}")
    st = res.get('style')
    if st:
        ov = st.get('overlap')
        if ov is None:
            lines.append(f"- **风格重叠率**：N/A（{st.get('error') or '无指纹'}）")
        else:
            lines.append(f"- **风格重叠率**（对标 `{st.get('pack_id')}`）："
                         f"**{ov:.3f}**")
    lines.append('')
    lines.append('> 以上校验由确定性规则引擎完成（平行五八度 / 时值完整性 / 音域 / '
                 '风格指纹），不依赖任何学习模型，过程可复现、可审计。')
    return '\n'.join(lines)


def export_canonical(score, xml_path: str, mid_path: Optional[str] = None) -> Dict[str, str]:
    """重新导出规范化文件（往返）：MusicXML（+ 可选 MIDI）。"""
    os.makedirs(os.path.dirname(xml_path) or '.', exist_ok=True)
    score.write('musicxml', fp=xml_path)
    out = {'xml': xml_path}
    if mid_path:
        try:
            os.makedirs(os.path.dirname(mid_path) or '.', exist_ok=True)
            score.write('midi', fp=mid_path)
            out['mid'] = mid_path
        except Exception:
            pass
    return out


__all__ = ['import_musicxml', 'revalidate', 'revalidate_report',
           'export_canonical']
