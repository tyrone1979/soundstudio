# -*- coding: utf-8 -*-
"""help_doc 与 /help 命令的测试：覆盖命令/功能目录与示例。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import help_doc as H   # noqa: E402
import llm_compose as L  # noqa: E402


def check(desc, cond):
    if not cond:
        print(f"  ✗ {desc}")
        return False
    print(f"  ✓ {desc}")
    return True


def main():
    fails = 0
    n = 0

    # 1) llm_compose 里出现的所有真实命令，在 help_doc 中都有条目（命令或别名）
    real_cmds = (L._NO_ARG_CMDS | L._ARG_CMDS)
    documented = {H._norm(c["cmd"]) for c in H.COMMANDS}
    alias_map = {H._norm(a) for c in H.COMMANDS for a in c.get("aliases", [])}
    for cmd in sorted(real_cmds):
        c = H._norm(cmd)
        ok = c in documented or c in alias_map
        n += 1
        if not ok:
            fails += 1
            print(f"  ✗ 命令 {cmd} 在帮助文档中缺失")
        else:
            print(f"  ✓ 命令 {cmd} 已在帮助文档中记录")
    # 帮助文档中的每个命令条目本身 cmd 合法
    for c in H.COMMANDS:
        n += 1
        if c["cmd"].startswith('/'):
            print(f"  ✓ 帮助条目 {c['cmd']} 以 / 开头")
        else:
            fails += 1
            print(f"  ✗ 帮助条目 {c['cmd']} 必须以 / 开头")

    # 2) 每个命令/功能都有示例与说明与效果
    for c in H.COMMANDS:
        n += 1
        ok = bool(c.get("example")) and bool(c.get("desc")) and bool(c.get("result"))
        if not ok:
            fails += 1
            print(f"  ✗ 命令 {c['cmd']} 缺示例/说明/效果")
        else:
            print(f"  ✓ 命令 {c['cmd']} 含示例+说明+效果")
    for f in H.FEATURES:
        n += 1
        ok = bool(f.get("example")) and bool(f.get("summary")) and bool(f.get("result"))
        if not ok:
            fails += 1
            print(f"  ✗ 功能 {f['id']} 缺示例/说明/效果")
        else:
            print(f"  ✓ 功能 {f['id']} 含示例+说明+效果")

    # 3) 主题查找
    n += 1
    t = H.find_topic("/chords")
    if t and t["kind"] == "command":
        print("  ✓ find_topic('/chords') → 命令")
    else:
        fails += 1
        print("  ✗ find_topic('/chords') 未命中命令")
    n += 1
    t = H.find_topic("和弦顾问")
    if t and t["kind"] == "feature":
        print("  ✓ find_topic('和弦顾问') → 功能")
    else:
        fails += 1
        print("  ✗ find_topic('和弦顾问') 未命中功能")
    n += 1
    t = H.find_topic("风格插值")
    if t and t["kind"] == "feature":
        print("  ✓ find_topic('风格插值') → 功能")
    else:
        fails += 1
        print("  ✗ find_topic('风格插值') 未命中功能")
    n += 1
    t = H.find_topic("")
    if t is None:
        print("  ✓ find_topic('') → None")
    else:
        fails += 1
        print("  ✗ find_topic('') 应为 None")

    # 4) 完整帮助覆盖所有示例
    full = H.build_help()
    for c in H.COMMANDS:
        n += 1
        if c["example"] in full:
            print(f"  ✓ 完整帮助含命令示例 {c['cmd']}")
        else:
            fails += 1
            print(f"  ✗ 完整帮助缺命令示例 {c['cmd']}")
    for f in H.FEATURES:
        n += 1
        if f["example"] in full:
            print(f"  ✓ 完整帮助含功能示例 {f['id']}")
        else:
            fails += 1
            print(f"  ✗ 完整帮助缺功能示例 {f['id']}")

    # 5) /help 命令集成（llm_compose.apply_command）
    n += 1
    txt = L.apply_command(L.ComposeSession(), "/help", "")
    if isinstance(txt, str) and "命令速查" in txt and "功能清单" in txt:
        print("  ✓ /help 返回完整帮助（含功能清单+命令速查）")
    else:
        fails += 1
        print("  ✗ /help 未返回完整帮助")
    n += 1
    single = L.apply_command(L.ComposeSession(), "/help", "/chords")
    if isinstance(single, str) and "/chords" in single and "示例" in single:
        print("  ✓ /help /chords 返回命令详情")
    else:
        fails += 1
        print("  ✗ /help /chords 未返回命令详情")

    # 6) parse_user_turn：/help 吃掉整行，不触发作曲
    n += 1
    cmds, nl = L.parse_user_turn("/help /chords I V vi IV")
    if cmds == [("/help", "/chords I V vi IV")] and nl == "":
        print("  ✓ /help 吃掉整行剩余，不留自然语言触发作曲")
    else:
        fails += 1
        print(f"  ✗ /help 解析异常：{cmds} | {nl!r}")

    print(f"\n帮助文档测试：{n - fails}/{n} 通过")
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
