# 风格包目录

- **真相源**：每个 pack 的 `meta.yaml` → `profile`（结构化；加载器按 planner/composer 阶段注入）
- **分类**：`taxonomy.yaml` 的 dimensions；目录按 `family/` 组织
- **可选**：`GENRE.md` 短介绍；`planner.md` / `composer.md` 仅特例追加（一般不要）
- **语料学习**：`python -m score_learning learn … --family X --pack-id Y` 直接更新该 pack（`features/` + `meta.yaml` empirical）

## 对话中使用

`/style <风格名>` 或界面下拉选择 → 创曲描述。例如：`/style 众赞歌 16小节四声部`

## 当前 packs（family / id）

| family | packs |
|--------|--------|
| academic | sonata-allegro, scherzo, fugue, piano-prelude, adagio-cantabile |
| dance | waltz, march, minuet, polka, mazurka, tango |
| cinematic | pirate-allegro, heroic-fanfare, chase-action, mystery-suspense, epic-battle, romantic-theme |
| intimate | rain-piano, nocturne, lullaby, ballade-piano |
| folk | chinese-pentatonic, celtic-jig, celtic-air |
| sacred | chorale, hymn |

详见上级 `../README.md` 与仓库根 `README.md`。
