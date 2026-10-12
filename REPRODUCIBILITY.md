# SPE artifact reproduction

The reported experiments were run with Python 3.11 on macOS using the
dependency snapshot in `requirements-lock.txt`. Corpus provenance and licences
are documented in `PROVENANCE.md`.

## Environment

```bash
python3.11 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements-lock.txt
```

Some optional desktop/audio dependencies require system libraries, but the
paper experiments and tests do not require audio playback.

## Complete paper run

From the repository root:

```bash
PYTHON=venv/bin/python ./scripts/reproduce_spe.sh
```

This regenerates the statistical, range-ablation, rule-ablation,
fault-injection, and cross-section-memory artifacts under
`runs/experiments/`, then runs the complete test suite. In particular,
`e1i_estimator_piece_bootstrap.json` is the paired whole-piece JM--WB
comparison used for the SPE estimator claims. The full statistical matrix is
computationally expensive. For a smoke test:

```bash
venv/bin/python -m score_learning.experiments --quick
venv/bin/python -m pytest \
  tests/test_leadsheet_memory.py \
  tests/test_grammar_leadsheet.py \
  tests/test_smoothing_stats.py -q
```

## Manuscript

```bash
cd papers/spe
tectonic -X compile main.tex
```

The expected manuscript output is a 12-page `papers/spe/main.pdf`.

## Manuscript result map

- Table 3: `runs/relation/learn_all_stages.json`
- Table 4: `runs/experiments/e2b_range_ablation.json`
- Figure 5: `runs/experiments/e6_fault_injection.json`
- Table 5 and its piece-level inference:
  `e1_matrix.json`, `e1g_lambda.json`,
  `e1h_piece_bootstrap.json`, and
  `e1i_estimator_piece_bootstrap.json`
- Human challenge-set metrics:
  `runs/experiments/e9_expert_adjudication.json`
- Table 6: `runs/experiments/e9_memory_eval.json`

The human-rating artifact records fixed anonymized expert judgments and is not
overwritten by the automated reproduction script. All computational results
are regenerated from the pinned code and cached or fetched corpora.
