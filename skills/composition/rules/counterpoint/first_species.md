# 第一类二声部对位（对位法权威）

本规则为项目内**对位法权威**：凡提示词/任务涉及对位（1:1、第一类、species、对位法），必须遵守。
完整条文与实现见 `music_score/two_part_species.py` 的 `COUNTERPOINT_RULES_DOC` / `get_counterpoint_rules_doc()`。

## 硬约束摘要

1. **记谱**：钢琴大谱表，右手高音谱号 + 左手低音谱号；两声部严格 1:1（同一时值）。
2. **中间音程**：以 3、6 为主；可反向到 5、8；**禁止纯四度**；不写不协和（2/7/增减等）。
3. **起止**：起音与末音外声部为纯八度（或同度）；末音同为 I 级；倒数第二拍上声部导音↑主或下声部Ⅱ↓Ⅰ。
4. **运动**：优先反向与斜向；同向须谨慎。
5. **声部**：禁止交叉与重叠；声部内部以级进为主。

## 系统路径

| 场景 | 行为 |
|------|------|
| 空白/新写 1:1 对位 | `generate_first_species_two_part` |
| 「根据当前乐谱写 1:1 对位」 | `extract_cf_from_score` → `generate_first_species_against_cf`（必要时 `prepare_cf_for_species`） |
| 校验 | `validate_first_species_two_part` |

帮助文档由 `help_doc.py` 从本规则自动生成「对位法」专章。
