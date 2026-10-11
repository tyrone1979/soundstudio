# 作曲分析报告：After Hours Alone（编辑者版）

> 面向音乐编辑者：核对谱面与作曲原则即可。生产过程、大模型输入输出与脚本链路见《作曲生产报告（程序员版）》：`fac169a7e4_report_dev.md`。

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
- 段落速度: 第 5 小节 Adagio（♩=66）
- 调内音级: C, D, E, F, G, A, B
- 声部 1: 小号 (Trumpet)，高音谱号，8 小节
- 声部 2: Piano，高音谱号，8 小节
- 声部 3: Contrabass，低音谱号，8 小节
- 声部 4: Piano，高音谱号，8 小节

## 3. 需关注的问题与提示

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 调号一致性 | ⚠️ 注意 | C 大调下出现变化音 Db, G#, Bb（经过音/离调） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 1（小号 (Trumpet)）与下方声部 2（Piano）在第 22.0 拍交叉：B4 低于 F5 |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 3（Contrabass）与下方声部 4（Piano）在第 0.5 拍交叉：A2 低于 E3（同类交叉共 17 处，此处为第 1 例） |
| 音域 | ⚠️ 注意 | 小号 (Trumpet): A4–G6（MIDI 69–91） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 41 项（✅ 37 / ⚠️ 4 / ❌ 0）。

## 4. 记谱与演奏法

**规则日志**（共 21 条，其中仲裁 0 次）

- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [spelling/correctness] p1m4 B- — → Bb：调号 0 下变音记号更少
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [spelling/correctness] p2m7 C# — → Db：调号 0 下变音记号更少
- [spelling/correctness] p2m8 C# — → Db：调号 0 下变音记号更少
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- …另有 9 条

可演奏性：1 项 MAJOR / 12 项总计
- [MAJOR] 声部1（Trumpet）音 91 超出音域 55–88
- [MINOR] 声部1 大跳 14 半音（>八度）
- [MINOR] 声部2 大跳 17 半音（>八度）
- [MINOR] 声部2 大跳 17 半音（>八度）
- [MINOR] 声部3 大跳 15 半音（>八度）
- [MINOR] 声部3 大跳 17 半音（>八度）
- [MINOR] 声部4 和弦跨度 19 > 八度，单手/单声部不易演奏
- [MINOR] 声部4 和弦跨度 19 > 八度，单手/单声部不易演奏
- …其余 4 项从略

## 5. 生成质量评估（三指标）

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **最近邻**：romantic-theme（相似度 0.59）— ✅ 无抄袭嫌疑
