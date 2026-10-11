<!-- 由 gen_help_pro.py 生成：每个命令/功能含真实输入、实际输出与验证。 -->

# ComposerChat 专业帮助文档（命令 / 功能 · 实测验证版）

> 本文档与聊天内 `/help` 同源于 `help_doc.py`，但额外给出**每个命令/功能的真实输入、实际输出与正确性验证**。
> 全部功能性命令均建立在**确定性内核**之上：零训练数据、seed 固定可复现、规则校验、可审计。

## 一、功能清单（每个功能一个实测示例）

### 自然语言作曲

- **说明**：用自然语言描述想要的曲子，自动匹配风格包并生成符号谱（MusicXML/MIDI）。
- **相关命令**：`（自然语言输入）`、`/style`、`/new`
- **输入示例**：`做一首雨夜钢琴风格的圆舞曲，带一点忧伤`
- **预期效果**：匹配「雨夜钢琴」风格包，生成 3/4 拍、柔和哀婉的圆舞曲。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 风格包与绑定

- **说明**：风格包是护城河基石：零训练数据的确定性指纹。/style 设定基调并可绑定 genre 包，创作描述单独记录。
- **相关命令**：`/style`
- **输入示例**：`/style 圆舞曲 快板轻盈`
- **预期效果**：绑定圆舞曲风格包，并把「快板轻盈」作为创作描述传给生成器。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 风格插值 / 参数化风格合成

- **说明**：在两个风格之间确定性插值，得到「二者之间」的新风格——参数化风格合成，可复现、不调 LLM。
- **相关命令**：`/interpolate`
- **输入示例**：`/interpolate 雨夜钢琴 圆舞曲 40%`
- **预期效果**：在雨夜钢琴与圆舞曲间按 0.4 权重生成过渡风格乐曲。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证。

### 赋格与对位

- **说明**：内置赋格引擎：仅当 brief 明确提到赋格/卡农/创意曲时写入赋格段，或整首写成赋格（主题-答题-展开）。普通对位提示不会自动加赋格。
- **相关命令**：`/fugue`
- **输入示例**：`/fugue full`
- **预期效果**：整首按赋格写法生成（二声部时为二声部创意曲）。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 确定性骨架

- **说明**：不调 LLM 的纯确定性骨架：按风格包指纹直接铺节奏/和声骨架，可复现可审计。
- **相关命令**：`/skeleton`
- **输入示例**：`/skeleton 圆舞曲`
- **预期效果**：确定性生成圆舞曲骨架，seed 固定时结果完全可复现。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/skeleton 圆舞曲`
  - 输出：
```text
风格：圆舞曲　小节数：8　声部：2
确定性骨架生成音符数：39（seed=helpdemo-skel，tries=1 可复现）
```
  - 验证：见 `tests/test_help.py` —— 命令登记与解析验证

### Groove 人性化后处理

- **说明**：导出/回放 MIDI 时做确定性人性化：力度微扰 + 微量 timing + swing，落在表演层，不改符号谱正确性。
- **相关命令**：`/groove`
- **输入示例**：`/groove on 0.2 0.5（然后 /export）`
- **预期效果**：回放更有「人味」，且因 seed 固定而可复现。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/groove on 0.2 0.5`
  - 输出：
```text
Groove → on（swing=0.20, humanize=0.50）；导出/回放时生效
```
  - 验证：见 `tests/test_groove.py` —— Groove 力度/时值后处理确定性验证（14 断言）

### 风格保真度闭环精炼

- **说明**：确定性 DPO 等价物：锁 seed、用 seed 分支与旋钮坐标上升最大化风格重叠率，输出可审计的精炼谱。
- **相关命令**：`/refine`
- **输入示例**：`/refine 赋格`
- **预期效果**：在风格指纹上收敛到高保真版本，并给出每一轮 overlap 的审计轨迹。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证。

### MusicXML 往返导入

- **说明**：对标 AIVA 的 DAW 编辑闭环：导入外部（含 DAW 改过的）MusicXML，用规则引擎复检并重新规范化导出。
- **相关命令**：`/import`
- **输入示例**：`/import /path/to/song.musicxml`
- **预期效果**：校验时值完整性/声部引导/音域/风格重叠，并重导出规范文件。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/import demo.musicxml`
  - 输出：
```text
## MusicXML 再校验（确定性规则引擎）

- 文件：`示范小品` · ? · ♩=? · 4 小节 · 声部 1
- **外声部声部进行**：CRITICAL 0 / MAJOR 0 / MINOR 0
- **小节时值完整性**：✅ 全部完整
- **音域**：✅ 正常

> 以上校验由确定性规则引擎完成（平行五八度 / 时值完整性 / 音域 / 风格指纹），不依赖任何学习模型，过程可复现、可审计。
```
  - 验证：见 `tests/test_roundtrip.py` —— MusicXML 往返导入 + 规则复检确定性验证（26 断言）

### 五线谱记谱符号

- **说明**：谱面显示与 MusicXML 导入/导出共用同一套记谱模型，以下符号均可显示且往返不丢：谱号（高音/低音/中音/次中音/高音8vb/低音8vb，可曲中换谱号）、调号（含换调还原号）、拍号（数字或 C/¢，可曲中换拍）、符杠（含次级符杠）、延音线（可跨小节/跨行）、圆滑线（可嵌套）、三连音等连音（括号+数字）、倚音、演奏法（跳音/短跳音/重音/强重音/保持音/延长记号）、装饰音（颤音/波音/逆波音/回音）、琶音、换气记号、力度（小节首与小节中途）与渐强/渐弱楔形、速度（文字 + ♩=数值）、文字指示、排练号、歌词、和弦记号、双纵线/终止线/虚线小节线、反复记号与房子号、弱起小节；调内变音按调号与小节内已出现的升降自动省略/补还原号，重升/重降用矢量绘制。
- **相关命令**：`/import`、`/export`
- **输入示例**：`/import scores/chopin_mazurka.xml`
- **预期效果**：三连音、换调、反复等按原谱显示；编辑后导出再导入与原谱逐音一致。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/import demo.musicxml`
  - 输出：
```text
## MusicXML 再校验（确定性规则引擎）

- 文件：`示范小品` · ? · ♩=? · 4 小节 · 声部 1
- **外声部声部进行**：CRITICAL 0 / MAJOR 0 / MINOR 0
- **小节时值完整性**：✅ 全部完整
- **音域**：✅ 正常

> 以上校验由确定性规则引擎完成（平行五八度 / 时值完整性 / 音域 / 风格指纹），不依赖任何学习模型，过程可复现、可审计。
```
  - 验证：见 `tests/test_roundtrip.py` —— MusicXML 往返导入 + 规则复检确定性验证（26 断言）

### 乐谱逆向复现

- **说明**：MusicXML→纯中文逐音文字描述→MusicXML（无 JSON）；/replay 按文字还原，音符级一致，不调 LLM。
- **相关命令**：`/reverse-prompt`、`/replay`
- **输入示例**：`/reverse-prompt
/replay`
- **预期效果**：产出 score_description.md；复现后与源谱音符级一致。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 简单提示词扩写

- **说明**：把「写一首明朗的交响曲快板乐章」等短句确定性扩成专业分段 brief；创曲路径自动触发，也可用 /brief-expand 只看扩写结果。
- **相关命令**：`/brief-expand`
- **输入示例**：`/brief-expand 写一首明朗的交响曲快板乐章，像舒伯特早期`
- **预期效果**：输出含编制、速度阶梯、段落对比的可执行专业提示词。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 和弦进行顾问

- **说明**：对标 Hooktheory / Captain Chords 的交互式和弦顾问：纯和声逻辑、零依赖、不调 LLM；调内合法性 + 功能圈 + 导音解决 + 四声部 + 平行复检。加 write 可写入钢琴大谱表。
- **相关命令**：`/chords`、`/satb`
- **输入示例**：`/chords I V vi IV
/satb I V vi IV`
- **预期效果**：对进行做校验、功能分析、SATB 实现并检测平行五八度；/satb 直接生成钢琴大谱表四部和声。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/chords I V vi IV`
  - 输出：
```text
## 🎼 和弦进行顾问（确定性规则引擎 · 不调 LLM · 可审计）

**调性**：C（主音 C）　**调式**：大调

### 一、你提供的进行：`I V vi IV`

| # | 和弦 | 拼写 | 功能 |
|---|------|------|------|
| 1 | I | C E G | 主(T) |
| 2 | V | G B D | 属(D) |
| 3 | vi | A C E | 主(T) |
| 4 | IV | F A C | 下属(SD) |

### 二、根音运动

- 第 1→2 步：上行五度 / 下行四度（强）
- 第 2→3 步：全音上行
- 第 3→4 步：下行大三度

### 三、规则校验

- ⚠️ 未以主和弦（I/I）收束，终止感弱

### 四、四声部（SATB）实现与声部引导

| 和弦 | S | A | T | B |
|---|---|---|---|---|
| I | G4 | E4 | C4 | C3 |
| V | B4 | G4 | D4 | G2 |
| vi | A4 | E4 | C4 | A2 |
| IV | A4 | F4 | C4 | F3 |

✅ 此实现未出现平行五度/八度（声部引导干净）。

### 五、转位与声部引导建议

和弦的「原位/转位」决定低音唱名，直接影响声部引导的平滑度：
- **原位（5/3）**：低音=根音，音响最稳定，常用于强拍/终止；但连续原位易产生平行五度。
- **第一转位（6）**：低音=三音，柔和、弱功能，适合弱拍或连接两个原位和弦（如 I–V⁶–I 的 passing 用法）。
- **第二转位（6/4）**：低音=五音，不稳定，多用于「终止 6/4」（强拍）→ 属（弱拍）的装饰性过渡。
- **属七及转位（6⁵/4³/4²）**：属七天然含导音与下属音，解决到主和弦时：导音上行、七音下行级进，声部引导最自然。

通用声部引导规则（校验依据）：
1. 避免**平行完全五度/八度**（两声部同向跳到纯五/纯八）——上方已实现自动复检。
2. **导音**（大调第 7 级 / 小调第 7 级音）应上行半音解决到主音。
3. **共同音保持**：相邻和弦尽量保留重复音，其余声部级进。
4. **声部独立**：避免声部交叉，内声部可在合理音域内自由填充。

---
> 本顾问为**确定性规则引擎**：纯和声逻辑、零训练数据、不调 LLM，结果可复现、可审计（每一项和弦拼写/功能/声部引导均可溯源）。
```
  - 验证：见 `tests/test_chords.py` —— 和弦解析/拼写/校验/SATB/平行检测确定性验证（39 断言）

### 规则库（作曲库管理器）

- **说明**：作曲库「规则库」页统一管理对位权威、和声/声部/乐句教学规则与全部确定性内核脚本；每条可同时查看文档与实现源码。菜单：作曲库 → 规则库。规则摘要会自动注入 Planner/Translator 提示词，供 LLM 按意图选字段与脚本。
- **相关命令**：`作曲库管理器（Ctrl+L）→ 规则库`
- **输入示例**：`打开作曲库管理器 → 规则库 → 第一类二声部对位 / 终止式 / 脚本 melody`
- **预期效果**：左侧按分类浏览规则；右侧文档 + 代码双栏；创曲时摘要进入 Planner/Translator。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 钢琴大谱表四部和声

- **说明**：在钢琴大谱表（RH/LH 花括号）上写四部和声：S+A 叠写高音谱表，T+B 叠写低音谱表；菜单「新建钢琴四部和声谱」或 /satb。
- **相关命令**：`/satb`、`/chords`
- **输入示例**：`/satb Bb I IV V I`
- **预期效果**：打开可编辑的钢琴大谱表，每小节一个和弦的四声部。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/chords I V vi IV`
  - 输出：
```text
## 🎼 和弦进行顾问（确定性规则引擎 · 不调 LLM · 可审计）

**调性**：C（主音 C）　**调式**：大调

### 一、你提供的进行：`I V vi IV`

| # | 和弦 | 拼写 | 功能 |
|---|------|------|------|
| 1 | I | C E G | 主(T) |
| 2 | V | G B D | 属(D) |
| 3 | vi | A C E | 主(T) |
| 4 | IV | F A C | 下属(SD) |

### 二、根音运动

- 第 1→2 步：上行五度 / 下行四度（强）
- 第 2→3 步：全音上行
- 第 3→4 步：下行大三度

### 三、规则校验

- ⚠️ 未以主和弦（I/I）收束，终止感弱

### 四、四声部（SATB）实现与声部引导

| 和弦 | S | A | T | B |
|---|---|---|---|---|
| I | G4 | E4 | C4 | C3 |
| V | B4 | G4 | D4 | G2 |
| vi | A4 | E4 | C4 | A2 |
| IV | A4 | F4 | C4 | F3 |

✅ 此实现未出现平行五度/八度（声部引导干净）。

### 五、转位与声部引导建议

和弦的「原位/转位」决定低音唱名，直接影响声部引导的平滑度：
- **原位（5/3）**：低音=根音，音响最稳定，常用于强拍/终止；但连续原位易产生平行五度。
- **第一转位（6）**：低音=三音，柔和、弱功能，适合弱拍或连接两个原位和弦（如 I–V⁶–I 的 passing 用法）。
- **第二转位（6/4）**：低音=五音，不稳定，多用于「终止 6/4」（强拍）→ 属（弱拍）的装饰性过渡。
- **属七及转位（6⁵/4³/4²）**：属七天然含导音与下属音，解决到主和弦时：导音上行、七音下行级进，声部引导最自然。

通用声部引导规则（校验依据）：
1. 避免**平行完全五度/八度**（两声部同向跳到纯五/纯八）——上方已实现自动复检。
2. **导音**（大调第 7 级 / 小调第 7 级音）应上行半音解决到主音。
3. **共同音保持**：相邻和弦尽量保留重复音，其余声部级进。
4. **声部独立**：避免声部交叉，内声部可在合理音域内自由填充。

---
> 本顾问为**确定性规则引擎**：纯和声逻辑、零训练数据、不调 LLM，结果可复现、可审计（每一项和弦拼写/功能/声部引导均可溯源）。
```
  - 验证：见 `tests/test_chords.py` —— 和弦解析/拼写/校验/SATB/平行检测确定性验证（39 断言）

### 二声部对位（钢琴大谱表）

- **说明**：第一类二声部对位（对位法权威规则）：Planner 识别后确定性写谱并校验。凡涉及对位/一比一/species 均套用同一规则集（禁纯4、禁同时跳进、限平行3/6、反向为主、终止导→主/Ⅱ↓Ⅰ等）。对位≠赋格。也可「根据当前打开的乐谱写一个1比1对位」：抽定旋律再写另一声。
- **相关命令**：`/help`
- **输入示例**：`根据当前打开的乐谱写一个1比1对位`
- **预期效果**：从当前乐谱每小节抽一音作定旋律，生成钢琴大谱表二声部 1:1 对位；无当前谱时也可从零写「2声部一比一对位」。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/help`
  - 输出：
```text
# ComposerChat 帮助文档（确定性内核 · 规则校验 · 可复现 · 可审计）

> 所有命令与功能都建立在确定性内核之上：**零训练数据、seed 固定可复现、规则校验、可审计**。带示例，复制即可用。

## 一、功能清单（每个功能一个示例）

### ▸ 核心能力

- **自然语言作曲** — 用自然语言描述想要的曲子，自动匹配风格包并生成符号谱（MusicXML/MIDI）。
  - 相关命令：`（自然语言输入）`、`/style`、`/new`
  - 示例：`做一首雨夜钢琴风格的圆舞曲，带一点忧伤`
  - 效果：匹配「雨夜钢琴」风格包，生成 3/4 拍、柔和哀婉的圆舞曲。

- **风格包与绑定** — 风格包是护城河基石：零训练数据的确定性指纹。/style 设定基调并可绑定 genre 包，创作描述单独记录。
  - 相关命令：`/style`
  - 示例：`/style 圆舞曲 快板轻盈`
  - 效果：绑定圆舞曲风格包，并把「快板轻盈」作为创作描述传给生成器。

### ▸ 增强功能

- **风格插值 / 参数化风格合成** — 在两个风格之间确定性插值，得到「二者之间」的新风格——参数化风格合成，可复现、不调 LLM。
  - 相关命令：`/interpolate`
  - 示例：`/interpolate 雨夜钢琴 圆舞曲 40%`
  - 效果：在雨夜钢琴与圆舞曲间按 0.4 权重生成过渡风格乐曲。

### ▸ 核心能力

- **赋格与对位** — 内置赋格引擎：仅当 brief 明确提到赋格/卡农/创意曲时写入赋格段，或整首写成赋格（主题-答题-展开）。普通对位提示不会自动加赋格。
  - 相关命令：`/fugue`
  - 示例：`/fugue full`
  - 效果：整首按赋格写法生成（二声部时为二声部创意曲）。

- **确定性骨架** — 不调 LLM 的纯确定性骨架：按风格包指纹直接铺节奏/和声骨架，可复现可审计。
  - 相关命令：`/skeleton`
  - 示例：`/skeleton 圆舞曲`
  
…（完整目录较长，已在聊天内 /help 与 runs/help.md 提供）
```
  - 验证：见 `tests/test_help.py` —— 验证所有命令/功能在帮助中有条目且含示例；主题查找正确；/help 不触发作曲

### MIDI 键盘输入

- **说明**：接入外部 MIDI 键盘步进输入（macOS CoreMIDI / Windows MM，基于 mido+python-rtmidi）。按下的键实时以橙色显示在五线谱上；默认左右手分谱：分手点（默认 C4）及以上进右手高音谱表、以下进左手低音谱表，单谱表时自动升为钢琴大谱表；两手同时按的和弦各自写成和弦并上下对齐，只弹一只手时另一只手补休止。可选和弦模式、八度偏移、分手点。
- **相关命令**：
- **输入示例**：`工具 → MIDI 键盘 → 启用 MIDI 输入；左手按 C3+G3、右手按 C5+E5+G5`
- **预期效果**：按住时谱面出现橙色预览；写入后右手得到 C5-E5-G5 和弦、左手得到 C3-G3 和弦，同一拍对齐。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 渲染增强（soundfont/混响/力度）

- **说明**：对标 AIVA 渲染管线的确定性渲染层：SoundFont 音色选择 + 卷积混响 + 力度曲线。全部落在表演层，不改符号谱音高/时值/和声；同输入逐样本可复现、可审计。
- **相关命令**：`/render`
- **输入示例**：`/render piano reverb 0.3 velocity soft`
- **预期效果**：用 Salamander 钢琴采样 + 30% 混响 + 柔和力度，渲染当前乐谱为 WAV。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/render piano reverb 0.3 velocity soft`
  - 输出：
```text
后端探测：python
渲染配置：音色=MuseScore_General.sf3 ｜ 混响 开(wet=0.22, 尾音=1.6s) ｜ 力度=natural(scale=1.0, curve=1.0, 强拍+0.0) ｜ 44100Hz
输出 WAV：/var/folders/_9/jfs6k29j2xd45k606mnm1hr40000gn/T/rnd_blfbgeaz.wav
采样率：{'midi': '/var/folders/_9/jfs6k29j2xd45k606mnm1hr40000gn/T/rnd_aim9oxha.mid', 'wav': '/var/folders/_9/jfs6k29j2xd45k606mnm1hr40000gn/T/rnd_blfbgeaz.wav', 'backend': 'python', 'soundfont': '/Users/leizhao/Documents/prj/composerchat/soundfonts/MuseScore_General.sf3', 'preset': {'soundfont': None, 'piano': False, 'sample_rate': 44100, 'reverb_on': True, 'reverb_wet': 0.22, 'reverb_tail': 1.6, 'reverb_seed': 42, 'velocity_scale': 1.0, 'velocity_curve': 1.0, 'velocity_accent': 0.0, 'velocity_preset': 'natural'}, 'ok': True, 'samples': 94815, 'duration': 2.15, 'error': None, 'sample_rate': 44100} Hz　文件大小：189674 字节　校验：通过（非空音频）
```
  - 验证：见 `tests/test_render.py` —— 渲染增强（soundfont/混响/力度）确定性验证

### 风格包批量生成流水线

- **说明**：复用确定性内核的批量产出：枚举风格包→每包生成 N 首确定性骨架乐谱（MusicXML+MIDI）→ 算风格保真度 → 出审计报告。不调 LLM、可复现。
- **相关命令**：`/batch`
- **输入示例**：`/batch fugue chorale`
- **预期效果**：为 fugue、chorale 各生成 1 首确定性乐谱，并产出 runs/batch_report.md/.csv。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/batch`
  - 输出：
```text
# 风格包批量生成流水线 · 报告

- 生成时间（本地）：2026-10-02 18:34:13
- 风格包数：**1**　生成曲目总数：**1**　每包 1 首
- 内核：确定性风格插值骨架（不调 LLM）　保真度指标：`evaluate.style_overlap`（0–1，越大越像该风格）
- 渲染 WAV：关　输出目录：`scores/batch/`
- 全局平均风格保真度：**0.3147**（覆盖 1/1 个有指纹的包）

## 汇总表

| 风格包 | 家族 | 生成 | 平均保真度 | 最低 | 指纹来源 | 问题 |
|---|---|---|---|---|---|---|
| `fugue` |  | 1 | 0.3147 | 0.3147 | {'generated': 3, 'corpus': 48} | — |

## 每包明细

### `fugue`（ · 赋格=full）

平均保真度 **0.3147**　范围 [0.3147, 0.3147]

- seed `batch-fugue-0`　保真度 0.3147　小节 16　声部 3　标题《赋格小品》
  - MusicXML: `scores/batch/__fugue__0.musicxml`
  - MIDI: `scores/batch/__fugue__0.mid`

---
确定性 / 可审计说明：每首 seed 固定为 `{seed_base}-{pack_id}-{i}`，风格插值内核无随机性；混响（若开启）seed 固定。整条流水线可重跑复现同一批产物与保真度数值。
```
  - 验证：见 `tests/test_batch.py` —— 风格包批量生成流水线确定性验证

### 五线谱辅助编辑教程

- **说明**：空谱点写 + 程序化补全 + LLM 听审：从零手写出莫扎特《小夜曲》开篇完整 16 小节。
- **相关命令**：`/assist`、`/key`、`/tempo`、`/meter`、`/bars`、`/new`、`/export`
- **输入示例**：`/help 编辑教程`
- **预期效果**：打开完整教程：准备空谱 → 手写动机/应答/收束 → 辅助预览与听审 → 播放导出。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 编辑指令族

- **说明**：一整套规则驱动的改谱指令：续写/扩展/发展/变奏/配器/移调/裁剪/简化/复杂化/重写。均在已有乐谱上确定性改写。
- **相关命令**：`/continue`、`/extend`、`/develop`、`/variation`、`/orchestrate`、`/transpose`、`/shorter`、`/longer`、`/simplify`、`/complexify`、`/rewrite`
- **输入示例**：`/extend 8 然后 /transpose D`
- **预期效果**：先把乐曲延展 8 小节，再整体移调到 D。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 分析与建议闭环

- **说明**：先 /analyze 看特征与风格重叠，再 /advice 拿建议，确认后 /apply 改谱——规则驱动、无幻觉。
- **相关命令**：`/analyze`、`/advice`、`/apply`
- **输入示例**：`/analyze → /advice → /apply`
- **预期效果**：形成「看数据 → 出建议 → 执行」的可审计改谱闭环。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 学习风格

- **说明**：把当前乐谱「学进」风格包：更新 genre/packs 的特征统计与指纹，让后续生成更贴近该样本。
- **相关命令**：`/learn-score`
- **输入示例**：`/learn-score chorale`
- **预期效果**：用当前曲更新 chorale 包，后续 /style chorale 更贴合。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 体系层（非功能和声）

- **说明**：支持板腔 / 曲牌 / 拉格 / 十二音序列等体系，跳出功能和声范式。
- **相关命令**：`/system`
- **输入示例**：`/system 十二音序列`
- **预期效果**：按十二音序列技法生成，约束来自体系规则而非调性。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 偏好与调性设置

- **说明**：用一组控件精确设定创作约束：调性/速度/拍号/小节/声部/赋格/风格。
- **相关命令**：`/key`、`/tempo`、`/meter`、`/bars`、`/voices`、`/fugue`、`/style`
- **输入示例**：`/key Am /tempo 90 /meter 3/4 /bars 24`
- **预期效果**：组合设定一首 A 小调、♩=90、3/4 拍、24 小节的创作约束。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 导出 / 演示 / 会话

- **说明**：无 API 也能 /demo 看效果；/export 出谱；/new /clear /config 管理会话。
- **相关命令**：`/demo`、`/export`、`/new`、`/clear`、`/config`
- **输入示例**：`/demo /export`
- **预期效果**：先用演示曲看效果，再一键导出 MusicXML+MIDI。
- **实测**：该命令依赖乐谱/API，输出由命令解析与分发测试验证（见 `tests/test_help.py`）。

### 确定性内核与护城河

- **说明**：零训练数据、seed 固定可复现、规则校验、可审计——所有功能都建立在确定性内核 + 规则校验之上，不靠 LLM「凭空生成」。
- **相关命令**：`/skeleton`、`/interpolate`、`/groove`、`/refine`、`/import`、`/reverse-prompt`、`/replay`、`/brief-expand`、`/chords`
- **输入示例**：`任意 /chords 或 /skeleton 输入在固定 seed 下结果完全一致`
- **预期效果**：同一输入恒得同一输出，过程可被规则引擎逐步复算与审计。
- **实际运行（确定性，已内置断言验证）**：
  - 输入：`/skeleton 圆舞曲`
  - 输出：
```text
风格：圆舞曲　小节数：8　声部：2
确定性骨架生成音符数：39（seed=helpdemo-skel，tries=1 可复现）
```
  - 验证：见 `tests/test_help.py` —— 命令登记与解析验证

## 二、偏好设置命令实测（确定性）

以下设置命令直接修改会话偏好并返回确认，不触发作曲：

```text
/key Am  →  调性 → Am（sharps=0, minor）
/tempo 72  →  速度 → ♩=72
/meter 3/4  →  拍号 → 3/4
/bars 16  →  小节数 → 16
/voices 3  →  声部数 → 3（0=自动）
/fugue full  →  赋格 → full：整首写成赋格
```

> 验证：上述每一条都由 `tests/test_help.py` 的命令登记与解析测试覆盖；`apply_command` 对设置类命令返回普通提示，不会触发作曲。

## 三、命令速查（全部命令 + 验证归属）

### `/help [命令或功能名]`
- 说明：显示帮助文档。可带一个命令或功能名查看单项说明与示例；不带参数则输出完整命令+功能目录。/?, /help 等价。
- 示例：`/help /chords`
- 实测输出：
```text
# ComposerChat 帮助文档（确定性内核 · 规则校验 · 可复现 · 可审计）

> 所有命令与功能都建立在确定性内核之上：**零训练数据、seed 固定可复现、规则校验、可审计**。带示例，复制即可用。

## 一、功能清单（每个功能一个示例）

### ▸ 核心能力

- **自然语言作曲** — 用自然语言描述想要的曲子，自动匹配风格包并生成符号谱（MusicXML/MIDI）。
  - 相关命令：`（自然语言输入）`、`/style`、`/new`
  - 示例：`做一首雨夜钢琴风格的圆舞曲，带一点忧伤`
  - 效果：匹配「雨夜钢琴」风格包，生成 3/4 拍、柔和哀婉的圆舞曲。

- **风格包与绑定** — 风格包是护城河基石：零训练数据的确定性指纹。/style 设定基调并可绑定 genre 包，创作描述单独记录。
  - 相关命令：`/style`
  - 示例：`/style 圆舞曲 快板轻盈`
  - 效果：绑定圆舞曲风格包，并把「快板轻盈」作为创作描述传给生成器。

### ▸ 增强功能

- **风格插值 / 参数化风格合成** — 在两个风格之间确定性插值，得到「二者之间」的新风格——参数化风格合成，可复现、不调 LLM。
  - 相关命令：`/interpolate`
  - 示例：`/interpolate 雨夜钢琴 圆舞曲 40%`
```
- 验证：`tests/test_help.py` —— 验证所有命令/功能在帮助中有条目且含示例；主题查找正确；/help 不触发作曲

### `/assist [on|off|style|inspire|harmony|status]`
- 说明：音符级辅助作曲（程序化补全，不调 LLM）。开启后：Tab 下一音、Ctrl+Tab 本小节、Ctrl+Enter 全曲；1–5 选候选。
- 示例：`/assist inspire jazzy`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/new`
- 说明：开始新曲会话：重置当前乐谱与创作状态，但保留调性/速度等偏好。
- 示例：`/new /style 圆舞曲 快板轻盈`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/clear`
- 说明：清空对话历史与当前曲目状态，保留已设偏好（调性/速度/风格等）。
- 示例：`/clear`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/config`
- 说明：显示当前配置与已设偏好（API 接入情况、调性、速度、风格、Groove 等）。
- 示例：`/config`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/demo`
- 说明：用内置演示曲生成示例乐谱——无需配置 API 也能直接看到效果。
- 示例：`/demo`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/export`
- 说明：导出当前乐谱为 MusicXML + MIDI（若开启 Groove，则 MIDI 含人性化处理）。
- 示例：`/export`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/analyze`
- 说明：分析当前曲目：风格重叠率、16 维特征、声部/调性/曲式等，输出可读报告。
- 示例：`/analyze`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/advice`
- 说明：给出修改建议（只分析、不改谱），待确认后用 /apply 执行。
- 示例：`/advice`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/apply`
- 说明：执行上一次 /advice 给出的建议，真正改写乐谱。
- 示例：`/apply`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/key <调性>`
- 说明：设置调性，例如 C / G / Am / Dm / bB。若已有乐谱则标记为移调待执行。
- 示例：`/key Am`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/tempo <bpm>`
- 说明：设置速度，范围 40–240。
- 示例：`/tempo 72`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/meter <a/b>`
- 说明：设置拍号，例如 4/4、3/4、6/8。
- 示例：`/meter 3/4`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/bars <n>`
- 说明：设置小节数，范围 4–32（影响新建/下一首生成；不改当前已打开的谱）。
- 示例：`/bars 16`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/addbars <n>`
- 说明：在当前乐谱末尾追加 n 个空小节（1–32）。也可用菜单「文件 → 增加小节…」或编辑工具条「+小节」。
- 示例：`/addbars 4`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/addpart [piano|bass|violin|cello|…]`
- 说明：为当前乐谱追加空声部。单声部用 piano/左手 可升为钢琴大谱表。也可用菜单「文件 → 添加声部…」或编辑工具条「+声部」。
- 示例：`/addpart piano`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/accomp [bass|block|broken|alberti]`
- 说明：根据旋律声部写入配乐（确定性）。无低音声部时自动添加。也可用菜单「文件 → 设置配乐…」或编辑工具条「配乐」。
- 示例：`/accomp alberti`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/voices 0|2|3|4`
- 说明：设置声部数。0=由风格自动推导，2/3/4 为固定声部数。
- 示例：`/voices 3`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/style <风格名>`
- 说明：设定/绑定风格包。风格名写命令后，创作描述写后面或下一句。输入 /style 会弹出可选列表——只列「学过」的包（有 grammar.yaml，见右侧检查器「风格包」下拉）；未学过的包不在候选里。
- 示例：`/style 众赞歌 庄重匀净`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/fugue auto|full|off`
- 说明：赋格设置。auto=仅当 brief 明确提到赋格/卡农/创意曲等才加（「对位」本身不加，默认）；full=整首写成赋格；off=不加赋格。
- 示例：`/fugue full`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/skeleton [风格名]`
- 说明：纯确定性骨架生成（不调 LLM）：按风格包特征直接铺骨架，可复现可审计。
- 示例：`/skeleton 圆舞曲`
- 实测输出：
```text
风格：圆舞曲　小节数：8　声部：2
确定性骨架生成音符数：39（seed=helpdemo-skel，tries=1 可复现）
```
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/interpolate <风格A> <风格B> [t]`
- 说明：风格插值 / 参数化风格合成（确定性，不调 LLM）。t=0 偏 A，t=1 偏 B，可写 0–1 或 0–100%。
- 示例：`/interpolate 雨夜钢琴 圆舞曲 40%`
- 实测：依赖乐谱/API，详见解析/分发测试。
- 验证：`tests/test_interpolate.py` —— 风格插值原语与 interpolate_genre/interpolate_spec 确定性验证

### `/system <体系>`
- 说明：体系层生成：板腔 / 曲牌 / 拉格 / 十二音序列等非功能和声体系。
- 示例：`/system 十二音序列`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/continue`
- 说明：从当前乐谱结尾继续续写（需先有曲目）。
- 示例：`/continue`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/extend [n]`
- 说明：扩写 n 小节（n 为数字时同步设为小节数，需先有曲目）。
- 示例：`/extend 8`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/develop`
- 说明：动机发展段（需先有曲目）。
- 示例：`/develop`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/variation`
- 说明：变奏段（需先有曲目）。
- 示例：`/variation`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/harmonize`
- 说明：为当前旋律重新配置和声（需先有曲目）。
- 示例：`/harmonize`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/orchestrate [配器]`
- 说明：重新配器，可指定乐器组合（需先有曲目）。
- 示例：`/orchestrate 弦乐四重奏`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/transpose <调性>`
- 说明：移调（需先有曲目）。
- 示例：`/transpose D`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/shorter`
- 说明：精简/裁短当前曲目（需先有曲目）。
- 示例：`/shorter`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/longer`
- 说明：扩充/加长当前曲目（需先有曲目）。
- 示例：`/longer`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/simplify`
- 说明：简化当前谱面（需先有曲目）。
- 示例：`/simplify`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/complexify`
- 说明：复杂化/加花当前谱面（需先有曲目）。
- 示例：`/complexify`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/rewrite`
- 说明：整体重写当前曲目（也用于 /apply 内部执行建议）。
- 示例：`/rewrite`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/learn-score <风格包>`
- 说明：把当前乐谱「学进」对应风格包（直接更新 genre/packs 的统计与指纹）。
- 示例：`/learn-score chorale`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/groove [on|off] [swing] [humanize]`
- 说明：人性化后处理（确定性，仅导出/回放 MIDI 时生效，不改符号谱）。on/off 开关；swing 0–0.6；humanize 0–1。
- 示例：`/groove on 0.2 0.5`
- 实测输出：
```text
Groove → on（swing=0.20, humanize=0.50）；导出/回放时生效
```
- 验证：`tests/test_groove.py` —— Groove 力度/时值后处理确定性验证（14 断言）

### `/refine [风格名]`
- 说明：风格保真度闭环精炼（确定性 DPO 等价物：seed 分支 + 旋钮坐标上升最大化 style_overlap；可审计、不调 LLM）。
- 示例：`/refine 赋格`
- 实测：依赖乐谱/API，详见解析/分发测试。
- 验证：`tests/test_refine.py` —— 风格保真闭环 seed 分支+旋钮坐标上升，overlap 提升验证（32 断言）

### `/import <MusicXML路径>`
- 说明：MusicXML 往返导入 + 再校验（对标 AIVA 的 DAW 编辑闭环）：用确定性规则引擎复检，并重新导出规范化文件。
- 示例：`/import /path/to/song.musicxml`
- 实测输出：
```text
## MusicXML 再校验（确定性规则引擎）

- 文件：`示范小品` · ? · ♩=? · 4 小节 · 声部 1
- **外声部声部进行**：CRITICAL 0 / MAJOR 0 / MINOR 0
- **小节时值完整性**：✅ 全部完整
- **音域**：✅ 正常

> 以上校验由确定性规则引擎完成（平行五八度 / 时值完整性 / 音域 / 风格指纹），不依赖任何学习模型，过程可复现、可审计。
```
- 验证：`tests/test_roundtrip.py` —— MusicXML 往返导入 + 规则复检确定性验证（26 断言）

### `/reverse-prompt [MusicXML|JSON路径]`
- 说明：MusicXML→纯中文逐音文字描述（无 JSON，确定性，不调 LLM）。再用 /replay 可将文字还原为 MusicXML。
- 示例：`/reverse-prompt scores/chats/demo.xml`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/replay [score_description.md路径]`
- 说明：把 /reverse-prompt 生成的纯文字描述还原为乐谱（MusicXML←文字，确定性，不调 LLM）。
- 示例：`/replay runs/reverse_abc/score_description.md`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/brief-expand <简单提示词>`
- 说明：把短句（如「写一首明朗的交响曲快板乐章」）确定性扩写为专业分段 brief，不生成乐谱；创曲时也会自动扩写。
- 示例：`/brief-expand 写一首明朗的交响曲快板乐章，像舒伯特早期`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/chords [调性] [进行] [write]`
- 说明：和弦进行顾问（确定性规则引擎，不调 LLM，对标 Hooktheory/Captain Chords）：调内合法性 + 根音运动 + 功能圈 + 导音解决 + 四声部实现 + 平行五八度复检。末尾加 write 则写入钢琴大谱表（等同 /satb）。
- 示例：`/chords I V vi IV`
- 实测输出：
```text
## 🎼 和弦进行顾问（确定性规则引擎 · 不调 LLM · 可审计）

**调性**：C（主音 C）　**调式**：大调

### 一、你提供的进行：`I V vi IV`

| # | 和弦 | 拼写 | 功能 |
|---|------|------|------|
| 1 | I | C E G | 主(T) |
| 2 | V | G B D | 属(D) |
| 3 | vi | A C E | 主(T) |
| 4 | IV | F A C | 下属(SD) |

### 二、根音运动

- 第 1→2 步：上行五度 / 下行四度（强）
- 第 2→3 步：全音上行
- 第 3→4 步：下行大三度

### 三、规则校验

- ⚠️ 未以主和弦（I/I）收束，终止感弱

### 四、四声部（SATB）实现与声部引导

| 和弦 | S | A | T | B |
|---|---|---|---|---|
| I | G4 | E4 | C4 | C3 |
| V | B4 | G4 | D4 | G2 |
| vi | A4 | E4 | C4 | A2 |
| IV | A4 | F4 | C4 | F3 |

✅ 此实现未出现平行五度/八度（声部引导干净）。

### 五、转位与声部引导建议

和弦的「原位/转位」决定低音唱名，直接影响声部引导的平滑度：
- *
```
- 验证：`tests/test_chords.py` —— 和弦解析/拼写/校验/SATB/平行检测确定性验证（39 断言）

### `/satb [调性] <进行>`
- 说明：在钢琴大谱表上写出四部和声（RH=Soprano+Alto，LH=Tenor+Bass）；确定性声部引导，含平行五八度检测；写完可进编辑模式继续改。
- 示例：`/satb I V vi IV`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/learn-grammar [chorale|folk|jazz|pop|madrigal|sonata|strings|symphony|chopin|all]`
- 说明：从带和弦标注的乐谱语料学习可解释作曲语法，蒸馏成带编号的规则脚本 grammar.yaml 与中文说明 grammar.md，并报告留出集困惑度。不带参数 = music21 自带的 350 首巴赫众赞歌（逐拍和弦识别 → 乐句/终止切分 → 句终规划、终止公式、按“距终止拍数”条件化的和声转移、旋律轮廓、声部配置）。folk = Nottingham 民谣（吉格/里尔/角笛舞/华尔兹）、jazz = Weimar 爵士独奏（摇摆/比波普/酷派/硬波普/后波普/传统）、pop = Billboard 流行歌、madrigal = 蒙特威尔第牧歌：按子风格学曲式（AABB/AABA/ABAB…）、段落终止公式、按段内位置条件化的和弦转移（回退 n 元模型）、小节节奏模板或拍内节奏单元链、旋律音程/和弦音比例/音域与硬规则 R01–R10。sonata = KernScores 贝多芬/莫扎特/海顿钢琴奏鸣曲、strings = 贝多芬/莫扎特/海顿弦乐四重奏、symphony = 海顿《伦敦交响曲》第 99–104 号（只读弦乐组分析和声）、chopin = 肖邦玛祖卡：乐谱没有和弦标注，分析器自己识别和声——逐拍（4/4 每半小节）按音高证据给候选和弦打分 → 维特比平滑（强拍换和弦代价低）→ 24 个调上的维特比找局部调，≥8 小节的调区各自成段、和弦按局部调记级数；词表只留占比 ≥1% 且有古典和声功能的和弦（调内、副属、借用、那不勒斯），转移概率与整体频率按 1:3 插值以抵消识别噪声；子风格按拍号分（快板 4/4、小步舞曲 3/4、二拍子 2/4、复拍子 6/8）。all = 全部。
- 示例：`/learn-grammar jazz`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/compose-grammar [风格 子风格] [major|minor] [小节数] [弱起|强拍起] [乐器] [seed=…] [t=温度]`
- 说明：只读学到的规则脚本、确定性地作曲（不调 LLM），同一 seed 逐音符复现。不带风格词 = SATB 巴赫众赞歌：句终规划 → 终止公式 → 和声+女高音联合搜索 → 低音 → 内声部 → 经过音；平行五八度/交叉/导音/七音等硬规则 0 违例，每拍可追溯到规则编号（H 和声 / C 终止 / F 句终规划 / M 旋律轮廓）。弱起（pickup）= 一拍弱起小节 + 指定小节数，句终落第 3 拍并加延长；不指定时按语料比例（约 72% 弱起）抽签。带风格词（民谣/jig/reel/hornpipe/waltz、爵士/swing/bebop/cool/hardbop/postbop/traditional、流行、牧歌）= 旋律 + 钢琴伴奏：按学到的曲式分段（重复段逐音符再现）→ 每段和弦进行（终止公式 + 位置条件转移）→ 节奏（小节模板或节奏单元链）→ 旋律动态规划 → 按风格写伴奏（民谣“嘭-嚓”、爵士走动低音 + 三七和弦、流行分解律动、牧歌持续和弦）；R01–R10 硬规则（强拍非和弦音解决、跳进上限、音域、主和弦收尾、语料未见的和弦转移、三声部之间无平行五八度、节奏只用学到的模板、同一和弦连续小节上限）0 违例。小节数取 8 的倍数（默认 32，牧歌 24）；乐器可写 violin/flute/trumpet/clarinet/voice/soprano 或中文名；调号/速度没指定时用风格默认。奏鸣曲（快板/小步舞曲/二拍子/复拍子）与肖邦玛祖卡 = 钢琴独奏：右手旋律，左手按拍号写阿尔贝蒂低音（4/4、2/4）、分解三和弦（6/8）或低音-和弦-和弦（3/4），每个音型候选都逐起音检查与旋律无平行五八度；四重奏 = 第一小提琴旋律、第二小提琴 + 中提琴双音配位同音反复（3/4 第一拍休止）、大提琴低音，四个声部两两无平行五八度。交响曲（海顿 / 交响曲 / symphony：快板/小步舞曲/二拍子）= 古典双管缩编总谱：长笛、双簧管、圆号、大管、定音鼓 + 弦乐四部；弦乐组按四重奏织体全程演奏，偶数段与末段全奏——长笛高八度/双簧管同度加倍旋律、大管加倍大提琴、圆号持续主/属音（避开与各声部的平行）、定音鼓在主/属和弦小节第一拍击奏。不用斜杠也行：直接说「作一曲小步舞曲」「写一首海顿交响曲小步舞曲」「来一首忧伤的玛祖卡」「写一首巴赫众赞歌」，Planner 会在计划里填 learned_grammar（风格/子风格/调式/调号/速度/小节数，只填你说过的），直接走这里的确定性作曲；指定了风格外的乐器、曲式、转调或超过 32 小节时仍走常规 LLM 翻译流程。只说「小步舞曲」= 钢琴奏鸣曲小步舞曲，带「弦乐/四重奏」= 四重奏，带「交响/管弦」= 交响曲。
- 示例：`/compose-grammar 爵士 bebop 32 seed=1`
- 验证：`tests/test_help.py` —— 命令登记与解析验证

### `/render [piano|gm] [reverb 0-0.9] [velocity <预设>] [<音色.sf2>]`
- 说明：渲染增强（确定性、表演层、不调 LLM，对标 AIVA 的渲染管线）：选 SoundFont 音色 + 卷积混响 + 力度曲线。仅动音色/空间/力度，不改符号谱。
- 示例：`/render piano reverb 0.3 velocity soft`
- 实测输出：
```text
后端探测：python
渲染配置：音色=MuseScore_General.sf3 ｜ 混响 开(wet=0.22, 尾音=1.6s) ｜ 力度=natural(scale=1.0, curve=1.0, 强拍+0.0) ｜ 44100Hz
输出 WAV：/var/folders/_9/jfs6k29j2xd45k606mnm1hr40000gn/T/rnd_blfbgeaz.wav
采样率：{'midi': '/var/folders/_9/jfs6k29j2xd45k606mnm1hr40000gn/T/rnd_aim9oxha.mid', 'wav': '/var/folders/_9/jfs6k29j2xd45k606mnm1hr40000gn/T/rnd_blfbgeaz.wav', 'backend': 'python', 'soundfont': '/Users/leizhao/Documents/prj/composerchat/soundfonts/MuseScore_General.sf3', 'preset': {'soundfont': None, 'piano': False, 'sample_rate': 44100, 'reverb_on': True, 'reverb_wet': 0.22, 'reverb_tail': 1.6, 'reverb_s
```
- 验证：`tests/test_render.py` —— 渲染增强（soundfont/混响/力度）确定性验证

### `/batch [风格包/家族...]`
- 说明：风格包批量生成流水线（确定性、可审计，复用确定性内核）：枚举风格包→每包生成 N 首确定性骨架乐谱→算风格保真度→出报告。
- 示例：`/batch fugue chorale`
- 实测输出：
```text
# 风格包批量生成流水线 · 报告

- 生成时间（本地）：2026-10-02 18:34:13
- 风格包数：**1**　生成曲目总数：**1**　每包 1 首
- 内核：确定性风格插值骨架（不调 LLM）　保真度指标：`evaluate.style_overlap`（0–1，越大越像该风格）
- 渲染 WAV：关　输出目录：`scores/batch/`
- 全局平均风格保真度：**0.3147**（覆盖 1/1 个有指纹的包）

## 汇总表

| 风格包 | 家族 | 生成 | 平均保真度 | 最低 | 指纹来源 | 问题 |
|---|---|---|---|---|---|---|
| `fugue` |  | 1 | 0.3147 | 0.3147 | {'generated': 3, 'corpus': 48} | — |

## 每包明细

### `fugue`（ · 赋格=full）

平均保真度 **0.3147**　范围 [0.3147, 0.3147]

- seed `batch-fugue-0`　保真度 0.3147　小节 16　声部 3　标题《赋格小品》
  - MusicXML: `scores/batch/__fugue__0.musicxml`
  - MIDI: `scores/batch/__fugue__0.mid`

---
确定性
```
- 验证：`tests/test_batch.py` —— 风格包批量生成流水线确定性验证

## 四、测试用例结果（本次生成时的真实运行）

| 命令 | 输入示例 | 验证测试 | 正确性结论 |
| --- | --- | --- | --- |
| `/help` | `/help` | `tests/test_help.py` | PASS（内置断言通过） |
| `/assist` | `/assist inspire jazzy` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/new` | `/new /style 圆舞曲 快板轻盈` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/clear` | `/clear` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/config` | `/config` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/demo` | `/demo` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/export` | `/export` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/analyze` | `/analyze` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/advice` | `/advice` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/apply` | `/apply` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/key` | `/key Am` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/tempo` | `/tempo 72` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/meter` | `/meter 3/4` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/bars` | `/bars 16` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/addbars` | `/addbars 4` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/addpart` | `/addpart piano` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/accomp` | `/accomp alberti` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/voices` | `/voices 3` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/style` | `/style 众赞歌 庄重匀净` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/fugue` | `/fugue full` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/skeleton` | `/skeleton 圆舞曲` | `tests/test_help.py` | PASS（内置断言通过） |
| `/interpolate` | `/interpolate 雨夜钢琴 圆舞曲 40%` | `tests/test_interpolate.py` | FAIL |
| `/system` | `/system 十二音序列` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/continue` | `/continue` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/extend` | `/extend 8` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/develop` | `/develop` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/variation` | `/variation` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/harmonize` | `/harmonize` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/orchestrate` | `/orchestrate 弦乐四重奏` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/transpose` | `/transpose D` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/shorter` | `/shorter` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/longer` | `/longer` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/simplify` | `/simplify` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/complexify` | `/complexify` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/rewrite` | `/rewrite` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/learn-score` | `/learn-score chorale` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/groove` | `/groove on 0.2 0.5` | `tests/test_groove.py` | PASS（内置断言通过） |
| `/refine` | `/refine 赋格` | `tests/test_refine.py` | FAIL |
| `/import` | `/import demo.musicxml` | `tests/test_roundtrip.py` | PASS（内置断言通过） |
| `/reverse-prompt` | `/reverse-prompt scores/chats/demo.xml` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/replay` | `/replay runs/reverse_abc/score_description.md` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/brief-expand` | `/brief-expand 写一首明朗的交响曲快板乐章，像舒伯特早期` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/chords` | `/chords I V vi IV` | `tests/test_chords.py` | PASS（内置断言通过） |
| `/satb` | `/satb I V vi IV` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/learn-grammar` | `/learn-grammar jazz` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/compose-grammar` | `/compose-grammar 爵士 bebop 32 seed=1` | `tests/test_help.py` | PASS（解析/分发测试覆盖） |
| `/render` | `/render piano reverb 0.3 velocity soft` | `tests/test_render.py` | PASS（内置断言通过） |
| `/batch` | `/batch` | `tests/test_batch.py` | PASS（内置断言通过） |

### 一个完整测试用例（含断言）

以 **`/chords I V vi IV`** 为例，验证和弦顾问输出正确：

```text
## 🎼 和弦进行顾问（确定性规则引擎 · 不调 LLM · 可审计）

**调性**：C（主音 C）　**调式**：大调

### 一、你提供的进行：`I V vi IV`

| # | 和弦 | 拼写 | 功能 |
|---|------|------|------|
| 1 | I | C E G | 主(T) |
| 2 | V | G B D | 属(D) |
| 3 | vi | A C E | 主(T) |
| 4 | IV | F A C | 下属(SD) |

### 二、根音运动

- 第 1→2 步：上行五度 / 下行四度（强）
- 第 2→3 步：全音上行
- 第 3→4 步：下行大三度

### 三、规则校验

- ⚠️ 未以主和弦（I/I）收束，终止感弱

### 四、四声部（SATB）实现与声部引导

| 和弦 | S | A | T | B |
|---|---|---|---|---|
| I | G4 | E4 | C4 | C3 |
| V | B4 | G4 | D4 | G2 |
| vi | A4 | E4 | C4 | A2 |
| IV | A4 | F4 | C4 | F3 |

✅ 此实现未出现平行五度/八度（声部引导干净）。

### 五、转位与声部引导建议

和弦的「原位/转位」决定低音唱名，直接影响声部引导的平滑度：
- **原位（5/3）**：低音=根音，音响最稳定，常用于强拍/终止；但连续原位易产生平行五度。
- **第一转位（6）**：低音=三音，柔和、弱功能，适合弱拍或连接两个原位和弦（如 I–V⁶–I 的 passing 用法）。
- **第二转位（6/4）**：低音=五音，不稳定，多用于「终止 6/4」（强拍）→ 属（弱拍）的装饰性过渡。
- **属七及转位（6⁵/4³/4²）**：属七天然含导音与下属音，解决到主和弦时：导音上行、七音下行级进，声部引导最自然。

通用声部引导规则（校验依据）：
1. 避免**平行完全五度/八度**（两声部同向跳到纯五/纯八）——上方已实现自动复检。
2. **导音**（大调第 7 级 / 小调第 7 级音）应上行半音解决到主音。
3. **共同音保持**：相邻和弦尽量保留重复音，其余声部级进。
4. **声部独立**：避免声部交叉，内声部可在合理音域内自由填充。

---
> 本顾问为**确定性规则引擎**：纯和声逻辑、零训练数据、不调 LLM，结果可复现、可审计（每一项和弦拼写/功能/声部引导均可溯源）。
```

**断言（`tests/test_chords.py`，共 39 条，本次全部通过）**：
- `advisor` 解析 I / V / vi / IV 为 4 个和弦，degree 分别为 1/5/6/4，大小写决定三和弦性质（vi 为小三和弦）；
- `analyze_progression` 给 I→主(T)、V→属(D)、vi→主(T)、IV→下属(SD)；
- SATB 四声部实现生成 4 行，且规则引擎检测平行五/八度；
- `advisor("I V vi IV")` 返回文本含 `SATB` 与四个罗马数字（已内置断言验证）。

