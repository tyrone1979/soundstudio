# 作曲分析报告：Symphonic Poem: The Lone Contrabass（编辑者版）

> 面向音乐编辑者：核对谱面与作曲原则即可。生产过程、大模型输入输出与脚本链路见《作曲生产报告（程序员版）》：`a42be432f6_report_dev.md`。

## 1. 要核对的作曲原则

1. **调内音高**：各音应落在该调号自然音阶内（调号升降号已计入；小调和声导音亦计入）。
2. **节奏完整**：每个声部每一小节时值之和应等于拍号。
3. **声部进行**：外声部（小提琴对大提琴）同向平行纯五度需避免；管弦乐八度加强不计入错误。
4. **声部交叉**：功能相邻声部不宜明显越过（木管互叠不计）。
5. **音域**：各声部应落在该乐器常用音域内。
6. **旋律**：器乐旋律允许较大跳进与较复杂节奏；极端无序跳进再提示。
7. **终止**：结束处最好含主音/主三和弦，旋律落在主音上更有完全终止感。

## 2. 基本信息

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

## 3. 需关注的问题与提示

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 调号一致性 | ⚠️ 注意 | C 大调下出现变化音 C, D, E, F, F#, G, G#, A, B（经过音/离调） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 4（小提琴 (Violin)）与下方声部 3（Clarinet）在第 2.0 拍交叉：C5 低于 E5（同类交叉共 13 处，此处为第 1 例） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 3（Clarinet）与下方声部 5（中提琴 (Viola)）在第 22.0 拍交叉：D4 低于 F4 |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 5（中提琴 (Viola)）与下方声部 1（Contrabass）在第 20.0 拍交叉：C4 低于 G4（同类交叉共 3 处，此处为第 1 例） |
| 音域 | ⚠️ 注意 | Contrabass: C3–A4（MIDI 48–69） |
| 音域 | ⚠️ 注意 | 定音鼓 (Timpani): C2–G4（MIDI 36–67） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 80 项（✅ 74 / ⚠️ 6 / ❌ 0）。

## 4. 记谱与演奏法

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

## 5. 生成质量评估（三指标）

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **最近邻**：ballade-piano（相似度 0.64）— ✅ 无抄袭嫌疑
