# -*- coding: utf-8 -*-
"""作曲过程透明化：测试用例。

不依赖真实大模型——用假的 chat_completion 模拟「翻译器输出 4/4 圆舞曲（错误）」
与「输出 3/4（正确）」两种情形，验证：
  1) session.compose_trace 完整记录 大模型 I/O + 逐脚本输入输出；
  2) 风格硬约束确实纠正了 4/4 → 3/4；
  3) 分析报告第 4 节（流程图 + 表格 + 自动问题定位）被正确生成。

运行：
  python tests/test_transparency.py
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import llm_compose as L
import composition_skills as skill_lib  # noqa: F401  (resolve_skills 需要)
import score_studio as STUDIO


# ---------------------------------------------------------------------------
# 假的大模型：planner 返回固定 plan；translator 返回由全局 MODE 控制的 program
# ---------------------------------------------------------------------------
PLANNER_PLAN = {
    "title": "晨光圆舞", "genre": "圆舞曲", "mood": ["bright"],
    "tempo": 120, "tempo_text": "Allegro", "meter": "4/4", "key_sharps": 0,
    "mode": "major", "instrumentation": "piano", "form": ["A", "A'"],
    "bars": 8, "motif": {}, "harmony": "", "rhythm": "flowing",
    "texture": "", "climax": "", "summary": "",
}

def _translator_program(meter, tempo, pattern):
    return {
        "reasoning": f"这是一首{meter}的圆舞曲，用{pattern}伴奏。",
        "calls": [
            {"script": "new_score", "args": {"title": "晨光圆舞", "tempo": tempo,
             "meter": meter, "voices": 2, "mode": "major", "key_sharps": 0, "bars": 8}},
            {"script": "set_sections", "args": {"sections": [
                {"name": "A", "bars": 4, "meter": meter},
                {"name": "A'", "bars": 4, "meter": meter}]}},
            {"script": "set_harmony", "args": {"section": "A",
             "progression": ["I", "V", "vi", "IV"]}},
            {"script": "accompaniment", "args": {"section": "A", "pattern": pattern,
             "register": "low", "density": 1.0}},
            {"script": "melody", "args": {"section": "A", "rhythm": "flowing"}},
            {"script": "set_harmony", "args": {"section": "A'",
             "progression": ["I", "ii", "V", "I"]}},
            {"script": "accompaniment", "args": {"section": "A'", "pattern": pattern,
             "register": "low", "density": 1.0}},
            {"script": "melody", "args": {"section": "A'", "rhythm": "flowing"}},
            {"script": "cadence", "args": {"bars": 2}},
            {"script": "finalize", "args": {}},
        ],
    }


MODE = {"meter": "4/4", "tempo": 120, "pattern": "broken_arpeggio"}  # 默认：错误情形


def fake_chat(messages):
    sysmsg = messages[0]["content"]
    if "Intent Parser" in sysmsg:
        return json.dumps(PLANNER_PLAN, ensure_ascii=False)
    return json.dumps(_translator_program(MODE["meter"], MODE["tempo"],
                                          MODE["pattern"]), ensure_ascii=False)


class FakeSession:
    def __init__(self):
        # 复刻真实会话 prefs 的默认键（merge_prefs_into_plan 用方括号访问）
        self.prefs = {
            "fugue": "off", "tempo": 0, "meter": "", "bars": 0,
            "key_sharps": 0, "mode": "major", "voices": 0, "seed": "",
            "style": "", "genre_id": "", "instrumentation": "",
        }
        self.plan = None
        self.score_spec = None
        self.last_skill_ids = []
        self.last_translate = None
        self.revision = 0
        self.composition_id = "test"
        self._pending_edit = None
        self.last_advice = ""
        self.compose_trace = None

    def save(self):
        pass


def run_case(text):
    L.chat_completion = fake_chat
    sess = FakeSession()
    score, spec, note = L.compose_from_text(sess, text)
    trace = sess.compose_trace
    model = STUDIO.m21_to_model(score, title=spec.get("title", "x"),
                                composer="test")
    tmp = tempfile.mktemp(suffix=".md")
    STUDIO.export_analysis_report(model, tmp, spec=spec, compose_trace=trace)
    with open(tmp, encoding="utf-8") as f:
        report = f.read()
    base, _ext = os.path.splitext(tmp)
    with open(base + "_dev.md", encoding="utf-8") as f:
        dev_report = f.read()
    return sess, spec, report, tmp, dev_report


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main():
    ok = True

    # ---------- 用例 1：错误翻译（LLM 写 4/4 圆舞曲） ----------
    MODE.update({"meter": "4/4", "tempo": 120, "pattern": "broken_arpeggio"})
    sess, spec, report, tmp, dev = run_case("做一首圆舞曲")
    tr = sess.compose_trace

    ok &= check("用例1 捕获 planner 调用", any(
        c["stage"] == "planner" for c in tr["llm_calls"]))
    ok &= check("用例1 捕获 translator 调用", any(
        c["stage"] == "translator" for c in tr["llm_calls"]))
    # translator 原始输出里确实写了 4/4（问题源头）—— raw(assistant) 是 LLM 本意
    trans = next(c for c in tr["llm_calls"] if c["stage"] == "translator")
    ok &= check("用例1 翻译器原始输出为 4/4（问题源头）",
                '"meter": "4/4"' in trans["assistant"],
                f"assistant={trans['assistant'][:80]}")
    # 经风格硬约束后，实际解析/执行到的拍号已被纠为 3/4
    parsed_meter = trans["parsed"]["calls"][0]["args"]["meter"]
    ok &= check("用例1 解析后拍号已被纠为 3/4", parsed_meter == "3/4",
                f"parsed meter={parsed_meter}")
    ok &= check("用例1 记录逐脚本输入输出", len(tr["kernel_calls"]) > 0,
                f"kernel_calls={len(tr['kernel_calls'])}")
    ok &= check("用例1 触发风格硬约束（4/4→3/4）", any(
        "4/4" in f and "3/4" in f for f in tr["constraint_fixes"]),
                f"fixes={tr['constraint_fixes']}")
    ok &= check("用例1 最终拍号为 3/4", spec["time_signature"] == "3/4",
                f"ts={spec['time_signature']}")
    ok &= check("用例1 速度被钳进 genre 范围(72)",
                spec["tempo"] == 72, f"tempo={spec['tempo']}")
    # 双报告拆分：编辑者版不含生成链路；程序员版（_dev）含第 4 节全量透明化
    ok &= check("用例1 编辑者版不含透明化节", "作曲过程透明化" not in report)
    ok &= check("用例1 编辑者版含基本信息与问题节",
                "## 2. 基本信息" in report and "## 3. 需关注的问题与提示" in report)
    ok &= check("用例1 程序员版含第4节标题", "## 4. 作曲过程透明化" in dev)
    ok &= check("用例1 程序员版含会话与生成环境", "## 1. 会话与生成环境" in dev)
    ok &= check("用例1 程序员版含 Mermaid 流程图", "```mermaid" in dev)
    ok &= check("用例1 程序员版含脚本表 new_score", "| new_score |" in dev)
    ok &= check("用例1 程序员版含自动问题定位(拍号)", "意图拍号↔谱面" in dev)
    ok &= check("用例1 程序员版记录了 4/4→3/4 纠正", "4/4" in dev and "3/4" in dev)

    # ---------- 用例 2：正确翻译（LLM 已写 3/4） ----------
    MODE.update({"meter": "3/4", "tempo": 72, "pattern": "waltz"})
    sess2, spec2, report2, _, dev2 = run_case("做一首圆舞曲")
    tr2 = sess2.compose_trace
    ok &= check("用例2 未触发风格硬约束（LLM 已正确）",
                not any("拍号" in f for f in tr2["constraint_fixes"]),
                f"fixes={tr2['constraint_fixes']}")
    ok &= check("用例2 最终拍号仍为 3/4", spec2["time_signature"] == "3/4")
    ok &= check("用例2 程序员版仍含第4节", "## 4. 作曲过程透明化" in dev2)

    # ---------- 用例 3：确定性骨架（不调 LLM） ----------
    MODE.update({"meter": "4/4", "tempo": 120, "pattern": "broken_arpeggio"})
    L.chat_completion = fake_chat
    sess3 = FakeSession()
    score3, spec3, note3 = L.compose_from_text(sess3, "/skeleton 圆舞曲")
    tr3 = sess3.compose_trace
    ok &= check("用例3 骨架路径不调 LLM", tr3["pipeline"].startswith("skeleton"))
    ok &= check("用例3 仍记录逐脚本输入输出", len(tr3["kernel_calls"]) > 0,
                f"kernel_calls={len(tr3['kernel_calls'])}")
    ok &= check("用例3 无 planner/translator 调用", len(tr3["llm_calls"]) == 0)

    # ---------- 用例 4：情绪词 + 错误翻译（欢快却被慢速纠正冲突） ----------
    MODE.update({"meter": "4/4", "tempo": 120, "pattern": "broken_arpeggio"})
    sess4, spec4, report4, _, dev4 = run_case("做一首欢快的圆舞曲")
    tr4 = sess4.compose_trace
    ok &= check("用例4 情绪约束触发", tr4["emotion_applied"] is True)
    # 情绪要快(132) 但 waltz genre 钳到 72 → 速度自动问题应为 🔴（在程序员版 4.5 表）
    ok &= check("用例4 程序员版提示情绪/速度冲突(🔴)", "情绪↔最终速度" in dev4
                and "🔴" in dev4)

    # 把用例1的样例程序员版报告另存，便于人工查看格式
    sample = os.path.join(ROOT, "tests", "sample_transparency_report.md")
    with open(sample, "w", encoding="utf-8") as f:
        f.write(dev)
    print(f"\n样例报告已生成：{sample}")

    print("\n=== " + ("ALL PASS ✅" if ok else "SOME FAILED ❌") + " ===")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
