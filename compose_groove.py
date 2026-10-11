# -*- coding: utf-8 -*-
"""确定性 Groove / 人性化后处理（P1，弥补 Magenta Groove）。

作用在 **MIDI 表演层**（pretty_midi），**不改符号谱**（MusicXML / music21）——
符号层保持结构正确、可校验、可复现（护城河）；Groove 只在导出 / 回放时让演奏更自然：

* 力度微扰（velocity ± humanize*25，落在 25–118 区间）
* 微量 timing 抖动（整音符平移，保持 duration 不变）
* 可选 swing（把每个 beat 内的「第二个八分」整体后移，制造拖拍感）

全部由 seed 派生随机源，同一 seed → 同一表演，契合「逐音符可复现」。

典型用法
-------
    from compose_groove import apply_groove, apply_groove_to_midi
    pm = pretty_midi.PrettyMIDI('x.mid')
    apply_groove(pm, seed='demo', swing=0.12, humanize=0.35)
    pm.write('x_groove.mid')
    # 或直接对文件：
    apply_groove_to_midi('x.mid', swing=0.12, humanize=0.35, seed='demo')
"""
from __future__ import annotations

import random
from typing import Any, Dict, Optional

VEL_MIN, VEL_MAX = 25, 118


def _derive_rng(seed: Any) -> random.Random:
    return random.Random(str(seed))


def apply_groove(pm, *, seed: Any = 'groove', swing: float = 0.0,
                humanize: float = 0.35,
                rng: Optional[random.Random] = None) -> Any:
    """就地对 ``pm``（pretty_midi.PrettyMIDI）做确定性 Groove，返回同一对象。

    swing/humanize 都为 0 时原样返回（无开销）。
    """
    if swing <= 0 and humanize <= 0:
        return pm
    try:
        import pretty_midi  # noqa: F401
    except Exception:
        return pm
    rng = rng or _derive_rng(seed)

    tempo = 120.0
    try:
        tcs = pm.get_tempo_changes()
        if len(tcs[1]):
            tempo = float(tcs[1][0])
    except Exception:
        pass
    if tempo <= 0:
        tempo = 120.0   # 损坏 MIDI 元数据兜底，避免除零
    beat_dur = 60.0 / tempo
    eighth = beat_dur / 2.0

    # ---- swing：每个 beat 内的「第二个八分」整体后移（保持 duration）----
    if swing > 0:
        try:
            beats = list(pm.get_beats())
        except Exception:
            beats = []
        offs: Dict[int, float] = {}
        for b in beats:
            b2 = b + beat_dur
            for inst in pm.instruments:
                for n in inst.notes:
                    if b < n.start < b2 and (n.start - b) > 0.35 * beat_dur:
                        offs[id(n)] = swing * eighth * (0.7 + 0.6 * rng.random())
        for inst in pm.instruments:
            for n in inst.notes:
                d = offs.get(id(n))
                if d:
                    n.start += d
                    n.end += d

    # ---- humanize：力度微扰 + 整音符微量 timing 抖动（保持 duration）----
    if humanize > 0:
        jit = humanize * 0.02 * beat_dur  # 抖动幅度（秒）
        for inst in pm.instruments:
            for n in inst.notes:
                dv = int(round(rng.uniform(-humanize, humanize) * 25))
                n.velocity = max(VEL_MIN, min(VEL_MAX, n.velocity + dv))
                dt = rng.uniform(-jit, jit)
                n.start += dt
                n.end += dt
    return pm


def groove_params_from_spec(spec: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """从 spec['groove'] 读取 Groove 参数；未开启返回 None。"""
    g = spec.get('groove') if isinstance(spec, dict) else None
    if not isinstance(g, dict) or not g.get('on'):
        return None
    return {
        'swing': float(g.get('swing', 0.0)),
        'humanize': float(g.get('humanize', 0.35)),
        'seed': g.get('seed', 'groove'),
    }


def apply_groove_to_midi(path: str, *, swing: float = 0.0, humanize: float = 0.35,
                         seed: Any = 'groove') -> bool:
    """读 ``path`` 处 MIDI，应用 Groove 后写回；返回是否应用了。"""
    if swing <= 0 and humanize <= 0:
        return False
    try:
        import pretty_midi
        pm = pretty_midi.PrettyMIDI(path)
        apply_groove(pm, seed=seed, swing=swing, humanize=humanize)
        pm.write(path)
        return True
    except Exception:
        return False
