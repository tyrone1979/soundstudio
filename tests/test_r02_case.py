from score_learning.grammar.rule_ablation import before_after_r02


def test_r02_case_is_attributable():
    out = before_after_r02(save=False)
    assert out['violations_on'] == []
    assert [v[0] for v in out['violations_off']] == ['R02']
    assert out['first_r02']['t'] == out['window'][-1]['t']
    assert 0 < out['beats_differing'] < out['n_beats']
