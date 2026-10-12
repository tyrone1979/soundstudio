# 作曲生产报告：英雄对峙（程序员版）

> 面向产品/工程：记录本次生成的完整生产链路与系统状态，用于核对「说得 vs 写得」、排查缺陷与回归测试；可整体喂回大模型进一步定位具体小节/声部的问题。谱面检查与编辑者版一致，编辑者版见：`2030c62765_report.md`。

## 1. 会话与生成环境

- 生成时间: 2026-09-27T13:36:00
- 生成路径: translate
- 命中技能: orchestrate, genre:academic/fugue, genre:cinematic/epic-battle
- 用户原始输入: 史诗战争 写一首史诗风格的管弦乐小品：弦乐组（小提琴+中提琴+大提琴）铺底推进， 圆号奏英雄气概的主题，定音鼓在高潮处滚奏，B段转入关系小调制造阴云， 结尾回到大调辉煌收束。不要
- 原始数据: `session.compose_trace`（本报告第 4 节即其展开，确定性可复现）

## 2. 谱面事实（与编辑者版「基本信息」一致）

- 调性: C 大调（0 个升号）
- 转调: 第 9 小节 → A 小调
- 拍号: 4/4
- 速度: 四分音符 = 96 BPM
- 段落速度: 第 9 小节 Agitato（♩=108）；第 17 小节 Allegro（♩=120）
- 调内音级: C, D, E, F, G, A, B
- 声部 1: 小提琴 (Violin)，高音谱号，24 小节
- 声部 2: 中提琴 (Viola)，ALTO，24 小节
- 声部 3: 大提琴 (Cello)，低音谱号，24 小节
- 声部 4: 圆号 (Horn)，高音谱号，24 小节

## 3. 自动检查结果（与编辑者版「第 3 节」一致）

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 调号一致性 | ⚠️ 注意 | A 小调 段出现变化音 Db；C 大调 段出现变化音 G#（转调段调外音/经过音） |
| 声部交叉 | ⚠️ 注意 | 相邻声部 3/4 在第 6.0 拍交叉：D3 低于 G3（同类交叉共 48 处，此处为第 1 例） |
| 音域 | ⚠️ 注意 | 小提琴 (Violin): B4–G7（MIDI 71–103） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 105 项（✅ 102 / ⚠️ 3 / ❌ 0）。

## 4. 作曲过程透明化（可追溯）

> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，便于核对「说得 vs 写得」是否一致。数据来自本次生成的 `session.compose_trace`，确定性可复现。把本报告整体喂回大模型，可进一步定位具体小节/声部的问题与修正建议。

### 4.1 流程图（本次实际链路）

```mermaid
flowchart TD
  U["① 用户输入<br/>史诗战争 写一首史诗风格的管弦乐小品：弦乐组（小提琴+中提琴+大提琴）铺…"] --> P["② Planner LLM<br/>提取意图 plan"]
  P --> E{"③ 情绪/节奏约束<br/>无情绪词"}
  E --> T["④ Translator LLM<br/>输出脚本 calls"]
  T --> G{"⑤ 风格硬约束<br/>无（LLM 已在规范内）"}
  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]
  K --> S["⑦ 渲染乐谱<br/>4/4 ♩=96 24小节"]
```

（文字版流程图，便于无 Mermaid 渲染环境阅读）

```
用户输入
  → [② Planner LLM] 提取意图 plan
  → [③ 情绪/节奏约束] 命中=否
  → [④ Translator LLM] 输出 calls + reasoning
  → [⑤ 风格硬约束] 触发=否
  → [⑥ 内核 run_program] 逐脚本执行
  → [⑦ 渲染谱面] 4/4 ♩=96 24小节
```

### 4.2 大模型输入输出（LLM I/O）

#### Planner（意图解析）

- **系统提示**（固定指令模板，已截断）：

```
You are a professional composer and music theorist acting as an Intent Parser.

Transform the user's natural-language request (and optional slash-commands already applied as preferences) into a Composition Plan JSON only.

Think like a composer first: infer genre, mood, form, motif character, harmony language, climax — do NOT invent note lists yet.

Output ONE JSON object, no Markdown:
{
  "title": "string",
  "genre": "string",
  "mood": ["..."],
  "tempo": 72,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major|minor",
  "instrumentation": ["Piano"] or ["Violin","Cello"],
  "form": ["A","B","A"],
  "bars": 8,
  "motif": {"character": "...", "development": ["repetition","sequence"]},
  "harmony": {"language": "...", "density": "sparse|moderate|dense"},
  "rhythm": {"character": "..."},
  "texture": "homophonic|polyphonic|sparse|orchestral",
  "climax": {"location": "section B", "dynamic": "f"},
  "difficulty": "easy|medium",
  "summary": "one sentence for the user",
  "edit_intent": null
}
Rules:
- Original music only; no copyrighted melodies.
- If user is vague, choose musically appropriate defaults.
- bars: 4–32. Prefer 8–16 for a brand-new short piece.
- If the user asks to extend/lengthen to N bars (e.g. 扩展到32小节), set bars=N (not 8).
- If edit_intent is extend/longer and no N given, increase bars vs existing_score (typically +8 to +16, max 32).
- If edit_intent is rewrit
…（已截断，完整 5748 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
```

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "user_request": "史诗战争 写一首史诗风格的管弦乐小品：弦乐组（小提琴+中提琴+大提琴）铺底推进， 圆号奏英雄气概的主题，定音鼓在高潮处滚奏，B段转入关系小调制造阴云， 结尾回到大调辉煌收束。不要",
  "preferences": {
    "key_sharps": 0,
    "mode": "major",
    "tempo": 96,
    "bars": 24,
    "meter": "4/4",
    "style": "赋格",
    "genre_id": "genre:academic/fugue",
    "instrumentation": "",
    "fugue": "auto",
    "voices": 4,
    "_explicit": {
      "tempo": true,
      "meter": true,
      "bars": true
    }
  },
  "edit_intent": null,
  "skills": [
    "orchestrate",
    "genre:academic/fugue",
    "genre:cinematic/epic-battle"
  ],
  "existing_plan": null,
  "revision_edits": null,
  "existing_score_summary": null
}
```

- **模型返回（原始）**：

```json
{
  "title": "英雄对峙",
  "genre": "史诗管弦乐小品",
  "mood": [
    "英雄气概",
    "壮阔",
    "阴云暗涌",
    "辉煌"
  ],
  "tempo": 96,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major",
  "instrumentation": [
    "Violin",
    "Viola",
    "Cello",
    "Horn",
    "Timpani"
  ],
  "form": [
    "A",
    "B",
    "A'"
  ],
  "bars": 24,
  "motif": {
    "character": "宽广、附点节奏的英雄主题，以圆号齐奏呈现，音程以四度、五度跳进为主",
    "development": [
      "repetition",
      "sequence",
      "rhythmic_augmentation",
      "tonal shift"
    ]
  },
  "harmony": {
    "language": "功能和声，以I–V–vi–IV与平行小调借用和弦为主，B段转入关系小调（a小调）制造阴云",
    "density": "moderate"
  },
  "rhythm": {
    "character": "进行曲式强拍推进，附点与切分音增强动力，定音鼓滚奏标记高潮"
  },
  "texture": "orchestral",
  "climax": {
    "location": "section A' 结尾（第21–24小节）",
    "dynamic": "ff"
  },
  "difficulty": "medium",
  "summary": "一首24小节的史诗管弦乐小品：弦乐铺底推进，圆号奏英雄主题，B段转入关系小调制造阴云，结尾回到大调辉煌收束。",
  "edit_intent": null
}
```

#### Translator（脚本翻译）

- **系统提示**（固定指令模板，已截断）：

````
# 翻译器（Translator）

你不是作曲家，是**翻译器**：把用户的自然语言音乐需求与 plan 翻译成
对确定性脚本的调用序列。你**不写任何音符**——所有音符由脚本生成并保证
正确（时值、调性、织体、力度、终止）。你的职责是选对脚本、给对参数。

## 输出格式（只输出 JSON，不要任何解释文字）

```
{"reasoning": "用中文简述你如何把需求翻译成参数与脚本（2–5 句）：识别了什么风格/情绪，怎么决定速度/调式/织体，段落如何安排，调用了哪些脚本。", "calls": [{"script": "脚本名", "args": {...}}, ...]}
```

## 硬规则

1. 调用顺序：`new_score` → `set_sections` → 对每个段落 `{ set_harmony →
   accompaniment → melody }` →（高潮段可加 `thicken`）→（最后一段可加
   `cadence`）→ `finalize`
2. `new_score` 与 `finalize` 必须各恰好一次，且分别在最前/最后
3. **每个段落**都必须调用 set_harmony / accompaniment / melody，
   一次都不许漏（漏了 finalize 会报错）
4. args 必须严格符合脚本文档的字段名与取值范围；不要发明不存在的参数
5. 脚本执行失败时你会收到错误信息——修正 calls 后完整重新输出
6. `plan.tempo` / `plan.mode` / `plan.mood` / `plan.tempo_text` /
   `plan._emotion_hint` 是已解析的**明确约束**（来自用户情绪/速度词或
   `/tempo`），**优先级最高**——调用 `new_score` / `set_harmony` / `melody`
   时直接采用，不要用风格基线默认值覆盖。`reasoning` 字段必须如实写出你
   如何据此翻译，便于用户核查。
7. **乐器编制**：brief 提到管弦乐/弦乐/铜管/木管/具体乐器（小提琴、长笛、
   圆号、定音鼓…）或某种编制（弦乐四重奏、铜管五重奏…）时，`new_score`
   **必须传 `instruments`**（长度=voices，从高音到低音，低音乐器放最后），
   可用名：flute/oboe/horn/trumpet/violin/viola/cello/contrabass/timpani/
   piano/soprano/alto/tenor/bass。例：「弦乐四重奏」→ voices=4 +
   `["violin","violin","viola","cello"]`；「管弦乐小品」→ voices=4 +
   `["flute","violin","cello","timpani"]` 之类按 brief 取舍。不传时 2 声部
   默认钢琴大谱表、3/4 声部默认人声 SATB。

## 音乐判断指南（把情绪翻译成参数）

| 用户意图 | melody | accompaniment | 其他 |
|---|---|---|---
…（已截断，完整 13548 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
````

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "plan": {
    "title": "英雄对峙",
    "genre": "史诗管弦乐小品",
    "mood": [
      "英雄气概",
      "壮阔",
      "阴云暗涌",
      "辉煌"
    ],
    "tempo": 96,
    "tempo_text": null,
    "meter": "4/4",
    "key_sharps": 0,
    "mode": "major",
    "_emotion_hint": null,
    "instrumentation": [
      "Violin",
      "Viola",
      "Cello",
      "Horn",
      "Timpani"
    ],
    "form": [
      "A",
      "B",
      "A'"
    ],
    "bars": 24,
    "motif": {
      "character": "宽广、附点节奏的英雄主题，以圆号齐奏呈现，音程以四度、五度跳进为主",
      "development": [
        "repetition",
        "sequence",
        "rhythmic_augmentation",
        "tonal shift"
      ]
    },
    "harmony": {
      "language": "功能和声，以I–V–vi–IV与平行小调借用和弦为主，B段转入关系小调（a小调）制造阴云",
      "density": "moderate"
    },
    "rhythm": {
      "character": "进行曲式强拍推进，附点与切分音增强动力，定音鼓滚奏标记高潮"
    },
    "texture": "orchestral",
    "climax": {
      "location": "section A' 结尾（第21–24小节）",
      "dynamic": "ff"
    },
    "summary": "一首24小节的史诗管弦乐小品：弦乐铺底推进，圆号奏英雄主题，B段转入关系小调制造阴云，结尾回到大调辉煌收束。"
  },
  "user_brief": "史诗战争 写一首史诗风格的管弦乐小品：弦乐组（小提琴+中提琴+大提琴）铺底推进， 圆号奏英雄气概的主题，定音鼓在高潮处滚奏，B段转入关系小调制造阴云， 结尾回到大调辉煌收束。不要"
}
```

- **模型返回（原始）**：

```json
{
  "reasoning": "A 段动机总时值超过 4/4 一小节容量，将其压缩为 4 拍：四分+八分+八分+四分+四分。其余段落与和声、织体、转调安排保持不变：A 大调宽广主题，B 转 a 小调阴云，A' 回大调辉煌并 thicken+cadence。",
  "calls": [
    {
      "script": "new_score",
      "args": {
        "title": "英雄对峙",
        "key_sharps": 0,
        "mode": "major",
        "meter": "4/4",
        "tempo": 96,
        "bars": 24,
        "voices": 4,
        "instruments": [
          "violin",
          "viola",
          "cello",
          "horn"
        ]
      }
    },
    {
      "script": "set_sections",
      "args": {
        "sections": [
          {
            "name": "A",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "mf",
            "tempo_text": "Andante",
            "bpm": 96
          },
          {
            "name": "B",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "f",
            "tempo_text": "Agitato",
            "bpm": 108
          },
          {
            "name": "A'",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "ff",
            "tempo_text": "Allegro",
            "bpm": 120
          }
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "A",
        "progression": [
          "I",
          "V",
          "vi",
          "IV",
          "I",
          "V",
          "IV",
          "V7"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "A",
        "pattern": "broken_arpeggio",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "new_score",
      "args": {
        "title": "英雄对峙",
        "key_sharps": 0,
        "mode": "major",
        "meter": "4/4",
        "tempo": 96,
        "bars": 24,
        "voices": 4,
        "instruments": [
          "violin",
          "viola",
          "cello",
          "horn"
        ]
      }
    },
    {
      "script": "set_sections",
      "args": {
        "sections": [
          {
            "name": "A",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "mf",
            "tempo_text": "Andante",
            "bpm": 96
          },
          {
            "name": "B",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "f",
            "tempo_text": "Agitato",
            "bpm": 108
          },
          {
            "name": "A'",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "ff",
            "tempo_text": "Allegro",
            "bpm": 120
          }
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "A",
        "progression": [
          "I",
          "V",
          "vi",
          "IV",
          "I",
          "V",
          "IV",
          "V7"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "A",
        "pattern": "broken_arpeggio",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "A",
        "register": "high",
        "rhythm": "flowing",
        "contour": "arch",
        "motif": [
          [
            1,
            "quarter"
          ],
          [
            5,
            "eighth"
          ],
          [
            5,
            "eighth"
          ],
          [
            3,
            "quarter"
          ],
          [
            4,
            "quarter"
          ]
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "B",
        "progression": [
          "i",
          "iv",
          "V7",
          "i",
          "VI",
          "iv",
          "V7",
          "i"
        ],
        "key_sharps": 0,
        "mode": "minor"
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "B",
        "pattern": "octave_pulse",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "B",
        "register": "high",
        "rhythm": "fragmented",
        "contour": "wave"
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "A'",
        "progression": [
          "I",
          "V",
          "IV",
          "V7",
          "I",
          "V",
          "IV",
          "I"
        ],
        "key_sharps": 0,
        "mode": "major"
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "A'",
        "pattern": "broken_arpeggio",
        "register": "low",
        "density": 2
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "A'",
        "register": "very_high",
        "rhythm": "driving",
        "contour": "ascending"
      }
    },
    {
      "script": "thicken",
      "args": {
        "section": "A'"
      }
    },
    {
      "script": "cadence",
      "args": {
        "section": "A'",
        "bars": 2
      }
    },
    {
      "script": "narrative",
      "args": {
        "intensity": 1.3,
        "surprises": 2,
        "silence": true
      }
    },
    {
      "script": "finalize",
      "args": {}
    }
  ]
}
```

### 4.3 脚本调用与输入输出

| # | 脚本 | 输入(args) | 输出（新增音符 / 状态变更） |
|---|---|---|---|
| 1 | new_score | title=英雄对峙，key_sharps=0，mode=major，meter=4/4，tempo=96，bars=24，voices=4，instruments=["violin", "viola", "cello", "horn"] | 建立乐曲：4/4 ♩=96 4声部 major；新增 — |
| 2 | set_sections | sections=[{"name": "A", "bars": 8, "meter": "4/4", "dynamic": "mf", "tempo_text": "Andante", "bpm": 96}, {"name": "B", "bars": 8, "meter": "4/4", "dynamic": "f", "tempo_text": "Agitato", "bpm": 108}, {"name": "A'", "bars": 8, "meter": "4/4", "dynamic": "ff", "tempo_text": "Allegro", "bpm": 120}] | 分段：A(8小节)，B(8小节)，A'(8小节)；新增 — |
| 3 | set_harmony | section=A，progression=["I", "V", "vi", "IV", "I", "V", "IV", "V7"] | 和声：段落A 进行['I', 'V', 'vi', 'IV', 'I', 'V', 'IV', 'V7']；新增 — |
| 4 | accompaniment | section=A，pattern=broken_arpeggio，register=low，density=1 | 伴奏：段落A pattern=broken_arpeggio register=low density=1；新增 声部2:+8，声部3:+8，声部4:+32 |
| 5 | new_score | title=英雄对峙，key_sharps=0，mode=major，meter=4/4，tempo=96，bars=24，voices=4，instruments=["violin", "viola", "cello", "horn"]　⟲ **LLM 重试**：此前调用序列出错回滚，从 new_score 重新执行 | 建立乐曲：4/4 ♩=96 4声部 major；新增 — |
| 6 | set_sections | sections=[{"name": "A", "bars": 8, "meter": "4/4", "dynamic": "mf", "tempo_text": "Andante", "bpm": 96}, {"name": "B", "bars": 8, "meter": "4/4", "dynamic": "f", "tempo_text": "Agitato", "bpm": 108}, {"name": "A'", "bars": 8, "meter": "4/4", "dynamic": "ff", "tempo_text": "Allegro", "bpm": 120}] | 分段：A(8小节)，B(8小节)，A'(8小节)；新增 — |
| 7 | set_harmony | section=A，progression=["I", "V", "vi", "IV", "I", "V", "IV", "V7"] | 和声：段落A 进行['I', 'V', 'vi', 'IV', 'I', 'V', 'IV', 'V7']；新增 — |
| 8 | accompaniment | section=A，pattern=broken_arpeggio，register=low，density=1 | 伴奏：段落A pattern=broken_arpeggio register=low density=1；新增 声部2:+8，声部3:+8，声部4:+32 |
| 9 | melody | section=A，register=high，rhythm=flowing，contour=arch，motif=[[1, "quarter"], [5, "eighth"], [5, "eighth"], [3, "quarter"], [4, "quarter"]] | 旋律：段落A rhythm=flowing；新增 声部1:+36 |
| 10 | set_harmony | section=B，progression=["i", "iv", "V7", "i", "VI", "iv", "V7", "i"]，key_sharps=0，mode=minor | 和声：段落B 进行['i', 'iv', 'V7', 'i', 'VI', 'iv', 'V7', 'i']；新增 — |
| 11 | accompaniment | section=B，pattern=octave_pulse，register=low，density=1 | 伴奏：段落B pattern=octave_pulse register=low density=1；新增 声部2:+8，声部3:+8，声部4:+32 |
| 12 | melody | section=B，register=high，rhythm=fragmented，contour=wave | 旋律：段落B rhythm=fragmented；新增 声部1:+42 |
| 13 | set_harmony | section=A'，progression=["I", "V", "IV", "V7", "I", "V", "IV", "I"]，key_sharps=0，mode=major | 和声：段落A' 进行['I', 'V', 'IV', 'V7', 'I', 'V', 'IV', 'I']；新增 — |
| 14 | accompaniment | section=A'，pattern=broken_arpeggio，register=low，density=2 | 伴奏：段落A' pattern=broken_arpeggio register=low density=2；新增 声部2:+8，声部3:+8，声部4:+64 |
| 15 | melody | section=A'，register=very_high，rhythm=driving，contour=ascending | 旋律：段落A' rhythm=driving；新增 声部1:+37 |
| 16 | thicken | section=A' | 加厚：段落A'；新增 — |
| 17 | cadence | section=A'，bars=2 | 终止：bars=2；新增 — |
| 18 | narrative | intensity=1.3，surprises=2，silence=True | narrative；新增 声部1:+6 |
| 19 | finalize |  | 收束装配：24小节 / 4声部；新增 — |

<details><summary>逐脚本新增音符样例（前 8 个 / 声部）</summary>

- **new_score**：声部1: —；声部2: —；声部3: —；声部4: —
- **set_sections**：声部1: —；声部2: —；声部3: —；声部4: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —
- **accompaniment**：声部1: —；声部2: E4:whole, B4:whole, C4:whole, A4:whole, E4:whole, B4:whole, A4:whole, B4:whole；声部3: G3:whole, D3:whole, E3:whole, C3:whole, G3:whole, D3:whole, C3:whole, D3:whole；声部4: C2:quarter, G2:quarter, C3:quarter, G3:quarter, G2:quarter, D2:quarter, G3:quarter, D3:quarter
- **new_score**：声部1: —；声部2: —；声部3: —；声部4: —
- **set_sections**：声部1: —；声部2: —；声部3: —；声部4: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —
- **accompaniment**：声部1: —；声部2: E4:whole, B4:whole, C4:whole, A4:whole, E4:whole, B4:whole, A4:whole, B4:whole；声部3: G3:whole, D3:whole, E3:whole, C3:whole, G3:whole, D3:whole, C3:whole, D3:whole；声部4: C2:quarter, G2:quarter, C3:quarter, G3:quarter, G2:quarter, D2:quarter, G3:quarter, D3:quarter
- **melody**：声部1: C6:quarter, G6:eighth, G6:eighth, E6:quarter, F6:quarter, B5:quarter, A5:eighth, G5:eighth；声部2: —；声部3: —；声部4: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —
- **accompaniment**：声部1: —；声部2: C4:whole, F4:whole, G#4:whole, C4:whole, A4:whole, F4:whole, G#4:whole, C4:whole；声部3: E3:whole, A3:whole, B3:whole, E3:whole, C3:whole, A3:whole, B3:whole, E3:whole；声部4: A2+A3:quarter, A2+A3:quarter, A2+A3:quarter, A2+A3:quarter, D2+D3:quarter, D2+D3:quarter, D2+D3:quarter, D2+D3:quarter
- **melody**：声部1: A5:quarter, E5:quarter, None:eighth, A5:quarter, G5:eighth, F5:eighth, F5:eighth, G5:eighth；声部2: —；声部3: —；声部4: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —
- **accompaniment**：声部1: —；声部2: E4:whole, B4:whole, A4:whole, B4:whole, E4:whole, B4:whole, A4:whole, E4:whole；声部3: G3:whole, D3:whole, C3:whole, D3:whole, G3:whole, D3:whole, C3:whole, G3:whole；声部4: C2:eighth, E3:eighth, G3:eighth, C3:eighth, G3:eighth, E3:eighth, C2:eighth, E3:eighth
- **melody**：声部1: G5:quarter, E5:quarter, F5:eighth, G5:eighth, G5:quarter, B5:quarter, D6:quarter, G6:quarter；声部2: —；声部3: —；声部4: —
- **thicken**：声部1: —；声部2: —；声部3: —；声部4: —
- **cadence**：声部1: —；声部2: —；声部3: —；声部4: —
- **narrative**：声部1: D6+D7:quarter, E6+E7:eighth, F6+F7:eighth, G6+G7:quarter, C5+C6:whole, C5+C6:whole；声部2: —；声部3: —；声部4: —
- **finalize**：声部1: —；声部2: —；声部3: —；声部4: —

</details>

### 4.4 本地强制约束（确定性兜底，优先级高于 LLM）

- 情绪/节奏约束：未触发（用户未给情绪/速度词，或已被风格基线覆盖）
- 风格/速度强制纠正：未触发（LLM 输出已在 genre 规范内）
### 4.5 自动问题定位（可疑点 + 程度）

| 检查项 | 程度 | 说明 |
|---|---|---|
| 情绪约束 | 🟢 未触发 | 用户未给情绪/速度词 |
| 意图拍号↔谱面 | 🟢 一致 | 最终拍号 4/4 |
| 计划小节↔实际小节 | 🟢 一致 | 24 小节 |
| 风格规范遵循 | 🟢 良好 | LLM 输出均在 genre 规范内，无需强制 |
| 计划编制↔实际编制 | 🟡 注意 | 计划编制含 Timpani，但谱面 4 个声部未落地（voices 数不足或 translator 取舍）——用户对编制的明确要求可能未满足 |

> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / 🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。

## 5. 记谱与演奏法

可演奏性：5 项 MAJOR / 34 项总计
- [MAJOR] 声部1（Violin）音 103 超出音域 55–98
- [MAJOR] 声部1（Violin）音 101 超出音域 55–98
- [MAJOR] 声部1（Violin）音 100 超出音域 55–98
- [MAJOR] 声部1（Violin）音 101 超出音域 55–98
- [MAJOR] 声部1（Violin）音 103 超出音域 55–98
- [MINOR] 声部1 大跳 19 半音（>八度）
- [MINOR] 声部4 大跳 17 半音（>八度）
- [MINOR] 声部4 大跳 17 半音（>八度）
- …其余 26 项从略

## 6. 生成质量评估（三指标）

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **最近邻**：hymn（相似度 0.41）— ✅ 无抄袭嫌疑
