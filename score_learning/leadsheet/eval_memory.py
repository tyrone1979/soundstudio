# -*- coding: utf-8 -*-
"""Cross-section memory evaluation for leadsheet generation (Table 6).

Evaluates the share of bars in later sections that recall an earlier bar
(either through section-level quotes/variants or bar-level motif recall)
before and after the memory planner, compared against empirical corpus rates.
Outputs runs/experiments/e9_memory_eval.json.
"""
import copy
import json
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from score_learning.leadsheet import script as S
from score_learning.leadsheet.compose import compose

STYLES: List[Tuple[str, str, str]] = [
    ('Piano sonata, allegro', 'sonata', 'allegro'),
    ('Symphony, allegro', 'symphony', 'allegro'),
    ('Waltz', 'folk', 'waltz'),
    ('Chopin mazurka', 'chopin', None),
    ('Madrigal', 'madrigal', None),
]


def eval_substyle(style_key: str, sub_key: str, n_seeds: int = 12,
                  enable_memory: bool = True) -> Dict[str, Any]:
    sc = copy.deepcopy(S.load(style_key))
    actual_sub = sub_key or (list(sc['subs'].keys())[0] if 'subs' in sc else None)

    if not enable_memory:
        # Disable motif recall
        if actual_sub and 'subs' in sc and actual_sub in sc['subs']:
            if 'motif_recall' in sc['subs'][actual_sub]['rhythm']:
                sc['subs'][actual_sub]['rhythm']['motif_recall'] = {'p_same': 0.0, 'p_shift': 0.0}
        elif 'rhythm' in sc and 'motif_recall' in sc['rhythm']:
            sc['rhythm']['motif_recall'] = {'p_same': 0.0, 'p_shift': 0.0}
        # Disable variant/quote roles in form plan
        if 'form' in sc and 'form_plan' in sc['form']:
            for fp in sc['form']['form_plan']:
                if isinstance(fp, dict) and 'sections' in fp:
                    for sec in fp['sections']:
                        if isinstance(sec, dict) and sec.get('role') in ('variant', 'quote'):
                            sec['role'] = 'introduce'

    tot_later = 0
    recalled = 0
    for seed in range(n_seeds):
        r = compose(sc, actual_sub, 'major', 0, 32, seed=str(seed))
        for u_idx, u in enumerate(r['units']):
            if u_idx == 0:
                continue
            is_copy = bool(u.get('copy_of'))
            for bar_str in u['rhythm']:
                tot_later += 1
                if is_copy or '回忆第' in bar_str or '沿用第' in bar_str:
                    recalled += 1

    rate = recalled / tot_later if tot_later else 0.0
    return {
        'recalled': recalled,
        'tot_later': tot_later,
        'rate': rate,
    }


def run_memory_eval(n_seeds: int = 12, save: bool = True) -> Dict[str, Any]:
    results = {}
    rows = []
    for label, style_key, sub_key in STYLES:
        sc = S.load(style_key)
        actual_sub = sub_key or (list(sc['subs'].keys())[0] if 'subs' in sc else None)
        mr = (sc['subs'][actual_sub]['rhythm'].get('motif_recall', {})
              if actual_sub and 'subs' in sc
              else sc.get('rhythm', {}).get('motif_recall', {}))
        corpus_rate = float(mr.get('p_same', 0.0)) + float(mr.get('p_shift', 0.0))

        bef = eval_substyle(style_key, sub_key, n_seeds=n_seeds, enable_memory=False)
        aft = eval_substyle(style_key, sub_key, n_seeds=n_seeds, enable_memory=True)

        row = {
            'label': label,
            'style': style_key,
            'sub': actual_sub,
            'corpus_rate': corpus_rate,
            'before_recalled': bef['recalled'],
            'before_tot': bef['tot_later'],
            'before_rate': bef['rate'],
            'after_recalled': aft['recalled'],
            'after_tot': aft['tot_later'],
            'after_rate': aft['rate'],
        }
        rows.append(row)
        print(f"{label:25s} | Before: {bef['rate']:5.1%} ({bef['recalled']}/{bef['tot_later']}) | "
              f"After: {aft['rate']:5.1%} ({aft['recalled']}/{aft['tot_later']}) | "
              f"Corpus: {corpus_rate:5.1%}")

    out = {
        'n_seeds': n_seeds,
        'rows': rows,
    }

    if save:
        out_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'runs', 'experiments', 'e9_memory_eval.json'
        )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"Saved evaluation results to {out_path}")

    return out


if __name__ == '__main__':
    run_memory_eval()
