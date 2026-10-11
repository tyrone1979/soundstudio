# -*- coding: utf-8 -*-
"""旋律 + 和弦（lead sheet）类风格的可解释语法学习与确定性作曲。

和 score_learning.grammar（巴赫四部众赞歌）同一思路：分析语料 → 分层计数 → 剪枝蒸馏成
带编号的规则脚本（grammar.yaml + 中文 grammar.md）→ 作曲器只读脚本、确定性搜索、
硬规则 0 违例、每个和弦与每小节节奏都能追溯到规则编号。

共享算法核心见 ``score_learning.learn_core``（Witten-Bell、上下文剪枝+留出回归、
k-means 多次重启）；本包保留曲式 / 节奏单元 / 复杂度预算等 leadsheet 专属层。

支持的风格（语料都自带和弦标注或自动识别）：
    folk / jazz / pop / madrigal / sonata / strings / symphony / chopin
"""
