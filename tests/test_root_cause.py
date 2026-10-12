from score_learning.grammar.check import rank_violations
from score_learning.grammar.fault_inject import attribution


def test_rank_prefers_smallest_support_and_is_stable():
    viol = [{'rule': 'R09', 't': 2.0, 'msg': 'a'},
            {'rule': 'R08', 't': 2.0, 'msg': 'b'},
            {'rule': 'R03/R04', 't': 2.0, 'msg': 'c'},
            {'rule': 'R05', 't': 2.0, 'msg': 'd'},
            {'rule': 'R08', 't': 3.0, 'msg': 'e'}]
    ranked = rank_violations(viol)
    assert [v['msg'] for v in ranked] == ['d', 'b', 'e', 'c', 'a']
    assert rank_violations([]) == []


def test_attribution_counts():
    rows = [
        {'placed': True, 'detected': True, 'rule': 'R05',
         'violations': [{'rule': 'R03/R04', 't': 1.0, 'msg': ''},
                        {'rule': 'R05', 't': 1.0, 'msg': ''}]},
        {'placed': True, 'detected': True, 'rule': 'R04',
         'violations': [{'rule': 'R03/R04', 't': 1.0, 'msg': ''}]},
        {'placed': True, 'detected': False, 'rule': 'R01', 'violations': []},
    ]
    at = attribution(rows)
    assert at['n'] == 2
    assert at['emission'] == 1
    assert at['ranked'] == 2
    assert at['ranked_top2'] == 2
    assert at['chance'] == 1.5
