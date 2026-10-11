# -*- coding: utf-8 -*-
"""【已过期】一次性脚本：为旧会话 2030c62765 重建 compose_trace 并重新生成双报告。

trace 数据逐字取自拆分前的原始报告（第 4 节完整保留在会话记录中），
写入会话 JSON 的 compose.compose_trace（新持久化字段），此后重启不丢。
迁移已完成（该会话产物已存在），无需再运行；后续版本删除。
"""
import json
import os
import sys
import warnings

warnings.warn('_rebuild_session_trace 是已完成的一次性迁移，无需再运行',
              DeprecationWarning, stacklevel=2)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(sys.path[0])
import score_studio as SS
from music21 import converter

CHAT_ID = '2030c62765'
META = os.path.join('scores', 'chats', f'{CHAT_ID}.json')
XML = os.path.join('scores', 'chats', f'{CHAT_ID}.xml')
REPORT = os.path.join('scores', 'chats', f'{CHAT_ID}_report.md')

USER_TEXT = ('史诗战争 写一首史诗风格的管弦乐小品：弦乐组（小提琴+中提琴+大提琴）铺底推进， '
             '圆号奏英雄气概的主题，定音鼓在高潮处滚奏，B段转入关系小调制造阴云， '
             '结尾回到大调辉煌收束。不要')

PLANNER_SYSTEM = """You are a professional composer and music theorist acting as an Intent Parser.

Transform the user's natural-language request (and optional slash-commands already applied as preferences) into a Composition Plan JSON only.

Think like a composer first: infer genre, mood, form, motif character, harmony language, climax — do NOT invent note lists yet.

Output ONE JSON object, no Markdown:
{
  "title": "string",
  "genre": "string",
  "mood": ["..."],
  "tempo": 72,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major|minor",
  "instrumentation": ["Piano"] or ["Violin","Cello"],
  "form": ["A","B","A"],
  "bars": 8,
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
- If user is vague, choose musically appropriate defaults.
- bars: 4–32. Prefer 8–16 for a brand-new short piece.
- If the user asks to extend/lengthen to N bars (e.g. 扩展到32小节), set bars=N (not 8).
- If edit_intent is extend/longer and no N given, increase bars vs existing_score (typically +8 to +16, max 32).
- If edit_intent is rewrit
…（已截断，完整 5748 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）"""

PLANNER_USER = {
    "user_request": USER_TEXT,
    "preferences": {
        "key_sharps": 0,
        "mode": "major",
        "tempo": 96,
        "bars": 24,
        "meter": "4/4",
        "style": "赋格",
        "genre_id": "genre:academic/fugue",
        "instrumentation": "",
        "fugue": "auto",
        "voices": 4,
        "_explicit": {
            "tempo": True,
            "meter": True,
            "bars": True,
        },
    },
    "edit_intent": None,
    "skills": [
        "orchestrate",
        "genre:academic/fugue",
        "genre:cinematic/epic-battle",
    ],
    "existing_plan": None,
    "revision_edits": None,
    "existing_score_summary": None,
}

PLAN = {
    "title": "英雄对峙",
    "genre": "史诗管弦乐小品",
    "mood": ["英雄气概", "壮阔", "阴云暗涌", "辉煌"],
    "tempo": 96,
    "meter": "4/4",
    "key_sharps": 0,
    "mode": "major",
    "instrumentation": ["Violin", "Viola", "Cello", "Horn", "Timpani"],
    "form": ["A", "B", "A'"],
    "bars": 24,
    "motif": {
        "character": "宽广、附点节奏的英雄主题，以圆号齐奏呈现，音程以四度、五度跳进为主",
        "development": ["repetition", "sequence", "rhythmic_augmentation", "tonal shift"],
    },
    "harmony": {
        "language": "功能和声，以I–V–vi–IV与平行小调借用和弦为主，B段转入关系小调（a小调）制造阴云",
        "density": "moderate",
    },
    "rhythm": {
        "character": "进行曲式强拍推进，附点与切分音增强动力，定音鼓滚奏标记高潮",
    },
    "texture": "orchestral",
    "climax": {"location": "section A' 结尾（第21–24小节）", "dynamic": "ff"},
    "difficulty": "medium",
    "summary": "一首24小节的史诗管弦乐小品：弦乐铺底推进，圆号奏英雄主题，B段转入关系小调制造阴云，结尾回到大调辉煌收束。",
    "edit_intent": None,
}

TRANSLATOR_SYSTEM = """# 翻译器（Translator）

你不是作曲家，是**翻译器**：把用户的自然语言音乐需求与 plan 翻译成
对确定性脚本的调用序列。你**不写任何音符**——所有音符由脚本生成并保证
正确（时值、调性、织体、力度、终止）。你的职责是选对脚本、给对参数。

## 输出格式（只输出 JSON，不要任何解释文字）

```
{"reasoning": "用中文简述你如何把需求翻译成参数与脚本（2–5 句）：识别了什么风格/情绪，怎么决定速度/调式/织体，段落如何安排，调用了哪些脚本。", "calls": [{"script": "脚本名", "args": {...}}, ...]}
```

## 硬规则

1. 调用顺序：`new_score` → `set_sections` → 对每个段落 `{ set_harmony →
   accompaniment → melody }` →（高潮段可加 `thicken`）→（最后一段可加
   `cadence`）→ `finalize`
2. `new_score` 与 `finalize` 必须各恰好一次，且分别在最前/最后
3. **每个段落**都必须调用 set_harmony / accompaniment / melody，
   一次都不许漏（漏了 finalize 会报错）
4. args 必须严格符合脚本文档的字段名与取值范围；不要发明不存在的参数
5. 脚本执行失败时你会收到错误信息——修正 calls 后完整重新输出
6. `plan.tempo` / `plan.mode` / `plan.mood` / `plan.tempo_text` /
   `plan._emotion_hint` 是已解析的**明确约束**（来自用户情绪/速度词或
   `/tempo`），**优先级最高**——调用 `new_score` / `set_harmony` / `melody`
   时直接采用，不要用风格基线默认值覆盖。`reasoning` 字段必须如实写出你
   如何据此翻译，便于用户核查。
7. **乐器编制**：brief 提到管弦乐/弦乐/铜管/木管/具体乐器（小提琴、长笛、
   圆号、定音鼓…）或某种编制（弦乐四重奏、铜管五重奏…）时，`new_score`
   **必须传 `instruments`**（长度=voices，从高音到低音，低音乐器放最后），
   可用名：flute/oboe/horn/trumpet/violin/viola/cello/contrabass/timpani/
   piano/soprano/alto/tenor/bass。例：「弦乐四重奏」→ voices=4 +
   `["violin","violin","viola","cello"]`；「管弦乐小品」→ voices=4 +
   `["flute","violin","cello","timpani"]` 之类按 brief 取舍。不传时 2 声部
   默认钢琴大谱表、3/4 声部默认人声 SATB。

## 音乐判断指南（把情绪翻译成参数）

| 用户意图 | melody | accompaniment | 其他 |
|---|---|---|---
…（已截断，完整 13548 字；固定指令模板见 skills/composition/base/ 与内核 kernel_docs）"""

TRANSLATOR_USER = {
    "plan": {
        "title": "英雄对峙",
        "genre": "史诗管弦乐小品",
        "mood": ["英雄气概", "壮阔", "阴云暗涌", "辉煌"],
        "tempo": 96,
        "tempo_text": None,
        "meter": "4/4",
        "key_sharps": 0,
        "mode": "major",
        "_emotion_hint": None,
        "instrumentation": ["Violin", "Viola", "Cello", "Horn", "Timpani"],
        "form": ["A", "B", "A'"],
        "bars": 24,
        "motif": {
            "character": "宽广、附点节奏的英雄主题，以圆号齐奏呈现，音程以四度、五度跳进为主",
            "development": ["repetition", "sequence", "rhythmic_augmentation", "tonal shift"],
        },
        "harmony": {
            "language": "功能和声，以I–V–vi–IV与平行小调借用和弦为主，B段转入关系小调（a小调）制造阴云",
            "density": "moderate",
        },
        "rhythm": {
            "character": "进行曲式强拍推进，附点与切分音增强动力，定音鼓滚奏标记高潮",
        },
        "texture": "orchestral",
        "climax": {"location": "section A' 结尾（第21–24小节）", "dynamic": "ff"},
        "summary": "一首24小节的史诗管弦乐小品：弦乐铺底推进，圆号奏英雄主题，B段转入关系小调制造阴云，结尾回到大调辉煌收束。",
    },
    "user_brief": USER_TEXT,
}

REASONING = ("A 段动机总时值超过 4/4 一小节容量，将其压缩为 4 拍：四分+八分+八分+四分+四分。"
             "其余段落与和声、织体、转调安排保持不变：A 大调宽广主题，B 转 a 小调阴云，"
             "A' 回大调辉煌并 thicken+cadence。")

CALLS = [
    {"script": "new_score", "args": {"title": "英雄对峙", "key_sharps": 0, "mode": "major",
                                     "meter": "4/4", "tempo": 96, "bars": 24, "voices": 4,
                                     "instruments": ["violin", "viola", "cello", "horn"]}},
    {"script": "set_sections", "args": {"sections": [
        {"name": "A", "bars": 8, "meter": "4/4", "dynamic": "mf", "tempo_text": "Andante", "bpm": 96},
        {"name": "B", "bars": 8, "meter": "4/4", "dynamic": "f", "tempo_text": "Agitato", "bpm": 108},
        {"name": "A'", "bars": 8, "meter": "4/4", "dynamic": "ff", "tempo_text": "Allegro", "bpm": 120}]}},
    {"script": "set_harmony", "args": {"section": "A",
                                       "progression": ["I", "V", "vi", "IV", "I", "V", "IV", "V7"]}},
    {"script": "accompaniment", "args": {"section": "A", "pattern": "broken_arpeggio",
                                         "register": "low", "density": 1}},
    {"script": "new_score", "args": {"title": "英雄对峙", "key_sharps": 0, "mode": "major",
                                     "meter": "4/4", "tempo": 96, "bars": 24, "voices": 4,
                                     "instruments": ["violin", "viola", "cello", "horn"]}},
    {"script": "set_sections", "args": {"sections": [
        {"name": "A", "bars": 8, "meter": "4/4", "dynamic": "mf", "tempo_text": "Andante", "bpm": 96},
        {"name": "B", "bars": 8, "meter": "4/4", "dynamic": "f", "tempo_text": "Agitato", "bpm": 108},
        {"name": "A'", "bars": 8, "meter": "4/4", "dynamic": "ff", "tempo_text": "Allegro", "bpm": 120}]}},
    {"script": "set_harmony", "args": {"section": "A",
                                       "progression": ["I", "V", "vi", "IV", "I", "V", "IV", "V7"]}},
    {"script": "accompaniment", "args": {"section": "A", "pattern": "broken_arpeggio",
                                         "register": "low", "density": 1}},
    {"script": "melody", "args": {"section": "A", "register": "high", "rhythm": "flowing",
                                  "contour": "arch",
                                  "motif": [[1, "quarter"], [5, "eighth"], [5, "eighth"],
                                            [3, "quarter"], [4, "quarter"]]}},
    {"script": "set_harmony", "args": {"section": "B",
                                       "progression": ["i", "iv", "V7", "i", "VI", "iv", "V7", "i"],
                                       "key_sharps": 0, "mode": "minor"}},
    {"script": "accompaniment", "args": {"section": "B", "pattern": "octave_pulse",
                                         "register": "low", "density": 1}},
    {"script": "melody", "args": {"section": "B", "register": "high", "rhythm": "fragmented",
                                  "contour": "wave"}},
    {"script": "set_harmony", "args": {"section": "A'",
                                       "progression": ["I", "V", "IV", "V7", "I", "V", "IV", "I"],
                                       "key_sharps": 0, "mode": "major"}},
    {"script": "accompaniment", "args": {"section": "A'", "pattern": "broken_arpeggio",
                                         "register": "low", "density": 2}},
    {"script": "melody", "args": {"section": "A'", "register": "very_high", "rhythm": "driving",
                                  "contour": "ascending"}},
    {"script": "thicken", "args": {"section": "A'"}},
    {"script": "cadence", "args": {"section": "A'", "bars": 2}},
    {"script": "narrative", "args": {"intensity": 1.3, "surprises": 2, "silence": True}},
    {"script": "finalize", "args": {}},
]

_E = 'E4:whole, B4:whole, C4:whole, A4:whole, E4:whole, B4:whole, A4:whole, B4:whole'
_ACC_A = [[], _E.split(', '),
          'G3:whole, D3:whole, E3:whole, C3:whole, G3:whole, D3:whole, C3:whole, D3:whole'.split(', '),
          'C2:quarter, G2:quarter, C3:quarter, G3:quarter, G2:quarter, D2:quarter, G3:quarter, D3:quarter'.split(', ')]
_ACC_B = [[],
          'C4:whole, F4:whole, G#4:whole, C4:whole, A4:whole, F4:whole, G#4:whole, C4:whole'.split(', '),
          'E3:whole, A3:whole, B3:whole, E3:whole, C3:whole, A3:whole, B3:whole, E3:whole'.split(', '),
          'A2+A3:quarter, A2+A3:quarter, A2+A3:quarter, A2+A3:quarter, D2+D3:quarter, D2+D3:quarter, D2+D3:quarter, D2+D3:quarter'.split(', ')]
_ACC_A2 = [[],
           'E4:whole, B4:whole, A4:whole, B4:whole, E4:whole, B4:whole, A4:whole, E4:whole'.split(', '),
           'G3:whole, D3:whole, C3:whole, D3:whole, G3:whole, D3:whole, C3:whole, G3:whole'.split(', '),
           'C2:eighth, E3:eighth, G3:eighth, C3:eighth, G3:eighth, E3:eighth, C2:eighth, E3:eighth'.split(', ')]
_Z = [[], [], [], []]

# (script, note, added_counts, added_sample)  —— 与原报告 4.3 表逐条对应
KERNEL = [
    ('new_score', '建立乐曲：4/4 ♩=96 4声部 major', [0, 0, 0, 0], _Z),
    ('set_sections', "分段：A(8小节)，B(8小节)，A'(8小节)", [0, 0, 0, 0], _Z),
    ('set_harmony', "和声：段落A 进行['I', 'V', 'vi', 'IV', 'I', 'V', 'IV', 'V7']", [0, 0, 0, 0], _Z),
    ('accompaniment', '伴奏：段落A pattern=broken_arpeggio register=low density=1', [0, 8, 8, 32], _ACC_A),
    ('new_score', '建立乐曲：4/4 ♩=96 4声部 major', [0, 0, 0, 0], _Z),
    ('set_sections', "分段：A(8小节)，B(8小节)，A'(8小节)", [0, 0, 0, 0], _Z),
    ('set_harmony', "和声：段落A 进行['I', 'V', 'vi', 'IV', 'I', 'V', 'IV', 'V7']", [0, 0, 0, 0], _Z),
    ('accompaniment', '伴奏：段落A pattern=broken_arpeggio register=low density=1', [0, 8, 8, 32], _ACC_A),
    ('melody', '旋律：段落A rhythm=flowing', [36, 0, 0, 0],
     ['C6:quarter, G6:eighth, G6:eighth, E6:quarter, F6:quarter, B5:quarter, A5:eighth, G5:eighth'.split(', '), [], [], []]),
    ('set_harmony', "和声：段落B 进行['i', 'iv', 'V7', 'i', 'VI', 'iv', 'V7', 'i']", [0, 0, 0, 0], _Z),
    ('accompaniment', '伴奏：段落B pattern=octave_pulse register=low density=1', [0, 8, 8, 32], _ACC_B),
    ('melody', '旋律：段落B rhythm=fragmented', [42, 0, 0, 0],
     ['A5:quarter, E5:quarter, None:eighth, A5:quarter, G5:eighth, F5:eighth, F5:eighth, G5:eighth'.split(', '), [], [], []]),
    ('set_harmony', "和声：段落A' 进行['I', 'V', 'IV', 'V7', 'I', 'V', 'IV', 'I']", [0, 0, 0, 0], _Z),
    ('accompaniment', "伴奏：段落A' pattern=broken_arpeggio register=low density=2", [0, 8, 8, 64], _ACC_A2),
    ('melody', "旋律：段落A' rhythm=driving", [37, 0, 0, 0],
     ['G5:quarter, E5:quarter, F5:eighth, G5:eighth, G5:quarter, B5:quarter, D6:quarter, G6:quarter'.split(', '), [], [], []]),
    ('thicken', "加厚：段落A'", [0, 0, 0, 0], _Z),
    ('cadence', '终止：bars=2', [0, 0, 0, 0], _Z),
    ('narrative', 'narrative', [6, 0, 0, 0],
     ['D6+D7:quarter, E6+E7:eighth, F6+F7:eighth, G6+G7:quarter, C5+C6:whole, C5+C6:whole'.split(', '), [], [], []]),
    ('finalize', '收束装配：24小节 / 4声部', [0, 0, 0, 0], _Z),
]


def main():
    kernel_calls = []
    for call, (script2, note, counts, sample) in zip(CALLS, KERNEL):
        script = call['script']
        assert script == script2, (script, script2)
        kernel_calls.append({'script': script, 'input': call['args'],
                             'output': {'note': note, 'added_counts': counts,
                                        'added_sample': sample}})

    trace = {
        'user_text': USER_TEXT,
        'pipeline': 'translate',
        'skill_ids': ['orchestrate', 'genre:academic/fugue', 'genre:cinematic/epic-battle'],
        'llm_calls': [
            {'stage': 'planner', 'title': 'Planner（意图解析）',
             'system': PLANNER_SYSTEM,
             'user': json.dumps(PLANNER_USER, ensure_ascii=False, indent=2),
             'assistant': json.dumps(PLAN, ensure_ascii=False, indent=2),
             'parsed': PLAN},
            {'stage': 'translator', 'title': 'Translator（脚本翻译）',
             'system': TRANSLATOR_SYSTEM,
             'user': json.dumps(TRANSLATOR_USER, ensure_ascii=False, indent=2),
             'assistant': json.dumps({'reasoning': REASONING, 'calls': CALLS},
                                     ensure_ascii=False, indent=2),
             'parsed': {'reasoning': REASONING, 'calls': CALLS}},
        ],
        'emotion': None,
        'emotion_applied': False,
        'constraint_fixes': [],
        'kernel_calls': kernel_calls,
        'repair': None,
        'result': {},
        'generated_at': '2026-09-27T13:36:00',
    }

    # 1) trace 写回会话 JSON（新持久化字段），重启/切会话后不再丢失
    with open(META, encoding='utf-8') as f:
        sess = json.load(f)
    compose = sess.setdefault('compose', {})
    compose['compose_trace'] = trace
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(sess, f, ensure_ascii=False, indent=2)
    print('trace 已写回会话 JSON')

    # 2) 用修复后的代码重新生成双报告
    spec = compose.get('score_spec')
    m21s = converter.parse(XML)
    model = SS.m21_to_model(m21s, title=str(spec.get('title') or ''))
    text, counts = SS.export_analysis_report(model, REPORT, spec=spec,
                                             compose_trace=trace)
    print('counts:', counts)
    for ln in text.splitlines():
        if ln.startswith(('# ', '## ')):
            print(ln)


if __name__ == '__main__':
    main()
