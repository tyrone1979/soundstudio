# 作曲生产报告：苍穹（程序员版）

> 面向产品/工程：记录本次生成的完整生产链路与系统状态，用于核对「说得 vs 写得」、排查缺陷与回归测试；可整体喂回大模型进一步定位具体小节/声部的问题。谱面检查与编辑者版一致，编辑者版见：`symphony_movement_test_report.md`。

## 1. 会话与生成环境

- 生成时间: 2026-09-28T06:04:47
- 生成路径: translator（LLM 翻译 + 确定性内核）
- 命中技能: extend-form
- 用户原始输入: 【作品】为完整交响乐团而作的单乐章交响曲《苍穹》，C 大调，4/4，共 32 小节，
奏鸣曲式：引子—主部—副部—展开部—再现部—尾声。

【编制】交响乐团（双管木管 + 四声部铜管 + 定音鼓 + 完整弦乐组）；旋律由木管（长笛）
与弦乐…
- 原始数据: `session.compose_trace`（本报告第 4 节即其展开，确定性可复现）

## 2. 谱面事实（与编辑者版「基本信息」一致）

- 调性: C 大调（0 个升号）
- 转调: 第 11 小节 → G 大调；第 16 小节 → A 小调
- 拍号: 4/4
- 速度: 四分音符 = 84 BPM
- 段落速度: 第 5 小节 Allegro con brio（♩=104）；第 11 小节 Allegro（♩=126）；第 16 小节 Agitato（♩=132）；第 24 小节 Tempo I（♩=118）；第 29 小节 Maestoso（♩=100）
- 调内音级: C, D, E, F, G, A, B
- 声部 1: 长笛 (Flute)，高音谱号，32 小节
- 声部 2: 双簧管 (Oboe)，高音谱号，32 小节
- 声部 3: Clarinet，高音谱号，32 小节
- 声部 4: Bassoon，低音谱号，32 小节
- 声部 5: 圆号 (Horn)，高音谱号，32 小节
- 声部 6: 小号 (Trumpet)，高音谱号，32 小节
- 声部 7: Trombone，低音谱号，32 小节
- 声部 8: Tuba，低音谱号，32 小节
- 声部 9: 定音鼓 (Timpani)，低音谱号，32 小节
- 声部 10: 小提琴 (Violin)，高音谱号，32 小节
- 声部 11: 小提琴 (Violin)，高音谱号，32 小节
- 声部 12: 中提琴 (Viola)，ALTO，32 小节
- 声部 13: 大提琴 (Cello)，低音谱号，32 小节
- 声部 14: Contrabass，低音谱号，32 小节

## 3. 自动检查结果（与编辑者版「第 3 节」一致）

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 调号一致性 | ⚠️ 注意 | A 小调 段出现变化音 Db, Eb, Gb, Bb；G 大调 段出现变化音 F（转调段调外音/经过音） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 3（Clarinet）与下方声部 1（长笛 (Flute)）在第 4.0 拍交叉：B4 低于 D5（同类交叉共 102 处，此处为第 1 例） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 12（中提琴 (Viola)）与下方声部 6（小号 (Trumpet)）在第 0.5 拍交叉：C4 低于 C5（同类交叉共 113 处，此处为第 1 例） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 6（小号 (Trumpet)）与下方声部 4（Bassoon）在第 20.5 拍交叉：A3 低于 C4（同类交叉共 8 处，此处为第 1 例） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 472 项（✅ 468 / ⚠️ 4 / ❌ 0）。

## 4. 作曲过程透明化（可追溯）

> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，便于核对「说得 vs 写得」是否一致。数据来自本次生成的 `session.compose_trace`，确定性可复现。把本报告整体喂回大模型，可进一步定位具体小节/声部的问题与修正建议。

### 4.1 流程图（本次实际链路）

```mermaid
flowchart TD
  U["① 用户输入<br/>【作品】为完整交响乐团而作的单乐章交响曲《苍穹》，C 大调，4/4，共 …"] --> P["② Planner LLM<br/>提取意图 plan"]
  P --> E{"③ 情绪/节奏约束<br/>情绪约束已触发"}
  E --> T["④ Translator LLM<br/>输出脚本 calls"]
  T --> G{"⑤ 风格硬约束<br/>无（LLM 已在规范内）"}
  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]
  K --> S["⑦ 渲染乐谱<br/>4/4 ♩=84 32小节"]
```

（文字版流程图，便于无 Mermaid 渲染环境阅读）

```
用户输入
  → [② Planner LLM] 提取意图 plan
  → [③ 情绪/节奏约束] 命中=是
  → [④ Translator LLM] 输出 calls + reasoning
  → [⑤ 风格硬约束] 触发=否
  → [⑥ 内核 run_program] 逐脚本执行
  → [⑦ 渲染谱面] 4/4 ♩=84 32小节
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
…（已截断，完整 2738 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
```

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "user_request": "【作品】为完整交响乐团而作的单乐章交响曲《苍穹》，C 大调，4/4，共 32 小节，\n奏鸣曲式：引子—主部—副部—展开部—再现部—尾声。\n\n【编制】交响乐团（双管木管 + 四声部铜管 + 定音鼓 + 完整弦乐组）；旋律由木管（长笛）\n与弦乐（第一小提琴）承担，低声部由大提琴与低音提琴承担。旋律音区最高到 high，\n不要用 very_high（会超出木管音域）；高潮靠加厚而非拔高音区。\n\n【速度】全曲必须按此速度阶梯：引子 ♩=84 → 主部 ♩=104 → 副部 ♩=126 →\n展开部 ♩=132 → 再现部 ♩=118 → 尾声 ♩=100（相邻段变化均 ≤±25%，平滑衔接）。\n六段小节数之和必须等于 32。\n\n【第 1 段 · 引子】4 小节 · Maestoso ♩=84 · ff · C 大调。庄严的号角式宣告，\n只用主、属功能（I–V），柱式和弦叠加长音低音，营造开阔的舞台感。\n\n【第 2 段 · 主部主题】6 小节 · Allegro con brio ♩=104 · f · C 大调。快板主部，\n旋律 driving、contour=arch，和声 I–vi–IV–V7。呈示全曲核心动机：属音起、级数\n5–5–1–3 的四音动机（四分–八分–四分–四分），每句首小节呈现并作二度模进；\n低声部用八度脉冲推进。\n\n【第 3 段 · 副部主题】5 小节 · Allegro ♩=126 · mf · 转 G 大调（+1 升号）。\n歌唱性副题，旋律 flowing、register=high、contour=arch，和声按新调 I–V7–I–IV，\n伴奏改为分解和弦，力度柔和下来，与刚健的主部形成抒情对照。\n\n【第 4 段 · 展开部】8 小节 · Agitato ♩=132 · ff · 转 a 小调（关系小调，不换调号，\nmode=minor）。冲突与不安：旋律 fragmented、register=high；和声 i–VI–iv–V7，\n并偶用 bII 那不勒斯和弦增添色彩；低声部改用半音下行（chromatic_descent）。\n本段为全曲戏剧高潮，用 thicken 对旋律八度加厚。\n\n【第 5 段 · 再现部】5 小节 · Tempo I ♩=118 · f · 回到 C 大调。主部动机以原调\n再现（可作节奏变化或倒影），和声 I–IV–V7–I，织体回到明确的调性肯定。\n\n【第 6 段 · 尾声】4 小节 · Maestoso ♩=100 · ff · C 大调。全奏收束，以 V7–I 的\n完满终止结束，末两小节为主和弦长音（显式终止式）。\n\n【不要】不要赋格，不要复调模仿，不要卡农。",
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
  "edit_intent": "extend",
  "skills": [
    "extend-form"
  ],
  "existing_plan": null,
  "revision_edits": null,
  "existing_score_summary": null
}
```

- **模型返回（原始）**：

```json
{
  "title": "苍穹",
  "genre": "交响曲（单乐章，奏鸣曲式）",
  "mood": [
    "庄严",
    "开阔",
    "戏剧性",
    "抒情",
    "不安",
    "辉煌"
  ],
  "tempo": 84,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major",
  "instrumentation": [
    "Flute",
    "Oboe",
    "Clarinet",
    "Bassoon",
    "Horn",
    "Trumpet",
    "Trombone",
    "Tuba",
    "Timpani",
    "Violin I",
    "Violin II",
    "Viola",
    "Cello",
    "Double Bass"
  ],
  "form": [
    "Introduction",
    "Exposition-Main",
    "Exposition-Sub",
    "Development",
    "Recapitulation",
    "Coda"
  ],
  "bars": 32,
  "motif": {
    "character": "号角式四音动机：属音起，级数 5–5–1–3，节奏为四分–八分–四分–四分，庄严而具推进力",
    "development": [
      "repetition",
      "sequence",
      "rhythmic change",
      "inversion",
      "fragmentation",
      "octave thickening"
    ]
  },
  "harmony": {
    "language": "C 大调功能性和声；引子仅 I–V，主部 I–vi–IV–V7，副部转 G 大调 I–V7–I–IV，展开部 a 小调 i–VI–iv–V7 并偶用 bII 那不勒斯和弦，再现部 I–IV–V7–I，尾声 V7–I 完满终止",
    "density": "moderate"
  },
  "rhythm": {
    "character": "引子柱式和弦配长音低音；主部 driving 八度脉冲；副部流动分解和弦；展开部 fragmented 与半音下行；再现部节奏变化或倒影；尾声全奏收束"
  },
  "texture": "orchestral",
  "climax": {
    "location": "section Development",
    "dynamic": "ff"
  },
  "difficulty": "medium",
  "summary": "为完整交响乐团而作的 32 小节单乐章交响曲《苍穹》，C 大调奏鸣曲式，按引子—主部—副部—展开部—再现部—尾声六段推进，速度阶梯 84→104→126→132→118→100，以展开部为戏剧高潮，尾声 V7–I 完满收束。",
  "edit_intent": "extend"
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
…（已截断，完整 15138 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
````

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "plan": {
    "title": "苍穹",
    "genre": "交响曲（单乐章，奏鸣曲式）",
    "mood": [
      "庄严",
      "开阔",
      "戏剧性",
      "抒情",
      "不安",
      "辉煌",
      "cheerful",
      "bright"
    ],
    "tempo": 132,
    "tempo_text": "Allegro",
    "meter": "4/4",
    "key_sharps": 0,
    "mode": "major",
    "_emotion_hint": {
      "group": "fast",
      "matched": [
        "不安",
        "快板"
      ],
      "bpm": 132,
      "tempo_text": "Allegro",
      "mode_bias": "major",
      "rhythm": "flowing",
      "dynamics": "f",
      "contour": "arch"
    },
    "instrumentation": [
      "Flute",
      "Oboe",
      "Clarinet",
      "Bassoon",
      "Horn",
      "Trumpet",
      "Trombone",
      "Tuba",
      "Timpani",
      "Violin I",
      "Violin II",
      "Viola",
      "Cello",
      "Double Bass"
    ],
    "form": [
      "Introduction",
      "Exposition-Main",
      "Exposition-Sub",
      "Development",
      "Recapitulation",
      "Coda"
    ],
    "bars": 32,
    "motif": {
      "character": "号角式四音动机：属音起，级数 5–5–1–3，节奏为四分–八分–四分–四分，庄严而具推进力",
      "development": [
        "repetition",
        "sequence",
        "rhythmic change",
        "inversion",
        "fragmentation",
        "octave thickening"
      ]
    },
    "harmony": {
      "language": "C 大调功能性和声；引子仅 I–V，主部 I–vi–IV–V7，副部转 G 大调 I–V7–I–IV，展开部 a 小调 i–VI–iv–V7 并偶用 bII 那不勒斯和弦，再现部 I–IV–V7–I，尾声 V7–I 完满终止",
      "density": "moderate"
    },
    "rhythm": {
      "character": "引子柱式和弦配长音低音；主部 driving 八度脉冲；副部流动分解和弦；展开部 fragmented 与半音下行；再现部节奏变化或倒影；尾声全奏收束"
    },
    "texture": "orchestral",
    "climax": {
      "location": "section Development",
      "dynamic": "ff"
    },
    "summary": "为完整交响乐团而作的 32 小节单乐章交响曲《苍穹》，C 大调奏鸣曲式，按引子—主部—副部—展开部—再现部—尾声六段推进，速度阶梯 84→104→126→132→118→100，以展开部为戏剧高潮，尾声 V7–I 完满收束。"
  },
  "user_brief": "【作品】为完整交响乐团而作的单乐章交响曲《苍穹》，C 大调，4/4，共 32 小节，\n奏鸣曲式：引子—主部—副部—展开部—再现部—尾声。\n\n【编制】交响乐团（双管木管 + 四声部铜管 + 定音鼓 + 完整弦乐组）；旋律由木管（长笛）\n与弦乐（第一小提琴）承担，低声部由大提琴与低音提琴承担。旋律音区最高到 high，\n不要用 very_high（会超出木管音域）；高潮靠加厚而非拔高音区。\n\n【速度】全曲必须按此速度阶梯：引子 ♩=84 → 主部 ♩=104 → 副部 ♩=126 →\n展开部 ♩=132 → 再现部 ♩=118 → 尾声 ♩=100（相邻段变化均 ≤±25%，平滑衔接）。\n六段小节数之和必须等于 32。\n\n【第 1 段 · 引子】4 小节 · Maestoso ♩=84 · ff · C 大调。庄严的号角式宣告，\n只用主、属功能（I–V），柱式和弦叠加长音低音，营造开阔的舞台感。\n\n【第 2 段 · 主部主题】6 小节 · Allegro con brio ♩=104 · f · C 大调。快板主部，\n旋律 driving、contour=arch，和声 I–vi–IV–V7。呈示全曲核心动机：属音起、级数\n5–5–1–3 的四音动机（四分–八分–四分–四分），每句首小节呈现并作二度模进；\n低声部用八度脉冲推进。\n\n【第 3 段 · 副部主题】5 小节 · Allegro ♩=126 · mf · 转 G 大调（+1 升号）。\n歌唱性副题，旋律 flowing、register=high、contour=arch，和声按新调 I–V7–I–IV，\n伴奏改为分解和弦，力度柔和下来，与刚健的主部形成抒情对照。\n\n【第 4 段 · 展开部】8 小节 · Agitato ♩=132 · ff · 转 a 小调（关系小调，不换调号，\nmode=minor）。冲突与不安：旋律 fragmented、register=high；和声 i–VI–iv–V7，\n并偶用 bII 那不勒斯和弦增添色彩；低声部改用半音下行（chromatic_descent）。\n本段为全曲戏剧高潮，用 thicken 对旋律八度加厚。\n\n【第 5 段 · 再现部】5 小节 · Tempo I ♩=118 · f · 回到 C 大调。主部动机以原调\n再现（可作节奏变化或倒影），和声 I–IV–V7–I，织体回到明确的调性肯定。\n\n【第 6 段 · 尾声】4 小节 · Maestoso ♩=100 · ff · C 大调。全奏收束，以 V7–I 的\n完满终止结束，末两小节为主和弦长音（显式终止式）。\n\n【不要】不要赋格，不要复调模仿，不要卡农。"
}
```

- **模型返回（原始）**：

```json
{
  "reasoning": "识别为交响曲（奏鸣曲式）单乐章，C 大调 4/4，采用 plan 的速度阶梯 84→104→126→132→118→100 与六段结构（4+6+5+8+5+4=32）。情绪组 fast（快板大调、f、arch），故主部 driving、副部 flowing 高音区，展开部按 brief 转 a 小调用 fragmented 与半音下行并 thicken，再现部回 C 大调，尾声 V7–I 完满终止。编制为双管交响乐团 14 声部，旋律路由长笛/第一小提琴，最高 high 不用 very_high，高潮靠加厚。",
  "calls": [
    {
      "script": "new_score",
      "args": {
        "title": "苍穹",
        "key_sharps": 0,
        "mode": "major",
        "meter": "4/4",
        "tempo": 84,
        "bars": 32,
        "voices": 14,
        "instruments": [
          "flute",
          "oboe",
          "clarinet",
          "bassoon",
          "horn",
          "trumpet",
          "trombone",
          "tuba",
          "timpani",
          "violin",
          "violin",
          "viola",
          "cello",
          "contrabass"
        ],
        "solo_instrument": "flute"
      }
    },
    {
      "script": "set_sections",
      "args": {
        "sections": [
          {
            "name": "引子",
            "bars": 4,
            "meter": "4/4",
            "dynamic": "ff",
            "tempo_text": "Maestoso",
            "bpm": 84
          },
          {
            "name": "主部主题",
            "bars": 6,
            "meter": "4/4",
            "dynamic": "f",
            "tempo_text": "Allegro con brio",
            "bpm": 104
          },
          {
            "name": "副部主题",
            "bars": 5,
            "meter": "4/4",
            "dynamic": "mf",
            "tempo_text": "Allegro",
            "bpm": 126
          },
          {
            "name": "展开部",
            "bars": 8,
            "meter": "4/4",
            "dynamic": "ff",
            "tempo_text": "Agitato",
            "bpm": 132
          },
          {
            "name": "再现部",
            "bars": 5,
            "meter": "4/4",
            "dynamic": "f",
            "tempo_text": "Tempo I",
            "bpm": 118
          },
          {
            "name": "尾声",
            "bars": 4,
            "meter": "4/4",
            "dynamic": "ff",
            "tempo_text": "Maestoso",
            "bpm": 100
          }
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "引子",
        "progression": [
          "I",
          "V"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "引子",
        "pattern": "block_chords",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "引子",
        "register": "mid",
        "rhythm": "sparse",
        "contour": "arch",
        "motif": [
          [
            5,
            "quarter"
          ],
          [
            5,
            "eighth"
          ],
          [
            1,
            "quarter"
          ],
          [
            3,
            "quarter"
          ]
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "主部主题",
        "progression": [
          "I",
          "vi",
          "IV",
          "V7"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "主部主题",
        "pattern": "octave_pulse",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "主部主题",
        "register": "high",
        "rhythm": "driving",
        "contour": "arch",
        "motif": [
          [
            5,
            "quarter"
          ],
          [
            5,
            "eighth"
          ],
          [
            1,
            "quarter"
          ],
          [
            3,
            "quarter"
          ]
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "副部主题",
        "key_sharps": 1,
        "mode": "major",
        "progression": [
          "I",
          "V7",
          "I",
          "IV"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "副部主题",
        "pattern": "broken_arpeggio",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "副部主题",
        "register": "high",
        "rhythm": "flowing",
        "contour": "arch"
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "展开部",
        "mode": "minor",
        "progression": [
          "i",
          "VI",
          "iv",
          "V7",
          "bII",
          "V7"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "展开部",
        "pattern": "chromatic_descent",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "展开部",
        "register": "high",
        "rhythm": "fragmented",
        "contour": "wave"
      }
    },
    {
      "script": "thicken",
      "args": {
        "section": "展开部"
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "再现部",
        "key_sharps": 0,
        "mode": "major",
        "progression": [
          "I",
          "IV",
          "V7",
          "I"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "再现部",
        "pattern": "octave_pulse",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "再现部",
        "register": "high",
        "rhythm": "driving",
        "contour": "arch",
        "motif": [
          [
            5,
            "quarter"
          ],
          [
            5,
            "eighth"
          ],
          [
            1,
            "quarter"
          ],
          [
            3,
            "quarter"
          ]
        ]
      }
    },
    {
      "script": "set_harmony",
      "args": {
        "section": "尾声",
        "progression": [
          "IV",
          "I",
          "V7",
          "I"
        ]
      }
    },
    {
      "script": "accompaniment",
      "args": {
        "section": "尾声",
        "pattern": "sustained",
        "register": "low",
        "density": 1
      }
    },
    {
      "script": "melody",
      "args": {
        "section": "尾声",
        "register": "high",
        "rhythm": "sparse",
        "contour": "descending"
      }
    },
    {
      "script": "cadence",
      "args": {
        "section": "尾声",
        "bars": 2
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
| 1 | new_score | title=苍穹，key_sharps=0，mode=major，meter=4/4，tempo=84，bars=32，voices=14，instruments=["flute", "oboe", "clarinet", "bassoon", "horn", "trumpet", "trombone", "tuba", "timpani", "violin", "violin", "viola", "cello", "contrabass"]，solo_instrument=flute | 建立乐曲：4/4 ♩=84 14声部 major；新增 — |
| 2 | set_sections | sections=[{"name": "引子", "bars": 4, "meter": "4/4", "dynamic": "ff", "tempo_text": "Maestoso", "bpm": 84}, {"name": "主部主题", "bars": 6, "meter": "4/4", "dynamic": "f", "tempo_text": "Allegro con brio", "bpm": 104}, {"name": "副部主题", "bars": 5, "meter": "4/4", "dynamic": "mf", "tempo_text": "Allegro", "bpm": 126}, {"name": "展开部", "bars": 8, "meter": "4/4", "dynamic": "ff", "tempo_text": "Agitato", "bpm": 132}, {"name": "再现部", "bars": 5, "meter": "4/4", "dynamic": "f", "tempo_text": "Tempo I", "bpm": 118}, {"name": "尾声", "bars": 4, "meter": "4/4", "dynamic": "ff", "tempo_text": "Maestoso", "bpm": 100}] | 分段：引子(4小节)，主部主题(6小节)，副部主题(5小节)，展开部(8小节)，再现部(5小节)，尾声(4小节)；新增 — |
| 3 | set_harmony | section=引子，progression=["I", "V"] | 和声：段落引子 进行['I', 'V']；新增 — |
| 4 | accompaniment | section=引子，pattern=block_chords，register=low，density=1 | 伴奏：段落引子 pattern=block_chords register=low density=1；新增 声部2:+32，声部3:+32，声部4:+32，声部5:+8，声部6:+8，声部7:+8，声部8:+8，声部9:+16，声部10:+32，声部11:+32，声部12:+32，声部13:+32，声部14:+8 |
| 5 | melody | section=引子，register=mid，rhythm=sparse，contour=arch，motif=[[5, "quarter"], [5, "eighth"], [1, "quarter"], [3, "quarter"]] | 旋律：段落引子 rhythm=sparse；新增 声部1:+13 |
| 6 | set_harmony | section=主部主题，progression=["I", "vi", "IV", "V7"] | 和声：段落主部主题 进行['I', 'vi', 'IV', 'V7']；新增 — |
| 7 | accompaniment | section=主部主题，pattern=octave_pulse，register=low，density=1 | 伴奏：段落主部主题 pattern=octave_pulse register=low density=1；新增 声部2:+48，声部3:+48，声部4:+48，声部5:+12，声部6:+12，声部7:+12，声部8:+12，声部9:+24，声部10:+48，声部11:+48，声部12:+48，声部13:+48，声部14:+24 |
| 8 | melody | section=主部主题，register=high，rhythm=driving，contour=arch，motif=[[5, "quarter"], [5, "eighth"], [1, "quarter"], [3, "quarter"]] | 旋律：段落主部主题 rhythm=driving；新增 声部1:+29 |
| 9 | set_harmony | section=副部主题，key_sharps=1，mode=major，progression=["I", "V7", "I", "IV"] | 和声：段落副部主题 进行['I', 'V7', 'I', 'IV']；新增 — |
| 10 | accompaniment | section=副部主题，pattern=broken_arpeggio，register=low，density=1 | 伴奏：段落副部主题 pattern=broken_arpeggio register=low density=1；新增 声部2:+40，声部3:+40，声部4:+40，声部5:+10，声部6:+10，声部7:+10，声部8:+10，声部9:+20，声部10:+40，声部11:+40，声部12:+40，声部13:+40，声部14:+20 |
| 11 | melody | section=副部主题，register=high，rhythm=flowing，contour=arch | 旋律：段落副部主题 rhythm=flowing；新增 声部1:+22 |
| 12 | set_harmony | section=展开部，mode=minor，progression=["i", "VI", "iv", "V7", "bII", "V7"] | 和声：段落展开部 进行['i', 'VI', 'iv', 'V7', 'bII', 'V7']；新增 — |
| 13 | accompaniment | section=展开部，pattern=chromatic_descent，register=low，density=1 | 伴奏：段落展开部 pattern=chromatic_descent register=low density=1；新增 声部2:+64，声部3:+64，声部4:+64，声部5:+16，声部6:+16，声部7:+16，声部8:+16，声部9:+32，声部10:+64，声部11:+64，声部12:+64，声部13:+64，声部14:+32 |
| 14 | melody | section=展开部，register=high，rhythm=fragmented，contour=wave | 旋律：段落展开部 rhythm=fragmented；新增 声部1:+44 |
| 15 | thicken | section=展开部 | 加厚：段落展开部；新增 — |
| 16 | set_harmony | section=再现部，key_sharps=0，mode=major，progression=["I", "IV", "V7", "I"] | 和声：段落再现部 进行['I', 'IV', 'V7', 'I']；新增 — |
| 17 | accompaniment | section=再现部，pattern=octave_pulse，register=low，density=1 | 伴奏：段落再现部 pattern=octave_pulse register=low density=1；新增 声部2:+40，声部3:+40，声部4:+40，声部5:+10，声部6:+10，声部7:+10，声部8:+10，声部9:+20，声部10:+40，声部11:+40，声部12:+40，声部13:+40，声部14:+20 |
| 18 | melody | section=再现部，register=high，rhythm=driving，contour=arch，motif=[[5, "quarter"], [5, "eighth"], [1, "quarter"], [3, "quarter"]] | 旋律：段落再现部 rhythm=driving；新增 声部1:+23 |
| 19 | set_harmony | section=尾声，progression=["IV", "I", "V7", "I"] | 和声：段落尾声 进行['IV', 'I', 'V7', 'I']；新增 — |
| 20 | accompaniment | section=尾声，pattern=sustained，register=low，density=1 | 伴奏：段落尾声 pattern=sustained register=low density=1；新增 声部2:+32，声部3:+32，声部4:+32，声部5:+8，声部6:+8，声部7:+8，声部8:+8，声部9:+16，声部10:+32，声部11:+32，声部12:+32，声部13:+32，声部14:+8 |
| 21 | melody | section=尾声，register=high，rhythm=sparse，contour=descending | 旋律：段落尾声 rhythm=sparse；新增 声部1:+6 |
| 22 | cadence | section=尾声，bars=2 | 终止：bars=2；新增 — |
| 23 | finalize |  | 收束装配：32小节 / 14声部；新增 声部1:+1 |

<details><summary>逐脚本新增音符样例（前 8 个 / 声部）</summary>

- **new_score**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **set_sections**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **accompaniment**：声部1: —；声部2: E5:eighth, G5:eighth, C6:eighth, F6:eighth, G6:eighth, D6:eighth, B5:eighth, G5:eighth；声部3: C5:eighth, E5:eighth, G5:eighth, C6:eighth, E6:eighth, G5:eighth, D5:eighth, B4:eighth；声部4: G3:eighth, C4:eighth, F3:eighth, G4:eighth, C5:eighth, G3:eighth, E4:eighth, C4:eighth；声部5: C3:half, C4:half, G3:half, G3:half, C3:half, C4:half, G3:half, G3:half；声部6: C5:half, C5:half, G4:half, G4:half, C5:half, C5:half, G4:half, G4:half；声部7: C3:half, C3:half, G3:half, G3:half, C3:half, C3:half, G3:half, G3:half；声部8: C3:half, C3:half, G2:half, G2:half, C3:half, C3:half, G2:half, G2:half；声部9: C3:quarter, None:quarter, C3:quarter, None:quarter, G2:quarter, None:quarter, G2:quarter, None:quarter；声部10: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E4:eighth, G4:eighth；声部11: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E4:eighth, G4:eighth；声部12: None:eighth, C4:eighth, E4:eighth, G4:eighth, C5:eighth, C4:eighth, E4:eighth, G4:eighth；声部13: None:eighth, C3:eighth, E3:eighth, G3:eighth, C4:eighth, C3:eighth, E3:eighth, G3:eighth；声部14: C2+E3+G3:half, C2+E3+G3:half, G2+B3+D3:half, G2+B3+D3:half, C2+E3+G3:half, C2+E3+G3:half, G2+B3+D3:half, G2+B3+D3:half
- **melody**：声部1: A4:quarter, A4:eighth, D4:quarter, F4:quarter, C5:eighth, D5:half, B4:half, C5:half；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **accompaniment**：声部1: —；声部2: E5:eighth, G5:eighth, C6:eighth, E6:eighth, G6:eighth, E6:eighth, B5:eighth, G5:eighth；声部3: C5:eighth, E5:eighth, G5:eighth, C6:eighth, E6:eighth, G5:eighth, D5:eighth, C5:eighth；声部4: G3:eighth, C4:eighth, E4:eighth, G4:eighth, C5:eighth, G3:eighth, D4:eighth, C4:eighth；声部5: C3:half, C4:half, A3:half, A3:half, F3:half, F3:half, G3:half, G3:half；声部6: C5:half, C5:half, A3:half, A4:half, F4:half, F5:half, G4:half, G4:half；声部7: C3:half, C3:half, A3:half, A3:half, F3:half, F3:half, G3:half, G3:half；声部8: C3:half, C3:half, A2:half, A2:half, F2:half, F2:half, G2:half, G2:half；声部9: C3:quarter, None:quarter, C3:quarter, None:quarter, A2:quarter, None:quarter, A2:quarter, None:quarter；声部10: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E4:eighth, G4:eighth；声部11: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E4:eighth, G4:eighth；声部12: None:eighth, C4:eighth, E4:eighth, G4:eighth, C5:eighth, C4:eighth, E4:eighth, G4:eighth；声部13: None:eighth, C3:eighth, E3:eighth, G3:eighth, C4:eighth, C3:eighth, E3:eighth, G3:eighth；声部14: C2+C3:quarter, C2+C3:quarter, C2+C3:quarter, C2+C3:quarter, A2+A3:quarter, A2+A3:quarter, A2+A3:quarter, A2+A3:quarter
- **melody**：声部1: G5:quarter, G5:eighth, C5:quarter, E5:quarter, G5:eighth, A5:quarter, C6:quarter, D6:eighth；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **accompaniment**：声部1: —；声部2: D5:eighth, G5:eighth, C6:eighth, D6:eighth, G6:eighth, D6:eighth, B5:eighth, G5:eighth；声部3: B4:eighth, D5:eighth, G5:eighth, B5:eighth, D6:eighth, G5:eighth, D5:eighth, B4:eighth；声部4: G3:eighth, B3:eighth, D4:eighth, G3:eighth, B3:eighth, G4:eighth, C4:eighth, A3:eighth；声部5: G3:half, G3:half, D3:half, D3:half, G3:half, G3:half, C3:half, C4:half；声部6: G4:half, G4:half, D4:half, D5:half, G4:half, G4:half, C5:half, C5:half；声部7: G3:half, G3:half, D3:half, D3:half, G3:half, G3:half, C3:half, C3:half；声部8: G2:half, G2:half, D2:half, D2:half, G2:half, G2:half, C3:half, C3:half；声部9: G2:quarter, None:quarter, G2:quarter, None:quarter, D3:quarter, None:quarter, D3:quarter, None:quarter；声部10: None:eighth, G4:eighth, B4:eighth, D5:eighth, G5:eighth, G5:eighth, B4:eighth, D4:eighth；声部11: None:eighth, G4:eighth, B4:eighth, D5:eighth, G5:eighth, G5:eighth, B4:eighth, D4:eighth；声部12: None:eighth, G4:eighth, B4:eighth, D4:eighth, G5:eighth, G4:eighth, B4:eighth, D4:eighth；声部13: None:eighth, G3:eighth, B3:eighth, D3:eighth, G3:eighth, G3:eighth, B3:eighth, D3:eighth；声部14: G2:quarter, D2:quarter, G3:quarter, D3:quarter, D2:quarter, A2:quarter, D3:quarter, A3:quarter
- **melody**：声部1: G5:quarter, B5:quarter, D6:quarter, G5:quarter, A5:eighth, A5:eighth, B5:quarter, C6:quarter；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **accompaniment**：声部1: —；声部2: E4:eighth, A4:eighth, C5:eighth, E5:eighth, B4:eighth, A5:eighth, D4:eighth, C5:eighth；声部3: C5:eighth, F5:eighth, A5:eighth, C6:eighth, E6:eighth, A6:eighth, E6:eighth, B5:eighth；声部4: A3:eighth, C4:eighth, E3:eighth, A3:eighth, C4:eighth, A4:eighth, E3:eighth, B3:eighth；声部5: A3:half, A3:half, F3:half, F3:half, D3:half, D3:half, E3:half, E3:half；声部6: A3:half, A4:half, F4:half, F5:half, D4:half, D5:half, E4:half, E5:half；声部7: A3:half, A3:half, F3:half, F3:half, D3:half, D3:half, E3:half, E3:half；声部8: A2:half, A2:half, F2:half, F2:half, D2:half, D2:half, E2:half, E2:half；声部9: A2:quarter, None:quarter, A2:quarter, None:quarter, F3:quarter, None:quarter, F3:quarter, None:quarter；声部10: None:eighth, A4:eighth, C5:eighth, E5:eighth, A5:eighth, A5:eighth, C5:eighth, E5:eighth；声部11: None:eighth, A4:eighth, C5:eighth, E5:eighth, A5:eighth, A5:eighth, C5:eighth, E5:eighth；声部12: None:eighth, A4:eighth, C4:eighth, E4:eighth, A4:eighth, A4:eighth, C4:eighth, E4:eighth；声部13: None:eighth, A3:eighth, C3:eighth, E3:eighth, A3:eighth, A3:eighth, C3:eighth, E3:eighth；声部14: A2:quarter, Ab2:quarter, Gb2:quarter, F2:quarter, F2:quarter, E2:quarter, Eb2:quarter, D2:quarter
- **melody**：声部1: A5:quarter, G5:eighth, F5:eighth, E5:quarter, C5:quarter, F5:quarter, A5:quarter, None:eighth；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **thicken**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **accompaniment**：声部1: —；声部2: E5:eighth, G5:eighth, C6:eighth, F6:eighth, G6:eighth, D6:eighth, C6:eighth, G5:eighth；声部3: C5:eighth, E5:eighth, G5:eighth, C6:eighth, F6:eighth, G5:eighth, D5:eighth, C5:eighth；声部4: G3:eighth, C4:eighth, F3:eighth, G4:eighth, C5:eighth, F3:eighth, E4:eighth, C4:eighth；声部5: C3:half, C4:half, F3:half, F3:half, G3:half, G3:half, C3:half, C4:half；声部6: C5:half, C5:half, F4:half, F5:half, G4:half, G4:half, C5:half, C5:half；声部7: C3:half, C3:half, F3:half, F3:half, G3:half, G3:half, C3:half, C3:half；声部8: C3:half, C3:half, F2:half, F2:half, G2:half, G2:half, C3:half, C3:half；声部9: C3:quarter, None:quarter, C3:quarter, None:quarter, F3:quarter, None:quarter, F3:quarter, None:quarter；声部10: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E4:eighth, G4:eighth；声部11: None:eighth, C5:eighth, E5:eighth, G5:eighth, C6:eighth, C5:eighth, E4:eighth, G4:eighth；声部12: None:eighth, C4:eighth, E4:eighth, G4:eighth, C5:eighth, C4:eighth, E4:eighth, G4:eighth；声部13: None:eighth, C3:eighth, E3:eighth, G3:eighth, C4:eighth, C3:eighth, E3:eighth, G3:eighth；声部14: C2+C3:quarter, C2+C3:quarter, C2+C3:quarter, C2+C3:quarter, F2+F3:quarter, F2+F3:quarter, F2+F3:quarter, F2+F3:quarter
- **melody**：声部1: G5:quarter, G5:eighth, C5:quarter, E5:quarter, G5:eighth, A5:quarter, C6:quarter, F6:quarter；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **set_harmony**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **accompaniment**：声部1: —；声部2: F4:eighth, A4:eighth, C5:eighth, F5:eighth, C6:eighth, A5:eighth, F5:eighth, C5:eighth；声部3: C5:eighth, F5:eighth, A5:eighth, C6:eighth, F6:eighth, A6:eighth, F6:eighth, C6:eighth；声部4: A3:eighth, C4:eighth, F4:eighth, A3:eighth, C5:eighth, A3:eighth, F4:eighth, B3:eighth；声部5: F3:half, F3:half, C3:half, C4:half, G3:half, G3:half, C3:half, C4:half；声部6: F4:half, F5:half, C5:half, C5:half, G4:half, G4:half, C5:half, C5:half；声部7: F3:half, F3:half, C3:half, C3:half, G3:half, G3:half, C3:half, C3:half；声部8: F2:half, F2:half, C3:half, C3:half, G2:half, G2:half, C3:half, C3:half；声部9: F3:quarter, None:quarter, F3:quarter, None:quarter, C3:quarter, None:quarter, C3:quarter, None:quarter；声部10: None:eighth, F4:eighth, A4:eighth, C5:eighth, F5:eighth, F5:eighth, A4:eighth, C5:eighth；声部11: None:eighth, F4:eighth, A4:eighth, C5:eighth, F5:eighth, F5:eighth, A4:eighth, C5:eighth；声部12: None:eighth, F4:eighth, A4:eighth, C4:eighth, F5:eighth, F4:eighth, A4:eighth, C4:eighth；声部13: None:eighth, F3:eighth, A3:eighth, C3:eighth, F4:eighth, F3:eighth, A3:eighth, C3:eighth；声部14: F2:half, C2:half, C2:half, G2:half, G2:half, D2:half, C2:half, G2:half
- **melody**：声部1: C6:half, A5:half, G5:whole, B5:whole, C6:half, C6:half；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **cadence**：声部1: —；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —
- **finalize**：声部1: C5+C6:whole；声部2: —；声部3: —；声部4: —；声部5: —；声部6: —；声部7: —；声部8: —；声部9: —；声部10: —；声部11: —；声部12: —；声部13: —；声部14: —

</details>

### 4.4 本地强制约束（确定性兜底，优先级高于 LLM）

- 情绪/节奏约束命中：`不安 / 快板` → ♩=132 Allegro · major · flowing · f
- 风格/速度强制纠正：未触发（LLM 输出已在 genre 规范内）
- **风格契约施加的约束**：无（该风格包没有 `style_contract.yaml`，或契约证据量不足、和声分区未生效）
### 4.5 自动问题定位（可疑点 + 程度）

| 检查项 | 程度 | 说明 |
|---|---|---|
| 情绪↔最终调式 | 🟢 一致 | 情绪要求 major，谱面 major |
| 情绪↔最终速度 | 🔴 严重 | 情绪要求约 ♩=132，但谱面 ♩=84（偏差过大） |
| 意图拍号↔谱面 | 🟢 一致 | 最终拍号 4/4 |
| 计划小节↔实际小节 | 🟢 一致 | 32 小节 |
| 风格规范遵循 | 🟢 良好 | LLM 输出均在 genre 规范内，无需强制 |
| 计划编制↔实际编制 | 🟡 注意 | 计划编制含 Double Bass，但谱面 14 个声部未落地（voices 数不足或 translator 取舍）——用户对编制的明确要求可能未满足 |

> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / 🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。

## 5. 记谱与演奏法

**规则日志**（共 130 条，其中仲裁 0 次）

- [spelling/correctness] p1m16 D- — → Db：调号 0 下变音记号更少
- [spelling/correctness] p1m16 G- — → Gb：调号 0 下变音记号更少
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [spelling/correctness] p1m20 B- — → Bb：调号 0 下变音记号更少
- [spelling/correctness] p1m20 B- — → Bb：调号 0 下变音记号更少
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [spelling/correctness] p1m21 D- — → Db：调号 0 下变音记号更少
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- …另有 118 条

可演奏性：0 项 MAJOR / 93 项总计
- [MINOR] 声部2 大跳 15 半音（>八度）
- [MINOR] 声部2 大跳 17 半音（>八度）
- [MINOR] 声部2 大跳 13 半音（>八度）
- [MINOR] 声部2 大跳 17 半音（>八度）
- [MINOR] 声部2 大跳 13 半音（>八度）
- [MINOR] 声部2 大跳 15 半音（>八度）
- [MINOR] 声部2 大跳 19 半音（>八度）
- [MINOR] 声部2 大跳 15 半音（>八度）
- …其余 85 项从略

## 6. 生成质量评估（三指标）

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **最近邻**：adagio-cantabile（相似度 0.44）— ✅ 无抄袭嫌疑
