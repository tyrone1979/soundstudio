# 作曲生产报告：C大调四部和声前奏（程序员版）

> 面向产品/工程：记录本次生成的完整生产链路与系统状态，用于核对「说得 vs 写得」、排查缺陷与回归测试；可整体喂回大模型进一步定位具体小节/声部的问题。谱面检查与编辑者版一致，编辑者版见：`2177b2418c_report.md`。

## 1. 会话与生成环境

- 生成时间: 2026-10-01T17:20:47
- 生成路径: composer（LLM 直出乐谱，无脚本序列）
- 命中技能: harmonize, genre:sacred/chorale
- 用户原始输入: 做一首钢琴四部和声，不要有任何变化音
- 原始数据: `session.compose_trace`（本报告第 4 节即其展开，确定性可复现）

## 2. 谱面事实（与编辑者版「基本信息」一致）

- 调性: C 大调（0 个升号）
- 拍号: 4/4
- 速度: 四分音符 = 76 BPM
- 调内音级: C, D, E, F, G, A, B
- 声部 1: Soprano，高音谱号，16 小节
- 声部 2: Alto，高音谱号，16 小节
- 声部 3: Tenor，低音谱号，16 小节
- 声部 4: Bass，低音谱号，16 小节

## 3. 自动检查结果（与编辑者版「第 3 节」一致）

（无）当前自动检查未发现需要关注的问题。

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 72 项（✅ 72 / ⚠️ 0 / ❌ 0）。

## 4. 作曲过程透明化（可追溯）

> 本节约等于把「需求 → 大模型 → 脚本 → 谱面」的完整链路展开，便于核对「说得 vs 写得」是否一致。数据来自本次生成的 `session.compose_trace`，确定性可复现。把本报告整体喂回大模型，可进一步定位具体小节/声部的问题与修正建议。

### 4.1 流程图（本次实际链路）

```mermaid
flowchart TD
  U["① 用户输入<br/>做一首钢琴四部和声，不要有任何变化音"] --> P["② Planner LLM<br/>提取意图 plan"]
  P --> E{"③ 情绪/节奏约束<br/>无情绪词"}
  E --> T["④ Translator LLM<br/>输出脚本 calls"]
  T --> G{"⑤ 风格硬约束<br/>无（LLM 已在规范内）"}
  G --> K["⑥ 内核 run_program<br/>逐脚本生成音符"]
  K --> S["⑦ 渲染乐谱<br/>4/4 ♩=76 16小节"]
```

（文字版流程图，便于无 Mermaid 渲染环境阅读）

```
用户输入
  → [② Planner LLM] 提取意图 plan
  → [③ 情绪/节奏约束] 命中=否
  → [④ Translator LLM] 输出 calls + reasoning
  → [⑤ 风格硬约束] 触发=否
  → [⑥ 内核 run_program] 逐脚本执行
  → [⑦ 渲染谱面] 4/4 ♩=76 16小节
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
  "user_request": "做一首钢琴四部和声，不要有任何变化音",
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
  "edit_intent": "harmonize",
  "skills": [
    "harmonize"
  ],
  "existing_plan": null,
  "revision_edits": null,
  "existing_score_summary": null
}
```

- **模型返回（原始）**：

```json
{
  "title": "C大调四部和声前奏",
  "genre": "众赞歌风格四部和声",
  "genre_id": "sacred/chorale",
  "mood": [
    "庄重",
    "平静",
    "明亮"
  ],
  "tempo": 120,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major",
  "instrumentation": [
    "piano"
  ],
  "form": [
    "A",
    "A'"
  ],
  "bars": 8,
  "motif": {
    "character": "级进为主的四声部合唱式短句，起音在主和弦",
    "development": [
      "repetition",
      "sequence"
    ]
  },
  "harmony": {
    "language": "严格自然音功能和声（仅C大调音阶内音，无任何变化音/半音），I–IV–V–I 与 vi–ii–V–I 终止式",
    "density": "moderate"
  },
  "rhythm": {
    "character": "以四分音符为主、句尾二分音符收束的平稳众赞歌节奏，四声部同起同落"
  },
  "texture": "homophonic",
  "climax": {
    "location": "第6–7小节",
    "dynamic": "mf"
  },
  "difficulty": "easy",
  "summary": "8小节C大调众赞歌式钢琴四部和声，只用自然音、无任何变化音，和声进行清晰规整。",
  "edit_intent": "harmonize"
}
```

### 4.3 脚本调用与输入输出

| # | 脚本 | 输入(args) | 输出（新增音符 / 状态变更） |
|---|---|---|---|
| 1 | new_score | title=C大调四部和声前奏，key_sharps=0，mode=major，meter=4/4，tempo=120，bars=16，voices=1，instruments=["piano"]，style=sacred/chorale | 建立乐曲：4/4 ♩=120 1声部 major；新增 — |
| 2 | set_sections | sections=[{"name": "A", "bars": 8, "meter": "4/4", "dynamic": "mp", "tempo_text": "Andante", "bpm": 120}, {"name": "A'", "bars": 8, "meter": "4/4", "dynamic": "mf", "tempo_text": "Andante", "bpm": 120}] | 分段：A(8小节)，A'(8小节)；新增 — |
| 3 | set_harmony | section=A，progression=["I", "IV", "V", "I", "vi", "ii", "V7", "I"] | 和声：段落A 进行['I', 'IV', 'V', 'I', 'vi', 'ii', 'V7', 'I']；新增 — |
| 4 | accompaniment | section=A，pattern=block_chords，register=low，density=1 | 伴奏：段落A pattern=block_chords register=low density=1；新增 声部1:+16 |
| 5 | new_score | title=C大调四部和声前奏，key_sharps=0，mode=major，meter=4/4，tempo=120，bars=16，voices=1，instruments=["piano"]，style=sacred/chorale　⟲ **LLM 重试**：此前调用序列出错回滚，从 new_score 重新执行 | 建立乐曲：4/4 ♩=120 1声部 major；新增 — |
| 6 | set_sections | sections=[{"name": "A", "bars": 8, "meter": "4/4", "dynamic": "mp", "tempo_text": "Andante", "bpm": 120}, {"name": "A'", "bars": 8, "meter": "4/4", "dynamic": "mf", "tempo_text": "Andante", "bpm": 120}] | 分段：A(8小节)，A'(8小节)；新增 — |
| 7 | set_harmony | section=A，progression=["I", "IV", "V", "I", "vi", "ii", "V7", "I"] | 和声：段落A 进行['I', 'IV', 'V', 'I', 'vi', 'ii', 'V7', 'I']；新增 — |
| 8 | melody | section=A，register=mid，rhythm=flowing，contour=arch | 旋律：段落A rhythm=flowing；新增 声部1:+38 |

<details><summary>逐脚本新增音符样例（前 8 个 / 声部）</summary>

- **new_score**：声部1: —
- **set_sections**：声部1: —
- **set_harmony**：声部1: —
- **accompaniment**：声部1: C2+E3+G3:half, C2+E3+G3:half, F2+A3+C3:half, F2+A3+C3:half, G2+B3+D3:half, G2+B3+D3:half, C2+E3+G3+Bb3:half, C2+E3+G3+Bb3:half
- **new_score**：声部1: —
- **set_sections**：声部1: —
- **set_harmony**：声部1: —
- **melody**：声部1: C5:quarter, G4:quarter, A4:eighth, B4:eighth, B4:quarter, C5:eighth, C5:eighth, D5:quarter

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
| 计划编制↔实际编制 | 🟡 注意 | 计划编制含 piano，但谱面 4 个声部未落地（voices 数不足或 translator 取舍）——用户对编制的明确要求可能未满足 |

> 说明：上表为程序自动比对「意图 ↔ 谱面」的初判（🔴 严重 / 🟡 注意 / 🟢 一致）。将本报告整体喂给大模型，可进一步定位具体小节/声部的问题与修正建议。

## 5. 记谱与演奏法

**规则日志**（共 3 条，其中仲裁 0 次）

- [stem/layout] 176 处 — 已应用：符干方向
- [layout/layout] 4 处 — 已应用：系统换行
- [rehearsal/layout] 1 处 — 已应用：排练记号

可演奏性：✅ 无音域/大跳/跨度/密度问题

## 生成质量评估

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **包级最近邻**：british-folk-tune（相似度 0.56）— ✅ 无抄袭嫌疑
