# compose_kernel.py 确定性作曲内核：脚本算法说明

> **摘要** —— compose_kernel 是「LLM 翻译器 + 确定性内核」架构中的确定性执行层。LLM 不写任何音符，只输出脚本调用序列；内核以受限的参数空间 + 硬不变量（每小节时值恒等、调性侧拼写、平行五八度规避）生成总谱，任何违约抛出 `KernelError` 回喂 LLM 重试。本文档给出全部 **8 个确认脚本**（deterministic scripts）的输入/输出、前置条件、算法与公式，作为 kernel_docs() 之外的数学化描述。实现文件：`compose_kernel.py`（约 1000 行）。

---

## 1. 系统架构与执行模型

```
用户提示词 → LLM 翻译器（只做参数决策） → {"calls":[{script, args},…]}
            → run_program() 顺序执行 8 脚本 → score_spec → MusicXML/MIDI/WAV
                     ↑ KernelError（违约信息回喂，重试 1 次）
```

执行器不变量（`put_bar`，对所有写小节的脚本生效）：

$$\sum_{e \in \text{bar}} \text{dur}(e) = \text{cap}(\text{bar}) \tag{1.1}$$

其中 cap 为该小节拍号容量（4/4 → 4.0，3/4 → 3.0，2/4 → 2.0 拍）。违约即抛错，**不可能产出时值缺口或超写**。

状态机 `_State` 持有：调号 `sharps`、调式、拍号、总小节数、段落表 `sections`、逐小节和声 `harmony[bar]`、双声部事件 `parts[0/1]['bars'][bar]`。

---

## 2. 八个确认脚本总览

| # | 脚本 | 职责 | 关键确定性机制 |
|---|------|------|----------------|
| 1 | `new_score` | 初始化空白总谱 | 参数域硬校验 |
| 2 | `set_sections` | 划分段落（结构/速度/力度/拍号） | BPM 平滑钳制、力度阶梯去重 |
| 3 | `set_harmony` | 段落和声（罗马数字） | 循环指派 + 罗马数字解析表 |
| 4 | `accompaniment` | 左手伴奏（7 种织体） | 时值模板 + 连续低音线插值 |
| 5 | `melody` | 右手旋律 | 锚点式两遍算法（§6） |
| 6 | `thicken` | 高潮段八度加厚 | 同拼写上移纯八度 |
| 7 | `cadence` | 终止式（V→I 长音） | 主/属音替换末 1–2 小节 |
| 8 | `finalize` | 汇总输出 spec | 完整性断言 + 平行五八度修正 |

调用顺序模板：`new_score → set_sections → 每段{ set_harmony → accompaniment → melody } →（高潮）thicken →（末段）cadence → finalize`。

---

## 3. `new_score` —— 总谱初始化

**输入**：title、key_sharps、mode、meter、tempo、bars。
**前置**：必须是第一个调用且只调一次。

参数域校验（越界抛 `KernelError`）：

$$k = \text{key\_sharps} \in [-7,7],\quad \frac{4\,\text{num}}{\text{den}} \in \{2,3,4\},\quad \sigma = \text{tempo} \in [30,240],\quad N = \text{bars} \in [8,64] \tag{3.1}$$

拍号约束 (3.1)₂ 保证全曲只出现 2/4、3/4、4/4，与节奏模板表（§7）的容量键一一对应。

---

## 4. `set_sections` —— 段落划分与宏观曲线

**输入**：sections 列表，每项 `{name, bars, meter?, dynamic, tempo_text?, bpm?}`。
**前置**：各段名唯一、各段小节之和 = N。

### 4.1 速度平滑（相邻段 BPM 钳制）

设第 $i$ 段请求速度为 $b_i$（缺省继承前段），有效速度 $e_i$ 递推：

$$e_0 = \sigma,\qquad e_i = \operatorname{clamp}\big(b_i,\ \lfloor 0.70\, e_{i-1}\rceil,\ \lfloor 1.25\, e_{i-1}\rceil\big) \in [30,240] \tag{4.1}$$

**意义**：相邻段速度变化被限制在 ±25%/±30% 内，「略微加快」不会被翻译成 76→126 的翻倍跳变；多段递进仍可达远端目标。速度标记 tempo_marks = 段落起点处 $(\text{bar}, \text{text}, e_i)$。

### 4.2 力度阶梯与拍号变化

- 力度：$D = [(1, v_1), (s_2, v_2), \dots]$，仅保留相邻变化项（去重）；
- 拍号变化：$\text{meter\_changes} = \{s_i : \text{meter}_i \neq \text{meter}_{i-1}\}$；
- 若第一段无速度标记，自动补 $(1, ♩=\sigma, \sigma)$。

---

## 5. `set_harmony` / `accompaniment` —— 和声与伴奏织体

### 5.1 `set_harmony`：循环指派

设段落和声进行为罗马数字列表 $P = [p_0, \dots, p_{L-1}]$：

$$H(\text{bar}) = P\big[(\text{bar} - s) \bmod L\big] \tag{5.1}$$

罗马数字解析 `parse_roman`：级数（含那不勒斯 bII、导七 vii°7）→ 根音音级；后缀 ∈ {∅, 7, °, °7, ø7, maj7} → 三度叠置表。例：Db 大调下 `bII → [D, Gb, A]`、`vii°7 → [C, Eb, Gb, A]`。

### 5.2 `accompaniment`：7 种织体的时值模板

记根音 r、三音 t、五音 f（+1 八度记 r' t' f'），小节容量 cap，密度 ρ ∈ {0.5, 1, 2}。所有模板满足不变量 (1.1)。

| 织体 | 音序模板（时值） | 用途 |
|------|------------------|------|
| `sustained` | cap=4: [r/2, f/2]；cap=3: [r/2, f/1] | 安静段长音 |
| `block_chords` | [r+t+f'/2]×2（柱式三和弦） | 支撑性和声 |
| `broken_arpeggio` | ρ=1: [r, f, r', f']/4 分音符；ρ=2: [r, t, f', r', f', t, r, t]/8 分音符截断至 2·cap | 前奏/收束琶音 |
| `alberti` | [r, f', t, f']/4 分音符 | 古典阿尔贝蒂低音 |
| `octave_pulse` | h = ⌊cap·ρ⌋ 个等长八度对 [r, r']，时长 cap/h | 冲突段脉冲 |
| `chromatic_descent` | 见公式 (5.2)–(5.4) | 冲突段半音下行低音 |
| `waltz` | cap=3: [r/1, t+f'/1, t+f'/1] | 圆舞曲 |

**chromatic_descent 连续低音线**（跨小节锚定，音区钳制）：

- 音区窗口：$[C_o,\ C_{o+1}]$，o 为低音八度；
- 小节起点（上一个落点 $p$ 向当前和弦根音的最近音，限 ±6 半音内折叠）：

$$m_{\text{start}} = \operatorname{fold}_{[p-6,\,p+6]}\big(\operatorname{nearest}(p,\ \text{pc}(r))\big),\quad \text{再折入音区窗口} \tag{5.2}$$

- 小节终点（向**下一小节**根音同理）：$m_{\text{tgt}}$；
- 比例走步插值（h = 每小节击数）：

$$m_i = m_{\text{start}} + \operatorname{round}\!\left(\frac{(m_{\text{tgt}} - m_{\text{start}})\, i}{h - 1}\right),\quad i = 0,\dots,h-1 \tag{5.3}$$

- 若前后和弦同根（$m_{\text{tgt}} = m_{\text{start}}$）：根音/五音交替 $m_i = m_{\text{start}}\ (i\text{ 偶}) \mid m_5\ (i\text{ 奇})$ 保持律动；
- 段落锚点跨小节传递：$p \leftarrow m_{h-1}$。

**调性侧拼写**（`_key_name`）：midi → 音名按优先级 prefer（和弦音）→ 调内音 → 调号侧表（降号调用 Db/Eb/Ab，永不出 C#/D-）。

---

## 6. `melody` —— 锚点式两遍旋律算法（本次重写的核心）

**输入**：register ∈ {low 62, mid 71, high 79, very_high 84}（音区中心 c）、rhythm ∈ {sparse, flowing, fragmented, driving}、contour、motif（可选）。
**输出**：逐小节旋律事件（音高/时值/连音线标记）。

### 6.1 乐句曲线（音高目标）

段落按 4 小节一句分句，第 $i$ 句第 $b$ 小节的旋律目标：

$$\tau(\text{bar}) = c + \Gamma\big[(\lfloor i/4 \rfloor + b) \bmod |\Gamma|\big],\quad \Gamma \in \begin{cases} [0,2,4,1,0,2,4,2] & \text{arch}\\ [0,3,0,-3,0,3,0,-3] & \text{wave}\\ [5,4,3,2,1,0,-1,-2] & \text{descending}\\ [-4,-2,0,2,4,6,8,10] & \text{ascending} \end{cases} \tag{6.1}$$

音区窗口 $W = [c-9,\ c+10]$。旋律状态 prev/back1 跨小节延续（休止则清零重起）。

### 6.2 节奏槽与强弱拍

每小节从模板表抽取时值槽 $S = [s_0, \dots, s_{n-1}]$（模板之和恒等于 cap，含 0.5 拍休止位）。**强拍槽**：$s_i \ge 1.0$；其余为弱拍。

### 6.3 第一遍：强拍锚点选择（硬性分层过滤）

对每个强拍槽依序选锚点 $m$（和弦音，窗口内）。无前一锚点时取最接近 $\tau$ 的和弦音；否则：

$$\mathcal{C}_1 = \{m : m \neq m_{\text{prev}},\ m \neq m_{\text{back}},\ |m - m_{\text{prev}}| \le 7\} \tag{6.2}$$

$$\mathcal{C}_2 = \{m : m \neq m_{\text{prev}}\},\qquad \text{pool} = \mathcal{C}_1 \lor \mathcal{C}_2 \lor \mathcal{C}_\text{all} \tag{6.3}$$

（$\mathcal{C}_1$ 非空必用之：**禁重复、禁回头、禁 > 7 半音大跳**；窄和弦退层放宽。）在 pool 内按代价最小选：

$$J(m) = 0.6\,|m - m_{\text{prev}}| + |m - \tau| + 4\,\mathbb{1}[\tau - m_{\text{prev}} > 2 \wedge m < m_{\text{prev}}] + 4\,\mathbb{1}[m_{\text{prev}} - \tau > 2 \wedge m > m_{\text{prev}}] \tag{6.4}$$

第一项 = 声部平滑度，第二项 = 曲线跟踪，三/四项 = 方向罚（目标在上别倒退、在下别上冲）。

### 6.4 第二遍：弱拍作为「朝下一锚点级进的经过音」

设 cur 为上一实际发音、$a$ 为本小节内下一个锚点（`next_a` 反向扫描预计算）：

$$m = \operatorname{scale\_step}\big(\text{cur},\ \operatorname{sgn}(a - \text{cur})\big),\qquad \text{若 } (m - a)\operatorname{sgn}(a-\text{cur}) > 0 \Rightarrow m \leftarrow a \tag{6.5}$$

即调内音阶级进朝锚点走，走过头则直接落在锚点上（半音到达）。`scale_step` 距离平局时**偏向行进方向一侧**（防止返回原音触发钟摆）。无后续锚点的末尾弱拍朝 $\tau$ 级进。

**同音持续封顶**：连续相同音高至多 2 次，

$$m = \text{cur} \wedge \text{run} \ge 2 \Rightarrow m \leftarrow \operatorname{scale\_step}(\text{cur}, \operatorname{sgn}(\tau - \text{cur})) \tag{6.6}$$

### 6.5 动机小节（乐句首小节）

若给定 motif（级数-时值对 $[(d_j, \delta_j)]$），按音阶级数映射到窗口内音阶，整句做二度模进（位移 $\delta \in \{0, +1, 0, -1\}$ 按句循环）；剩余时值由 `_fill_continuation` 用 (6.4) 的变体（权重 0.5/1.0，重复罚 2.5）补和弦音。末段末小节末音强制吸附主音（终止预备）；句首/句末加 slur 标记。

---

## 7. 共享子程序与数据表

| 子程序/表 | 公式/定义 | 用途 |
|-----------|-----------|------|
| `_nearest_pc(t, p, lo, hi)` | $p + 12\lfloor (t-p)/12 \rceil$，折叠入 [lo,hi] | 最近同名音 |
| `_scale_step_to(prev, d, W, Σ)` | $\arg\min_{m \in \Sigma \cap W} |m - (prev+d)|$，平局偏向 d 侧 | 调内级进 |
| `_RHYTHMS` | (风格, 容量) → 模板表，模块导入时自检 Σdur = cap | 节奏 |
| `REGISTER_CENTERS` | {low:62, mid:71, high:79, very_high:84} | 音区 |
| `_key_name` | prefer → 调内 → 调号侧表 | 调性侧拼写 |
| `_rng(*key)` | 以标题/段落名为种的确定性随机 | 可复现性 |

---

## 8. `thicken` —— 高潮八度加厚

对段落内高音声部每个单音事件 $e$（音名 $n$，八度 $o$）：

$$e.\text{pitch} \leftarrow [\,n^o,\ n^{o+1}\,] \tag{8.1}$$

同名上移纯八度、**保持拼写**（不经 music21 transpose，避免等音重拼 A♭→G#）。每小节首音加 accent（无既有标记时）。无可加厚音符则抛错（防 LLM 漏调 melody）。

---

## 9. `cadence` —— 终止式

只允许末段。替换末 $n \in \{1,2\}$ 小节（n=2 时倒数第二小节用属音 V、末小节用主音 I）：

- LH：根音长音 $r^2$，时值由贪心分解 $\text{cap} \to [4,2,1,0.5]$ 逐级拆分；
- RH：主音双八度 $[t^4, t^5]$ 同拆。

产生「主和弦长音 + 主音旋律位」的完全终止感，覆盖此前任何内容（force=True）。

---

## 10. `finalize` —— 汇总与平行五八度修正

### 10.1 完整性断言

每个声部 × 每个小节必须有事件（否则提示翻译器漏调 melody/accompaniment）。若未调过 `cadence`，自动补（末段终止是硬保证）。

### 10.2 外声部平行纯五/纯八度规避（`_fix_parallel_outer`）

对 RH 逐事件与 LH 同时刻低音配对，区间类 $iv = (top - bot) \bmod 12$。连续两对满足：

$$iv = iv_{\text{prev}} \in \{0, 7\} \ \wedge\ (top - top_{\text{prev}})(bot - bot_{\text{prev}}) > 0 \tag{10.1}$$

（同种纯音程 + 同向进行 = 平行）时，替换旋律音为**所有和弦音中离原音最近、且与当前低音不构成新纯五/纯八度**者：

$$m^* = \arg\min_{m \in \text{chord} \cap [55,96],\ (m - bot) \bmod 12 \notin \{0,7\}} |m - top| \tag{10.2}$$

八度对织体保持双音结构。早期版本固定换成和弦三音，会在窄旋律位上砸出 ±8 半音大跳；(10.2) 保证了「修平行不伤线条」。

### 10.3 输出 spec

`{title, key_sharps, mode, time_signature, tempo, bars, tempo_marks, meter_changes, parts:[{name, clef, events(扁平), dynamics(阶梯)}]}`，交由下游 `spec_to_music21` 渲染、`_apply_velocities_to_midi` 做力度后处理。

---

## 11. 已知限制

1. 踏板（pedal）、hairpin（渐强渐弱记号）、曲中转调（调号变化）暂不支持；
2. 平行修正为顺序贪心（"尽力而为"），极端织体下可能残留相邻声部平行；
3. 拍号仅支持 2/4、3/4、4/4（与节奏模板容量一一对应）；
4. `chromatic_descent` 的半音线在密度 1 时为四分音符粒度。

## 12. 回归验证

- `_replay_check.py`：无 LLM 的确定性重放（复现典型调用序列），供内核改动后快速回归；
- `e2e_user_prompt_test.py`：用户原提示词 → LLM → 内核 → XML/MID/WAV/报告 → 注册聊天列表的端到端测试；
- `_verify_templates()`：模块导入时自检所有节奏模板时值恒等 (1.1)。
