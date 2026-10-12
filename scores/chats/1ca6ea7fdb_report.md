# 作曲分析报告：雨夜琴声

本文件供人工或大模型核对。第 3 节仅列出需关注的问题与提示。

## 1. 要核对的作曲原则

1. **调内音高**：各音应落在该调号自然音阶内（调号升降号已计入；小调和声导音亦计入）。
2. **节奏完整**：每个声部每一小节时值之和应等于拍号。
3. **声部进行**：外声部（小提琴对大提琴）同向平行纯五度需避免；管弦乐八度加强不计入错误。
4. **声部交叉**：功能相邻声部不宜明显越过（木管互叠不计）。
5. **音域**：各声部应落在该乐器常用音域内。
6. **旋律**：器乐旋律允许较大跳进与较复杂节奏；极端无序跳进再提示。
7. **终止**：结束处最好含主音/主三和弦，旋律落在主音上更有完全终止感。

## 2. 基本信息

- 调性: A 小调（0 个升号）
- 拍号: 4/4
- 速度: 四分音符 = 64 BPM
- 调内音级: C, D, E, F, G, A, B
- 声部 1: Soprano，高音谱号，24 小节
- 声部 2: Alto，高音谱号，24 小节
- 声部 3: Bass，低音谱号，24 小节

## 3. 需关注的问题与提示

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 调号一致性 | ⚠️ 注意 | C 大调下出现变化音 C#, D#, F#, G#, A#（经过音/离调） |
| 声部进行 | ⚠️ 注意 | 声部1/3 在第 6.0→8.0 拍 平行五度（A4->B4 对 D2->E2）（管弦织体常见，可接受） |
| 声部交叉 | ⚠️ 注意 | 相邻声部 1/2 在第 38.0 拍交叉：F4 低于 A4 |
| 音域 | ⚠️ 注意 | Soprano: C4–A6（MIDI 60–93） |
| 音域 | ⚠️ 注意 | Bass: C2–B3（MIDI 36–59） |
| 终止式 | ⚠️ 注意 | C 大调结尾音级含 C#, A，旋律落在 A6（下中音） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 79 项（✅ 73 / ⚠️ 6 / ❌ 0）。

## 4. 作曲过程透明化（可追溯）

> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，便于核对「说得 vs 写得」是否一致。数据来自本次生成的 `session.compose_trace`，确定性可复现。把本报告整体喂回大模型，可进一步定位具体小节/声部的问题与修正建议。

### 4.1 流程图（本次实际链路）

```mermaid
flowchart TD
  U["① 用户输入<br/>请为「雨夜独自练琴的钢琴学生」创作一首钢琴小品，要求： 1. 风格：夜曲…"] --> P["② Planner LLM<br/>提取意图 plan"] P --> E{"③ 情绪/节奏约束<br/>情绪约束已触发"} E --> T["④ Translator LLM<br/>输出脚本 calls"]
  T --> G{"⑤ 风格硬约束<br/>风格硬约束已纠正"}
  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]
  K --> S["⑦ 渲染乐谱<br/>4/4 ♩=96 24小节"]
```

（文字版流程图，便于无 Mermaid 渲染环境阅读）

```
用户输入
  → [② Planner LLM] 提取意图 plan
  → [③ 情绪/节奏约束] 命中=是
  → [④ Translator LLM] 输出 calls + reasoning
  → [⑤ 风格硬约束] 触发=是
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
…（已截断，完整 5555 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
```

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "user_request": "请为「雨夜独自练琴的钢琴学生」创作一首钢琴小品，要求：\n1. 风格：夜曲（nocturne），但情绪从\"忧伤\"开始、中段转向\"宁静释然\"、结尾回归\"忧郁\"；\n2. 结构：A(8小节)–B(8小节)–A'(8小节) 三段，B 段是情绪转折点；\n3. 调性：A 段 A 小调，B 段转到 C 大调（关系大调），A' 段回 A 小调收束；\n4. 拍号 4/4，速度 ♩=63，左手分解和弦伴奏，右手歌唱性旋律；\n5. 结尾要有带属七解决的完整终止式。",
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
    "compose-full",
    "genre:academic/piano-prelude",
    "genre:intimate/nocturne"
  ],
  "existing_plan": null,
  "revision_edits": null,
  "existing_score_summary": null
}
```

- **模型返回（原始）**：

```json
{
  "title": "雨夜琴声",
  "genre": "nocturne",
  "mood": [
    "忧伤",
    "宁静释然",
    "忧郁"
  ],
  "tempo": 63,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "minor",
  "instrumentation": [
    "Piano"
  ],
  "form": [
    "A",
    "B",
    "A'"
  ],
  "bars": 24,
  "motif": {
    "character": "如歌的叹息式旋律，带附点与装饰音，气息悠长",
    "development": [
      "repetition",
      "sequence",
      "ornamentation",
      "modal shift"
    ]
  },
  "harmony": {
    "language": "功能和声，A段A小调 i–iv–V7–i，B段C大调 I–vi–IV–V，A'段回A小调并强化属七到主",
    "density": "moderate"
  },
  "rhythm": {
    "character": "左手流动分解和弦（十六分音符琶音），右手长线条与自由rubato"
  },
  "texture": "homophonic",
  "climax": {
    "location": "section B",
    "dynamic": "mf"
  },
  "difficulty": "medium",
  "summary": "一首A小调夜曲，三段体A–B–A'，左手分解和弦、右手歌唱旋律，中段转C大调带来宁静释然，结尾以属七解决收束回忧郁。",
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

## 音乐判断指南（把情绪翻译成参数）

| 用户意图 | melody | accompaniment | 其他 |
|---|---|---|---|
| 安静/朦胧/pp 前奏、结尾 | rhythm=sparse, register=mid/low | sustained 或 broken_arpeggio(density 0.5–1) | 可给 motif |
| 不安/挣扎/冲突 | rhythm=fragmented, register=high | octave_pulse 或 chromatic_descent | 和声用 bII/vii°7/V7 |
| 高潮/宣泄/ff | rhythm=driving, register=high/very_high | broken_arpeggio(density 2) | thicken |
| 平静收束 | rhythm=sparse, contour=descending | sustained | cadence |

## 情绪 / 速度词的确定性映射（plan 已解析，最高优先级）

用户的「欢快 / 快
…（已截断，完整 11789 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
````

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "plan": {
    "title": "雨夜琴声",
    "genre": "nocturne",
    "mood": [
      "忧伤",
      "宁静释然",
      "忧郁",
      "sad",
      "melancholic"
    ],
    "tempo": 64,
    "tempo_text": "Adagio",
    "meter": "4/4",
    "key_sharps": 0,
    "mode": "minor",
    "_emotion_hint": {
      "group": "slow",
      "matched": [
        "宁静",
        "忧伤",
        "忧郁"
      ],
      "bpm": 64,
      "tempo_text": "Adagio",
      "mode_bias": "minor",
      "rhythm": "sparse",
      "dynamics": "p",
      "contour": "descending"
    },
    "instrumentation": [
      "Piano"
    ],
    "form": [
      "A",
      "B",
      "A'"
    ],
    "bars": 24,
    "motif": {
      "character": "如歌的叹息式旋律，带附点与装饰音，气息悠长",
      "development": [
        "repetition",
        "sequence",
        "ornamentation",
        "modal shift"
      ]
    },
    "harmony": {
      "language": "功能和声，A段A小调 i–iv–V7–i，B段C大调 I–vi–IV–V，A'段回A小调并强化属七到主",
      "density": "moderate"
    },
    "rhythm": {
      "character": "左手流动分解和弦（十六分音符琶音），右手长线条与自由rubato"
    },
    "texture": "homophonic",
    "climax": {
      "location": "section B",
      "dynamic": "mf"
    },
    "summary": "一首A小调夜曲，三段体A–B–A'，左手分解和弦、右手歌唱旋律，中段转C大调带来宁静释然，结尾以属七解决收束回忧郁。"
  },
  "user_brief": "请为「雨夜独自练琴的钢琴学生」创作一首钢琴小品，要求：\n1. 风格：夜曲（nocturne），但情绪从\"忧伤\"开始、中段转向\"宁静释然\"、结尾回归\"忧郁\"；\n2. 结构：A(8小节)–B(8小节)–A'(8小节) 三段，B 段是情绪转折点；\n3. 调性：A 段 A 小调，B 段转到 C 大调（关系大调），A' 段回 A 小调收束；\n4. 拍号 4/4，速度 ♩=63，左手分解和弦伴奏，右手歌唱性旋律；\n5. 结尾要有带属七解决的完整终止式。"
}
```

- **模型返回（原始）**：

```json
{
  "reasoning": "用户明确要求夜曲风格、A小调、4/4、♩=63、A(8)-B(8)-A'(8)三段体，情绪从忧伤→宁静释然→忧郁。plan 的 _emotion_hint 为 slow 组（忧伤/忧郁/宁静），速度 64、小调、sparse、p、descending，与用户 brief 一致，故直接采用。new_score 用 A 小调（key_sharps=0, mode=minor）、4/4、bpm=64、24 小节。三段各 8 小节：A 段 i-iv-V7-i 忧伤 sparse；B 段转 C 大调 I-vi-IV-V 宁静释然、力度 mf 略升；A' 段回 A 小调强化 V7-i，sparse descending 收束。因夜曲基调织体为 broken_arpeggio，左手全程分解和弦。中段 B 安排赋格以增加对位层次，故 B 段只调 fugue。末段 A' 用 cadence(bars=2) 实现属七解决的完整终止。",
  "calls": [
    {
      "script": "new_score",
      "args": {
        "title": "雨夜琴声",
        "key_sharps": 0,
        "mode": "minor",
        "meter": "4/4",
        "tempo": 64,
        "bars": 24,
        "voices": 3
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
            "dynamic": "p",
            "tempo_text": "Adagio",
            "bpm": 64
          },
          {
            "name": "B",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "mf",
            "tempo_text": "Andantino",
            "bpm": 72
          },
          {
            "name": "A'",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "p",
            "tempo_text": "Adagio",
            "bpm": 62
          }
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "A",
        "progression": [
          "i",
          "iv",
          "V7",
          "i",
          "i",
          "iv",
          "V7",
          "i"
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
        "register": "mid",
        "rhythm": "sparse",
        "contour": "descending",
        "motif": [
          [
            5,
            "quarter"
          ],
          [
            4,
            "eighth"
          ],
          [
            3,
            "eighth"
          ],
          [
            2,
            "half"
          ]
        ]
      }
    },
    {
      "script": "fugue",
      "args": {
        "section": "B",
        "voices": 3,
        "answer": "tonal",
        "stretto": true,
        "episodes": true,
        "pedal": true
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "A'",
        "progression": [
          "i",
          "iv",
          "V7",
          "i",
          "i",
          "VI",
          "V7",
          "i"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "A'",
        "pattern": "broken_arpeggio",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "A'",
        "register": "mid",
        "rhythm": "sparse",
        "contour": "descending"
      }
    },
    {
      "script": "cadence",
      "args": {
        "bars": 2,
        "section": "A'"
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
| 1 | new_score | title=雨夜琴声，key_sharps=0，mode=minor，meter=4/4，tempo=64，bars=24，voices=3 | 建立乐曲：4/4 ♩=64 3声部 minor；新增 — |
| 2 | set_sections | sections=[{"name": "A", "bars": 8, "meter": "4/4", "dynamic": "p", "tempo_text": "Adagio", "bpm": 64}, {"name": "B", "bars": 8, "meter": "4/4", "dynamic": "mf", "tempo_text": "Andantino", "bpm": 72}, {"name": "A'", "bars": 8, "meter": "4/4", "dynamic": "p", "tempo_text": "Adagio", "bpm": 62}] | 分段：A(8小节)，B(8小节)，A'(8小节)；新增 — |
| 3 | set_harmony | section=A，progression=["i", "iv", "V7", "i", "i", "iv", "V7", "i"] | 和声：段落A 进行['i', 'iv', 'V7', 'i', 'i', 'iv', 'V7', 'i']；新增 — |
| 4 | accompaniment | section=A，pattern=broken_arpeggio，register=low，density=1 | 伴奏：段落A pattern=broken_arpeggio register=low density=1；新增 声部2:+8，声部3:+32 |
| 5 | melody | section=A，register=mid，rhythm=sparse，contour=descending，motif=[[5, "quarter"], [4, "eighth"], [3, "eighth"], [2, "half"]] | 旋律：段落A rhythm=sparse；新增 声部1:+23 |
| 6 | fugue | section=B，voices=3，answer=tonal，stretto=True，episodes=True，pedal=True | 赋格：段落B；新增 声部1:+35，声部2:+30，声部3:+13 |
| 7 | set_harmony | section=A'，progression=["i", "iv", "V7", "i", "i", "VI", "V7", "i"] | 和声：段落A' 进行['i', 'iv', 'V7', 'i', 'i', 'VI', 'V7', 'i']；新增 — |
| 8 | accompaniment | section=A'，pattern=broken_arpeggio，register=low，density=1 | 伴奏：段落A' pattern=broken_arpeggio register=low density=1；新增 声部2:+8，声部3:+32 |
| 9 | melody | section=A'，register=mid，rhythm=sparse，contour=descending | 旋律：段落A' rhythm=sparse；新增 声部1:+19 |
| 10 | cadence | bars=2，section=A' | 终止：bars=2；新增 — |
| 11 | finalize |  | 收束装配：24小节 / 3声部；新增 声部1:+4 |

<details><summary>逐脚本新增音符样例（前 8 个 / 声部）</summary>

- **new_score**：声部1: —；声部2: —；声部3: —
- **set_sections**：声部1: —；声部2: —；声部3: —
- **set_harmony**：声部1: —；声部2: —；声部3: —
- **accompaniment**：声部1: —；声部2: C4:whole, F4:whole, G#4:whole, C4:whole, C4:whole, F4:whole, G#4:whole, C4:whole；声部3: A2:quarter, E2:quarter, A3:quarter, E3:quarter, D2:quarter, A2:quarter, D3:quarter, A3:quarter
- **melody**：声部1: E5:quarter, D5:eighth, C5:eighth, B5:half, F5:quarter, D5:quarter, A4:half, B4:quarter；声部2: —；声部3: —
- **fugue**：声部1: None:whole, B4:eighth, A4:eighth, G4:quarter, F4:quarter, E4:eighth, E4:eighth, E4:eighth；声部2: E4:eighth, D4:eighth, C4:quarter, B4:quarter, A4:eighth, A4:eighth, E4:eighth, D4:eighth；声部3: None:whole, None:whole, E3:eighth, D3:eighth, C3:quarter, B3:quarter, A3:eighth, A3:eighth
- **set_harmony**：声部1: —；声部2: —；声部3: —
- **accompaniment**：声部1: —；声部2: C4:whole, F4:whole, G#4:whole, C4:whole, C4:whole, A4:whole, G#4:whole, C4:whole；声部3: A2:quarter, E2:quarter, A3:quarter, E3:quarter, D2:quarter, A2:quarter, D3:quarter, A3:quarter
- **melody**：声部1: E5:quarter, C5:quarter, A4:half, D5:quarter, F5:half, A5:quarter, D5:whole, C5:quarter；声部2: —；声部3: —
- **cadence**：声部1: —；声部2: —；声部3: —
- **finalize**：声部1: F4:half, C5:half, A5+A6:whole, A5+A6:whole；声部2: —；声部3: —

</details>

### 4.4 本地强制约束（确定性兜底，优先级高于 LLM）

- 情绪/节奏约束命中：`宁静 / 忧伤 / 忧郁` → ♩=64 Adagio · minor · sparse · p
- 风格/速度强制纠正：基础速度 ♩=64 → ♩=96（genre 范围 72–126）
### 4.5 自动问题定位（可疑点 + 程度）

| 检查项 | 程度 | 说明 |
|---|---|---|
| 情绪↔最终调式 | 🟢 一致 | 情绪要求 minor，谱面 minor |
| 情绪↔最终速度 | 🔴 严重 | 情绪要求约 ♩=64，但谱面 ♩=96（偏差过大） |
| 意图拍号↔谱面 | 🟢 一致 | 最终拍号 4/4 |
| 计划小节↔实际小节 | 🟢 一致 | 24 小节 |
| 风格规范遵循 | 🟡 注意 | LLM 偏离 genre 规范，已强制纠正 1 处（说明模型未严格遵循风格包） |

> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / 🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。

## 5. 记谱与演奏法

**规则日志**（共 31 条，其中仲裁 0 次）

- [spelling/correctness] p1m5 E- — → Eb：调号 0 下变音记号更少
- [spelling/correctness] p1m5 D- — → Db：调号 0 下变音记号更少
- [spelling/correctness] p1m5 B- — → Bb：调号 0 下变音记号更少
- [spelling/correctness] p1m13 G- — → Gb：调号 0 下变音记号更少
- [spelling/correctness] p1m13 A- — → G#：导音：主音下方半音按功能拼升号
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- …另有 19 条

可演奏性：0 项 MAJOR / 17 项总计
- [MINOR] 声部1 大跳 16 半音（>八度）
- [MINOR] 声部1 大跳 18 半音（>八度）
- [MINOR] 声部2 大跳 16 半音（>八度）
- [MINOR] 声部2 大跳 14 半音（>八度）
- [MINOR] 声部3 大跳 17 半音（>八度）
- [MINOR] 声部3 大跳 17 半音（>八度）
- [MINOR] 声部3 大跳 14 半音（>八度）
- [MINOR] 声部3 大跳 17 半音（>八度）
- …其余 9 项从略

## 6. 生成质量评估（三指标）

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **最近邻**：heroic-fanfare（相似度 0.86）— ✅ 无抄袭嫌疑
