# 四部和声声部进行基础

综合 [Integrated Music Theory — Fundamentals of Part-writing](https://intmus.github.io/inttheory/11-further-part-writing/a1-funds-of-part-writing.html) 与 [RWU Composing Music — SATB](https://rwu.pressbooks.pub/musictheory/chapter/spacing-voicing-and-doubling-of-chord-tones-in-satb-style/)。

## 音域（约）

| 声部 | 常用音域 |
|------|----------|
| Soprano | C4–G5 |
| Alto | G3–D5 |
| Tenor | C3–G4 |
| Bass | E2–C4 |

## 间距与排列

- 相邻上三声部间距通常 ≤ 八度；低音与次中音可更宽。
- 密集/开放排列均可，但避免声部交叉与不合理重叠。
- **倍音**：三和弦常倍根音；慎倍导音；属七常全现或省五度。

## 运动原则

1. 声部内部以**级进**为主；大跳后宜反向级进「回收」。
2. 共同音尽量保留在同一声部。
3. 倾向音：导音↑主（尤其外声部）；七度音级进解决。
4. **禁止**外声部（及教学众赞中任意两声部）平行纯五 / 纯八。
5. 反向与斜向优先于同向。

## 本系统

- `music_score/satb_piano.py`：SATB→钢琴大谱表折叠。
- 众赞/圣咏风格包与编辑器平行检测共用平行规则。
- 提示词写「四部和声/众赞」时优先本规则；写「对位」时优先第一类对位规则。
