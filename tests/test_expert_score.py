from score_learning.grammar.expert_score import (
    cohen_kappa, resolve_cadences, score_checker, wilson,
)


def test_kappa_and_wilson():
    assert cohen_kappa(['有错', '无错', '有错'], ['有错', '无错', '有错']) == 1.0
    assert cohen_kappa(['有错', '无错'], ['无错', '有错']) == -1.0
    lo, hi = wilson(8, 10)
    assert 0.4 < lo < 0.8 < hi <= 1.0
    assert wilson(0, 0) == (None, None)


def _key():
    items = [{'item': 'E001', 'stratum': 'R01'}, {'item': 'E002', 'stratum': 'R01'},
             {'item': 'E003', 'stratum': 'clean'}, {'item': 'E004', 'stratum': 'clean'}]
    by_rule = {r: 0 for r in ('R01', 'R02', 'R03', 'R03/R04', 'R06', 'R07', 'R08', 'R09')}
    by_rule['R01'] = 10
    return {'items': items,
            'population': {'bach': {'by_rule': by_rule, 'clean': 100, 'flagged': 10}}}


def test_score_checker_precision_recall():
    rows = {'E001': {'verdict': '有错', 'type': '平行五八度', 'label': '正确'},
            'E002': {'verdict': '无错', 'type': '', 'label': '有误'},
            'E003': {'verdict': '无错', 'type': '', 'label': ''},
            'E004': {'verdict': '有错', 'type': '其他', 'label': ''}}
    res = score_checker(_key(), {'A': rows, 'B': dict(rows)})
    a = res['per_rater']['A']
    assert a['per_stratum']['R01']['rate'] == 0.5
    assert a['per_stratum']['R01']['type_match'] == 1
    assert a['miss_rate_unflagged'] == 0.5
    # TP̂ = 0.5 × 10 = 5, FN̂ = 0.5 × 100 = 50
    assert abs(a['recall_estimated'] - 5 / 55) < 1e-3
    assert a['chord_label_correct'] == 0.5
    assert res['kappa']['binary'] == 1.0


def test_resolve_cadences():
    s1 = {'C1': {'verdict': 'RECODE_HC', 'confidence': '高', 'basis': 'x'},
          'C2': {'verdict': 'KEEP_OTHER', 'confidence': '中', 'basis': 'y'},
          'C3': {'verdict': '', 'confidence': '', 'basis': ''}}
    s2 = {'C1': {'verdict': 'RECODE_HC', 'confidence': '中', 'basis': 'z'},
          'C2': {'verdict': 'NOT_CADENCE', 'confidence': '高', 'basis': 'w'},
          'C3': {'verdict': '', 'confidence': '', 'basis': ''}}
    r = resolve_cadences({'a.xlsx': s1, 'b.xlsx': s2})
    assert list(r['agreed']) == ['C1']
    assert r['conflict'] == {'C2': {'a.xlsx': 'KEEP_OTHER', 'b.xlsx': 'NOT_CADENCE'}}
    assert r['pending'] == ['C3']
