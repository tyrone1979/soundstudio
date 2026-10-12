# -*- coding: utf-8 -*-
"""帮助文档「实测验证版」的测试用例：每个确定性命令都真实执行一次并断言通过。

运行：python tests/test_help_pro.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gen_help_pro as G  # noqa: E402

fail = 0


def check(name, cond, extra=''):
    global fail
    if cond:
        print(f'  ✓ {name}')
    else:
        fail += 1
        print(f'  ✗ {name} {extra}')


def main():
    print('[test_help_pro] 每个确定性命令真实执行并验证')
    cache = {}
    # 1) 全部确定性命令实测必须通过
    for cmd, (inp, _fn) in G.CMD_EXAMPLES.items():
        r = G.run_example(cmd)
        cache[cmd] = r
        check(f'{cmd} 实测通过（输入：{inp}）', r['ok'], r['output'][:200])
    # 2) 设置类命令（确定性提示，不触发作曲）
    sset = G.run_settings()
    check('设置类命令返回非空提示', bool(sset) and '→' in sset)
    # 3) 生成的文档存在且覆盖全部命令
    doc_path = os.path.join(G.ROOT, 'runs', 'help_pro.md')
    check('runs/help_pro.md 已生成', os.path.exists(doc_path))
    if os.path.exists(doc_path):
        with open(doc_path, encoding='utf-8') as f:
            doc = f.read()
        for cmd in [c['cmd'] for c in G.COMMANDS]:
            check(f'文档含命令 {cmd}', f'`{cmd}' in doc)
        check('文档含「测试用例结果」章节', '测试用例结果' in doc)
        check('文档含完整测试用例（/chords）', '/chords' in doc)
    if fail == 0:
        print('\nhelp_pro 全部确定性命令验证通过')
    else:
        print(f'\n失败 {fail} 项')
        sys.exit(1)


if __name__ == '__main__':
    main()
