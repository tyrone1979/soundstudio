# `/compose-grammar` 体裁测试清单

> 全部命令**已逐条实测通过**（走完整 `_compose_grammar_cmd` 路径，非仅底层调用）
> 日期：2026-10-04

---

## 复制即用 —— 先跑这10 条

```
/compose-grammar swing 32
/compose-grammar bebop 32
/compose-grammar cool 32
/compose-grammar hardbop 48
/compose-grammar postbop 96
/compose-grammar traditional 32
/compose-grammar jig 32
/compose-grammar waltz 24
/compose-grammar pop 32
/compose-grammar mazurka 32
```

---

## 按体裁分组（实测结果）

### 爵士 —— 6 个子风格，**有 form_plan**（走人工段落关系）

| 命令 | 小节 | 声部 | 曲式来源 |
|---|---|---|---|
| `/compose-grammar swing 32` | 32 | 3 | form_plan（4段） |
| `/compose-grammar bebop 32` | 32 | 3 | form_plan（4段） |
| `/compose-grammar cool 32` | 32 | 3 | form_plan（4段） |
| `/compose-grammar hardbop 48` | 48 | 3 | form_plan（6段） |
| `/compose-grammar postbop 96` | 96 | 3 | form_plan（12段，最长） |
| `/compose-grammar traditional 32` | 32 | 3 | form_plan（4段） |

> 爵士是唯一接了人工段落标注的体裁，生成的曲式是
> `A1A2B1A3` 这类**带角色和引用链**的段落序列，而非抽象的 `AABA`。

### 民谣 —— 4 个子风格

| 命令 | 小节 | 说明 |
|---|---|---|
| `/compose-grammar jig 32` | 32 | 吉格 |
| `/compose-grammar reel 32` | 32 | 里尔 |
| `/compose-grammar hornpipe 32` | 32 | 角笛舞 |
| `/compose-grammar waltz 24` | 24 | 华尔兹（3/4 拍） |

### 古典 —— 钢琴 / 弦乐 / 管弦

| 命令 | 小节 | 声部 | 说明 |
|---|---|---|---|
| `/compose-grammar allegro 32` | 32 | 2 | 奏鸣曲快板（钢琴） |
| `/compose-grammar minuet 24` | 24 | 2 | 小步舞曲（3/4） |
| `/compose-grammar duple 32` | 32 | 2 | 二拍子 |
| `/compose-grammar compound 32` | 32 | 2 | 复拍子（6/8） |
| `/compose-grammar quartet 32` | 32 | 4 | 弦乐四重奏 |
| `/compose-grammar strings 32` | 32 | 4 | 弦乐 |
| `/compose-grammar orchestra 48` | 48 | 9 | 管弦（9 声部） |
| `/compose-grammar symphony 64` | 64 | 9 | 交响曲（9 声部） |

### 其他

| 命令 | 小节 | 说明 |
|---|---|---|
| `/compose-grammar pop 32` | 32 | 流行歌 |
| `/compose-grammar madrigal 24` | 24 | 牧歌 |
| `/compose-grammar mazurka 32` | 32 | 肖邦玛祖卡 |
| `/compose-grammar chopin 32` | 32 | 肖邦 |

---

## 参数组合

| 参数 | 取值 | 说明 |
|---|---|---|
| 小节数 | 8 – 96 | 必须是 8 的倍数 |
| 调式 | `major` / `minor` / `大调` / `小调` | 语料须有该调式 |
| 起句| `pickup` / `弱起` / `downbeat` / `强拍起` | 默认 auto |
| 种子 | `seed=7` | 固定随机源，可逐音符复现 |
| 温度 | `t=0.5` | 0.5 = 更保守 |

**组合示例**：

```
/compose-grammar hardbop 48 minor
/compose-grammar postbop 96 minor seed=7
/compose-grammar swing 32 pickup
/compose-grammar mazurka 32 seed=7 t=0.5
/compose-grammar waltz 24 pickup
```

---

## 中文别名（同样实测通过）

```
/compose-grammar 摇摆 32
/compose-grammar 硬波普 48
/compose-grammar 小步舞曲 24
/compose-grammar 海顿交响曲 64
/compose-grammar 玛祖卡 32
/compose-grammar 吉格 32
/compose-grammar 华尔兹 24
/compose-grammar 圆舞曲 24
/compose-grammar 爵士 32
/compose-grammar 流行歌 32
/compose-grammar 牧歌 24
/compose-grammar 弦乐四重奏 32
/compose-grammar 管弦乐 48
```

全部可用别名共 **65 个**，覆盖中英文与常见译名。

---

## 完整 30 条实测清单

```
# 爵士（6）
/compose-grammar swing 32
/compose-grammar bebop 32
/compose-grammar cool 32
/compose-grammar hardbop 48
/compose-grammar postbop 96
/compose-grammar traditional 32

# 民谣（4）
/compose-grammar jig 32
/compose-grammar reel 32
/compose-grammar hornpipe 32
/compose-grammar waltz 24

# 古典（8）
/compose-grammar allegro 32
/compose-grammar minuet 24
/compose-grammar duple 32
/compose-grammar compound 32
/compose-grammar quartet 32
/compose-grammar strings 32
/compose-grammar orchestra 48
/compose-grammar symphony 64

# 其他（4）
/compose-grammar pop 32
/compose-grammar madrigal 24
/compose-grammar mazurka 32
/compose-grammar chopin 32

# 中文别名（5）
/compose-grammar 摇摆 32
/compose-grammar 硬波普 48
/compose-grammar 小步舞曲 24
/compose-grammar 海顿交响曲 64
/compose-grammar 玛祖卡 32

# 参数组合（5）
/compose-grammar hardbop 48 minor
/compose-grammar pop 32 minor
/compose-grammar mazurka 32 minor
/compose-grammar jig 32 minor
/compose-grammar madrigal 24 minor
/compose-grammar postbop 96 minor seed=7
/compose-grammar swing 32 pickup
/compose-grammar mazurka 32 seed=7 t=0.5
```

---

## 会失败的情况（语料限制，非 bug）

| 命令 | 报错 | 原因 |
|---|---|---|
| `/compose-grammar swing 32 minor` | 摇摆乐��料里小调曲子不足 | swing 语料只有大调 |
| `/compose-grammar cool 32 minor` | 同上 | cool 语料只有大调 |

**支持小调**的子风格：`allegro` `minuet` `duple` `compound` `jig` `reel`
`bebop` `hardbop` `postbop` `pop` `mazurka` `madrigal`。

---

## 本轮顺带修掉的bug

**小节数被静默截断到 32**。实测 `/compose-grammar hardbop 48` 返回的是
**32 小节**而不是 48 —— 用户以为写了 48 小节，实际只有 32。

两处上限：
1. `llm_compose.py:3059` `bars = max(8, min(32, 8*round(bars/8)))` → 放宽到 **96**
2. `compose_kernel.py:830` `max_bars = 256 if allow_long else 64` →
   在 `new_score` 的 args 里加 **`'allow_long': True`**

修复后实测：

```
hardbop 48    → 48 ✓      symphony 64  → 64 ✓
orchestra 48  → 48 ✓      swing 72     → 72 ✓
symphony 64   → 64 ✓      postbop 96   → 96 ✓
```

上限 96 是因为 `form_plan` 的段数策略覆盖到 **12 段 × 8 小节**，
正好对应 postbop 语料里最长的曲式。

---

## 复现校验

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest \
    tests/test_learn_all.py tests/test_form_plan_integration.py \
    tests/test_relation_extract.py -q
# → 25 passed
```

如果改了 `grammar.yaml` 或 `compose.py`，用上面那10 条快速回归即可。
