# 小节关系 → 规则脚本 → `/compose-grammar` 接入说明

> 回答「如何用关系提取的结果生成脚本，让 `/compose-grammar` 用上」
> 日期：2026-10-04

---

## 一句话回答

关系提取的结果**不能直接**喂给 `/compose-grammar` —— 中间缺一个**编译环节**。
已建好三段流水线，跑三条命令即可接通：

```bash
# 1. 从人工标注提取小节关系（456 首 / 35412 小节 / 8802 条边）
venv/bin/python -m score_learning.relation.extract --limit 1000

# 2. 编译成 grammar.yaml 能吃的 form_plan
venv/bin/python -m score_learning.relation.compile_form_plan

# 3. 注入风格包（自动备份 .bak）
venv/bin/python -m score_learning.relation.inject_form_plan
```

已注入 6 个子风格：`jazz/swing-standard` 的 swing / bebop / cool / hardbop / postbop / traditional。

---

## 一、为什么需要编译这一步

`/compose-grammar` 的调用链：

```
llm_compose._compose_grammar_cmd
  → compose_kernel._s_leadsheet_grammar        (L4293)
      → lscript.load(style)                    读 grammar.yaml
      → ls_compose(...)                        score_learning/leadsheet/compose.py
          → plan_form(g, rng, total_bars, layout)    L206← 唯一的曲式规划入口
```

**`plan_form` 只从 `script['subs'][sub]['form']` 取曲式信息**，而改造前那里只有：

```yaml
form:
  templates: [['AABA', 0.4286, 24], ['AB', 0.1607, 9], ...]   # 只是一串字母
  units: [[4, 0.7321, 41], ...]
  openings: {...}
```

于是 `plan_form` 只能从 `'AABA'` 里**挑一个字符串**，完全不知道：

| 缺失信息 | 后果 |
|---|---|
| 每段多长 | 恒定 8 小节（`UNIT`），真实语料是 6/8/10/12/13 各种 |
| `A2` 是 `A1` 的**引用**还是**变体** | 无法表达材料回归 |
| 哪段是引入、哪段是尾声 | 所有段地位平等|
| 段间和声配置差异 | `contrast` 关系丢失 |

关系提取结果里的这些信息是**人工标注的**，所以值得接进去。

---

## 二、编译产物：`form.form_plan`

```yaml
form:
  form_plan:
    unit_bars: 8
    source: Weimar Jazz 人工段落标注 / SWING
    policies:
      '4':                       # 按段数索引
        - form: A1A2B1A3        # 注意是带编号的段落序列，不是 AABA
          p: 0.8
          n: 4
          n_variants: 4
          segments:              # ★ 逐段细节，这是新增能力
            - {label: A1, family: A, index: 1, role: introduce, role_zh: 引入,
               bars: 10, ref: null, idea: 主题引用(t3-4)}
            - {label: A2, family: A, index: 2, role: sequence, role_zh: 递进变体,
               bars: 10, ref: A1, idea: 旋律陈述}# ← ref 指向被引用的段
            - {label: B1, family: B, index: 1, role: introduce, role_zh: 引入,
               bars: 8,  ref: null, idea: 旋律陈述}
            - {label: A3, family: A, index: 3, role: coda, role_zh: 尾声,
               bars: 13, ref: A1, idea: 旋律陈述}
          relations:             # 段间关系
            - {rel: sequence,  from: A1, to: A2, zh: 递进}
            - {rel: contrast,  from: A2, to: B1, zh: 对比}
            - {rel: contrast,  from: B1, to: A3, zh: 对比}
    segment_len: {'4': {'8': 10, '10': 2, '13': 1, ...}}   # 段落长度分布
    role_dist: {introduce: 134, sequence: 84, quote: 62, coda: 28}
    idea_by_role:               # 各角色的典型即兴手法
      introduce: {即兴乐句: 289, Bebop 线: 196, 旋律陈述: 75}
      quote:     {Bebop 线: 269, 即兴乐句: 257, ...}
      sequence:  {Bebop 线: 133, 即兴乐句: 122, ...}
```

段落角色由关系类型推导（`compile_form_plan._seg_role`）：

| 角色 | 判定 | 含义 |
|---|---|---|
| `introduce` | 该字母族首次出现 | 引入段 |
| `sequence` | 同族序号递增（A1→A2） | 递进变体 |
| `quote` | 同族材料再现 | 引用 |
| `coda` | 末段且序号>1 | 尾声 |

---

## 三、`plan_form` 的改动

`score_learning/leadsheet/compose.py` 加了 `_relation_form()`（L211），
`plan_form` 优先读 `form_plan`，读不到就**原路回退**（完全向后兼容）：

```python
def plan_form(g, rng, total_bars, layout):
    n_units = max(1, min(12, int(round(total_bars / UNIT))))   # 上限 4→12
    rel = _relation_form(g, n_units, rng, total_bars)
    if rel is not None:
        ...弱起处理...
        return rel
    # ↓ 原有 templates 逻辑，一行未改
    cands = [(t, p) for t, p, _n in form['templates'] if len(t) == n_units]
```

**实测效果**（`compose(sc,'swing','major',0,32)`）：

```
plan.form      = I1A1B1A1          ← 改造前是 'AABA'（抽象字母）
plan.source    = form_plan
plan.form_rule = 人工段落标注的 4 段曲式（I1(引入,1小节) → A1(引入,15小节)
                 → B1(引入,15小节) → A1(引用,1小节)）；段间关系 3 条
violations     = 0
生成耗时        = 0.4s（1 次尝试即零违例）
```

---

## 四、改动清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `score_learning/relation/extract.py` | 新增 | 从 `wjazzd.db` 提取小节关系 |
| `score_learning/relation/compile_form_plan.py` | 新增 | 关系 → `form_plan` 编译器 |
| `score_learning/relation/inject_form_plan.py` | 新增 | 写进 grammar.yaml（带 .bak） |
| `score_learning/leadsheet/compose.py` |改 | 加 `_relation_form()`；`plan_form` 优先读它；段数上限 4→12 |
| `tests/test_form_plan_integration.py` | 新增 | 7 项回归测试 |
| `skills/.../jazz/swing-standard/grammar.yaml` | 改 | 6 个 sub 注入 `form_plan`（含 .bak） |

**`compose_kernel.py` 与 `llm_compose.py` 完全没动** ——
`_s_leadsheet_grammar` 只`lscript.load(style)` 一次就自动获得新能力。
这是把信息写进 yaml 而非改调用链的好处。

---

## 五、三个实测踩过的坑

### 1. 段数键在 `policies` 之下（静默失效）

最初写成 `plan.get(str(n_units))`，而段数键实际在 `plan['policies']` 里。
`form_plan`明明加载成功、`_relation_form` 却**静默返回 None**，
生成结果还是老样子（`source: None`）。已用测试钉死：

```python
pol = (plan.get('policies') or {}).get(str(n_units)) or \
      (plan.get('policies') or {}).get(n_units)
```

### 2. 人工段长 ≠ 请求小节数

标注里段落是 10/10/8/13 小节（合计 41），而请求 32 小节。
直接用会让 `plan['bars']=41` 与请求不符。
现按比例缩放并修正取整误差，实测 32/40/48/64/96 小节全部精确吻合。

### 3. 自引用

同名标签在多段中重复出现（`A1` 既是首次出现也是后续段的标签名），
填 `ref` 时按标签名匹配会得到 `A1(引入←A1)`。已加 `if src == tgt: continue`。

另外修了一个原有上限：`n_units = min(4, ...)` 会把 9-12 段的长曲式
（Weimar postbop 常见）一律截成 4 段，已放宽到 12。

---

## 六、验证

```bash
# 新增 16 项测试
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest \
    tests/test_form_plan_integration.py tests/test_relation_extract.py -q
# → 16 passed

# 端到端
venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from score_learning.leadsheet import script as lscript
from score_learning.leadsheet.compose import compose
r = compose(lscript.load('jazz'),'swing','major',0,32,seed='x',layout='downbeat')
print(r['plan']['source'], r['plan']['form'], len(r['violations']))"
# → form_plan I1A1B1A1 0
```

各子风格段数覆盖（`policies` 的键）：
swing 8 种、cool 9 种、hardbop 9 种、postbop 9 种、bebop 7 种、traditional 5 种。
16/24 小节（2-3 段）无对应 policy，会自动回退到 `templates` 老逻辑。

---

## 七、下一步（尚未做）

1. **`_compose_once` 还没用 `segments` 的 `idea` 字段** ——
   知道「这段是 Bebop 线」但还没约束旋律生成。接上后可用
   `idea_by_role` 约束每段的旋律走向。
2. **`ref` 还没落到旋律材料上** ——
   知道 `A2` 引用 `A1`，但生成时 `A2` 仍是独立写 melody，
   没有真的复用 `A1` 的动机。这是「引用」关系真正生效的关键一步。
3. **终止式没按角色分配** —— `cause` 关系标了「段落终止→下一段起始」，
   但还没在 `coda` 段强制用 PAC 终止。
4. **非爵士体裁没有 form_plan** —— Weimar 只覆盖 8 种爵士体裁，
   民谣/流行/古典的包仍走 `templates`。

---

## 八、回滚

```bash
# 恢复 grammar.yaml
cp skills/composition/genre/packs/jazz/swing-standard/grammar.yaml.bak \
   skills/composition/genre/packs/jazz/swing-standard/grammar.yaml
```

`plan_form` 的改动是「优先读 form_plan，读不到回退」，
删掉 `form_plan` 字段即自动恢复原行为，无需回滚代码。
