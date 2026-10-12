# 作曲分析报告：早春交响快板（编辑者版）

> 面向音乐编辑者：核对谱面与作曲原则即可。生产过程、大模型输入输出与脚本链路见《作曲生产报告（程序员版）》：`82e1e9a885_report_dev.md`。

## 1. 要核对的作曲原则

1. **调内音高**：各音应落在该调号自然音阶内（调号升降号已计入；小调和声导音亦计入）。
2. **节奏完整**：每个声部每一小节时值之和应等于拍号。
3. **声部进行**：外声部（小提琴对大提琴）同向平行纯五度需避免；管弦乐八度加强不计入错误。
4. **声部交叉**：功能相邻声部不宜明显越过（木管互叠不计）。
5. **音域**：各声部应落在该乐器常用音域内。
6. **旋律**：器乐旋律允许较大跳进与较复杂节奏；极端无序跳进再提示。
7. **终止**：结束处最好含主音/主三和弦，旋律落在主音上更有完全终止感。

## 2. 基本信息

- 调性: D 大调（2 个升号）
- 转调: 第 16 小节 → B 小调
- 拍号: 4/4
- 速度: 四分音符 = 92 BPM
- 段落速度: 第 5 小节 Allegro（♩=112）；第 11 小节 Allegro（♩=120）；第 16 小节 Agitato（♩=126）；第 24 小节 Tempo I（♩=112）；第 29 小节 Maestoso（♩=100）
- 调内音级: C#, D, E, F#, G, A, B
- 声部 1: 长笛 (Flute)，高音谱号，32 小节
- 声部 2: 双簧管 (Oboe)，高音谱号，32 小节
- 声部 3: Clarinet，高音谱号，32 小节
- 声部 4: Bassoon，低音谱号，32 小节
- 声部 5: 圆号 (Horn)，高音谱号，32 小节
- 声部 6: 小号 (Trumpet)，高音谱号，32 小节
- 声部 7: 定音鼓 (Timpani)，低音谱号，32 小节
- 声部 8: 小提琴 (Violin)，高音谱号，32 小节
- 声部 9: 中提琴 (Viola)，ALTO，32 小节
- 声部 10: 大提琴 (Cello)，低音谱号，32 小节
- 声部 11: Contrabass，低音谱号，32 小节

## 3. 需关注的问题与提示

| 检查类别 | 状态 | 描述 |
|---|---|---|
| 声部进行 | ⚠️ 注意 | 声部6/4 在第 119.5→120.0 拍 平行五度（D5->A4 对 G3->D3）（管弦织体常见，可接受） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 3（Clarinet）与下方声部 1（长笛 (Flute)）在第 16.0 拍交叉：D5 低于 F#5（同类交叉共 114 处，此处为第 1 例） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 9（中提琴 (Viola)）与下方声部 6（小号 (Trumpet)）在第 2.0 拍交叉：D4 低于 D5（同类交叉共 82 处，此处为第 1 例） |
| 声部交叉 | ⚠️ 注意 | 纵向上方声部 6（小号 (Trumpet)）与下方声部 4（Bassoon）在第 17.5 拍交叉：D4 低于 F#4（同类交叉共 18 处，此处为第 1 例） |
| 音域 | ⚠️ 注意 | 长笛 (Flute): D4–E7（MIDI 62–100） |

> 说明：平行五/八度仅检查最高与最低外声部；相邻声部交叉为提示项。 共检查 372 项（✅ 367 / ⚠️ 5 / ❌ 0）。

## 4. 记谱与演奏法

**规则日志**（共 106 条，其中仲裁 0 次）

- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] accent — 挂 Accent：叙事/风格标记 accent
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [spelling/correctness] p2m18 B- — → A#：导音：主音下方半音按功能拼升号
- [spelling/correctness] p2m18 B- — → A#：导音：主音下方半音按功能拼升号
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- [articulation/style] tenuto — 挂 Tenuto：叙事/风格标记 tenuto
- …另有 94 条

可演奏性：6 项 MAJOR / 118 项总计
- [MAJOR] 声部1（Flute）音 98 超出音域 60–96
- [MAJOR] 声部1（Flute）音 97 超出音域 60–96
- [MAJOR] 声部1（Flute）音 97 超出音域 60–96
- [MAJOR] 声部1（Flute）音 98 超出音域 60–96
- [MAJOR] 声部1（Flute）音 100 超出音域 60–96
- [MAJOR] 声部1（Flute）音 98 超出音域 60–96
- [MINOR] 声部2 大跳 20 半音（>八度）
- [MINOR] 声部2 大跳 20 半音（>八度）
- …其余 110 项从略

## 5. 生成质量评估（三指标）

- **风格重叠率**：N/A（未指定目标包）
- **属性遵循度**：1.00（0 项）
- **最近邻**：symphony（相似度 0.47）— ✅ 无抄袭嫌疑
