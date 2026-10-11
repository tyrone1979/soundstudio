# 作曲生产报告：二声部一比一对位练习（据当前乐谱）（程序员版）

> 面向产品/工程：记录本次生成的完整生产链路与系统状态，用于核对「说得 vs 写得」、排查缺陷与回归测试；可整体喂回大模型进一步定位具体小节/声部的问题。谱面检查与编辑者版一致，编辑者版见：`f7537f2a7a_report.md`。

## 1. 会话与生成环境

- 生成时间: 2026-10-01T19:17:00
- 生成路径: two_part_species_from_current（抽定旋律 + 确定性对位，未写音符 LLM）
- 命中技能: compose-full
- 用户原始输入: 根据当前打开的乐谱写一个1比1对位
- 原始数据: `session.compose_trace`（本报告第 4 节即其展开，确定性可复现）

## 2. 谱面事实（与编辑者版「基本信息」一致）

- 调性: C 大调（0 个升号）
- 拍号: 4/4
- 速度: 四分音符 = 120 BPM
- 调内音级: C, D, E, F, G, A, B
- 声部 1: Piano，高音谱号，12 小节
- 声部 2: Piano，低音谱号，12 小节

## 3. 自动检查结果（与编辑者版「第 3 节」一致）

（无）当前自动检查未发现需要关注的问题。

> 说明：众赞歌/钢琴四部叠写检查全部逻辑声部对的平行五八度；管弦织体仅查外声部平行五度（八度加强不报）。相邻声部交叉为提示项。 共检查 31 项（✅ 31 / ⚠️ 0 / ❌ 0）。

## 4. 作曲过程透明化（可追溯）

> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，便于核对「说得 vs 写得」是否一致。数据来自本次生成的 `session.compose_trace`，确定性可复现。把本报告整体喂回大模型，可进一步定位具体小节/声部的问题与修正建议。

### 4.1 流程图（本次实际链路）

```mermaid
flowchart TD
  U["① 用户输入<br/>根据当前打开的乐谱写一个1比1对位"] --> P["② Planner LLM<br/>提取意图 plan"]
  P --> E{"③ 情绪/节奏约束<br/>无情绪词"}
  E --> T["④ Translator LLM<br/>输出脚本 calls"]
  T --> G{"⑤ 风格硬约束<br/>风格硬约束已纠正"}
  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]
  K --> S["⑦ 渲染乐谱<br/>4/4 ♩=120 12小节"]
```

（文字版流程图，便于无 Mermaid 渲染环境阅读）

```
用户输入
  → [② Planner LLM] 提取意图 plan
  → [③ 情绪/节奏约束] 命中=否
  → [④ Translator LLM] 输出 calls + reasoning
  → [⑤ 风格硬约束] 触发=是
  → [⑥ 内核 run_program] 逐脚本执行
  → [⑦ 渲染谱面] 4/4 ♩=120 12小节
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
  "texture": "homophonic|polyphonic|sparse|orchestral|first-species",
  "species": false,
  "species_from_current": false,
  "want_fugue": false,
  "climax": {"location": "section B", "dynamic": "f"},
  "difficulty": "easy|medium",
  "summary": "one sentence for the user",
  "edit_intent"
…（已截断，完整 6645 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）
```

- **用户输入**（喂给模型的结构化上下文）：

```json
{
  "user_request": "根据当前打开的乐谱写一个1比1对位",
  "preferences": {
    "key_sharps": 0,
    "mode": "major",
    "tempo": 120,
    "bars": 12,
    "meter": "4/4",
    "style": "",
    "genre_id": "",
    "instrumentation": "",
    "fugue": "off",
    "voices": 2,
    "_explicit": {}
  },
  "edit_intent": null,
  "skills": [
    "compose-full"
  ],
  "existing_plan": {
    "title": "二声部一比一对位练习（据当前乐谱）",
    "genre": "第一类二声部对位（基于当前乐谱）",
    "genre_id": "",
    "mood": [
      "平稳",
      "清澈",
      "古典"
    ],
    "tempo": 120,
    "meter": "4/4",
    "key_sharps": 0,
    "mode": "major",
    "instrumentation": [
      "piano",
      "piano"
    ],
    "form": "species",
    "bars": 12,
    "texture": "first-species",
    "species": true,
    "species_from_current": true,
    "want_fugue": false,
    "summary": "根据当前打开的 12 小节乐谱，抽取每小节一音作定旋律，按第一类（一比一）对位规则生成钢琴大谱表二声部对位，保持原调性、拍号与速度。",
    "user_brief": "根据当前打开的乐谱写一个1比1对位",
    "_species_hint": {
      "kind": "two_part_species",
      "from_current": true,
      "fixed_cf": "upper"
    }
  },
  "revision_edits": null,
  "existing_score_summary": {
    "title": "二声部一比一对位练习（据当前乐谱）",
    "bars": 12,
    "tempo": 120,
    "parts": [
      "Piano RH",
      "Piano LH"
    ]
  }
}
```

- **模型返回（原始）**：

```json
{
  "title": "二声部一比一对位练习（据当前乐谱）",
  "genre": "第一类二声部对位（基于当前乐谱）",
  "genre_id": "",
  "mood": [
    "平稳",
    "清澈",
    "古典"
  ],
  "tempo": 120,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major",
  "instrumentation": [
    "piano",
    "piano"
  ],
  "form": [
    "species"
  ],
  "bars": 12,
  "motif": {
    "character": "定旋律取自当前乐谱每小节一音，级进为主、轮廓平稳",
    "development": [
      "repetition"
    ]
  },
  "harmony": {
    "language": "调性功能和声，I–IV–V–I 骨架，三六度为主",
    "density": "sparse"
  },
  "rhythm": {
    "character": "全音符一比一，每小节一声部一音"
  },
  "texture": "first-species",
  "species": true,
  "species_from_current": true,
  "want_fugue": false,
  "climax": {
    "location": "第9–10小节",
    "dynamic": "mf"
  },
  "difficulty": "easy",
  "summary": "根据当前打开的 12 小节乐谱，抽取每小节一音作定旋律，按第一类（一比一）对位规则生成钢琴大谱表二声部对位，保持原调性、拍号与速度。",
  "edit_intent": null
}
```

### 4.3 脚本调用与输入输出

（本次路径未经过确定性脚本内核：`two_part_species_from_current（抽定旋律 + 确定性对位，未写音符 LLM）`—— 该路径由 LLM 直接产出乐谱或纯确定性生成，无逐脚本记录）

### 4.4 本地强制约束（确定性兜底，优先级高于 LLM）

- 情绪/节奏约束：未触发（用户未给情绪/速度词，或已被风格基线覆盖）
- 风格/速度强制纠正：定旋律来自当前乐谱（role=upper）
- 风格/速度强制纠正：另一声部按对位法硬约束生成；对位≠赋格
- **风格契约施加的约束**：无（该风格包没有 `style_contract.yaml`，或契约证据量不足、和声分区未生效）
### 4.5 自动问题定位（可疑点 + 程度）

| 检查项 | 程度 | 说明 |
|---|---|---|
| 情绪约束 | 🟢 未触发 | 用户未给情绪/速度词 |
| 意图拍号↔谱面 | 🟢 一致 | 最终拍号 4/4 |
| 计划小节↔实际小节 | 🟢 一致 | 12 小节 |
| 风格规范遵循 | 🟡 注意 | LLM 偏离 genre 规范，已强制纠正 2 处（说明模型未严格遵循风格包） |
| 计划编制↔实际编制 | 🟢 一致 | 计划 2 件乐器与谱面 2 声部对应 |

> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / 🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。

## 5. 记谱与演奏法

**规则日志**（共 3 条，其中仲裁 0 次）

- [stem/layout] 24 处 — 已应用：符干方向
- [layout/layout] 3 处 — 已应用：系统换行
- [rehearsal/layout] 1 处 — 已应用：排练记号

可演奏性：✅ 无音域/大跳/跨度/密度问题

## 生成质量评估

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **包级最近邻**：waltz（相似度 0.70）— ✅ 无抄袭嫌疑
