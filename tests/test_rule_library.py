# -*- coding: utf-8 -*-
"""规则库：catalog + 对位权威 + 内核脚本自动登记。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rule_library as RL  # noqa: E402

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


def test_catalog_and_manual():
    print('\n=== catalog / 手工规则 ===')
    check('catalog.yaml 存在', os.path.isfile(RL.CATALOG_PATH), RL.CATALOG_PATH)
    cats = RL.categories()
    check('至少 5 个分类', len(cats) >= 5, str([c['id'] for c in cats]))
    check('含 counterpoint 分类', any(c['id'] == 'counterpoint' for c in cats))
    check('含 kernel_script 分类', any(c['id'] == 'kernel_script' for c in cats))

    rules = RL.list_rules(include_scripts=False)
    ids = {r['id'] for r in rules}
    check('含第一类对位规则', 'species_first_two_part' in ids, str(sorted(ids)))
    check('含终止式规则', 'cadence_types' in ids)
    check('含乐句模型', 'phrase_model_functions' in ids)
    check('含 SATB 声部进行', 'satb_voice_leading' in ids)
    check('含乐段/句子', 'phrase_period_sentence' in ids)

    cp = RL.get_rule('species_first_two_part')
    check('对位规则可取回', cp is not None)
    doc = (cp or {}).get('doc') or ''
    check('对位文档含权威条文或硬约束', 
          ('硬约束' in doc) or ('第一类' in doc) or ('纯四' in doc) or ('3' in doc),
          doc[:120])
    check('对位绑定了 two_part_species 代码',
          any('two_part_species' in (b.get('ref') or '')
              for b in (cp or {}).get('code') or []))


def test_kernel_scripts_merged():
    print('\n=== 内核脚本自动合并 ===')
    snap = RL.rule_library_snapshot()
    check('总数 > 手工数', snap['total'] > snap['manual'], str(snap))
    check('内核脚本数 ≥ 8', snap['kernel_script'] >= 8, str(snap))
    mel = RL.get_rule('kernel_script_melody')
    check('melody 脚本规则存在', mel is not None)
    if mel:
        check('melody 文档含说明', 'melody' in (mel.get('doc') or '').lower())
        code = RL.format_rule_code(mel)
        check('melody 代码非空', len(code) > 40 and '（本规则未绑定' not in code,
              code[:80])
    fin = RL.get_rule('kernel_script_finalize')
    check('finalize 脚本规则存在', fin is not None)


def test_formatters_and_tabs():
    print('\n=== 格式化 / UI 常量 ===')
    r = RL.get_rule('cadence_types')
    check('cadence 规则存在', r is not None)
    doc = RL.format_rule_doc(r or {})
    check('format_rule_doc 含标题', '终止' in doc or 'cadence' in doc.lower(), doc[:80])
    code = RL.format_rule_code(r or {})
    check('cadence 有代码面板文本', len(code) > 10)

    from composition_skill_ui import SkillLibraryDialog
    check('TAB_RULES 存在', hasattr(SkillLibraryDialog, 'TAB_RULES'))
    check('TAB 数量为 6', SkillLibraryDialog.TAB_TAXONOMY == 5)
    check('TAB_RULES == 4', SkillLibraryDialog.TAB_RULES == 4)


def test_help_topic():
    print('\n=== 帮助 ===')
    from help_doc import find_topic
    hit = find_topic('规则库')
    check('find_topic(规则库) 命中', hit is not None)
    if hit:
        check('命中规则库功能',
              hit['entry']['id'] == '规则库（作曲库管理器）',
              str(hit['entry'].get('id')))


def test_prompt_injection():
    print('\n=== Planner/Translator 注入 ===')
    pb = RL.rules_prompt_block('planner')
    tb = RL.rules_prompt_block('translator')
    check('planner 摘要非空', len(pb) > 200, str(len(pb)))
    check('translator 摘要非空', len(tb) > 200, str(len(tb)))
    check('planner 含对位规则 id', 'species_first_two_part' in pb)
    check('planner 含终止', 'cadence_types' in pb or '终止' in pb)
    check('translator 含 cadence 脚本提示', 'cadence' in tb.lower())
    check('translator 含 set_harmony', 'set_harmony' in tb)
    check('其他 stage 返回空', RL.rules_prompt_block('composer') == '')

    import composition_skills as SL
    SL.invalidate_cache()
    planner = SL.build_system_prompt('planner', [])
    translator = SL.build_system_prompt('translator', [])
    check('build_system_prompt(planner) 含规则库摘要标记',
          '--- 规则库摘要（Planner/Translator 必遵）---' in planner,
          planner[-200:])
    check('build_system_prompt(planner) 含 species 对照',
          'species_first_two_part' in planner or '第一类' in planner)
    check('build_system_prompt(translator) 含规则库摘要标记',
          '--- 规则库摘要（Planner/Translator 必遵）---' in translator)
    check('build_system_prompt(translator) 含本阶段脚本对照',
          'set_harmony' in translator and 'cadence' in translator.lower())


if __name__ == '__main__':
    test_catalog_and_manual()
    test_kernel_scripts_merged()
    test_formatters_and_tabs()
    test_help_topic()
    test_prompt_injection()
    print(f'\n通过 {_PASSED} / 失败 {_FAILED}')
    sys.exit(1 if _FAILED else 0)
