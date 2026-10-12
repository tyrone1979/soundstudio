# ComposerChat / 乐谱工作室（Score Studio）

[English](README.md) | **中文**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

基于 PyQt5 的**确定性符号音乐创作工作台**：用自然语言（或纯确定性命令）生成**可编辑乐谱**，全程规则校验、逐音符可复现、生成过程可审计、零训练数据。

> 差异化一句话：**确定性内核 + LLM 前端 + 规则校验 + 可复现 + 赋格引擎**。市面上没有同构产品（详见 `docs/对比分析_符号音乐调研报告.md`）。

## 它解决什么问题

| 能力 | 说明 |
|------|------|
| **可复现** | 同一 `seed` / `subseeds` 必产出同一首曲子；`spec.json` 即复现凭据 |
| **结构正确可保证** | 时值必满、终止式必有、对位合法、导音拼写符合音乐性 |
| **五线谱可视化编辑** | 谱面上直接增删改音符，AI 式智能提示 + 三级实时校验（详见下文「乐谱编辑器」） |
| **赋格引擎** | 明确要求时整首赋格（`/fugue full`）或插入赋格段；brief 未提赋格时 LLM 不得越权自加（确定性摘除兜底） |
| **段落级转调** | `set_harmony` 支持 `key_sharps`/`mode`，B 段转调直接写入 MusicXML `<key><mode>` |
| **钢琴大谱表** | 2 声部 Piano 自动合并为单 part 双谱表（`<staves>2</staves>` + 花括号） |
| **可审计** | 记谱层 `RuleLog` 分规则计数；评估层三指标可量化风格契合度；提示词→乐谱→分析报告三方一致性测试 |
| **零训练数据** | 规则/算法驱动 → 零版权风险、零幻觉、零不可复现 |

## 架构总览

```
自然语言 / 命令
   │
   ▼
style_routing  两级路由（家族 → 包，中文同义词归一，失败抛 StyleRoutingError）
   │
   ▼
style_skeleton 风格骨架（注入 profile → 确定性脚本调用序列）
   │
   ▼
compose_kernel 确定性内核（derive_subseeds + 软权重 + 分枝随机源）
   │
   ▼
notation       记谱层（拼写/连断/连谱线/导音，RuleLog 审计）
   │
   ▼
validate_score_spec + evaluate（三指标：style_overlap / style_rank / attribute_adherence）
   │
   ▼
score_studio（PyQt5 界面）· deliver（LilyPond / runs 归档）· MusicXML / MIDI
```

## 帮助文档

- **应用内「帮助」菜单**（Studio 菜单栏）：
  - `帮助文档（完整 · 每条带示例）`（快捷键 `Ctrl+H`）—— 弹窗渲染完整命令/功能目录（每条均带示例）；
  - `查看单项说明（命令/功能）…` —— 输入命令名或功能名查看单项详情；
  - `设计理念 · 护城河` —— 说明确定性内核与护城河；
  - `专业文档（命令/功能实测验证版）` —— 用系统默认应用打开 `runs/help_pro.md`。
  - 聊天内同样可用 `/help` 查看完整目录，`/help <命令或功能名>` 看单项详情。
- **专业文档 `runs/help_pro.md`**（由 `python gen_help_pro.py` 生成）：每个命令/功能给出
  **真实输入 → 实际输出 → 正确性验证** 三段式，确定性命令真实执行并通过内置断言，文末附
  「测试用例结果」汇总表与完整 `/chords` 测试用例。
- 对应的测试：`tests/test_help_pro.py`（每个确定性命令真实执行并断言通过）、`tests/test_help.py`（命令/功能目录与示例覆盖）。

## 快速开始

```bash
pip install -r requirements.txt
# Windows
start.bat
# macOS / Linux
./start_studio.sh
```

主入口：`score_studio.py`（也可用 `start.bat`）。需配置 LLM API（见 `llm_config.example.json`）；未配置时可用 `/demo`。

```bash
python score_studio.py            # 图形界面
python llm_compose.py             # 命令行会话（可选）
```

### 界面语言（中文 / English）

菜单 **视图 → 界面语言 / Language** 可在中文与英文之间即时切换，选择会被记住（QSettings）。
也可用环境变量指定启动语言：

```bash
COMPOSERCHAT_LANG=en python score_studio.py
```

翻译只作用于界面外壳（菜单、按钮、提示、对话框、状态栏、风格包名称等，见 `i18n.py`）；
乐谱内容、对话记录、LLM 回复与分析报告保持生成时的语言。

## 风格包（15 包 / 9 家族）

真相源是各包的 `meta.yaml → profile`；创曲命中风格时注入内核。标 ★ 的包已从语料学到
`grammar.yaml`（确定性语法生成，Studio 的风格下拉框只列这些）。

| 家族 | 包 |
|------|----|
| academic（学院古典） | fugue、piano-prelude |
| classical（古典管弦） | classical-sonata、piano-sonata ★、string-quartet ★、symphony ★ |
| dance（舞曲） | mazurka、waltz |
| folk（民俗民族） | british-folk-tune ★、celtic-jig |
| jazz（爵士） | swing-standard ★ |
| pop（流行） | pop-standard ★ |
| romantic（浪漫钢琴） | chopin-piano ★ |
| sacred（宗教仪式） | chorale ★ |
| vocal（声乐复调） | madrigal ★ |

维度定义见 `skills/composition/genre/taxonomy.yaml`。

## 命令参考（Studio 聊天框 / 命令行）

命令可与自然语言混用，一行可写多个。

| 命令 | 说明 |
|------|------|
| 自然语言 | 直接描述曲子；自动匹配风格包。显式速度/拍号/调性**直通谱面**，genre 只做提示不做覆盖 |
| `/style 雨夜钢琴` | 绑定风格包（注入 `meta.yaml → profile`） |
| `/skeleton [风格名]` | 纯确定性骨架生成，**不调 LLM**（先 `/style` 定基调） |
| `/interpolate <A> <B> [t]` | 风格插值/参数化风格合成（**不调 LLM**；t=0 偏 A、t=1 偏 B，t 可为 0–1 或 0–100%） |
| `/refine [风格名]` | 风格保真度闭环精炼（**不调 LLM**；seed 分支 + 旋钮坐标上升最大化 `style_overlap`，确定性 DPO 等价物，全程可审计） |
| `/import <MusicXML路径>` | MusicXML 往返导入 + 再校验（**不调 LLM**；对标 AIVA DAW 编辑闭环：确定性规则引擎复检平行五八度/时值完整性/音域/风格重叠率，并重新导出规范化 MusicXML+MIDI） |
| `/chords [调性] [进行]` | 和弦进行顾问（**不调 LLM**；纯和声逻辑，零外部依赖：调内合法性 + 根音运动 + 功能圈 + 导音解决 + 四声部(SATB)实现 + 平行五八度复检；对标 Hooktheory/Captain Chords，确定性、可审计） |
| `/render [piano\|gm] [reverb 0-0.9] [velocity <预设>] [<音色.sf2>]` | 渲染增强（**不调 LLM**；确定性、表演层）：SoundFont 音色 + 卷积混响 + 力度曲线，仅动音色/空间/力度，**不改符号谱**；对标 AIVA 渲染管线 |
| `/batch [风格包/家族...]` | 风格包批量生成流水线（**不调 LLM**；确定性、可审计）：枚举风格包→每包生成 N 首确定性骨架乐谱→算风格保真度→出 `runs/batch_report.md`/`.csv` |
| `/groove [on\|off] [swing] [humanize]` | 导出/回放 MIDI 时做确定性人性化（力度微扰 + 微量 timing + swing），不改符号谱 |
| `/fugue full\|auto\|off` | 赋格控制（`full`=整首赋格；`auto`=LLM 按 brief 自行决定是否插赋格段，未提不加；`off`=禁止） |
| `/system 板腔\|曲牌\|拉格\|序列` | 体系层生成（确定性，不走功能和声内核） |
| `/key Am` `/tempo 72` `/meter 3/4` `/bars 16` `/voices 2\|3\|4` | 参数微调 |
| `/advice` | 分析当前谱，只给修改建议 |
| `可以` / `执行` / `/apply` | 按上一轮建议整曲改写 |
| `/continue` `/extend [n]` `/develop` `/variation` `/harmonize` | 续写/扩展/发展/变奏/配和声 |
| `/orchestrate [配器]` `/transpose C` `/shorter` `/longer` `/simplify` `/complexify` `/rewrite` | 配器/移调/长短/简繁/重写 |
| `/learn-score chorale` | 把当前谱学进对应**风格包**（直接改 `meta.yaml`） |
| `/analyze` | 生成评估报告（含记谱小节 + 三指标） |
| `/export` | 导出当前乐谱（MusicXML / MIDI） |
| `/demo` | 无 API 时的演示曲 |
| `/help` `/new` `/clear` `/config` | 帮助 / 新曲 / 清空 / 配置 |

### 帮助文档（每个命令、每个功能都带示例）

输入 `/help` 查看**完整命令 + 功能目录**，每个命令和每个功能都附有可复制的示例；
输入 `/help <命令或功能名>` 查看单项详情，例如：

- `/help /chords` —— 查看 `/chords` 命令的语法、说明、示例、效果
- `/help 和弦顾问` —— 查看「和弦进行顾问」功能的说明、相关命令、示例、效果
- `/help 风格插值` —— 查看「风格插值」功能的说明与示例

帮助内容由 `help_doc.py` 单一来源生成，**与 `runs/help.md` 始终一致**（聊天内 `/help` 与该文件同源）。

## 乐谱编辑器（五线谱可视化编辑 + 智能提示 + 实时校验）

走带条「✎ 编辑」进入编辑模式，在渲染的五线谱上直接编辑，类似 AI 代码编辑器的体验：

**编辑操作**
- 工具：选择 / ♪ 音符 / 𝄽 休止 / ⌫ 擦除；点击谱表插入（横坐标精确定位插入点）
- 拖拽音符直接改音高；键盘 `↑↓` 音级移位、`Shift+↑↓` 八度、`1-5` 时值、`r` 休止、`.` 附点、`#`/`b` 变音、`s`/`a`/`t` 跳音/重音/保持音、`Del` 删除、`Ctrl+Z/Y` 撤销重做（60 步快照栈）
- 编辑防抖后自动写回 MusicXML，分析报告与导出保持一致

**智能提示（AI 补全式）**
- 鼠标悬停即出现半透明 ghost 音符 + 候选条：按「导音解决 > 级进 > 主/属和弦琶音 > 重复」排序，每条候选带音乐理由，点击即插入；时值自动钳到小节剩余容量

**三级实时校验（仿 lint）**
- 🔴 硬禁止：小节时值溢出、超出记谱范围——落笔即拒
- 🟠 严格校验（可关）：平行五八度（onset 对齐判定）、超八度跳进——编辑时禁止；存量乐谱只黄色提示，避免误报噪音
- 🟡 警告放行：调外变化音、边缘音域——黄色徽章标注

分层实现：`music_score/editor_core.py`（纯逻辑规则/建议/控制器，无 Qt 依赖）、`editor_ui.py`（工具条/建议条）、`exporter.py`（model→music21）。说明：连线（slur/tie）暂不参与编辑，编辑写回后既有连线会丢失。

## 子系统说明

### 确定性内核 `compose_kernel.py`
- `derive_subseeds`：由主 `seed` 派生 7 维子种子；`rng_for(dim, *parts)` 提供分枝随机源，保证旋律/赋格/叙事各自独立可复现。
- 软权重 `_weighted_choice` / `_rhythm_weights_for`：按属性（uniform/rest/long_start/short_end/dense/sparse）连续调节节奏，而非离散模板。
- `st.fugue_roles` + `spec['fugue_roles']`：赋格角色分档（subject/answer/countersubject/episode/middle-*/stretto/pedal/cadence）。
- 段落级转调：`set_harmony` 可选 `key_sharps`/`mode`，段首记入 `spec['key_changes']`；melody/cadence/平行修正按段落调性取音阶。
- 钢琴大谱表：2 声部 Piano 输出 `parts[].staff` + `spec['staff_groups']`（brace 组合标记）。

### 记谱层 `notation.py`
- `spell_name(tonic_pc=)`：小调导音强制升号，拼写符合音乐性；转调谱按逐小节调号拼写。
- `RuleLog`：分规则计数（spelling / stem / beams / articulation / layout / rehearsal）。
- `spec_to_music21` 经 `makeMeasures` 产出带小节谱，供记谱与评估使用；支持曲中换拍号、段落级转调（`<key><fifths>+<mode>`）、大谱表（`PartStaff` + `StaffGroup` 花括号合并为单 part 双谱表）、显式 tempo 直通（MetronomeMark + 文字标记）。

### 风格路由 `style_routing.py`
- 两级路由：家族（academic/dance/cinematic/intimate/folk/sacred）+ 中文同义词 → 包，置信度归一化。
- `route()` / `route_or_raise()` / `explain()`；`strict=True` 时抛 `StyleRoutingError`（含候选与家族提示），替代静默失败。

### 评估与指纹 `evaluate.py`
- `style_vector`（16 维）→ `pack_fingerprint`（3 个 seed 样本 ⊕ 语料，缓存到 `<pack>/fingerprint.json`）。
- 跨包 z-score 标准化（`Z_CLIP=3.0`）。
- 三指标：`style_overlap`（余弦）、`style_rank`（top1–top3 命中）、`attribute_adherence`、`nearest_neighbor` 阈值 0.995。
- `evaluate_report(spec, ...)` 供 `/analyze` 调用。

### 体系层 `systems.py`
- 四种确定性体系，直接产出 spec（规避功能和声假设）：
  - `banqiang` 板腔体（上下句 · 板式变化，真换拍号）
  - `qupai` 曲牌体（联曲 · 字位填腔）
  - `raga` 拉格（调式 · 塔拉循环，持续音全程发声）
  - `serial` 十二音序列（P/R/I/RI）
- `build_system(kind, **kw)` / `SYSTEM_LABELS` / `list_systems()`。

### 交付 `deliver.py`
- `score_to_lilypond` / `export_lilypond`：自写 `.ly` 文本生成器（本机无 LilyPond 二进制时使用）。
- `render_pdf`：若已安装 LilyPond（`brew install lilypond`）则渲染 PDF，否则提示。
- `archive_run`：把一次生成归档到 `runs/<时间戳>-<曲名>/`（spec.json / score.musicxml / score.mid / score.ly / pdf / report / manifest）。

## Score → Skill（语料学习）

从 MusicXML **学习制作技巧**（非背音符），结果**直接写入风格包**：

```bash
python -m score_learning learn scores/corpus/bach_chorales_10 \
  --family sacred --pack-id chorale --genre 众赞歌 --no-llm
```

产物：`skills/composition/genre/packs/<family>/<id>/{meta.yaml, features/*.json, empirical_statistics.json, EMPIRICAL_SKILL.md}`。
详见 [`score_learning/README.md`](score_learning/README.md) 与 [`skills/composition/README.md`](skills/composition/README.md)。

## 依赖（摘要）

PyQt5、music21、PyYAML，以及播放/合成相关库（见 `requirements.txt`）。播放可走 FluidSynth（若已配置）或系统 GS Wavetable。

## 项目结构（核心）

```
score_studio.py       图形界面（对话创曲 + 谱面编辑 + 风格浏览 + 体系层）
llm_compose.py        对话/命令解析 + 内核编排 + 记谱接入
compose_kernel.py     确定性内核（子种子 / 软权重 / 赋格角色 / 段落转调 / 大谱表）
notation.py           记谱层（拼写 / 规则日志 / 小节化 / 转调配号）
i18n.py               界面中英文切换
music_score/          五线谱编辑器（models / renderer / editor_core / editor_ui / exporter / interaction / audio_engine）
style_routing.py      两级风格路由
style_skeleton.py     风格骨架（profile → 脚本调用序列）
evaluate.py           风格指纹 + 三指标评估
systems.py            体系层（板腔/曲牌/拉格/序列）
deliver.py            LilyPond 导出 + runs/ 归档
narrative.py          叙事型生成
studio_daw.py         风格/体系浏览器组件
skills/composition/   风格包 + 任务 Skill（真相源）
score_learning/       语料 → Skill 学习
tests/                内核/往返/帮助/报告一致性测试 + 编辑器自测
scripts/demos/        播放 / 写谱小示例（play_*、make_g_major_score）
scripts/dev/          一次性维护与重放脚本（不被主程序引用）
scripts/eval/         音色库对比、盲评材料导出、织体检查等评估脚本
docs/                 设计文档、评审材料、调研报告
```

`scripts/` 下的脚本都从仓库根目录解析路径，可在任意目录直接运行，例如
`venv/bin/python scripts/eval/check_texture.py --ab`。

RAS-FR（四声部声部进行约束的统计发现方法）的代码与结果数据已独立发布在
[github.com/tyrone1979/ras](https://github.com/tyrone1979/ras)，本仓库不再包含
`score_learning/induction`、`diagnostics`、`dcra` 及其测试。

### 测试

```bash
python -m pytest tests/ -q                              # 全量回归
python tests/test_prompt_to_report.py                   # 提示词→乐谱→分析报告 三方一致性（65 断言）
python tests/score_editor_selftest.py                   # 编辑器规则/建议/控制器/往返（54 断言）
```

`test_prompt_to_report.py` 另设 `COMPOSE_E2E_LLM=1` 开关可跑真实 LLM 管线做同样的遵循性断言（默认跳过，不依赖 API）。

## 竞品定位（简述）

- **AIVA**（商业形态最接近）：对标它的 DAW 内编辑——我们已落地五线谱可视化编辑 + 智能提示 + 实时校验，并以确定性 + 可审计性作为反差优势。
- **Magenta Studio**（功能清单最接近）：补它的 Groove（微时值）与 Interpolate（动机渐变）。
- **NotaGen**（学术水平最接近）：学它的风格保真度优化（规则校验 + 风格匹配度双目标）。
- **Suno / Udio 等**：输出音频波形而非乐谱，属不同赛道，不构成竞争。

完整对比见 `docs/对比分析_符号音乐调研报告.md`。

## 许可证

本项目以 [GNU General Public License v3.0](LICENSE)（GPL-3.0-only）发布。界面依赖的 PyQt5
采用 GPL v3 授权，因此整个应用随之采用 GPL-3.0。

第三方语料（CCARH MuseData、McGill Billboard、Nottingham、Weimar Jazz Database 等）与
SoundFont 音色**不随仓库分发**，由脚本按需下载，各自遵循原许可；仓库内仅附 10 首公有领域的
巴赫众赞歌。来源与许可明细见 [`PROVENANCE.md`](PROVENANCE.md)。
