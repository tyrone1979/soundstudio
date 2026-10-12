# -*- coding: utf-8 -*-
"""风格保真度闭环优化（确定性 DPO 等价物）。

竞品差距
--------
NotaGen 用 CLaMP-DPO（基于学习的风格偏好微调）把生成结果拉向目标风格。

我们的护城河是「确定性内核 + 规则校验 + 可复现 + 可审计、零训练数据」，
因此这里用**规则引导的闭环迭代精炼**作为确定性等价物：

  * 反馈信号：``evaluate.style_overlap(spec, pack_id)``
    —— 生成谱与该风格包指纹的余弦相似度（0–1，越大越像该风格）。
  * 目标向量：``evaluate.pack_fingerprint(pack_id)['gen_vector']``
    —— 该包「自然生成」的均值指纹（原始 16 维，与 ``_VECTOR_SPEC`` 对齐）。
  * 策略（坐标上升，纯规则、确定性、可审计）：
      1) seed 分支：同参数下用多个确定性 seed 生成候选，按 style_overlap 选最优；
      2) 诊断：把候选原始向量与包目标向量逐维比较，找出偏离最大的可控维；
      3) 微调：把对应旋钮（melody 音域 / 伴奏密度 / 声部 / 小节）朝目标方向挪一步，重新生成；
      4) 重复 2)–3) 至轮数耗尽、重叠率达标或与上一轮无提升（收敛）。
  * 全程写审计轨迹（每轮候选 + 分数 + 应用的变换），契合「可审计」护城河。

与学习的 DPO 相比：我们用规则打分（style_overlap）替代学习到的奖励，
用确定性坐标上升替代梯度更新——同样在「风格偏好」方向上迭代，但零训练数据、
seed 固定、过程可复现可审计。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# 可控维 → 向量下标（与 evaluate._VECTOR_SPEC + span + form.count 对齐）
_REG_IDX = 0     # melody.mean_midi
_DENS_IDX = 5    # rhythm.density_notes_per_bar
_VOICE_IDX = 12  # texture.voice_count
_BARS_IDX = 13   # global.bars

_REGISTER_ORDER = ('low', 'mid', 'high', 'very_high')


def register_step(current: Optional[str], direction: int) -> Optional[str]:
    """音域档位单步移动。direction>0 升高、<0 降低；越界或非法返回 None。"""
    if current not in _REGISTER_ORDER:
        current = 'mid'
    i = _REGISTER_ORDER.index(current)
    j = i + (1 if direction > 0 else -1)
    if 0 <= j < len(_REGISTER_ORDER):
        return _REGISTER_ORDER[j]
    return None


# ---------------------------------------------------------------- 内部工具

def _spec_features(spec: Dict[str, Any]) -> Dict[str, Any]:
    from evaluate import spec_features
    return spec_features(spec)


def _raw_vector(spec: Dict[str, Any]) -> List[float]:
    from evaluate import style_vector
    return style_vector(_spec_features(spec))


def _overlap(spec: Dict[str, Any], pack_id: str) -> Optional[float]:
    from evaluate import style_overlap
    return style_overlap(spec, pack_id)


def _build_spec(pack_id: str, kw: Dict[str, Any], trace=None) -> Dict[str, Any]:
    import style_skeleton
    kw = {k: v for k, v in kw.items() if v is not None}
    # tries=1：禁用多试种子的自动改写，保证 seed 固定、结果可复现
    return style_skeleton.skeleton_spec(pack_id, trace=trace, tries=1, **kw)


def _eval_candidate(pack_id: str, kw: Dict[str, Any], tag: str,
                   target: Sequence[float], trace=None) -> Dict[str, Any]:
    try:
        spec = _build_spec(pack_id, kw, trace=trace)
    except Exception:
        # 该候选的内核生成未通过校验（tries=1 不自动换种子），跳过它
        return {'kw': dict(kw), 'spec': None, 'overlap': None,
                'raw': [], 'tag': tag}
    ov = _overlap(spec, pack_id)
    raw = _raw_vector(spec)
    return {'kw': dict(kw), 'spec': spec, 'overlap': ov,
            'raw': raw, 'tag': tag}


def _diagnose_nudges(raw: Sequence[float], target: Sequence[float],
                     kw: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """根据向量偏离，产出候选微调（每个是一份完整 kw）。"""
    out: List[Tuple[str, Dict[str, Any]]] = []
    eps = 1e-6

    # 1) melody 音域（mean_midi）
    if _REG_IDX < len(raw) and _REG_IDX < len(target):
        d = raw[_REG_IDX] - target[_REG_IDX]
        cur = kw.get('register_override')
        if d < -eps and register_step(cur, +1):
            nk = dict(kw); nk['register_override'] = register_step(cur, +1)
            out.append(('reg+', nk))
        elif d > eps and register_step(cur, -1):
            nk = dict(kw); nk['register_override'] = register_step(cur, -1)
            out.append(('reg-', nk))

    # 2) 密度（density_notes_per_bar）
    if _DENS_IDX < len(raw) and _DENS_IDX < len(target):
        d = raw[_DENS_IDX] - target[_DENS_IDX]
        if d < -eps:
            nk = dict(kw); nk['density_override'] = 2.0
            out.append(('dens+', nk))
        elif d > eps:
            nk = dict(kw); nk['density_override'] = 0.5
            out.append(('dens-', nk))

    # 3) 声部（voice_count）
    if _VOICE_IDX < len(raw) and _VOICE_IDX < len(target):
        d = raw[_VOICE_IDX] - target[_VOICE_IDX]
        cur = int(kw.get('voices') or 0)
        if d < -eps and cur < 4:
            nk = dict(kw); nk['voices'] = cur + 1
            out.append(('vc+', nk))
        elif d > eps and cur > 2:
            nk = dict(kw); nk['voices'] = cur - 1
            out.append(('vc-', nk))

    # 4) 小节（bars）
    if _BARS_IDX < len(raw) and _BARS_IDX < len(target):
        d = raw[_BARS_IDX] - target[_BARS_IDX]
        cur = int(kw.get('bars') or 16)
        if d < -eps and cur < 64:
            nk = dict(kw); nk['bars'] = min(64, cur + 4)
            out.append(('bars+', nk))
        elif d > eps and cur > 8:
            nk = dict(kw); nk['bars'] = max(8, cur - 4)
            out.append(('bars-', nk))

    return out


# ---------------------------------------------------------------- 主入口

def refine_style(pack_id: str, base_kw: Optional[Dict[str, Any]] = None, *,
                rounds: int = 4, branch_seeds: int = 3,
                threshold: Optional[float] = None,
                trace: Optional[List[Any]] = None) -> Dict[str, Any]:
    """确定性闭环风格保真度精炼。

    参数
    ----
    pack_id: 目标风格包 id（如 ``'sacred/chorale'``）。
    base_kw: 起始生成参数（bars/voices/tempo/meter/fugue/seed…）；None 时由包默认补齐。
    rounds: 坐标上升轮数（不含初始 seed 分支那一轮）。
    branch_seeds: 初始 seed 分支数。
    threshold: 重叠率达标阈值；达到即提前停止（None 表示只做最大化）。
    trace: 可选，传入列表则记录内核调用链（透明化）。

    返回
    ----
    ``{'spec', 'pack_id', 'overlap', 'kw', 'rounds', 'converged', 'improved', 'target_vector'}``
    """
    base_kw = {k: v for k, v in (base_kw or {}).items() if v is not None}
    # 归一化 pack_id：兼容 'genre:family/pack' 与 'pack' 两种写法
    pid = pack_id.rsplit('/', 1)[-1] if str(pack_id).startswith('genre:') \
        else str(pack_id)
    from evaluate import pack_fingerprint
    try:
        fp = pack_fingerprint(pid)
    except Exception as e:
        raise RuntimeError(f'无法获取风格包指纹 {pack_id!r}：{e}')
    target = fp.get('gen_vector') or fp.get('vector') or []
    if not target:
        raise RuntimeError(
            f'风格包 {pack_id!r} 指纹为空（先跑 evaluate.build_all_fingerprints）')

    # 钉死 seed：refine 必须可复现，内核在 seed=None 时可能取随机默认
    base_kw.setdefault('seed', f"{pid}-refine-base")

    # 以包「自然生成」的典型结构为起点（贴近目标，减少无效迭代）
    if 'voices' not in base_kw and len(target) > _VOICE_IDX:
        base_kw['voices'] = max(2, min(4, int(round(target[_VOICE_IDX]))))
    if 'bars' not in base_kw and len(target) > _BARS_IDX:
        base_kw['bars'] = max(8, min(64, int(round(target[_BARS_IDX]))))

    # 与包指纹生成口径对齐：赋格型风格包的指纹用 fugue='full' 采样，
    # 若未显式指定 fugue，则统一用 'full'，否则 refine 的重叠率会被口径差异压低。
    try:
        import style_skeleton
        g = style_skeleton.resolve_genre(pid)
        fdims = (g or {}).get('dimensions') or {}
        if fdims.get('form') == 'fugue' and \
                str(base_kw.get('fugue') or 'auto') in ('auto', None, ''):
            base_kw['fugue'] = 'full'
    except Exception:
        pass

    audit: List[Dict[str, Any]] = []

    # —— 初始 seed 分支选择（确定性 DPO 的「样本偏好排序」等价物）——
    best = _eval_candidate(pid, base_kw, 'init', target, trace=trace)
    for b in range(max(0, int(branch_seeds))):
        kw = dict(base_kw); kw['seed'] = f"{pid}-refine-s{b}"
        c = _eval_candidate(pid, kw, f'seed{b}', target, trace=trace)
        if (c['overlap'] is not None and
                (best['overlap'] is None or c['overlap'] > best['overlap'])):
            best, base_kw = c, dict(c['kw'])
    audit.append({
        'phase': 'seed-branch', 'best_overlap': best['overlap'],
        'best_tag': best['tag'], 'kw': dict(best['kw']),
    })
    if best['spec'] is None:
        raise RuntimeError(
            f'风格包 {pack_id!r} 的初始生成均未通过内核校验，无法精炼')

    improved_any = False
    converged = False
    for r in range(1, max(0, int(rounds)) + 1):
        raw = best['raw']
        nudges = _diagnose_nudges(raw, target, best['kw'])
        if not nudges:
            converged = True
            audit.append({'phase': f'round{r}', 'note': '无明显偏离，收敛',
                          'best_overlap': best['overlap']})
            break
        round_best = best
        scored: List[Tuple[str, Optional[float]]] = []
        for tag, nk in nudges:
            c = _eval_candidate(pid, nk, tag, target, trace=trace)
            scored.append((tag, c['overlap']))
            if (c['overlap'] is not None and
                    (round_best['overlap'] is None or
                     c['overlap'] > round_best['overlap'] + 1e-9)):
                round_best = c
        if (round_best is best or
                (round_best['overlap'] is not None and best['overlap'] is not None and
                 round_best['overlap'] <= best['overlap'] + 1e-9)):
            converged = True
            audit.append({'phase': f'round{r}', 'note': '无提升，收敛',
                          'best_overlap': best['overlap'], 'scored': scored})
            break
        improved_any = True
        best = round_best
        base_kw = dict(best['kw'])
        audit.append({'phase': f'round{r}', 'applied': best['tag'],
                      'best_overlap': best['overlap'], 'kw': dict(best['kw']),
                      'scored': scored})

    if threshold is not None and best['overlap'] is not None and \
            best['overlap'] >= threshold:
        converged = True

    return {
        'spec': best['spec'],
        'pack_id': pid,
        'overlap': best['overlap'],
        'kw': dict(best['kw']),
        'rounds': audit,
        'converged': converged,
        'improved': improved_any,
        'target_vector': list(target),
    }


def refine_report(res: Dict[str, Any]) -> str:
    """审计摘要（markdown），契合「可审计」护城河。"""
    lines = ['## 风格保真度闭环精炼（确定性）', '']
    ov = res.get('overlap')
    lines.append(f"- 目标风格包：`{res.get('pack_id')}`")
    lines.append(f"- 最终风格重叠率：**{'(未知)' if ov is None else f'{ov:.3f}'}**")
    lines.append(f"- 收敛：{'是' if res.get('converged') else '否'} · "
                 f"有提升：{'是' if res.get('improved') else '否'}")
    lines.append('')
    lines.append('### 迭代轨迹（每轮候选与重叠率）')
    for rd in res.get('rounds', []):
        ph = rd.get('phase', '?')
        if rd.get('scored'):
            detail = '；'.join(
                f'{t}={("%.3f" % s if s is not None else "NA")}'
                for t, s in rd['scored'])
        elif rd.get('best_overlap') is not None:
            detail = f"最优={rd['best_overlap']:.3f}（{rd.get('best_tag')}）"
        else:
            detail = rd.get('note', '')
        lines.append(f"- **{ph}**：{detail}")
    lines.append('')
    lines.append('> 全部候选由确定性内核生成（seed 固定），重叠率由 '
                 '`evaluate.style_overlap` 计算，过程可复现、可审计。')
    return '\n'.join(lines)


__all__ = ['register_step', 'refine_style', 'refine_report']
