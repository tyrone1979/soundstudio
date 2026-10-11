# 作曲生产报告：C大调小赋格（程序员版）

> 面向产品/工程：记录本次生成的完整生产链路与系统状态，用于核对「说得 vs 写得」、排查缺陷与回归测试；可整体喂回大模型进一步定位具体小节/声部的问题。谱面检查与编辑者版一致，编辑者版见：`b0f18e2291_report.md`。

## 1. 会话与生成环境

- 生成时间: 2026-10-01T17:29:52
- 生成路径: composer（LLM 直出乐谱，无脚本序列）
- 命中技能: compose-full, genre:academic/fugue
- 用户原始输入: 来一首赋格
- 原始数据: `session.compose_trace`（本报告第 4 节即其展开，确定性可复现）

## 2. 谱面事实（与编辑者版「基本信息」一致）

- 调性: C 大调（0 个升号）
- 拍号: 4/4
- 速度: 四分音符 = 120 BPM
- 段落速度: 第 9 小节 Poco animato（♩=132）；第 13 小节 Stretto（♩=144）；第 16 小节 Rit.（♩=112）
- 调内音级: C, D, E, F, G, A, B
- 声部 1: Piano，高音谱号，16 小节
- 声部 2: Piano，低音谱号，16 小节

## 3. 自动检查结果（与编辑者版「第 3 节」一致）

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 声部进行 | ❌ 问题 | 声部1/2 在第 7.0→8.0 拍 平行五度（D5->E5 对 G3->A3） |
| 声部进行 | ❌ 问题 | 声部1/2 在第 31.0→32.0 拍 平行五度（D5->E5 对 G3->A3） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 39 项（✅ 37 / ⚠️ 0 / ❌ 2）。

## 4. 作曲过程透明化（可追溯）

> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，便于核对「说得 vs 写得」是否一致。数据来自本次生成的 `session.compose_trace`，确定性可复现。把本报告整体喂回大模型，可进一步定位具体小节/声部的问题与修正建议。

### 4.1 流程图（本次实际链路）

```mermaid
flowchart TD
  U["① 用户输入<br/>来一首赋格"] --> P["② Planner LLM<br/>提取意图 plan"]
  P --> E{"③ 情绪/节奏约束<br/>无情绪词"}
  E --> T["④ Translator LLM<br/>输出脚本 calls"]
  T --> G{"⑤ 风格硬约束<br/>无（LLM 已在规范内）"}
  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]
  K --> S["⑦ 渲染乐谱<br/>4/4 ♩=120 16小节"]
```

（文字版流程图，便于无 Mermaid 渲染环境阅读）

```
用户输入
  → [② Planner LLM] 提取意图 plan
  → [③ 情绪/节奏约束] 命中=否
  → [④ Translator LLM] 输出 calls + reasoning
  → [⑤ 风格硬约束] 触发=否
  → [⑥ 内核 run_program] 逐脚本执行
  → [⑦ 渲染谱面] 4/4 ♩=120 16小节
```

### 4.2 大模型输入输出（LLM I/O）

#### Planner（意图解析）

- **系统提示**（固定指令模板，已截断）：

```
You are a professional composer and music theorist acting as an Intent Parser.

Transform the user's natural-language request (and optional slash-commands already applied as preferences) into a Composition Plan JSON only.

Think like a composer first: infer genre, mood, form, motif character, harmony language, climax — do NOT invent note lists yet.

A **风格目录（仅已学包）** is injected at the end of this system prompt. Use it to set `genre_id`.

Output ONE JSON object, no Markdown:
{
  "title": "string",
  "genre": "string (human label, e.g. 摇摆标准曲（爵士）)",
  "genre_id": "family/pack_id from 风格目录, e.g. jazz/swing-standard; empty if none",
  "mood": ["..."],
  "tempo": 72,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major|minor",
  "instrumentation": ["trumpet","piano","contrabass"] (kernel English names; map fiddle→violin, double bass→contrabass, tin whistle→flute; avoid unknown names),
  "form": ["A","B","A"],
  "bars": 16,
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
- If user is vague, choose
…（已截断，完整 4384 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
```

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "user_request": "来一首赋格",
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
  "title": "C大调小赋格",
  "genre": "赋格（学院派）",
  "genre_id": "academic/fugue",
  "mood": [
    "严谨",
    "明快",
    "逻辑清晰"
  ],
  "tempo": 120,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major",
  "instrumentation": [
    "piano"
  ],
  "form": [
    "主题呈示",
    "答题与对题",
    "密接和应"
  ],
  "bars": 16,
  "motif": {
    "character": "级进为主、带附点节奏的短小主题，首音突出、尾音收束明确",
    "development": [
      "repetition",
      "sequence",
      "inversion",
      "stretto"
    ]
  },
  "harmony": {
    "language": "功能调性，主题在属调答题，中间以近关系调离调，终止式清晰",
    "density": "moderate"
  },
  "rhythm": {
    "character": "稳定四拍脉动，主题附点与八分音符交替，对题以更流动的十六分音符形成对比"
  },
  "texture": "polyphonic",
  "climax": {
    "location": "密接和应段落",
    "dynamic": "f"
  },
  "difficulty": "medium",
  "summary": "一首C大调四声部小赋格：主题呈示后在属调答题，经对题与间插展开，最终以密接和应推向高潮。",
  "edit_intent": null
}
```

### 4.3 脚本调用与输入输出

| # | 脚本 | 输入(args) | 输出（新增音符 / 状态变更） |
|---|---|---|---|
| 1 | new_score | title=C大调小赋格，key_sharps=0，mode=major，meter=4/4，tempo=120，bars=16，voices=1，instruments=["piano"]，style=academic/fugue | 建立乐曲：4/4 ♩=120 1声部 major；新增 — |
| 2 | set_sections | sections=[{"name": "主题呈示", "bars": 6, "meter": "4/4", "dynamic": "mp", "tempo_text": "Allegro", "bpm": 120}, {"name": "答题与对题", "bars": 6, "meter": "4/4", "dynamic": "mf", "tempo_text": "Allegro", "bpm": 120}, {"name": "密接和应", "bars": 4, "meter": "4/4", "dynamic": "f", "tempo_text": "Allegro", "bpm": 124}] | 分段：主题呈示(6小节)，答题与对题(6小节)，密接和应(4小节)；新增 — |
| 3 | set_harmony | section=主题呈示，progression=["I", "V7", "I", "IV", "V7", "I"] | 和声：段落主题呈示 进行['I', 'V7', 'I', 'IV', 'V7', 'I']；新增 — |
| 4 | accompaniment | section=主题呈示，pattern=sustained，register=low，density=1 | 伴奏：段落主题呈示 pattern=sustained register=low density=1；新增 声部1:+12 |
| 5 | new_score | title=C大调小赋格，key_sharps=0，mode=major，meter=4/4，tempo=120，bars=16，voices=1，instruments=["piano"]，style=academic/fugue　⟲ **LLM 重试**：此前调用序列出错回滚，从 new_score 重新执行 | 建立乐曲：4/4 ♩=120 1声部 major；新增 — |
| 6 | set_sections | sections=[{"name": "主题呈示", "bars": 6, "meter": "4/4", "dynamic": "mp", "tempo_text": "Allegro", "bpm": 120}, {"name": "答题与对题", "bars": 6, "meter": "4/4", "dynamic": "mf", "tempo_text": "Allegro", "bpm": 120}, {"name": "密接和应", "bars": 4, "meter": "4/4", "dynamic": "f", "tempo_text": "Allegro", "bpm": 124}] | 分段：主题呈示(6小节)，答题与对题(6小节)，密接和应(4小节)；新增 — |
| 7 | set_harmony | section=主题呈示，progression=["I", "V7", "I", "IV", "V7", "I"] | 和声：段落主题呈示 进行['I', 'V7', 'I', 'IV', 'V7', 'I']；新增 — |
| 8 | accompaniment | section=主题呈示，pattern=sustained，register=low，density=1 | 伴奏：段落主题呈示 pattern=sustained register=low density=1；新增 声部1:+12 |

<details><summary>逐脚本新增音符样例（前 8 个 / 声部）</summary>

- **new_score**：声部1: —
- **set_sections**：声部1: —
- **set_harmony**：声部1: —
- **accompaniment**：声部1: C2:half, G2:half, G2:half, D2:half, C2:half, G2:half, F2:half, C2:half
- **new_score**：声部1: —
- **set_sections**：声部1: —
- **set_harmony**：声部1: —
- **accompaniment**：声部1: C2:half, G2:half, G2:half, D2:half, C2:half, G2:half, F2:half, C2:half

</details>

### 4.4 本地强制约束（确定性兜底，优先级高于 LLM）

- 情绪/节奏约束：未触发（用户未给情绪/速度词，或已被风格基线覆盖）
- 风格/速度强制纠正：未触发（LLM 输出已在 genre 规范内）
- **风格契约施加的约束**：无（该风格包没有 `style_contract.yaml`，或契约证据量不足、和声分区未生效）
### 4.5 自动问题定位（可疑点 + 程度）

| 检查项 | 程度 | 说明 |
|---|---|---|
| 情绪约束 | 🟢 未触发 | 用户未给情绪/速度词 |
| 意图拍号↔谱面 | 🟢 一致 | 最终拍号 4/4 |
| 计划小节↔实际小节 | 🟢 一致 | 16 小节 |
| 风格规范遵循 | 🟢 良好 | LLM 输出均在 genre 规范内，无需强制 |
| 生成路径 | 🟡 提示 | 旧 composer 路径：LLM 直出乐谱 JSON，无脚本调用序列（透明度为脚本级缺失，建议改用翻译路径） |
| 本地修复 | 🟡 注意 | 校验后触发了本地修复（时值/声部进行），说明初版谱存在 CRITICAL/MAJOR 问题 |
| 计划编制↔实际编制 | 🟢 一致 | 计划 1 件乐器与谱面 2 声部对应 |

> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / 🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。

## 5. 记谱与演奏法

**规则日志**（共 4 条，其中仲裁 0 次）

- [stem/layout] 116 处 — 已应用：符干方向
- [beams/layout] 18 处 — 已应用：连符尾分组
- [layout/layout] 4 处 — 已应用：系统换行
- [rehearsal/layout] 1 处 — 已应用：排练记号

可演奏性：✅ 无音域/大跳/跨度/密度问题

## 生成质量评估

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **包级最近邻**：british-folk-tune（相似度 0.64）— ✅ 无抄袭嫌疑
