# -*- coding: utf-8 -*-
"""交付层：LilyPond 乐谱导出 + runs/ 归档。

LilyPond
--------
``music21.lily.translate`` **必须**先找到 lilypond 二进制才能工作（连生成文本
都要跑一次二进制），本机没装就没法用。所以这里自己写一个最小 .ly 生成器：
不依赖任何外部程序即可产出可读、可编译的 LilyPond 源码；要出 PDF 时才调用
``lilypond`` 二进制，找不到就明确告诉用户装什么、怎么装，而不是静默失败。

runs/ 归档
----------
每次生成把"这一版到底是怎么来的"全部落盘：spec（含 seed / 子种子 / 软权重 /
体系参数）、MusicXML、MIDI、LilyPond 源码、（可能有的）PDF、分析报告、
manifest（含三指标）。这样任何一份成品都能被**逐音符复现**和**审计**——
这是确定性内核相对"采样一次就没了"的生成模型最实在的优势，之前却没落盘。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_ROOT = os.path.join(HERE, 'runs')

# ---------------------------------------------------------------- LilyPond

#: 时值(四分音符数) → LilyPond 时长记号
_LY_DUR = {
    4.0: '1', 3.0: '2.', 2.0: '2', 1.5: '4.', 1.0: '4', 0.75: '8.',
    0.5: '8', 0.375: '16.', 0.25: '16', 0.125: '32',
}


def ly_duration(ql: float) -> str:
    """四分音符数 → LilyPond 时长记号（找不到就取不大于它的最大记号）。"""
    q = round(float(ql), 6)
    if q in _LY_DUR:
        return _LY_DUR[q]
    best = None
    for k in sorted(_LY_DUR, reverse=True):
        if k <= q + 1e-9:
            best = k
            break
    return _LY_DUR.get(best, '4')


def ly_pitch(name: str) -> str:
    """'C#4' / 'Bb3' → LilyPond 音名 'cis'' / 'bes,'。

    LilyPond 的中央 C（C4）记作 c'：八度 4 → 1 个撇，3 → 无，2 → 1 个逗号。
    """
    s = str(name or 'r').strip()
    if s.lower() in ('r', 'rest', 'none', ''):
        return 'r'
    m = re.match(r'^([A-Ga-g])([#b]*)(-?\d+)$', s)
    if not m:
        return 'r'
    letter, acc, octv = m.group(1).lower(), m.group(2), int(m.group(3))
    # 升：is / 降：es（LilyPond 荷兰式）
    suffix = 'is' * acc.count('#') + 'es' * acc.count('b')
    if octv >= 4:
        marks = "'" * (octv - 3)
    else:
        marks = ',' * (3 - octv)
    return f'{letter}{suffix}{marks}'


def ly_key(sharps: int, mode: str = 'major') -> str:
    """调号 → LilyPond \\key 记号（如 \\key g \\major）。"""
    tbl = {0: 'c', 1: 'g', 2: 'd', 3: 'a', 4: 'e', 5: 'b', 6: 'fis', 7: 'cis',
           -1: 'f', -2: 'bes', -3: 'ees', -4: 'aes', -5: 'des', -6: 'ges',
           -7: 'ces'}
    try:
        k = tbl[int(sharps)]
    except Exception:
        k = 'c'
    return f'\\key {k} \\{str(mode or "major").lower()}'


def ly_clef(clef: str) -> str:
    c = str(clef or 'treble').lower()
    return {'bass': '\\clef bass', 'alto': '\\clef alto',
            'tenor': '\\clef tenor'}.get(c, '\\clef treble')


def score_to_lilypond(score, title: str = '', composer: str = '',
                      sharps: int = 0, mode: str = 'major',
                      ts: str = '4/4', tempo: int = 90,
                      spec: Optional[Dict[str, Any]] = None) -> str:
    """music21 分数 → LilyPond 源码（不依赖 lilypond 二进制）。

    ``spec`` 可选：给了就用 spec 里各声部的 clef（music21 的 Part 常常没有
    Clef 对象，只按对象找会一律退化成高音谱号）。
    """
    from music21 import chord as m21chord, note as m21note
    spec_clefs = [str((p or {}).get('clef') or '')
                  for p in ((spec or {}).get('parts') or [])]

    title = str(title or 'Untitled').replace('"', '')
    composer = str(composer or '').replace('"', '')
    out: List[str] = [
        '\\version "2.24.0"', '',
        '\\header {',
        f'  title = "{title}"',
        f'  composer = "{composer}"',
        '  tagline = ##f',
        '}', '',
        '\\score {', '  <<',
    ]
    try:
        parts = list(score.parts) or [score]
    except Exception:
        parts = [score]
    for pi, part in enumerate(parts):
        try:
            pname = str(getattr(part, 'partName', '') or f'Part{pi + 1}')
        except Exception:
            pname = f'Part{pi + 1}'
        pname = pname.replace('"', '')
        clef = 'treble'
        try:
            for c in part.getElementsByClass('Clef'):
                sign = str(getattr(c, 'sign', 'G') or 'G')
                clef = {'G': 'treble', 'F': 'bass', 'C': 'alto'}.get(sign, 'treble')
                break
        except Exception:
            pass
        if pi < len(spec_clefs) and spec_clefs[pi]:
            clef = str(spec_clefs[pi]).lower()
        out.append(f'    \\new Staff \\with {{ instrumentName = #"{pname}" }} {{')
        out.append(f'      {ly_clef(clef)}')
        out.append(f'      {ly_key(sharps, mode)}')
        out.append(f'      \\time {ts}')
        if pi == 0:
            out.append(f'      \\tempo 4 = {int(tempo)}')
        try:
            els = list(part.recurse().notesAndRests)
        except Exception:
            els = []
        line: List[str] = []
        for el in els:
            try:
                ql = float(el.duration.quarterLength)
            except Exception:
                continue
            d = ly_duration(ql)
            if isinstance(el, m21chord.Chord):
                ps = ' '.join(ly_pitch(p.nameWithOctave) for p in el.pitches
                              if ly_pitch(p.nameWithOctave) != 'r')
                line.append(f'<{ps}>{d}' if ps else f'r{d}')
            elif isinstance(el, m21note.Note):
                line.append(f'{ly_pitch(el.pitch.nameWithOctave)}{d}')
            else:
                line.append(f'r{d}')
            if len(line) >= 8:
                out.append('      ' + ' '.join(line))
                line = []
        if line:
            out.append('      ' + ' '.join(line))
        out.append('    }')
    out += ['  >>', '  \\layout { }', '  \\midi { }', '}', '']
    return '\n'.join(out)


def find_lilypond() -> Optional[str]:
    """找 lilypond 二进制（PATH 或常见安装位置）。"""
    p = shutil.which('lilypond')
    if p:
        return p
    for cand in ('/usr/local/bin/lilypond', '/opt/homebrew/bin/lilypond',
                 '/Applications/LilyPond.app/Contents/Resources/bin/lilypond'):
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def render_pdf(ly_path: str, out_dir: Optional[str] = None) -> Tuple[Optional[str], str]:
    """把 .ly 编译成 PDF。返回 (pdf路径或None, 说明)。

    没装 lilypond 时不静默失败——明确给出安装方式。
    """
    exe = find_lilypond()
    if not exe:
        return None, ('未找到 lilypond 二进制，PDF 未生成。'
                      '已写出 .ly 源码，安装后可离线编译：'
                      'macOS 用 `brew install lilypond`，或官网 lilypond.org 下载；'
                      '装好后重跑 deliver.render_pdf("<该 .ly 路径>")。')
    out_dir = out_dir or os.path.dirname(os.path.abspath(ly_path)) or '.'
    base = os.path.splitext(os.path.basename(ly_path))[0]
    pdf = os.path.join(out_dir, base + '.pdf')
    # 先清掉旧残留：否则本次编译失败时会把上一次的陈旧 PDF 当成功产物。
    # 必须在 subprocess 之前删除，否则会误删刚生成的 PDF。
    try:
        if os.path.exists(pdf):
            os.remove(pdf)
    except Exception:
        pass
    try:
        r = subprocess.run([exe, '--pdf', '-o', out_dir, ly_path],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        return None, f'lilypond 调用失败：{e}'
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or '')[-400:]
        return None, f'lilypond 退出码 {r.returncode}，未产出 PDF：{tail}'
    if os.path.exists(pdf):
        return pdf, 'ok'
    return None, 'lilypond 退出码 0 但未产出 PDF，请检查 .ly 源码与输出目录权限'


def export_lilypond(score, path: str, title: str = '', composer: str = '',
                    sharps: int = 0, mode: str = 'major', ts: str = '4/4',
                    tempo: int = 90, want_pdf: bool = True,
                    spec: Optional[Dict[str, Any]] = None
                    ) -> Dict[str, Any]:
    """写 .ly（并尽量出 PDF），返回 {'ly': path, 'pdf': path|None, 'msg': str}。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    src = score_to_lilypond(score, title=title, composer=composer, sharps=sharps,
                            mode=mode, ts=ts, tempo=tempo, spec=spec)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    res: Dict[str, Any] = {'ly': path, 'pdf': None, 'msg': '已写出 LilyPond 源码'}
    if want_pdf:
        pdf, msg = render_pdf(path)
        res['pdf'], res['msg'] = pdf, msg
    return res


# ---------------------------------------------------------------- runs/ 归档

def _slug(text: str, limit: int = 24) -> str:
    s = re.sub(r'[^\w一-鿿-]+', '-', str(text or '')).strip('-')
    return (s[:limit] or 'score')


def archive_run(spec: Dict[str, Any], score=None, *,
                report_md: str = '', wav_path: str = '',
                root: str = RUNS_ROOT, want_pdf: bool = True,
                metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一次生成的所有产物归档到 ``runs/<时间戳>-<曲名>/``。

    返回 manifest（含各产物路径、seed、体系/风格、三指标）。
    """
    ts = _dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    title = str(spec.get('title') or 'untitled')
    d = os.path.join(root, f'{ts}-{_slug(title)}')
    os.makedirs(d, exist_ok=True)

    written: Dict[str, str] = {}
    errors: List[str] = []   # 归档失败项（写入 manifest，不再静默吞掉）
    # 1) spec（含 seed / 子种子 / 软权重 / 体系参数）——复现的唯一凭据
    sp = os.path.join(d, 'spec.json')
    try:
        with open(sp, 'w', encoding='utf-8') as f:
            json.dump(spec, f, ensure_ascii=False, indent=2, default=str)
        written['spec'] = sp
    except Exception as e:
        errors.append(f'spec.json 写入失败（复现凭据缺失）：{e}')

    if score is not None:
        # 2) MusicXML
        try:
            xp = os.path.join(d, 'score.musicxml')
            score.write('musicxml', xp)
            written['musicxml'] = xp
        except Exception as e:
            errors.append(f'musicxml 写入失败：{e}')
        # 3) MIDI
        try:
            mp = os.path.join(d, 'score.mid')
            score.write('midi', mp)
            written['midi'] = mp
        except Exception as e:
            errors.append(f'midi 写入失败：{e}')
        # 4) LilyPond（+ 可能的 PDF）
        try:
            lp = os.path.join(d, 'score.ly')
            r = export_lilypond(
                score, lp, title=title,
                composer=str(spec.get('composer') or ''),
                sharps=int(spec.get('key_sharps') or 0),
                mode=str(spec.get('mode') or 'major'),
                ts=str(spec.get('time_signature') or '4/4'),
                tempo=int(spec.get('tempo') or 90), want_pdf=want_pdf,
                spec=spec)
            written['lilypond'] = lp
            if r.get('pdf'):
                written['pdf'] = r['pdf']
            elif r.get('msg'):
                written['lilypond_note'] = r['msg']
        except Exception as e:
            errors.append(f'lilypond 导出失败：{e}')

    # 5) WAV（若已合成）
    if wav_path and os.path.exists(wav_path):
        try:
            dst = os.path.join(d, 'score.wav')
            shutil.copy2(wav_path, dst)
            written['wav'] = dst
        except Exception as e:
            errors.append(f'wav 归档失败：{e}')

    # 6) 报告 + 三指标
    if report_md:
        try:
            rp = os.path.join(d, 'report.md')
            with open(rp, 'w', encoding='utf-8') as f:
                f.write(report_md)
            written['report'] = rp
        except Exception as e:
            errors.append(f'report 写入失败：{e}')
    if metrics is None:
        try:
            import evaluate as _ev
            metrics = _ev.evaluate(spec)
        except Exception:
            metrics = None

    sysinfo = spec.get('system') or {}
    manifest = {
        'created': _dt.datetime.now().isoformat(timespec='seconds'),
        'title': title,
        'seed': spec.get('seed'),
        'subseeds': spec.get('subseeds'),
        'soft_weights': spec.get('soft_weights'),
        'genre_id': spec.get('genre_id'),
        'system': sysinfo or None,
        'bars': spec.get('bars'), 'voices': spec.get('voices'),
        'time_signature': spec.get('time_signature'),
        'tempo': spec.get('tempo'),
        'files': written,
        'errors': errors or None,
        'metrics': metrics,
        'reproduce': (
            f"run_program(style_skeleton.build_skeleton_calls("
            f"{spec.get('genre_id')!r}, bars={spec.get('bars')}, "
            f"voices={spec.get('voices')}, seed={spec.get('seed')!r}))"
            if spec.get('genre_id') else
            f"systems.build_system({sysinfo.get('kind')!r}, "
            f"bars={spec.get('bars')}, seed={spec.get('seed')!r})"
        ),
    }
    try:
        with open(os.path.join(d, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        errors.append(f'manifest 写入失败：{e}')
    manifest['dir'] = d
    return manifest


def list_runs(root: str = RUNS_ROOT, limit: int = 50) -> List[Dict[str, Any]]:
    """列出最近的归档（倒序）。"""
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root), reverse=True)[:limit]:
        mp = os.path.join(root, name, 'manifest.json')
        if not os.path.exists(mp):
            continue
        try:
            with open(mp, encoding='utf-8') as f:
                d = json.load(f)
            d['dir'] = os.path.join(root, name)
            out.append(d)
        except Exception:
            continue
    return out


__all__ = [
    'score_to_lilypond', 'ly_pitch', 'ly_duration', 'ly_key', 'ly_clef',
    'find_lilypond', 'render_pdf', 'export_lilypond',
    'archive_run', 'list_runs', 'RUNS_ROOT',
]
