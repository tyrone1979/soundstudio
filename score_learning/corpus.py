# -*- coding: utf-8 -*-
"""Batch analyze a folder of MusicXML/MIDI into feature JSONs."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from .analyzer import analyze_path

# ABC 记谱在民间音乐库里是主流格式（music21 原生支持 converter.parse）；
# 以前只认 XML/MIDI，导致 oneills/ryansMammoth 这类单曲 ABC 无法直接入库。
SCORE_EXTS = {'.xml', '.musicxml', '.mxl', '.mid', '.midi', '.abc', '.krn'}


def list_score_files(root: str) -> List[str]:
    out: List[str] = []
    if os.path.isfile(root):
        return [root] if os.path.splitext(root)[1].lower() in SCORE_EXTS else []
    for dirpath, _, files in os.walk(root):
        for name in files:
            if os.path.splitext(name)[1].lower() in SCORE_EXTS:
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def analyze_corpus(
    root: str,
    genre_hint: str = '',
    features_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    files = list_score_files(root)
    features: List[Dict[str, Any]] = []
    if features_dir:
        os.makedirs(features_dir, exist_ok=True)
    used_names: set = set()
    for path in files:
        try:
            feat = analyze_path(path, genre_hint=genre_hint)
        except Exception as e:
            feat = {
                'metadata': {'title': os.path.basename(path), 'source': path, 'error': str(e)},
                'global': {},
                'melody': {},
                'rhythm': {},
                'harmony': {'available': False},
            }
        features.append(feat)
        if features_dir and not feat.get('metadata', {}).get('error'):
            stem = os.path.splitext(os.path.basename(path))[0]
            # 不同子目录的同名文件会互相覆盖：重名时追加序号去重
            name = stem
            k = 1
            while name in used_names:
                k += 1
                name = f'{stem}_{k}'
            used_names.add(name)
            out = os.path.join(features_dir, f'{name}.json')
            with open(out, 'w', encoding='utf-8') as f:
                json.dump(feat, f, ensure_ascii=False, indent=2)
    return features


def load_features_dir(features_dir: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(features_dir):
        return out
    for name in sorted(os.listdir(features_dir)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(features_dir, name)
        with open(path, encoding='utf-8') as f:
            out.append(json.load(f))
    return out
