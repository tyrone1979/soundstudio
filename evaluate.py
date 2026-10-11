# -*- coding: utf-8 -*-
"""生成质量评估：六维指纹 + 三指标。

对照《符号音乐与器乐生成专题调研报告》第 7 章的评测三件套
（分布保真 FMD / 属性遵循度 / 人评），本软件能补的是前两项中的
「风格重叠率」与「属性遵循度」，外加一项报告没有但本软件最该有的
「最近邻防抄袭」（确定性内核 + 小模板库最容易自我复制）。

三个指标
--------
1. **风格重叠率** ``style_overlap``
   生成谱的六维指纹与目标风格包指纹的余弦相似度（0–1）。
   风格包指纹 = 若干个 seed 的生成样本指纹均值 + 该包已有语料 features 均值
   （语料权重更高）；生成 26 份指纹的命令：:func:`build_all_fingerprints`。

2. **属性遵循度** ``attribute_adherence``
   用户明确要求的属性（速度/拍号/调号/调式/小节数/声部数/赋格）是否逐条命中。
   内核对这些是**硬保证**，所以指标常态应为 1.0；出现 <1.0 一定是真 bug
   或请求本身越界（如 bars 超出 8–64）。

3. **最近邻防抄袭** ``nearest_neighbor``
   与「其他风格包指纹」的余弦相似度；≥ ``NEAR_DUP_THRESHOLD`` 判高度相似。

4. **语料对照** ``nearest_corpus_piece`` / ``nearest_corpus_fragment``
   与本包 ``features/*.json`` 单曲及（可选）源谱滑动窗片段比余弦；
   分别用 ``CORPUS_PIECE_NEAR_DUP`` / ``CORPUS_FRAG_NEAR_DUP`` 判抄袭嫌疑。

指纹维度（六维，来自 score_learning.analyzer）
---------------------------------------------
global（曲式规模）/ form（段落结构）/ melody（旋律形态）/
rhythm（节奏密度与重音）/ harmony（和声节奏与调式根音）/ texture（织体声部）。
参与相似度计算的只取其中**可比的数值分量**，不含曲名等标识信息。
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import composition_skills as skill_lib

HERE = os.path.dirname(os.path.abspath(__file__))
PACKS_ROOT = os.path.join(HERE, 'skills', 'composition', 'genre', 'packs')
NEAR_DUP_THRESHOLD = 0.995       # 包级指纹：相似度 ≥ 此值判高度相似
CORPUS_PIECE_NEAR_DUP = 0.98     # 语料整曲：≥ 此值疑似整曲抄袭
CORPUS_FRAG_NEAR_DUP = 0.99      # 语料片段：≥ 此值疑似片段抄袭


# ---------------------------------------------------------------- 向量化

#: (段名, 键名, 归一化除数)。除数只影响各维量纲，余弦相似度下等价于加权。
_VECTOR_SPEC: Tuple[Tuple[str, str, float], ...] = (
    ('melody', 'mean_midi', 96.0),
    ('melody', 'stepwise_ratio', 1.0),
    ('melody', 'leap_ratio', 1.0),
    ('melody', 'large_leap_ratio', 1.0),
    ('melody', 'repeated_note_ratio', 1.0),
    ('rhythm', 'density_notes_per_bar', 8.0),
    ('rhythm', 'rest_ratio', 1.0),
    ('rhythm', 'syncopation_proxy', 1.0),
    ('rhythm', 'beat1_onset_share', 1.0),
    ('rhythm', 'bass_beat1_ratio', 1.0),
    ('harmony', 'harmonic_rhythm_chords_per_bar', 4.0),
    ('harmony', 'unique_roots', 12.0),
    ('texture', 'voice_count', 4.0),
    ('global', 'bars', 32.0),
)


def _num(x) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def style_vector(feat: Dict[str, Any]) -> List[float]:
    """六维特征字典 → 数值向量（缺失维度记 0）。"""
    out: List[float] = []
    for sec, key, div in _VECTOR_SPEC:
        v = _num((feat.get(sec) or {}).get(key))
        out.append(v / div if div else v)
    # 音域跨度（melody.range 是 ['D4','G5'] 这类音名）
    try:
        from music21 import pitch as m21pitch
        rg = (feat.get('melody') or {}).get('range') or []
        if len(rg) == 2:
            span = abs(m21pitch.Pitch(str(rg[1])).midi
                       - m21pitch.Pitch(str(rg[0])).midi) / 24.0
        else:
            span = 0.0
    except Exception:
        span = 0.0
    out.append(span)
    # 段落数（form 长度）
    out.append(len(feat.get('form') or []) / 8.0)
    return out


#: 指纹读盘缓存。`all_fingerprints()` / `pack_stats()` 会被 style_overlap、
#: style_rank、nearest_neighbor、evaluate_report 反复调用，而每次都要把 31 个
#: `fingerprint.json` 重新读一遍——实测一次报告渲染就会触发几十次读盘。这些
#: 文件只在重新学习/重算指纹时才变，用「全部指纹文件的 mtime 签名」做失效依据
#: 即可，不牺牲正确性（指纹一变签名就变，缓存自然失效）。
_FP_CACHE: Dict[str, Any] = {}


def _fingerprint_signature() -> tuple:
    out = []
    for g in skill_lib.list_genres():
        pdir = g.get('path')
        if not pdir:
            continue
        p = os.path.join(pdir, 'fingerprint.json')
        try:
            out.append((str(p), os.path.getmtime(p)))
        except OSError:
            continue
    return tuple(out)


def invalidate_fingerprint_cache():
    """指纹被重算/重新学习后调用，强制下次重新读盘。"""
    _FP_CACHE.clear()


def pack_stats() -> Tuple[List[float], List[float]]:
    """全体风格包指纹的逐维均值/标准差（用于标准化）。

    不做标准化时，26 个包的指纹两两余弦相似度都在 0.92–1.00 之间——因为所有
    分量同号，余弦被"大家都是四平八稳的调性音乐"这个共性主导，区分度不够。
    标准化后比较的是"偏离风格均值的方向"，余弦才有判别力。
    """
    sig = _fingerprint_signature()
    if _FP_CACHE.get('stats_sig') == sig:
        return _FP_CACHE['stats']
    fps = all_fingerprints()
    if not fps:
        _FP_CACHE['stats_sig'], _FP_CACHE['stats'] = sig, ([], [])
        return [], []
    # 只统计同维的合法向量：异常/旧版指纹可能维度不一致或不是列表
    vecs = [v for v in fps.values() if isinstance(v, (list, tuple)) and v]
    if not vecs:
        _FP_CACHE['stats_sig'], _FP_CACHE['stats'] = sig, ([], [])
        return [], []
    n = min(len(v) for v in vecs)
    if n == 0:
        _FP_CACHE['stats_sig'], _FP_CACHE['stats'] = sig, ([], [])
        return [], []
    mean = [sum(float(v[i]) for v in vecs) / len(vecs) for i in range(n)]
    var = [sum((float(v[i]) - mean[i]) ** 2 for v in vecs) / len(vecs)
           for i in range(n)]
    std = [math.sqrt(x) for x in var]
    _FP_CACHE['stats_sig'], _FP_CACHE['stats'] = sig, (mean, std)
    return mean, std


#: 标准化后的截断界。某一维若在风格间几乎无差异（std 极小），一首偏离稍大的曲子
#: 会被放大成 z≈15 这样的离群值，单维就能把余弦相似度掀翻——实测 rest_ratio 曾让
#: 一首正经赋格对赋格指纹的相似度变成 -0.01。截断到 ±3 之后，离群维仍有发言权，
#: 但不能独裁。
Z_CLIP = 3.0


def zscore(vec: Sequence[float], mean: Sequence[float],
           std: Sequence[float], clip: float = Z_CLIP) -> List[float]:
    """逐维标准化并截断；标准差过小的维度记 0（该维在风格间无差异）。"""
    out = []
    for i, v in enumerate(vec):
        s = std[i] if i < len(std) else 0.0
        m = mean[i] if i < len(mean) else 0.0
        if not s or s <= 1e-9:
            out.append(0.0)
            continue
        z = (float(v) - m) / s
        out.append(max(-clip, min(clip, z)))
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；任一向量为零向量时返回 0（无法比较，不是"完全相似"）。"""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(x) ** 2 for x in a[:n]))
    nb = math.sqrt(sum(float(x) ** 2 for x in b[:n]))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


# ---------------------------------------------------------------- 谱 → 指纹

def spec_features(spec: Dict[str, Any]) -> Dict[str, Any]:
    """spec → 六维特征（经 music21 → analyzer，与语料同口径）。

    必须走记谱层（``makeMeasures``）：扁平流没有 Measure，analyzer 会把
    ``global.bars`` 读成 0，进而让「和声节奏 = 和弦数/bars」除零成一个巨大值，
    在余弦相似度里压掉其余所有维度——那样算出来的重叠率会全部 ≈1.0，没有区分度。
    """
    import llm_compose
    from score_learning import analyzer as _az
    sc = llm_compose.spec_to_music21(spec)
    return _az.analyze_m21(sc, source=str(spec.get('title') or ''),
                           genre_hint=str(spec.get('genre_hint') or ''))


def spec_vector(spec: Dict[str, Any]) -> List[float]:
    return style_vector(spec_features(spec))


# ---------------------------------------------------------------- 包指纹

def _pack_dir(pack_id: str) -> Optional[str]:
    for g in skill_lib.list_genres():
        if str(g.get('pack_id')) == pack_id:
            return g.get('path')
    return None


def _corpus_vectors(pack_id: str) -> List[List[float]]:
    """该包已有语料 features/*.json 的向量（真实曲目，权重更高）。"""
    return [p['vector'] for p in corpus_piece_pool(pack_id)]


def corpus_piece_pool(pack_id: str) -> List[Dict[str, Any]]:
    """本包语料单曲池：[{name, vector, title, source, path}, …]。"""
    pdir = _pack_dir(pack_id)
    if not pdir:
        return []
    fd = os.path.join(pdir, 'features')
    if not os.path.isdir(fd):
        return []
    out: List[Dict[str, Any]] = []
    for fn in sorted(os.listdir(fd)):
        if not fn.endswith('.json'):
            continue
        path = os.path.join(fd, fn)
        try:
            with open(path, encoding='utf-8') as f:
                feat = json.load(f)
            meta = feat.get('metadata') or {}
            out.append({
                'name': os.path.splitext(fn)[0],
                'vector': style_vector(feat),
                'title': str(meta.get('title') or fn),
                'source': str(meta.get('source') or ''),
                'path': path,
                'bars': int((feat.get('global') or {}).get('bars') or 0),
            })
        except Exception:
            continue
    return out


def nearest_corpus_piece(spec: Dict[str, Any], pack_id: str,
                         top_k: int = 3,
                         threshold: float = CORPUS_PIECE_NEAR_DUP
                         ) -> Dict[str, Any]:
    """生成谱 ↔ 本包语料单曲最近邻（整曲特征向量）。"""
    try:
        vec = spec_vector(spec)
    except Exception:
        return {'name': None, 'similarity': None, 'is_near_duplicate': False,
                'threshold': threshold, 'top': [], 'n_pool': 0}
    pool = corpus_piece_pool(pack_id)
    sims: List[Tuple[str, float, str]] = []
    for p in pool:
        s = cosine(vec, p['vector'])
        sims.append((p['name'], s, p.get('title') or p['name']))
    sims.sort(key=lambda x: -x[1])
    top = [{'name': n, 'similarity': round(s, 4), 'title': t}
           for n, s, t in sims[:max(1, int(top_k))]]
    best = top[0] if top else None
    sim = best['similarity'] if best else None
    return {
        'name': best['name'] if best else None,
        'title': best.get('title') if best else None,
        'similarity': sim,
        'is_near_duplicate': bool(sim is not None and sim >= threshold),
        'threshold': threshold,
        'top': top,
        'n_pool': len(pool),
    }


def _slice_score_measures(score, start_bar: int, end_bar: int):
    """music21 Score → 仅含 [start_bar, end_bar] 小节的新 Score。"""
    from music21 import stream
    out = stream.Score()
    if score.metadata is not None:
        out.metadata = score.metadata
    parts = list(score.parts) if hasattr(score, 'parts') else []
    if not parts:
        # 扁平流：尽力按 measure 切
        parts = [score]
    for p in parts:
        np = stream.Part()
        # 仅复制字符串 part id；数值 id 会被 music21 当成内存地址并警告
        pid = getattr(p, 'id', None)
        if isinstance(pid, str) and pid:
            np.id = pid
        kept = False
        for m in p.getElementsByClass('Measure'):
            num = int(getattr(m, 'number', 0) or 0)
            if start_bar <= num <= end_bar:
                np.append(m)
                kept = True
        if kept:
            out.append(np)
    return out


def _window_vectors_from_score(score, *, window_bars: int = 4, hop: int = 2,
                               source_tag: str = ''
                               ) -> List[Dict[str, Any]]:
    """对 music21 Score 做滑动窗分析 → [{id, start_bar, vector}, …]。"""
    from score_learning import analyzer as _az
    max_bar = 0
    for p in (score.parts if hasattr(score, 'parts') else [score]):
        for m in p.getElementsByClass('Measure'):
            max_bar = max(max_bar, int(getattr(m, 'number', 0) or 0))
    if max_bar < 1:
        return []
    w = max(1, int(window_bars))
    h = max(1, int(hop))
    out: List[Dict[str, Any]] = []
    start = 1
    while start + w - 1 <= max_bar:
        end = start + w - 1
        try:
            sliced = _slice_score_measures(score, start, end)
            feat = _az.analyze_m21(
                sliced, source=f'{source_tag}#{start}-{end}', genre_hint='')
            out.append({
                'id': f'{source_tag or "win"}@{start}-{end}',
                'start_bar': start,
                'end_bar': end,
                'vector': style_vector(feat),
            })
        except Exception:
            pass
        start += h
    return out


def _load_score_path(path: str):
    """加载语料源谱（MusicXML / MIDI / kern）。失败返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        from music21 import converter
        return converter.parse(path)
    except Exception:
        return None


def corpus_fragment_pool(pack_id: str, *, window_bars: int = 4,
                         hop: int = 4, max_pieces: int = 40
                         ) -> List[Dict[str, Any]]:
    """语料片段池：优先对源谱滑窗；源谱不可用则退化为整曲一条。

    ``max_pieces`` 均匀抽样，避免 jazz 等大包扫全量过慢。
    """
    pieces = corpus_piece_pool(pack_id)
    if not pieces:
        return []
    if len(pieces) > max_pieces:
        step = max(1, len(pieces) // max_pieces)
        pieces = pieces[::step][:max_pieces]
    out: List[Dict[str, Any]] = []
    for p in pieces:
        sc = _load_score_path(p.get('source') or '')
        if sc is not None:
            wins = _window_vectors_from_score(
                sc, window_bars=window_bars, hop=hop, source_tag=p['name'])
            if wins:
                out.extend(wins)
                continue
        # 退化：整曲当一条「片段」
        out.append({
            'id': f"{p['name']}@full",
            'start_bar': 1,
            'end_bar': int(p.get('bars') or 0) or None,
            'vector': p['vector'],
            'piece': p['name'],
        })
    return out


def nearest_corpus_fragment(spec: Dict[str, Any], pack_id: str,
                            window_bars: int = 4, hop: int = 4,
                            top_k: int = 3,
                            threshold: float = CORPUS_FRAG_NEAR_DUP,
                            max_corpus_pieces: int = 12
                            ) -> Dict[str, Any]:
    """生成谱滑动窗 ↔ 语料片段最近邻。

    默认 ``max_corpus_pieces=12``、``hop=4``，控制报告路径耗时；
    审计脚本可加大抽样做更严扫描。
    """
    empty = {
        'name': None, 'similarity': None, 'is_near_duplicate': False,
        'threshold': threshold, 'gen_start_bar': None, 'corpus_id': None,
        'top': [], 'n_pool': 0, 'n_gen_windows': 0,
    }
    try:
        import llm_compose
        score = llm_compose.spec_to_music21(spec)
    except Exception:
        return empty
    gen_wins = _window_vectors_from_score(
        score, window_bars=window_bars, hop=hop,
        source_tag=str(spec.get('seed') or 'gen'))
    if not gen_wins:
        return empty
    pool = corpus_fragment_pool(
        pack_id, window_bars=window_bars, hop=max(window_bars, 1),
        max_pieces=max_corpus_pieces)
    if not pool:
        return {**empty, 'n_gen_windows': len(gen_wins)}
    best_hits: List[Dict[str, Any]] = []
    for gw in gen_wins:
        for cp in pool:
            s = cosine(gw['vector'], cp['vector'])
            best_hits.append({
                'similarity': s,
                'gen_start_bar': gw['start_bar'],
                'gen_end_bar': gw['end_bar'],
                'corpus_id': cp['id'],
                'corpus_start_bar': cp.get('start_bar'),
            })
    best_hits.sort(key=lambda x: -x['similarity'])
    # 去重：同一 corpus_id 只留最高分
    seen = set()
    top: List[Dict[str, Any]] = []
    for h in best_hits:
        cid = h['corpus_id']
        if cid in seen:
            continue
        seen.add(cid)
        top.append({
            'corpus_id': cid,
            'similarity': round(h['similarity'], 4),
            'gen_start_bar': h['gen_start_bar'],
            'gen_end_bar': h['gen_end_bar'],
            'corpus_start_bar': h.get('corpus_start_bar'),
        })
        if len(top) >= max(1, int(top_k)):
            break
    best = top[0] if top else None
    sim = best['similarity'] if best else None
    return {
        'name': best['corpus_id'] if best else None,
        'corpus_id': best['corpus_id'] if best else None,
        'similarity': sim,
        'is_near_duplicate': bool(sim is not None and sim >= threshold),
        'threshold': threshold,
        'gen_start_bar': best['gen_start_bar'] if best else None,
        'top': top,
        'n_pool': len(pool),
        'n_gen_windows': len(gen_wins),
    }


def mean_vector(vs: Sequence[Sequence[float]]) -> List[float]:
    if not vs:
        return []
    n = min(len(v) for v in vs)
    if n == 0:
        return []
    return [sum(float(v[i]) for v in vs) / len(vs) for i in range(n)]


def pack_fingerprint(pack_id: str, seeds: int = 3, force: bool = False,
                     bars: int = 16, voices: int = 3) -> Dict[str, Any]:
    """风格包指纹（带缓存）：生成样本均值 ⊕ 语料均值。

    结果写入 ``packs/<family>/<pack>/fingerprint.json``，可被版本控制，
    人可读可改（与风格包本身一样是"写出来的知识"）。
    """
    pdir = _pack_dir(pack_id)
    if not pdir:
        raise ValueError(f'未知风格包 {pack_id!r}')
    cache = os.path.join(pdir, 'fingerprint.json')
    if not force and os.path.exists(cache):
        try:
            with open(cache, encoding='utf-8') as f:
                fp = json.load(f)
            # 缓存必须是含非空 vector 列表的 dict，损坏缓存走重建
            if isinstance(fp, dict) and isinstance(fp.get('vector'), list) \
                    and fp['vector']:
                return fp
        except Exception:
            pass

    import style_skeleton
    from compose_kernel import run_program
    gid = None
    gmeta: Dict[str, Any] = {}
    for g in skill_lib.list_genres():
        if str(g.get('pack_id')) == pack_id:
            gid = g.get('id')
            gmeta = g
            break
    # 指纹必须按"这个包本来的样子"采样：赋格包的自然形态是整首赋格
    # （fugue='full'）。若一律用默认配置采样，整首赋格会因为休止率、密度差异
    # 被判成"不像赋格"——那是采样口径的问题，不是曲子的问题。
    dims = gmeta.get('dimensions') or {}
    fugue_mode = 'full' if str(dims.get('form') or '') == 'fugue' else None
    gen_vecs: List[List[float]] = []
    errors: List[str] = []
    for i in range(max(1, int(seeds))):
        try:
            kw = {'bars': bars, 'voices': voices, 'seed': f'{pack_id}-fp{i}'}
            if fugue_mode:
                kw['fugue'] = fugue_mode
            gen_vecs.append(spec_vector(run_program(
                style_skeleton.build_skeleton_calls(gid, **kw))))
        except Exception as e:
            errors.append(repr(e)[:120])
    gen_mean = mean_vector(gen_vecs)
    corpus = _corpus_vectors(pack_id)
    corpus_mean = mean_vector(corpus)
    # 有语料时默认语料占 2/3。例外：语料是单声部 lead-sheet（voice_count≈1）
    # 而生成是多声部编制（爵士 combo 等）——若仍偏语料，生成永远「不像自己」。
    # 此时改为生成占 2/3，语料只校正调性/和声惯性。
    if corpus_mean and gen_mean:
        corpus_vc = 0.0
        try:
            import yaml  # local：契约里的 voice_count 比 empirical means 更稳
            pdir = _pack_dir(pack_id) or ''
            ctp = os.path.join(pdir, 'style_contract.yaml')
            if os.path.isfile(ctp):
                with open(ctp, encoding='utf-8') as f:
                    ctd = yaml.safe_load(f) or {}
                corpus_vc = float(
                    ((ctd.get('texture') or {}).get('voice_count')) or 0)
        except Exception:
            corpus_vc = 0.0
        if 0 < corpus_vc < 1.5:
            vec = [(c + 2 * g) / 3 for c, g in zip(corpus_mean, gen_mean)]
        else:
            vec = [(2 * c + g) / 3 for c, g in zip(corpus_mean, gen_mean)]
    else:
        vec = corpus_mean or gen_mean
    fp = {
        'pack_id': pack_id,
        'genre_id': gid,
        'vector': [round(x, 6) for x in vec],
        'dims': [f'{s}.{k}' for s, k, _d in _VECTOR_SPEC] + ['melody.span', 'form.count'],
        'sources': {'generated': len(gen_vecs), 'corpus': len(corpus)},
        'gen_vector': [round(x, 6) for x in gen_mean],
        'corpus_vector': [round(x, 6) for x in corpus_mean],
        'errors': errors,
    }
    try:
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(fp, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    # 显式失效：不能只依赖 mtime 签名——部分文件系统 mtime 只有秒级精度，
    # 同一秒内重算同一包会写出相同 mtime，缓存就不会失效。
    invalidate_fingerprint_cache()
    return fp


def build_all_fingerprints(seeds: int = 3, force: bool = False) -> Dict[str, bool]:
    """补齐全部风格包的六维指纹（此前只有 4 个包有语料 features）。"""
    out: Dict[str, bool] = {}
    for g in skill_lib.list_genres():
        pid = str(g.get('pack_id') or '')
        if not pid or not g.get('enabled', True):
            continue
        try:
            pack_fingerprint(pid, seeds=seeds, force=force)
            out[pid] = True
        except Exception as e:
            out[pid] = False
    return out


def all_fingerprints() -> Dict[str, List[float]]:
    """{pack_id: 向量}（读缓存；未生成的跳过）。

    带读盘缓存（见 `_fingerprint_signature`）：本函数会被风格重叠率/命中排名/
    最近邻分别调用，一次报告渲染累计几十次，而每次读盘在受管沙箱下约 38ms
    —— 实测单次验收测试有 88/109 秒耗在这里的重复读盘上。
    """
    sig = _fingerprint_signature()
    if _FP_CACHE.get('vec_sig') == sig:
        return dict(_FP_CACHE['vecs'])
    out: Dict[str, List[float]] = {}
    for g in skill_lib.list_genres():
        pid = str(g.get('pack_id') or '')
        pdir = g.get('path')
        if not pid or not pdir:
            continue
        fp_path = os.path.join(pdir, 'fingerprint.json')
        if not os.path.exists(fp_path):
            continue
        try:
            with open(fp_path, encoding='utf-8') as f:
                out[pid] = json.load(f)['vector']
        except Exception:
            continue
    _FP_CACHE['vec_sig'], _FP_CACHE['vecs'] = sig, out
    return dict(out)


# ---------------------------------------------------------------- 指标

def style_overlap(spec: Dict[str, Any], pack_id: str,
                  standardize: bool = True) -> Optional[float]:
    """指标①：生成谱与目标风格包指纹的相似度（0–1，越大越像该风格）。

    默认做跨包标准化（见 :func:`pack_stats`）。注意：这是个**相对量**——
    它回答"这份谱在 26 个风格里更靠近哪一个"，而不是"绝对像不像肖邦"。
    """
    try:
        vec = spec_vector(spec)
    except Exception:
        # 无效 spec（如 parts 为空）无法评估，返回 None 而非崩溃
        return None
    try:
        fp = pack_fingerprint(pack_id)
    except Exception:
        return None
    if not isinstance(fp, dict):
        return None
    pv = fp.get('vector') or []
    if not pv:
        return None
    if standardize:
        mean, std = pack_stats()
        if mean:
            vec, pv = zscore(vec, mean, std), zscore(pv, mean, std)
    return round(cosine(vec, pv), 4)


def style_rank(spec: Dict[str, Any], pack_id: Optional[str] = None
               ) -> Dict[str, Any]:
    """指标①（可判定版）：把生成谱与全部风格包比一遍，看目标包排第几。

    这才是与报告「流派命中率」可比的量：top1 / top3 是否命中。
    返回 {'rank','total','top1','top3','sims': [(pack_id, sim), …]}
    """
    try:
        vec = spec_vector(spec)
    except Exception:
        return {'rank': None, 'total': 0, 'top1': False, 'top3': False,
                'sims': []}
    fps = all_fingerprints()
    if not fps:
        return {'rank': None, 'total': 0, 'top1': False, 'top3': False, 'sims': []}
    mean, std = pack_stats()
    sims = []
    for pid, pv in fps.items():
        a, b = (zscore(vec, mean, std), zscore(pv, mean, std)) if mean else (vec, pv)
        sims.append((pid, round(cosine(a, b), 4)))
    sims.sort(key=lambda x: -x[1])
    rank = None
    if pack_id:
        for i, (pid, _s) in enumerate(sims, start=1):
            if pid == pack_id:
                rank = i
                break
    return {
        'rank': rank, 'total': len(sims),
        'top1': rank == 1, 'top3': bool(rank and rank <= 3),
        'sims': sims[:5],
    }


def attribute_adherence(spec: Dict[str, Any], want: Dict[str, Any]
                        ) -> Dict[str, Any]:
    """指标②：属性遵循度。只检查 ``want`` 里**明确要求**的属性。

    返回 {'score': 0–1, 'checks': [{'attr','want','got','ok'}], 'n': n}
    """
    checks: List[Dict[str, Any]] = []
    pairs = (
        ('tempo', 'tempo', lambda a, b: int(a) == int(b)),
        ('meter', 'time_signature', lambda a, b: str(a) == str(b)),
        ('key_sharps', 'key_sharps', lambda a, b: int(a) == int(b)),
        ('mode', 'mode', lambda a, b: str(a) == str(b)),
        ('bars', 'bars', lambda a, b: int(a) == int(b)),
        ('voices', 'voices', lambda a, b: int(a) == int(b)),
    )
    for wkey, skey, eq in pairs:
        if want.get(wkey) is None:
            continue
        got = spec.get(skey)
        try:
            ok = eq(want[wkey], got)
        except Exception:
            ok = False
        checks.append({'attr': wkey, 'want': want[wkey], 'got': got, 'ok': bool(ok)})
    if want.get('fugue') is not None:
        wf = str(want['fugue']).lower()
        got = bool(spec.get('fugue'))
        ok = (got is False and wf in ('off', 'none', 'false', '0')) or (
            got is True and wf in ('auto', 'full', 'on', 'true', '1'))
        checks.append({'attr': 'fugue', 'want': want['fugue'], 'got': got, 'ok': ok})
    n = len(checks)
    score = (sum(1 for c in checks if c['ok']) / n) if n else 1.0
    return {'score': round(score, 4), 'checks': checks, 'n': n}


def nearest_neighbor(spec: Dict[str, Any],
                     pool: Optional[Dict[str, Sequence[float]]] = None,
                     exclude: Optional[str] = None
                     ) -> Dict[str, Any]:
    """指标③：最近邻（防抄袭）。

    ``pool`` 默认为全部风格包指纹（排除自身 ``exclude``）。
    返回 {'name','similarity','is_near_duplicate'}。
    """
    vec = spec_vector(spec)
    pool = pool if pool is not None else all_fingerprints()
    mean, std = pack_stats()
    best_name, best_sim = None, -1.0
    for name, v in pool.items():
        if exclude and name == exclude:
            continue
        a, b = (zscore(vec, mean, std), zscore(v, mean, std)) if mean else (vec, v)
        s = cosine(a, b)
        if s > best_sim:
            best_name, best_sim = name, s
    return {
        'name': best_name,
        'similarity': round(best_sim, 4) if best_name else None,
        'is_near_duplicate': bool(best_name and best_sim >= NEAR_DUP_THRESHOLD),
        'threshold': NEAR_DUP_THRESHOLD,
    }


def evaluate(spec: Dict[str, Any], want: Optional[Dict[str, Any]] = None,
             pack_id: Optional[str] = None, *,
             corpus_compare: bool = True) -> Dict[str, Any]:
    """风格/属性/包级最近邻；可选语料单曲与片段对照。"""
    want = want or {}
    pid = pack_id or str(spec.get('genre_id') or '')
    if pid.startswith('genre:'):
        # 'genre:family/pack' → pack；退化格式 'genre:pack' → 去 'genre:' 前缀
        pid = pid.rsplit('/', 1)[-1] if '/' in pid else pid[len('genre:'):]
    res: Dict[str, Any] = {'pack_id': pid or None}
    res['style_overlap'] = style_overlap(spec, pid) if pid else None
    res['style_rank'] = style_rank(spec, pid or None)
    res['attribute_adherence'] = attribute_adherence(spec, want)
    res['nearest_neighbor'] = nearest_neighbor(spec, exclude=pid or None)
    if corpus_compare and pid:
        res['corpus_piece'] = nearest_corpus_piece(spec, pid)
        res['corpus_fragment'] = nearest_corpus_fragment(spec, pid)
    else:
        res['corpus_piece'] = None
        res['corpus_fragment'] = None
    return res


def evaluate_report(spec: Dict[str, Any], want: Optional[Dict[str, Any]] = None,
                    pack_id: Optional[str] = None, *,
                    corpus_compare: bool = True) -> str:
    """给报告面板/聊天的质量小结（markdown）。"""
    r = evaluate(spec, want=want, pack_id=pack_id, corpus_compare=corpus_compare)
    lines = ['## 生成质量评估', '']
    ov = r['style_overlap']
    pack_s = f"（目标包 {r['pack_id']}）" if r['pack_id'] else '（未指定目标包）'
    lines.append(f"- **风格重叠率**：{'N/A' if ov is None else f'{ov:.2f}'}{pack_s}")
    rk = r.get('style_rank') or {}
    if rk.get('rank'):
        hit = '✅ top1 命中' if rk['top1'] else (
            '⚠️ top3 命中' if rk['top3'] else '❌ 未进前三')
        lines.append(f"- **风格命中**：第 {rk['rank']}/{rk['total']} 名 — {hit}")
    aa = r['attribute_adherence']
    lines.append(f"- **属性遵循度**：{aa['score']:.2f}（{aa['n']} 项）")
    bad = [c for c in aa['checks'] if not c['ok']]
    for c in bad[:6]:
        lines.append(f"  - ❌ {c['attr']}：要求 {c['want']}，实际 {c['got']}")
    nn = r['nearest_neighbor']
    if nn['name']:
        flag = '⚠️ 高度相似，建议换 seed 重生成' if nn['is_near_duplicate'] else '✅ 无抄袭嫌疑'
        lines.append(f"- **包级最近邻**：{nn['name']}（相似度 {nn['similarity']:.2f}）— {flag}")
    else:
        lines.append('- **包级最近邻**：无可用指纹（先跑 `evaluate.build_all_fingerprints()`）')
    # 语料对照
    cp = r.get('corpus_piece') or {}
    cf = r.get('corpus_fragment') or {}
    if cp or cf:
        lines.append('')
        lines.append('### 语料对照')
        lines.append('')
        if cp.get('name'):
            flag = ('⚠️ 疑似整曲抄袭' if cp.get('is_near_duplicate')
                    else '✅ 非整曲复制')
            title = cp.get('title') or cp['name']
            lines.append(
                f"- **语料单曲最近邻**：`{cp['name']}`（《{title}》，"
                f"相似度 {cp['similarity']:.2f}，池 {cp.get('n_pool', 0)}）— {flag}")
        elif corpus_compare and r.get('pack_id'):
            lines.append('- **语料单曲最近邻**：无 features（该包尚未灌语料特征）')
        if cf.get('corpus_id'):
            flag = ('⚠️ 疑似片段抄袭' if cf.get('is_near_duplicate')
                    else '✅ 无高相似片段')
            lines.append(
                f"- **语料片段最近邻**：`{cf['corpus_id']}` "
                f"（生成窗起点小节 {cf.get('gen_start_bar')}，"
                f"相似度 {cf['similarity']:.2f}，"
                f"池 {cf.get('n_pool', 0)} / 生成窗 {cf.get('n_gen_windows', 0)}）"
                f"— {flag}")
        elif corpus_compare and r.get('pack_id') and cp.get('n_pool'):
            lines.append('- **语料片段最近邻**：未能切窗（源谱不可用或过短）')
    return '\n'.join(lines)


__all__ = [
    'style_vector', 'cosine', 'spec_features', 'spec_vector',
    'pack_fingerprint', 'build_all_fingerprints', 'all_fingerprints',
    'invalidate_fingerprint_cache', 'pack_stats',
    'style_overlap', 'attribute_adherence', 'nearest_neighbor',
    'corpus_piece_pool', 'nearest_corpus_piece',
    'corpus_fragment_pool', 'nearest_corpus_fragment',
    'evaluate', 'evaluate_report',
    'NEAR_DUP_THRESHOLD', 'CORPUS_PIECE_NEAR_DUP', 'CORPUS_FRAG_NEAR_DUP',
]
