# 作曲 Skill / Genre 库 — 文件职责（避免重复）

## 总原则

| 层级 | 真相源 | 说明 |
|------|--------|------|
| 任务 Skill | `base/*.md` + 可选 `skills/<id>/planner.md` 等 | 管线提示词 |
| 风格 Genre | **`meta.yaml` → `profile`** | 调式/速度/拍号/句法/和声/旋律/织体/色彩 |

运行时：`base` + 任务追加 + **自动注入 genre profile**。  
风格包**不必**再写 planner/composer，除非有 profile 盖不住的「仅某一阶段」特例。

---

## 任务 Skill（`skills/<id>/`）

| 文件 | 给谁看 | 是否必填 | 内容 |
|------|--------|----------|------|
| `SKILL.md` | 人 / UI | 建议有 | 何时用 + 可选 **Gotchas**（硬性创作禁忌，给人看） |
| `planner.md` | LLM 计划阶段 | 可选 | 仅追加与本任务相关的计划约束 |
| `composer.md` | LLM 作曲阶段 | 可选 | 仅追加与本任务相关的写谱约束 |

特殊：`score-analysis`（`/advice`）走 `pipeline: advice`，只输出修改建议、不改谱；解析脚本在 `skills/score-analysis/scripts/parse_score.py`，规则清单在 `references/checklist.md`，系统提示在 `base/advice.md`。确认后用「可以」或 `/apply` 整曲改写。

共用大段提示在 `base/planner.md`、`base/composer.md` 等，**不要**每个任务复制一份。  
**防单调等全曲硬规则**写在 `base/composer.md`（会注入 LLM）；`compose-full/SKILL.md` 的 Gotchas 与之对齐，供人阅读。

---

## 风格 Genre（`genre/packs/<family>/<id>/`）

| 文件 | 给谁看 | 是否必填 | 内容 |
|------|--------|----------|------|
| **`meta.yaml`** | 路由 + LLM | **必填** | dimensions、triggers、**profile**（含可选 `empirical`） |
| `features/` | 语料学习 | 可选 | Score Analyzer 输出的 Feature JSON |
| `empirical_*.json` / `EMPIRICAL_SKILL.md` | 审计 | 可选 | 语料统计与技巧摘要 |
| `GENRE.md` | 人 / UI | 可选 | 短介绍 |
| `planner.md` / `composer.md` | LLM | **通常不要** | 仅特例 |

创曲命中风格时注入 `profile`。语料学习**直接更新本目录的 `meta.yaml`**，不写单独的 `learned/`：

- `python -m score_learning learn-m21 --all` — **全 corpus 按曲风分桶**（非混学）
- `/learn-score chorale` — 当前曲写入对应风格包

详见 [`score_learning/README.md`](../../score_learning/README.md)。

---

## 常见重复（已避免）

- ❌ 在 `GENRE.md`、`planner.md`、`composer.md` 里各写一遍调式/速度/拍号…  
- ✅ 只写在 `meta.yaml` → `profile`  
- ❌ 风格包再抄一份 `base/composer.md`  
- ✅ 风格只叠加 profile（+ 极少特例 md）
