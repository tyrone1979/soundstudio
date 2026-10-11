# 统一学习报告

**10/10 阶段成功**，总耗时 18.2s

| 阶段 | 说明 | 结果 | 耗时 |
|---|---|---|---|
| `chorale` | 巴赫众赞歌 → sacred/chorale 规则 | ✅ | 1.3s |
| `leadsheet:folk` | folk → 风格包规则 | ✅ | 1.3s |
| `leadsheet:jazz` | jazz → 风格包规则 | ✅ | 1.6s |
| `leadsheet:pop` | pop → 风格包规则 | ✅ | 1.0s |
| `leadsheet:madrigal` | madrigal → 风格包规则 | ✅ | 0.5s |
| `leadsheet:sonata` | sonata → 风格包规则 | ✅ | 2.3s |
| `leadsheet:strings` | strings → 风格包规则 | ✅ | 0.7s |
| `leadsheet:symphony` | symphony → 风格包规则 | ✅ | 2.2s |
| `leadsheet:chopin` | chopin → 风格包规则 | ✅ | 0.6s |
| `relation` | 人工段落标注 → form_plan（段落结构与关系） | ✅ | 6.6s |

## `chorale` — 巴赫众赞歌 → sacred/chorale 规则

- 语料：**350** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/sacred/chorale/grammar.yaml`

## `leadsheet:folk` — folk → 风格包规则

- 语料：**908** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/folk/british-folk-tune/grammar.yaml`
- 子风格 **4** 个，训练 789 / 留出 88

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| jig | major | 4.5200 | 4.9600 | 6.7800 | **已回退** |
| jig | minor | 9.2800 ⚠️ | 9.0000 | 8.7600 | 保留 |
| reel | major | 4.3200 | 4.6300 | 6.6600 | 保留 |
| reel | minor | 5.5200 | 5.9700 | 6.0100 | 保留 |
| hornpipe | major | 2.8900 | 3.6300 | 4.7800 | 保留 |
| waltz | major | 7.5300 | 6.8200 | 8.0700 | 保留 |

> ⚠️ **1** 个调式的规则模型在留出集上差于一元基线。

> ℹ️ **1** 个调式的上下文剪枝在留出集上负收益，已由 `prune_with_regression` 自动回退。

## `leadsheet:jazz` — jazz → 风格包规则

- 语料：**410** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/jazz/swing-standard/grammar.yaml`
- 子风格 **6** 个，训练 361 / 留出 44

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| swing | major | 8.3500 | 10.1500 | 15.5700 | **已回退** |
| bebop | major | 7.0800 | 6.9800 | 17.0600 | **已回退** |
| cool | major | 3.9000 | 6.0600 | 21.3300 | **已回退** |
| hardbop | major | 10.8600 | 8.8600 | 20.3200 | 保留 |
| hardbop | minor | 12.8900 | 23.1100 | 17.3700 | **已回退** |
| postbop | major | 9.4200 | 15.5400 | 29.5600 | **已回退** |
| postbop | minor | 11.0900 | 10.8700 | 24.1900 | 保留 |
| traditional | major | 8.4200 | 10.6600 | 14.4700 | **已回退** |

> ℹ️ **6** 个调式的上下文剪枝在留出集上负收益，已由 `prune_with_regression` 自动回退。

## `leadsheet:pop` — pop → 风格包规则

- 语料：**198** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/pop/pop-standard/grammar.yaml`
- 子风格 **1** 个，训练 167 / 留出 23

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| pop | major | 8.2900 | 7.7800 | 15.6100 | 保留 |
| pop | minor | 10.7300 | 10.7300 | 15.1200 | 保留 |

## `leadsheet:madrigal` — madrigal → 风格包规则

- 语料：**48** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/vocal/madrigal/grammar.yaml`
- 子风格 **1** 个，训练 47 / 留出 1

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| madrigal | minor | 10.7800 | 9.3500 | 11.6400 | 保留 |

## `leadsheet:sonata` — sonata → 风格包规则

- 语料：**724** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/classical/piano-sonata/grammar.yaml`
- 子风格 **4** 个，训练 657 / 留出 67

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| allegro | major | 6.7700 | 6.7800 | 6.9900 | 保留 |
| allegro | minor | 7.8500 | 7.7300 | 8.4100 | 保留 |
| minuet | major | 7.0400 | 7.1600 | 7.7500 | 保留 |
| minuet | minor | 6.4200 | 6.2500 | 6.8700 | **已回退** |
| duple | major | 6.6300 | 6.8300 | 7.2500 | 保留 |
| duple | minor | 7.1600 | 7.3400 | 7.4400 | 保留 |
| compound | major | 6.4900 | 6.9100 | 6.9300 | **已回退** |

> ℹ️ **2** 个调式的上下文剪枝在留出集上负收益，已由 `prune_with_regression` 自动回退。

## `leadsheet:strings` — strings → 风格包规则

- 语料：**190** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/classical/string-quartet/grammar.yaml`
- 子风格 **4** 个，训练 152 / 留出 38

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| allegro | major | 6.4400 | 6.4300 | 6.7000 | 保留 |
| minuet | major | 8.3600 | 8.3500 | 8.7600 | 保留 |
| duple | major | 7.9900 ⚠️ | 7.9000 | 7.9600 | 保留 |
| duple | minor | 11.0100 ⚠️ | 10.0000 | 10.0100 | 保留 |

> ⚠️ **2** 个调式的规则模型在留出集上差于一元基线。

## `leadsheet:symphony` — symphony → 风格包规则

- 语料：**640** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/classical/symphony/grammar.yaml`
- 子风格 **4** 个，训练 627 / 留出 13

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| minuet | major | 6.7500 | 7.2800 | 7.9500 | 保留 |
| minuet | minor | 2.5200 | 3.2200 | 3.4000 | **已回退** |

> ℹ️ **1** 个调式的上下文剪枝在留出集上负收益，已由 `prune_with_regression` 自动回退。

## `leadsheet:chopin` — chopin → 风格包规则

- 语料：**242** 首
- 规则脚本：`/Users/leizhao/Documents/prj/composerchat/skills/composition/genre/packs/romantic/chopin-piano/grammar.yaml`
- 子风格 **1** 个，训练 201 / 留出 6

| 子风格 | 调式 | 规则困惑度 | 无位置 | 一元基线 | 剪枝 |
|---|---|---|---|---|---|
| mazurka | major | 8.5700 | 10.3000 | 10.1000 | **已回退** |
| mazurka | minor | 8.9300 | 8.9900 | 10.3700 | 保留 |

> ℹ️ **1** 个调式的上下文剪枝在留出集上负收益，已由 `prune_with_regression` 自动回退。

## `relation` — 人工段落标注 → form_plan（段落结构与关系）

- 曲目 **456** 首 / 小节 **35,412** / 关系边 **8,802**

  关系类型：cause 2,836、contrast 1,589、quote 1,443、introduce 1,073、continue 776、unclear 659、sequence 426

- 体裁覆盖：**7**

**已注入风格包**：
- jazz/swing-standard/swing ← SWING（8 种段数）
- jazz/swing-standard/bebop ← BEBOP（7 种段数）
- jazz/swing-standard/cool ← COOL（9 种段数）
- jazz/swing-standard/hardbop ← HARDBOP（9 种段数）
- jazz/swing-standard/postbop ← POSTBOP（9 种段数）
- jazz/swing-standard/traditional ← TRADITIONAL（5 种段数）
