# -*- coding: utf-8 -*-
"""可解释结构语法学习：乐谱 → 分层概率语法 → 带编号的确定性作曲脚本。

    analyze  逐拍切片（和弦 / 声部 / 乐句 / 终止 / 装饰）
    learn    分层计数学习（全曲 → 乐句 → 拍 → 声部），剪枝蒸馏成规则
    script   规则脚本读写（grammar.yaml）+ 中文说明书（grammar.md）
    compose  只读脚本的确定性作曲器（动态规划 + 硬规则 + 逐拍规则追溯）
    check    四部和声硬规则检查

共享算法核心见 ``score_learning.learn_core``（与 leadsheet 流水线统一：
Witten-Bell、上下文剪枝+留出回归、k-means 多次重启）。
"""
