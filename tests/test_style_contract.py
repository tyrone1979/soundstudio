# -*- coding: utf-8 -*-
"""测试：确定性风格契约（StyleContract）—— 验证风格不再是提示词而是可执行参数。

核心命题有三个：
  A. 同一契约 + 同一 seed ⇒ 逐音符可复现（确定性）
  B. 不同契约 ⇒ 产生体裁正确的不同结果（风格真的生效）
  C. 不传契约 ⇒ 与历史行为完全一致（零侵入）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compose_kernel as K          # noqa: E402
import style_contract as SC          # noqa: E402

ok = 0
bad = 0


def check(name, cond, extra=''):
    global ok, bad
    if cond:
        ok += 1
        print(f'  ✅ {name}')
    else:
        bad += 1
        print(f'  ❌ {name}  {extra}')


PROG = ['I', 'vi', 'ii', 'V', 'I']


def build(style, seed='contrast-test'):
    calls = [
        {'script': 'new_score',
         'args': {'title': 'T', 'meter': '4/4', 'bars': 8, 'voices': 3,
                  'seed': seed, 'style': style}},
        {'script': 'set_sections',
         'args': {'sections': [{'name': 'A', 'bars': 8, 'dynamic': 'mf'}]}},
        {'script': 'set_harmony', 'args': {'section': 'A', 'progression': PROG}},
        {'script': 'accompaniment',
         'args': {'section': 'A', 'pattern': 'block_chords'}},
        {'script': 'melody', 'args': {'section': 'A', 'rhythm': 'flowing'}},
        {'script': 'cadence', 'args': {'bars': 1}},
        {'script': 'finalize', 'args': {}},
    ]
    holder = {}
    orig = K.SCRIPT_IMPL['set_harmony']

    def spy(st, args):
        r = orig(st, args)
        holder['state'] = st
        return r
    K.SCRIPT_IMPL['set_harmony'] = spy
    try:
        spec = K.run_program({'calls': calls})
    finally:
        K.SCRIPT_IMPL['set_harmony'] = orig
    return spec, holder.get('state')


JAZZ = {
    'pack_id': 'jazz-demo', 'evidence_n': 60,
    'harmony': {'quality_weights': {
        'dominant7': 0.35, 'major7': 0.2, 'minor7': 0.25,
        'triad': 0.15, 'half_diminished7': 0.05},
        'evidence_n': 60},
    'tempo': {'default': 132, 'min': 100, 'max': 180, 'evidence_n': 60},
}
CHORALE = {
    'pack_id': 'chorale-demo', 'evidence_n': 24,
    'harmony': {'quality_weights': {'triad': 1.0}, 'evidence_n': 24},
    'tempo': {'default': 76, 'min': 60, 'max': 92, 'evidence_n': 24},
}


def seventh_bars(st):
    if st is None:
        return 0, 0
    tot = 0
    sev = 0
    for bar in st.harmony:
        ch = st.harmony[bar]
        if not ch:
            continue
        tot += 1
        if len(ch) >= 4:
            sev += 1
    return sev, tot


def test_contract_basics():
    print('=== 契约数据结构 ===')
    c = SC.contract_from_dict(JAZZ)
    check('harmony 可用（evidence 足够）', c.harmony.is_usable())
    check('七和弦占比计算正确',
          abs(c.harmony.seventh_ratio() - 0.85) < 0.001,
          f'得到 {c.harmony.seventh_ratio()}')
    check('tempo 可用', c.tempo.is_usable())

    little = SC.contract_from_dict(
        {'evidence_n': 2, 'harmony': {'quality_weights': {'triad': 1.0},
                                      'evidence_n': 2}})
    check('证据不足时该分区不生效', not little.harmony.is_usable())

    ch = SC.contract_from_dict(
        {'evidence_n': 24, 'harmony': {'quality_weights': {'triad': 1.0},
                                       'evidence_n': 24}})
    check('众赞歌契约七和弦占比为 0', ch.harmony.seventh_ratio() == 0.0)


def test_style_changes_harmony():
    print('=== 风格真的改变和声（不是提示词）===')
    _, jazz_st = build(JAZZ)
    _, chor_st = build(CHORALE)
    _, base_st = build(None)

    js, jt = seventh_bars(jazz_st)
    cs, ct = seventh_bars(chor_st)
    bs, bt = seventh_bars(base_st)
    check('爵士契约产生七和弦', js > 0, f'{js}/{jt}')
    check('众赞歌契约不产生七和弦', cs == 0, f'{cs}/{ct}')
    check('无契约时不产生七和弦', bs == 0, f'{bs}/{bt}')
    check('爵士的七和弦多于众赞歌', js > cs, f'jazz={js} chorale={cs}')


def test_determinism():
    print('=== 确定性：同契约同 seed 可复现 ===')
    a1, s1 = build(JAZZ)
    a2, s2 = build(JAZZ)
    check('两次生成 tempo 相同', a1.get('tempo') == a2.get('tempo'),
          f'{a1.get("tempo")} vs {a2.get("tempo")}')
    h1 = {b: list(c) for b, c in s1.harmony.items()}
    h2 = {b: list(c) for b, c in s2.harmony.items()}
    check('两次生成和声逐音相同', h1 == h2)


def test_tempo_default():
    print('=== 契约提供体裁缺省速度 ===')
    jspec, _ = build(JAZZ)
    cspec, _ = build(CHORALE)
    bspec, _ = build(None)
    check('爵士契约给出 132', jspec.get('tempo') == 132, str(jspec.get('tempo')))
    check('众赞歌契约给出 76', cspec.get('tempo') == 76, str(cspec.get('tempo')))
    check('无契约时为内核默认 90', bspec.get('tempo') == 90, str(bspec.get('tempo')))

    # 显式参数必须优先于契约
    calls = [
        {'script': 'new_score',
         'args': {'title': 'T', 'meter': '4/4', 'tempo': 60, 'bars': 8,
                  'voices': 3, 'seed': 'x', 'style': JAZZ}},
        {'script': 'set_sections',
         'args': {'sections': [{'name': 'A', 'bars': 8, 'dynamic': 'mf'}]}},
        {'script': 'set_harmony', 'args': {'section': 'A', 'progression': PROG}},
        {'script': 'accompaniment',
         'args': {'section': 'A', 'pattern': 'block_chords'}},
        {'script': 'melody', 'args': {'section': 'A', 'rhythm': 'flowing'}},
        {'script': 'cadence', 'args': {'bars': 1}},
        {'script': 'finalize', 'args': {}},
    ]
    sp = K.run_program({'calls': calls})
    check('显式 tempo 优先于契约', sp.get('tempo') == 60, str(sp.get('tempo')))


def test_voice_leading_correct():
    print('=== 配和声法：不得给属和弦加大七 ===')
    _, jazz_st = build(JAZZ)
    # V 级所在小节的音集合不能是 Vmaj7（G B D F#）
    vmaj7 = {'G', 'B', 'D', 'F#'}
    found_vmaj7 = False
    for bar in jazz_st.harmony:
        names = set(jazz_st.harmony[bar])
        if vmaj7 <= names and len(names) == 4:
            found_vmaj7 = True
    check('未产生 Vmaj7（大七属和弦是配和声法错误）', not found_vmaj7)
    check('style_notes 记录了施加的约束',
          len(getattr(jazz_st, 'style_notes', [])) > 0)


def test_build_from_features():
    print('=== 从语料特征聚合契约 ===')
    feats = []
    for i in range(12):
        feats.append({
            'metadata': {'title': f't{i}'},
            'global': {'meter': '6/8' if i < 9 else '9/8', 'tempo': 110 + i,
                       'bars': 16},
            'melody': {'stepwise_ratio': 0.5, 'leap_ratio': 0.12,
                       'repeated_note_ratio': 0.1, 'mean_midi': 72.0},
            'rhythm': {'density_notes_per_bar': 5.0, 'beat1_onset_share': 0.25,
                       'syncopation_proxy': 0.68},
            'harmony': {'available': True, 'quality_hist': {
                'dominant7': 0.4, 'minor7': 0.3, 'triad': 0.3},
                'harmonic_rhythm_chords_per_bar': 1.2},
            'groove': {'swing_ratio': 1.9, 'triplet_ratio': 0.1},
            'form_metrics': {'repetition_ratio': 0.5, 'phrase_len': 4.0,
                             'unique_motifs': 4},
            'texture': {'voice_count': 3},
        })
    c = SC.build_contract_from_features(feats, pack_id='jig', family='folk')
    check('拍号主值识别为 6/8', c.meter.primary == '6/8', str(c.meter.primary))
    check('拍号权重比例正确',
          abs(c.meter.weights.get('6/8', 0) - 0.75) < 0.01,
          str(c.meter.weights))
    check('品质分布聚合成功',
          abs(c.harmony.quality_weights.get('dominant7', 0) - 0.4) < 0.01,
          str(c.harmony.quality_weights))
    check('swing 律动被提取', c.rhythm.swing_ratio == 1.9,
          str(c.rhythm.swing_ratio))
    check('级进比聚合成功', c.melody.stepwise_ratio == 0.5)
    check('足够样本 → 分区生效',
          set(c.active_sections()) >= {'meter', 'melody', 'rhythm', 'harmony'},
          str(c.active_sections()))

    # 证据不足
    c2 = SC.build_contract_from_features(feats[:2], pack_id='x')
    check('样本不足时分区不生效', 'melody' not in c2.active_sections(),
          str(c2.active_sections()))


def test_learned_packs_are_distinguishable():
    """用**真实语料学到的**契约验证：不同体裁必须能被测出来区别。

    这一条是整套「外部语料 → 学习 → 契约 → 生成」链路的验收口。前面几条
    用例用的是手写的契约，只能证明机制通；这里用的是从 Nottingham /
    KernScores / Weimar Jazz / CoCoPops 真谱子上学出来的数值，证明**学到的
    东西真的有体裁信息**而不是噪声。

    判据选七和弦占比，因为它是唯一能把爵士与其它体裁在量化层面分开的指标；
    而它之所以现在测得出，是因为 analyzer 改用 Chord.commonName 判品质
    （Chord.quality 会把 C7 与 Cmaj7 都报 'major'，占比恒为 0）。
    """
    print('=== 真实语料学到的契约必须互相区分 ===')
    packs = {
        'jazz/swing-standard': 'jazz',
        'classical/symphony': 'classical',
        'classical/classical-sonata': 'classical',
        'pop/pop-standard': 'pop',
        'folk/british-folk-tune': 'folk',
        'sacred/chorale': 'sacred',
    }
    contracts = {}
    for pid in packs:
        c = SC.resolve_contract(pid)
        check(f'{pid} 能解析到契约', c is not None and c.evidence_n > 0,
              f'得到 {c}')
        contracts[pid] = c
    if any(c is None for c in contracts.values()):
        return

    jazz = contracts['jazz/swing-standard'].harmony.seventh_ratio()
    chor = contracts['sacred/chorale'].harmony.seventh_ratio()
    check('爵士的七和弦占比显著高于众赞歌', jazz > chor + 0.3,
          f'jazz={jazz} chorale={chor}')
    check('爵士确实以七和弦为主（>0.6）', jazz > 0.6, f'{jazz}')
    check('众赞歌确实几乎不用七和弦（<0.2）', chor < 0.2, f'{chor}')

    # 语料规模：够大才谈得上「学到」
    check('爵士语料样本量足够', contracts['jazz/swing-standard'].evidence_n >= 100,
          str(contracts['jazz/swing-standard'].evidence_n))
    check('民谣语料样本量足够',
          contracts['folk/british-folk-tune'].evidence_n >= 100,
          str(contracts['folk/british-folk-tune'].evidence_n))

    # 速度按体裁分化，不能都撞到内核默认 90
    tempos = {pid: c.tempo.default for pid, c in contracts.items()}
    check('各体裁学到了不同的缺省速度', len(set(tempos.values())) >= 4,
          str(tempos))


def test_learned_pack_drives_generation():
    """学到的契约必须真的改变生成结果（不只是躺在 YAML 里）。"""
    print('=== 真实契约驱动内核生成 ===')
    sj, st_j = build('jazz/swing-standard')
    sc_, st_c = build('sacred/chorale')
    js, jt = seventh_bars(st_j)
    cs, ct = seventh_bars(st_c)
    check('爵士包生成的七和弦多于众赞歌包', js > cs, f'jazz={js}/{jt} chorale={cs}/{ct}')
    check('爵士包给了体裁速度', sj.get('tempo') == 155, str(sj.get('tempo')))
    check('众赞歌包给了体裁速度', sc_.get('tempo') == 76, str(sc_.get('tempo')))
    # 交响包学到的多是 3/4（快板/小步舞曲乐章），拍号必须被契约带出来
    ss, _ = build('classical/symphony')
    check('交响包把学到的拍号落到谱上', ss.get('time_signature') == '3/4',
          str(ss.get('time_signature')))


def test_publish_is_idempotent():
    """重复对着同一份语料 learn 必须收敛：不累加文本、不空顶版本号。

    真实事故：众赞歌的 melody.rules 里同一句话被累加到 6 遍，版本号被两轮
    无变化的 learn 从 2.8 顶到 2.10。
    """
    print('=== 学习结果可重复（幂等）===')
    from score_learning import publish as PB

    stats = {
        'total_scores': 24,
        'means': {'stepwise_ratio': 0.12, 'leap_ratio': 0.008},
        'distributions': {'key': {'C major': 24}},
        'rules': [{'rule': 'meter=4/4', 'frequency': 0.75,
                   'universality': 'core', 'evidence_count': 18}],
    }
    prof = {}
    for _ in range(4):
        prof = PB.merge_empirical_into_profile(prof, stats)
    rules = prof['melody']['rules']
    check('反复 learn 不累加 melody.rules 文本',
          rules.count('以级进为主。') == 1, f'{rules.count("以级进为主。")} 次：{rules[:80]}')
    check('反复 learn 不累加 constraints.must',
          len(prof['constraints']['must']) == len(set(prof['constraints']['must'])),
          str(prof['constraints']['must']))

    fp1 = PB._stats_fingerprint(stats)
    fp2 = PB._stats_fingerprint(dict(stats))
    check('同内容指纹一致', fp1 == fp2, f'{fp1} vs {fp2}')
    changed = dict(stats, total_scores=25)
    check('内容变了指纹就变', PB._stats_fingerprint(changed) != fp1)
    check('无 fingerprint 时视为有变化',
          PB._bump_version('2.8') == '2.9')


def test_yaml_roundtrip():
    print('=== YAML 落地与回读 ===')
    import tempfile
    c = SC.contract_from_dict(JAZZ)
    d = tempfile.mkdtemp()
    p = os.path.join(d, SC.CONTRACT_FILENAME)
    c.save(p)
    check('契约文件已写出', os.path.isfile(p))
    back = SC.load_contract(p)
    check('回读后 pack_id 一致', back.pack_id == c.pack_id)
    check('回读后品质分布一致',
          back.harmony.quality_weights == c.harmony.quality_weights)
    check('回读后仍可用', back.harmony.is_usable())

    # 通过 pack_id 解析
    got = SC.resolve_contract(
        {'pack_id': 'inline-jazz',
         'harmony': {'quality_weights': {'triad': 1.0}, 'evidence_n': 20}})
    check('内联 dict 解析成功', got is not None and got.harmony.is_usable())


if __name__ == '__main__':
    test_contract_basics()
    test_style_changes_harmony()
    test_determinism()
    test_tempo_default()
    test_voice_leading_correct()
    test_build_from_features()
    test_learned_packs_are_distinguishable()
    test_learned_pack_drives_generation()
    test_publish_is_idempotent()
    test_yaml_roundtrip()
    print(f'\n总计：{ok} 通过 / {bad} 失败')
    raise SystemExit(1 if bad else 0)
