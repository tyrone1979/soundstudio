# 作曲生产报告：Symphonic Poem: The Lone Contrabass（程序员版）

> 面向产品/工程：记录本次生成的完整生产链路与系统状态，用于核对「说得 vs 写得」、排查缺陷与回归测试；可整体喂回大模型进一步定位具体小节/声部的问题。谱面检查与编辑者版一致，编辑者版见：`a42be432f6_report.md`。

## 1. 会话与生成环境

- 生成时间: 2026-09-27T16:27:02
- 生成路径: translator（LLM 翻译 + 确定性内核）
- 命中技能: compose-full
- 用户原始输入: 写一首交响诗，主题由低音提琴独奏，其余声部轻奏。
- 原始数据: `session.compose_trace`（本报告第 4 节即其展开，确定性可复现）

## 2. 谱面事实（与编辑者版「基本信息」一致）

- 调性: C 大调（0 个升号）
- 拍号: 4/4
- 速度: 四分音符 = 120 BPM
- 调内音级: C, D, E, F, G, A, B
- 声部 1: Contrabass，低音谱号，8 小节
- 声部 2: 长笛 (Flute)，高音谱号，8 小节
- 声部 3: Clarinet，高音谱号，8 小节
- 声部 4: 小提琴 (Violin)，高音谱号，8 小节
- 声部 5: 中提琴 (Viola)，ALTO，8 小节
- 声部 6: 大提琴 (Cello)，低音谱号，8 小节
- 声部 7: 圆号 (Horn)，高音谱号，8 小节
- 声部 8: 定音鼓 (Timpani)，低音谱号，8 小节

## 3. 自动检查结果（与编辑者版「第 3 节」一致）

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 调号一致性 | ⚠️ 注意 | C 大调下出现变化音 C, D, E, F, F#, G, G#, A, B（经过音/离调） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 4（小提琴 (Violin)）与下方声部 3（Clarinet）在第 2.0 拍交叉：C5 低于 E5（同类交叉共 13 处，此处为第 1 例） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 3（Clarinet）与下方声部 5（中提琴 (Viola)）在第 22.0 拍交叉：D4 低于 F4 |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 5（中提琴 (Viola)）与下方声部 1（Contrabass）在第 20.0 拍交叉：C4 低于 G4（同类交叉共 3 处，此处为第 1 例） |
| 音域 | ⚠️ 注意 | Contrabass: C3–A4（MIDI 48–69） |
| 音域 | ⚠️ 注意 | 定音鼓 (Timpani): C2–G4（MIDI 36–67） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 80 项（✅ 74 / ⚠️ 6 / ❌ 0）。

## 4. 作曲过程透明化（可追溯）

> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，便于核对「说得 vs 写得」是否一致。数据来自本次生成的 `session.compose_trace`，确定性可复现。把本报告整体喂回大模型，可进一步定位具体小节/声部的问题与修正建议。

### 4.1 流程图（本次实际链路）

```mermaid
flowchart TD
  U["① 用户输入<br/>写一首交响诗，主题由低音提琴独奏，其余声部轻奏。"] --> P["② Planner LLM<br/>提取意图 plan"]
  P --> E{"③ 情绪/节奏约束<br/>无情绪词"}
  E --> T["④ Translator LLM<br/>输出脚本 calls"]
  T --> G{"⑤ 风格硬约束<br/>无（LLM 已在规范内）"}
  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]
  K --> S["⑦ 渲染乐谱<br/>4/4 ♩=120 8小节"]
```

（文字版流程图，便于无 Mermaid 渲染环境阅读）

```
用户输入
  → [② Planner LLM] 提取意图 plan
  → [③ 情绪/节奏约束] 命中=否
  → [④ Translator LLM] 输出 calls + reasoning
  → [⑤ 风格硬约束] 触发=否
  → [⑥ 内核 run_program] 逐脚本执行
  → [⑦ 渲染谱面] 4/4 ♩=120 8小节
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
…（已截断，完整 2397 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
```

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "user_request": "写一首交响诗，主题由低音提琴独奏，其余声部轻奏。",
  "preferences": {
    "key_sharps": 0,
    "mode": "major",
    "tempo": 120,
    "bars": 8,
    "meter": "4/4",
    "style": "",
    "genre_id": "",
    "instrumentation": "",
    "fugue": "auto",
    "voices": 0,
    "_explicit": {}
  },
  "edit_intent": null,
  "skills": [
    "compose-full"
  ],
  "existing_plan": null,
  "revision_edits": null,
  "existing_score_summary": null
}
```

- **模型返回（原始）**：

```json
{
  "title": "Symphonic Poem: The Lone Contrabass",
  "genre": "Symphonic poem",
  "mood": [
    "mysterious",
    "brooding",
    "expansive",
    "tender"
  ],
  "tempo": 120,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major",
  "instrumentation": [
    "Double Bass",
    "Violin",
    "Viola",
    "Cello",
    "Flute",
    "Clarinet",
    "Horn",
    "Timpani"
  ],
  "form": [
    "A",
    "A'",
    "B",
    "A''"
  ],
  "bars": 8,
  "motif": {
    "character": "A slow, rising perfect-fourth sigh in the low register, answered by a falling minor-second, evoking solitude and distant horizons.",
    "development": [
      "repetition",
      "sequence",
      "inversion",
      "rhythmic augmentation"
    ]
  },
  "harmony": {
    "language": "Late-Romantic extended tonality with modal mixture and pedal points; sparse, open fifths in the low strings.",
    "density": "sparse"
  },
  "rhythm": {
    "character": "Broad, speech-like phrases in the solo bass; gentle, almost static accompaniment in the upper strings."
  },
  "texture": "homophonic",
  "climax": {
    "location": "section B",
    "dynamic": "mf"
  },
  "difficulty": "medium",
  "summary": "An 8-bar symphonic poem where a solitary double bass sings the main theme over hushed, sustained orchestral support.",
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
   可用名：flute/oboe/**piccolo**/clarinet/bassoon/horn/trumpet/trombone/
   tuba/violin/viola/cello/contrabass/timpani/**harp**/piano/soprano/alto/
   tenor/bass。例：「弦乐四重奏」→ voices=4 +
   `["violin","violin","viola","cello"]`；「管弦乐小品」→ voices=4 +
   `["flute","violin","cello","timpani"]` 之类按 brief 取舍。不传时 2 声部
   默认钢琴大谱表、3/4 声部默认人声 SATB。
   - **「交响乐 / 交响曲 / 管
…（已截断，完整 10474 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
````

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "plan": {
    "title": "Symphonic Poem: The Lone Contrabass",
    "genre": "Symphonic poem",
    "mood": [
      "mysterious",
      "brooding",
      "expansive",
      "tender"
    ],
    "tempo": 120,
    "tempo_text": null,
    "meter": "4/4",
    "key_sharps": 0,
    "mode": "major",
    "_emotion_hint": null,
    "instrumentation": [
      "Double Bass",
      "Violin",
      "Viola",
      "Cello",
      "Flute",
      "Clarinet",
      "Horn",
      "Timpani"
    ],
    "form": [
      "A",
      "A'",
      "B",
      "A''"
    ],
    "bars": 8,
    "motif": {
      "character": "A slow, rising perfect-fourth sigh in the low register, answered by a falling minor-second, evoking solitude and distant horizons.",
      "development": [
        "repetition",
        "sequence",
        "inversion",
        "rhythmic augmentation"
      ]
    },
    "harmony": {
      "language": "Late-Romantic extended tonality with modal mixture and pedal points; sparse, open fifths in the low strings.",
      "density": "sparse"
    },
    "rhythm": {
      "character": "Broad, speech-like phrases in the solo bass; gentle, almost static accompaniment in the upper strings."
    },
    "texture": "homophonic",
    "climax": {
      "location": "section B",
      "dynamic": "mf"
    },
    "summary": "An 8-bar symphonic poem where a solitary double bass sings the main theme over hushed, sustained orchestral support."
  },
  "user_brief": "写一首交响诗，主题由低音提琴独奏，其余声部轻奏。"
}
```

- **模型返回（原始）**：

```json
{
  "reasoning": "用户要求一首交响诗，低音提琴独奏主题，其余声部轻奏。plan 给出 mood 神秘/阴郁/辽阔/温柔，速度 120，4/4 拍，C 大调，8 小节，结构 A-A'-B-A''，织体 homophonic，和声为晚期浪漫扩展调性、稀疏开放五度。乐器编制含 Double Bass 独奏，我按 plan.instrumentation 传入 instruments 并设 solo_instrument='contrabass'，让内核把旋律路由到低音提琴并收敛音区。8 小节分四段各 2 小节，速度按 plan.tempo=120 保持 Allegro 但力度用 p/mp 表现轻奏与神秘；B 段为高潮 mf，A'' 收束。每段 set_harmony 用稀疏开放五度/晚期浪漫级数，accompaniment 用 sustained 轻奏，melody 按低音提琴独奏给 sparse/flowing，末段加 cadence。",
  "calls": [
    {
      "script": "new_score",
      "args": {
        "title": "Symphonic Poem: The Lone Contrabass",
        "key_sharps": 0,
        "mode": "major",
        "meter": "4/4",
        "tempo": 120,
        "bars": 8,
        "voices": 8,
        "instruments": [
          "flute",
          "clarinet",
          "violin",
          "viola",
          "cello",
          "horn",
          "contrabass",
          "timpani"
        ],
        "solo_instrument": "contrabass"
      }
    },
    {
      "script": "set_sections",
      "args": {
        "sections": [
          {
            "name": "A",
            "bars": 2,
            "meter": "4/4",
            "dynamic": "p",
            "tempo_text": "Allegro",
            "bpm": 120
          },
          {
            "name": "A'",
            "bars": 2,
            "meter": "4/4",
            "dynamic": "mp",
            "tempo_text": "Allegro",
            "bpm": 120
          },
          {
            "name": "B",
            "bars": 2,
            "meter": "4/4",
            "dynamic": "mf",
            "tempo_text": "Allegro",
            "bpm": 120
          },
          {
            "name": "A''",
            "bars": 2,
            "meter": "4/4",
            "dynamic": "p",
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
          "V"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "A",
        "pattern": "sustained",
        "register": "low",
        "density": 0.5
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "A",
        "register": "low",
        "rhythm": "sparse",
        "contour": "arch",
        "motif": [
          [
            5,
            "quarter"
          ],
          [
            1,
            "eighth"
          ],
          [
            7,
            "eighth"
          ],
          [
            1,
            "half"
          ]
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "A'",
        "progression": [
          "vi",
          "IV"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "A'",
        "pattern": "sustained",
        "register": "low",
        "density": 0.5
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "A'",
        "register": "low",
        "rhythm": "flowing",
        "contour": "wave"
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "B",
        "progression": [
          "IV",
          "V7"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "B",
        "pattern": "broken_arpeggio",
        "register": "mid",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "B",
        "register": "mid",
        "rhythm": "driving",
        "contour": "ascending"
      }
    },
    {
      "script": "thicken",
      "args": {
        "section": "B"
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "A''",
        "progression": [
          "I",
          "V7"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "A''",
        "pattern": "sustained",
        "register": "low",
        "density": 0.5
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "A''",
        "register": "low",
        "rhythm": "sparse",
        "contour": "descending"
      }
    },
    {
      "script": "cadence",
      "args": {
        "section": "A''",
        "bars": 2
      }
    },
    {
      "script": "narrative",
      "args": {
        "intensity": 0.8,
        "surprises": 1,
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
| 1 | new_score | title=Symphonic Poem: The Lone Contrabass，key_sharps=0，mode=major，meter=4/4，tempo=120，bars=8，voices=8，instruments=["flute", "clarinet", "violin", "viola", "cello", "horn", "contrabass", "timpani"]，solo_instrument=contrabass | 建立乐曲：4/4 ♩=120 8声部 major；新增 — |
| 2 | set_sections | sections=[{"name": "A", "bars": 2, "meter": "4/4", "dynamic": "p", "tempo_text": "Allegro", "bpm": 120}, {"name": "A'", "bars": 2, "meter": "4/4", "dynamic": "mp", "tempo_text": "Allegro", "bpm": 120}, {"name": "B", "bars": 2, "meter": "4/4", "dynamic": "mf", "tempo_text": "Allegro", "bpm": 120}, {"name": "A''", "bars": 2, "meter": "4/4", "dynamic": "p", "tempo_text": "Allegro", "bpm": 120}] | 分段：A(2小节)，A'(2小节)，B(2小节)，A''(2小节)；新增 — |
| 3 | set_harmony | section=A，progression=["I", "V"] | 和声：段落A 进行['I', 'V']；新增 — |
| 4 | accompaniment | section=A，pattern=sustained，register=low，density=0.5 | 伴奏：段落A pattern=sustained register=low density=0.5；新增 声部2:+16，声部3:+16，声部4:+16，声部5:+16，声部6:+16，声部7:+4，声部8:+4 |
| 5 | melody | section=A，register=low，rhythm=sparse，contour=arch，motif=[[5, "quarter"], [1, "eighth"], [7, "eighth"], [1, "half"]] | 旋律：段落A rhythm=sparse；新增 声部1:+7 |
| 6 | set_harmony | section=A'，progression=["vi", "IV"] | 和声：段落A' 进行['vi', 'IV']；新增 — |
| 7 | accompaniment | section=A'，pattern=sustained，register=low，density=0.5 | 伴奏：段落A' pattern=sustained register=low density=0.5；新增 声部2:+16，声部3:+16，声部4:+16，声部5:+16，声部6:+16，声部7:+4，声部8:+4 |
| 8 | melody | section=A'，register=low，rhythm=flowing，contour=wave | 旋律：段落A' rhythm=flowing；新增 声部1:+9 |
| 9 | set_harmony | section=B，progression=["IV", "V7"] | 和声：段落B 进行['IV', 'V7']；新增 — |
| 10 | accompaniment | section=B，pattern=broken_arpeggio，register=mid，density=1 | 伴奏：段落B pattern=broken_arpeggio register=mid density=1；新增 声部2:+16，声部3:+16，声部4:+16，声部5:+16，声部6:+16，声部7:+4，声部8:+8 |
| 11 | melody | section=B，register=mid，rhythm=driving，contour=ascending | 旋律：段落B rhythm=driving；新增 声部1:+9 |
| 12 | thicken | section=B | 加厚：段落B；新增 — |
| 13 | set_harmony | section=A''，progression=["I", "V7"] | 和声：段落A'' 进行['I', 'V7']；新增 — |
| 14 | accompaniment | section=A''，pattern=sustained，register=low，density=0.5 | 伴奏：段落A'' pattern=sustained register=low density=0.5；新增 声部2:+16，声部3:+16，声部4:+16，声部5:+16，声部6:+16，声部7:+4，声部8:+4 |
| 15 | melody | section=A''，register=low，rhythm=sparse，contour=descending | 旋律：段落A'' rhythm=sparse；新增 声部1:+4 |
| 16 | cadence | section=A''，bars=2 | 终止：bars=2；新增 — |
| 17 | narrative | intensity=0.8，surprises=1，silence=True | narrative；新增 — |
| 18 | finalize |  | 收束装配：8小节 / 8声部；新增 — |

<details><summary>逐脚本新增音符样例（前 8 个 / 声部）</summary>

- **new_score**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **set_sections**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **accompaniment**：声部1: —；声部2: G5:eighth, C6:eighth, E6:eighth, G6:eighth, C7:eighth, F6:eighth, D6:eighth, C6:eighth；声部3: C5:eighth, E5:eighth, G5:eighth, C6:eighth, F6:eighth, G5:eighth, D6:eighth, B5:eighth；声部4: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E5:eighth, G5:eighth；声部5: None:eighth, C4:eighth, E4:eighth, G4:eighth, C5:eighth, C4:eighth, E4:eighth, G4:eighth；声部6: None:eighth, C3:eighth, E3:eighth, G3:eighth, C4:eighth, C3:eighth, E3:eighth, G3:eighth；声部7: C4:half, C4:half, G3:half, G3:half；声部8: C2:half, G2:half, G2:half, D2:half
- **melody**：声部1: F4:quarter, B3:eighth, A4:eighth, B3:half, D4:quarter, G4:half, D4:quarter；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **accompaniment**：声部1: —；声部2: A5:eighth, C6:eighth, E6:eighth, A6:eighth, C7:eighth, A6:eighth, D6:eighth, C6:eighth；声部3: C5:eighth, F5:eighth, A5:eighth, C6:eighth, E6:eighth, A6:eighth, D6:eighth, C6:eighth；声部4: None:eighth, A4:eighth, C5:eighth, E5:eighth, A5:eighth, A5:eighth, C5:eighth, E5:eighth；声部5: None:eighth, A4:eighth, C4:eighth, E4:eighth, A5:eighth, A4:eighth, C4:eighth, E4:eighth；声部6: None:eighth, A3:eighth, C3:eighth, E3:eighth, A4:eighth, A3:eighth, C3:eighth, E3:eighth；声部7: A3:half, A3:half, F3:half, F3:half；声部8: A2:half, E2:half, F2:half, C2:half
- **melody**：声部1: C4:quarter, E4:quarter, D4:eighth, C4:eighth, A3:quarter, G3:eighth, F3:eighth, F3:quarter；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **accompaniment**：声部1: —；声部2: A5:eighth, C6:eighth, F6:eighth, A6:eighth, C7:eighth, A6:eighth, F6:eighth, C6:eighth；声部3: C5:eighth, F5:eighth, A5:eighth, C6:eighth, F6:eighth, A6:eighth, F6:eighth, C6:eighth；声部4: None:eighth, F5:eighth, A5:eighth, C5:eighth, F6:eighth, F5:eighth, A5:eighth, C5:eighth；声部5: None:eighth, F4:eighth, A4:eighth, C4:eighth, F5:eighth, F4:eighth, A4:eighth, C4:eighth；声部6: None:eighth, F3:eighth, A3:eighth, C3:eighth, F4:eighth, F3:eighth, A4:eighth, C3:eighth；声部7: F3:half, F3:half, G3:half, G3:half；声部8: F3:quarter, C3:quarter, F4:quarter, C4:quarter, G3:quarter, D3:quarter, G4:quarter, D4:quarter
- **melody**：声部1: F4:quarter, C4:quarter, A3:quarter, F3:quarter, G3:eighth, A3:eighth, B3:quarter, D4:quarter；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **thicken**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **accompaniment**：声部1: —；声部2: G5:eighth, C6:eighth, F6:eighth, G6:eighth, C7:eighth, G6:eighth, E6:eighth, B5:eighth；声部3: C5:eighth, F5:eighth, G5:eighth, C6:eighth, E6:eighth, G6:eighth, E6:eighth, B5:eighth；声部4: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E5:eighth, G5:eighth；声部5: None:eighth, C4:eighth, E4:eighth, G4:eighth, C5:eighth, C4:eighth, E4:eighth, G4:eighth；声部6: None:eighth, C3:eighth, E3:eighth, G3:eighth, C4:eighth, C3:eighth, E3:eighth, G3:eighth；声部7: C4:half, C4:half, G3:half, G3:half；声部8: C2:half, G2:half, G2:half, D2:half
- **melody**：声部1: G4:quarter, E4:quarter, C4:half, C4:whole；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **cadence**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **narrative**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —
- **finalize**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —

</details>

### 4.4 本地强制约束（确定性兜底，优先级高于 LLM）

- 情绪/节奏约束：未触发（用户未给情绪/速度词，或已被风格基线覆盖）
- 风格/速度强制纠正：未触发（LLM 输出已在 genre 规范内）
### 4.5 自动问题定位（可疑点 + 程度）

| 检查项 | 程度 | 说明 |
|---|---|---|
| 情绪约束 | 🟢 未触发 | 用户未给情绪/速度词 |
| 意图拍号↔谱面 | 🟢 一致 | 最终拍号 4/4 |
| 计划小节↔实际小节 | 🟢 一致 | 8 小节 |
| 风格规范遵循 | 🟢 良好 | LLM 输出均在 genre 规范内，无需强制 |
| 本地修复 | 🟡 注意 | 校验后触发了本地修复（时值/声部进行），说明初版谱存在 CRITICAL/MAJOR 问题 |
| 计划编制↔实际编制 | 🟡 注意 | 计划编制含 Double Bass，但谱面 8 个声部未落地（voices 数不足或 translator 取舍）——用户对编制的明确要求可能未满足 |

> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / 🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。

## 5. 记谱与演奏法

**规则日志**（共 33 条，其中仲裁 0 次）

- [spelling/correctness] p1m3 G- — → F#：调号 0 下变音记号更少
- [spelling/correctness] p1m3 A- — → G#：调号 0 下变音记号更少
- [spelling/correctness] p1m3 G- — → F#：调号 0 下变音记号更少
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- …另有 21 条

可演奏性：4 项 MAJOR / 10 项总计
- [MAJOR] 声部1（Contrabass）音 69 超出音域 28–67
- [MINOR] 声部1 大跳 17 半音（>八度）
- [MINOR] 声部1 大跳 19 半音（>八度）
- [MINOR] 声部8 大跳 17 半音（>八度）
- [MAJOR] 声部8（Timpani）音 65 超出音域 36–60
- [MINOR] 声部8 大跳 17 半音（>八度）
- [MAJOR] 声部8（Timpani）音 67 超出音域 36–60
- [MINOR] 声部8 大跳 17 半音（>八度）
- …其余 2 项从略

## 6. 生成质量评估（三指标）

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **最近邻**：ballade-piano（相似度 0.64）— ✅ 无抄袭嫌疑
