# -*- coding: utf-8 -*-
"""测试：作曲库（任务 Skill / 风格 Genre / 内核脚本）与「注入链」是否真的活着。

这个文件的首要命题：
  A. 库里每一样东西都要能回答「它到底有没有生效」——注入矩阵必须可测。
  B. 任务 Skill 的 SKILL.md 能通过 index.yaml 的 inject 声明真正进入提示词。
  C. 新增风格包必须通过 taxonomy 校验，否则会被静默踢出关键词路由。
     （这是本轮真实发生过的回归：「来一段爵士摇摆标准曲」曾路由到 None。）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import composition_skills as SL          # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, extra=''):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f'  ✅ {name}')
    else:
        _FAILED += 1
        print(f'  ❌ {name}  {extra}')


# ---------------------------------------------------------------- 注入链

def test_stage_registry():
    print('\n=== 阶段登记 ===')
    check('STAGE_FILES 登记了 translator',
          SL.STAGE_FILES.get('translator') == 'translator.md',
          str(sorted(SL.STAGE_FILES)))
    check('INJECT_STAGES 与 STAGE_FILES 完全一致',
          set(SL.INJECT_STAGES) == set(SL.STAGE_FILES),
          f'{sorted(SL.INJECT_STAGES)} vs {sorted(SL.STAGE_FILES)}')
    missing = [st for st in SL.INJECT_STAGES
               if not os.path.isfile(os.path.join(
                   SL.BASE_DIR, SL.STAGE_FILES[st]))]
    check('每个阶段都有 base/<stage>.md', not missing, str(missing))


def test_injection_matrix():
    print('\n=== 注入矩阵 ===')
    m = SL.injection_matrix()
    ids = {s['id'] for s in SL.list_skills(force=True)}
    check('矩阵覆盖全部任务 Skill', set(m) == ids, str(set(m) ^ ids))
    check('矩阵不含风格包 id（风格走 profile 注入）',
          not any(SL.is_genre_id(k) for k in m))
    bad = {k: v for k, v in m.items() if set(v) != set(SL.INJECT_STAGES)}
    check('每个 skill 都统计了全部阶段', not bad, str(bad))

    # compose-full 是新创作的默认 skill，必须真的能进翻译器
    live = SL.live_stages('compose-full')
    check('compose-full 注入 translator', 'translator' in live, str(live))
    check('compose-full 注入内容非空',
          len(SL._addon_text('compose-full', 'translator')) > 500)

    # stub 型 skill 不得被无差别注入（防止「全阶段回退 SKILL.md」的误改）
    stub = [sid for sid in ids if sid in ('transpose-key', 'harmonize',
                                          'simplify', 'complexify',
                                          'edit-measure', 'edit-symbol',
                                          'repair-timing')]
    noisy = [sid for sid in stub if SL.live_stages(sid)]
    check('未声明 inject 的 stub 型 skill 一律不注入', not noisy, str(noisy))


def test_hard_rules_reach_translator():
    print('\n=== 硬规则真的进了翻译器提示词 ===')
    prompt = SL.build_system_prompt('translator', ['compose-full'])
    for kw in ('禁止连续 4 小节', '四度以上跳进', '调性偏离', '平行纯五度'):
        check(f'翻译器提示词含「{kw}」', kw in prompt)
    # 没传 skill 时不得夹带
    plain = SL.build_system_prompt('translator', [])
    check('不传 skill 时不夹带 compose-full 规则',
          '禁止连续 4 小节' not in plain)


def test_inject_declaration_fallback():
    print('\n=== inject 声明 → SKILL.md 回退 ===')
    check('compose-full 声明了 inject',
          'translator' in (SL.get_skill('compose-full', force=True)
                           .get('inject') or []))
    # 直接验证回退规则：只有声明过的阶段才回退
    declared = SL._inject_stages('compose-full')
    check('回退集合来自 index.yaml', declared == ('translator',), str(declared))
    check('未声明的阶段不回退',
          SL._addon_text('compose-full', 'repair') == '')
    check('风格包 id 不参与 inject',
          SL._inject_stages('genre:jazz/swing-standard') == ())


# ---------------------------------------------------------------- 风格库

def test_all_packs_pass_taxonomy():
    print('\n=== 全部风格包通过 taxonomy 校验 ===')
    SL.invalidate_cache()
    genres = SL.list_genres(force=True)
    bad = [(g['id'], g.get('taxonomy_errors')) for g in genres
           if g.get('taxonomy_errors')]
    for gid, errs in bad:
        print(f'      {gid}: {"; ".join(errs)}')
    check(f'{len(genres)} 个风格包全部无校验错误', not bad, f'{len(bad)} 个有错')

    tax = SL.load_taxonomy(force=True)
    fams = set(((tax.get('dimensions') or {}).get('family') or {})
               .get('values') or {})
    used = {(g.get('dimensions') or {}).get('family') for g in genres}
    check('所有 family 都已登记进 taxonomy', used <= fams, str(used - fams))
    check('可用（参与路由）的风格包 = 全部',
          len(SL._enabled_genres()) == len(genres),
          f'{len(SL._enabled_genres())}/{len(genres)}')


def test_new_families_routable():
    print('\n=== 新增风格族可以被关键词路由到 ===')
    SL.invalidate_cache()
    cases = [
        ('来一段爵士摇摆标准曲', 'genre:jazz/swing-standard'),
        ('写一首交响曲', 'genre:classical/symphony'),
        ('写首流行歌', 'genre:pop/pop-standard'),
    ]
    for text, want in cases:
        got = (SL.match_genre(text) or {}).get('id')
        check(f'「{text}」→ {want}', got == want, str(got))


def test_genre_contract_lookup():
    print('\n=== 风格契约查询 ===')
    # 已有 grammar.yaml 的包：旧契约作废，返回 None
    c, path = SL.genre_contract('genre:jazz/swing-standard')
    check('有语法的包不再读旧契约', c is None, path)
    check('仍返回契约路径（便于诊断）', path.endswith('style_contract.yaml'), path)
    check('特征文件数仍可查', SL.genre_feature_count('genre:jazz/swing-standard') > 0)

    c2, path2 = SL.genre_contract('genre:dance/waltz')
    # waltz 无 grammar：若磁盘上有契约则返回契约，否则 None（两者都合法）
    check('无语法的老包查询不抛错', True)
    import style_contract as SC
    check('CONTRACT_FILENAME 与查询函数同源',
          SL.genre_contract_path('genre:jazz/swing-standard') == path)
    check('路径后缀正确', path2.endswith(SC.CONTRACT_FILENAME), path2)


# ---------------------------------------------------------------- 脚本库

def test_script_library():
    print('\n=== 内核脚本库 ===')
    lib = SL.script_library()
    names = [d['script'] for d in lib]
    check('脚本库非空', len(lib) >= 8, str(len(lib)))
    check('包含 new_score 与 finalize',
          'new_score' in names and 'finalize' in names, str(names))
    check('每个脚本都有说明与参数',
          all(d['desc'] and d['args'] for d in lib),
          str([d['script'] for d in lib if not (d['desc'] and d['args'])]))
    check('与 compose_kernel.SCRIPT_IMPL 键一致',
          set(names) == set(_impl_keys()), str(set(names) ^ set(_impl_keys())))
    notes = SL.script_notes()
    check('有调用顺序模板', 'new_score' in notes['call_order'])
    check('有段落织体映射', '安静段' in notes['texture_map'])


def _impl_keys():
    import compose_kernel as K
    return list(K.SCRIPT_IMPL)


def test_ui_formatter():
    print('\n=== UI 只读视图（离屏，不起窗口）===')
    try:
        import composition_skill_ui as U
    except Exception as e:                       # PyQt 缺失时跳过而非失败
        print(f'  （跳过：{e}）')
        return
    c, _p = SL.genre_contract('genre:jazz/swing-standard')
    if c is None:
        # 有 grammar 的包不再暴露契约；用未学语法的包测格式化器
        c, _p = SL.genre_contract('genre:dance/waltz')
        if c is None:
            import style_contract as SC
            path = SL.genre_contract_path('genre:jazz/swing-standard')
            if os.path.isfile(path):
                c = SC.load_contract(path)
    text = U.format_contract(c)
    check('契约视图非空', bool(text) and ('evidence_n' in text or '样本量' in text
          or '还没有' in text), text[:120])
    if c is not None:
        check('契约视图含七和弦占比或分区', '七和弦' in text or '生效分区' in text or 'tempo' in text.lower())
    empty = U.format_contract(None)
    check('无契约时给出补齐指引而非崩溃',
          'score_learning' in empty and 'style_contract.yaml' in empty)


def test_learned_is_the_ui_visible_set():
    """UI 只列「学过」的风格：口径 = 有 grammar.yaml（/learn-grammar）。

    主界面原先左栏有一棵风格树，把 31 个包（含 25 个没有任何实测语料的）一股脑
    摆出来。现在左栏已删，风格只从两处进入：右侧检查器「风格包」下拉、对话面板
    的 /style 下拉与弹出列表。这两处必须同源，且只列学过的那批。
    """
    print('\n=== UI 只列「学过」的风格（grammar.yaml 口径）===')
    SL.invalidate_cache()
    learned = SL.learned_genres(force=True)
    want = {
        'genre:classical/symphony', 'genre:classical/piano-sonata',
        'genre:classical/string-quartet', 'genre:folk/british-folk-tune',
        'genre:jazz/swing-standard', 'genre:pop/pop-standard',
        'genre:sacred/chorale', 'genre:romantic/chopin-piano',
        'genre:vocal/madrigal',
    }
    got = {g['id'] for g in learned}
    check('学过集合 = 有 grammar.yaml 的 9 个包', got == want, str(got ^ want))
    check('未学习的老包判为未学过',
          not SL.is_learned('genre:dance/waltz')
          and not SL.is_learned('genre:classical/classical-sonata'))
    check('学过的语法包判为学过', SL.is_learned('genre:sacred/chorale')
          and SL.is_learned('genre:classical/piano-sonata'))
    check('非风格 id 一律 False，不误判',
          not SL.is_learned('compose-full') and not SL.is_learned(''))
    alln = len(SL.list_genres(force=True))
    check(f'学过集合是真子集（{len(learned)}/{alln}）',
          0 < len(learned) < alln, f'{len(learned)}/{alln}')

    try:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
        from PyQt5.QtWidgets import QApplication
        import studio_daw
    except Exception as e:                       # PyQt 缺失时跳过而非失败
        print(f'  （跳过 UI 部分：{e}）')
        return

    check('左侧浏览器类已从 studio_daw 移除',
          not hasattr(studio_daw, 'StyleBrowser'))

    app = QApplication.instance() or QApplication([])
    insp = studio_daw.InspectorPanel()
    ui_ids = {insp.style_combo.itemData(i)
              for i in range(insp.style_combo.count())
              if insp.style_combo.itemData(i)}
    check('检查器「风格包」下拉只列学过的',
          ui_ids == want, str(ui_ids ^ want))

    # 对话面板的 /style 列表必须与检查器同源
    import score_studio as S
    w = S.StudioWindow()
    try:
        chat_ids = {gid for _label, _name, gid in w._genre_style_choices()}
        check('对话面板 /style 列表只列学过的',
              chat_ids == want, str(chat_ids ^ want))
        check('两处风格入口同源',
              chat_ids == ui_ids, str(chat_ids ^ ui_ids))
        check('主界面已无左侧停靠面板',
              not hasattr(w, 'browser_dock') and not hasattr(w, 'browser'))
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_library_all():
    test_stage_registry()
    test_injection_matrix()
    test_hard_rules_reach_translator()
    test_inject_declaration_fallback()
    test_all_packs_pass_taxonomy()
    test_new_families_routable()
    test_genre_contract_lookup()
    test_learned_is_the_ui_visible_set()
    test_script_library()
    test_ui_formatter()
    print(f'\n总计：✅ {_PASSED} / ❌ {_FAILED}')
    assert _FAILED == 0, f'{_FAILED} 项失败'


if __name__ == '__main__':
    test_library_all()
