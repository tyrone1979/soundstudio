# -*- coding: utf-8 -*-
"""对话创曲：两阶段 LLM（意图计划 → 作曲）+ 本地 MusicXML + 校验修复。

流程：
  用户自然语言/命令
    → Intent/Plan LLM（作曲规格 JSON）
    → Composer LLM（声部 events JSON，不做 MusicXML）
    → music21 生成 MusicXML
    → 本地校验（时值等）
    → 必要时 Repair LLM 修 events，再校验

配置：llm_config.json 或 MUSIC_LLM_API_KEY / MUSIC_LLM_BASE_URL / MUSIC_LLM_MODEL
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import datetime
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from music21 import articulations as m21articulations
from music21 import chord as m21chord
from music21 import clef as m21clef
from music21 import dynamics as m21dynamics
from music21 import expressions as m21expressions
from music21 import instrument
from music21 import key
from music21 import metadata as m21meta
from music21 import meter
from music21 import note as m21note
from music21 import pitch as m21pitch
from music21 import spanner as m21spanner
from music21 import stream
from music21 import tempo

import composition_skills as skill_lib

# 确定性选区重写复用内核的音符级原语（和弦符号解析 / 旋律 token 解析 / 调内拼写），
# 让「选中音符/小节 + 提示词」在无 LLM Key 时也能仅重写该区域（与确定性内核理念一致）。
from compose_kernel import (  # noqa: E402
    _parse_chord_symbol, _is_chord_symbol, parse_roman,
    _resolve_melody_token, _scale_names, _pc, _midi, _name_oct,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'llm_config.json')
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scores', 'llm_compose_state.json')
DEFAULT_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_MODEL = 'gpt-4o-mini'


def _atomic_json_dump(path: str, data: Any, **dump_kw) -> None:
    """Write JSON via tempfile + os.replace so crash mid-write cannot truncate."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    kw = {'ensure_ascii': False, 'indent': 2}
    kw.update(dump_kw)
    fd, tmp = tempfile.mkstemp(
        prefix='.tmp_', suffix='.json',
        dir=os.path.dirname(os.path.abspath(path)) or '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, **kw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

DUR_QL = {
    'whole': 4.0, 'half': 2.0, 'quarter': 1.0,
    'eighth': 0.5, '16th': 0.25, '32nd': 0.125,
}

# 附点时值：内核的音符级 `notes=` 会产出 dotted_* 名，本模块的自动生成不会。
# 因此这里**只增量登记**、不并入 DUR_QL —— 保证 `_fit_duration_name` 这类按
# DUR_QL 迭代的自动路径完全不受影响（既有风格指纹零漂移），同时让 dotted_* 的
# 时值换算 / 校验 / 渲染都认得它。
_EXTRA_DUR_QL = {
    'dotted_whole': 6.0, 'dotted_half': 3.0,
    'dotted_quarter': 1.5, 'dotted_eighth': 0.75,
}
#: 附点时值名 → (music21 type, dots)。music21 的 `type=` 不认 "dotted_quarter"
#: 这种拼写（会抛 DurationException），必须拆成「基础时值 + 附点数」。
_DUR_BASE_DOTS = {
    'dotted_whole': ('whole', 1), 'dotted_half': ('half', 1),
    'dotted_quarter': ('quarter', 1), 'dotted_eighth': ('eighth', 1),
}

# 力度记号 → MIDI velocity（导出 MIDI 时生效，影响播放强弱）
# 压缩到可听区间：pp≈62 保证最弱段清晰可闻；相邻档差 ~8 避免跳变突兀
DYNAMIC_VELOCITIES = {
    'ppp': 55, 'pp': 62, 'p': 70, 'mp': 78, 'mf': 86,
    'f': 95, 'ff': 105, 'fff': 116, 'sf': 92, 'sfz': 96,
}

#: 作曲主路径只保留 **LLM 自然语言 → /compose-grammar**。以下命令的入口
#: 统一退役并指向 /compose-grammar；底层实现刻意保留（tests/ 与 studio 的
#: 历史会话仍引用，删除会破坏可复现性）。
_RETIRED_COMPOSITION_CMDS = {
    '/skeleton': '新曲请用 /compose-grammar；例：/compose-grammar 交响曲 快板',
    '/interpolate': '风格插值已并入 /compose-grammar 的风格描述；'
                    '例：/compose-grammar 圆舞曲 带夜曲色彩',
}

#: 编辑类命令：改谱入口一并退役，作曲请重新生成。
_RETIRED_EDIT_CMDS = (
    '/rewrite', '/continue', '/apply', '/advice',
    '/extend', '/develop', '/variation', '/harmonize', '/orchestrate',
    '/transpose', '/shorter', '/longer', '/simplify', '/complexify',
)

KEY_ALIASES = {
    'c': (0, 'major'), 'cmaj': (0, 'major'), 'c大调': (0, 'major'),
    'am': (0, 'minor'), 'amin': (0, 'minor'), 'a小调': (0, 'minor'),
    'g': (1, 'major'), 'gmaj': (1, 'major'), 'g大调': (1, 'major'),
    'em': (1, 'minor'), 'emin': (1, 'minor'), 'e小调': (1, 'minor'),
    'd': (2, 'major'), 'dmaj': (2, 'major'), 'd大调': (2, 'major'),
    'bm': (2, 'minor'), 'bmin': (2, 'minor'), 'b小调': (2, 'minor'),
    'a': (3, 'major'), 'amaj': (3, 'major'), 'a大调': (3, 'major'),
    'f#m': (3, 'minor'), 'f#min': (3, 'minor'),
    'e': (4, 'major'), 'emaj': (4, 'major'),
    'f': (-1, 'major'), 'fmaj': (-1, 'major'), 'f大调': (-1, 'major'),
    'dm': (-1, 'minor'), 'dmin': (-1, 'minor'), 'd小调': (-1, 'minor'),
    'bb': (-2, 'major'), 'bbmaj': (-2, 'major'),
    'gm': (-2, 'minor'), 'gmin': (-2, 'minor'), 'g小调': (-2, 'minor'),
}

INSTRUMENT_FACTORY = {
    'flute': (instrument.Flute, 73, 'treble'),
    'oboe': (instrument.Oboe, 68, 'treble'),
    'horn': (instrument.Horn, 60, 'treble'),
    'trumpet': (instrument.Trumpet, 56, 'treble'),
    'violin': (instrument.Violin, 40, 'treble'),
    'viola': (instrument.Viola, 41, 'alto'),
    'cello': (instrument.Violoncello, 42, 'bass'),
    'violoncello': (instrument.Violoncello, 42, 'bass'),
    'contrabass': (instrument.Contrabass, 43, 'bass'),
    'doublebass': (instrument.Contrabass, 43, 'bass'),
    'piano': (instrument.Piano, 0, 'treble'),
    'timpani': (instrument.Timpani, 47, 'bass'),
    # 交响/管弦编制补充（双管 + 低音铜管 + 色彩乐器）
    'piccolo': (instrument.Piccolo, 72, 'treble'),
    'clarinet': (instrument.Clarinet, 71, 'treble'),
    'bassoon': (instrument.Bassoon, 70, 'bass'),
    'trombone': (instrument.Trombone, 57, 'bass'),
    'tuba': (instrument.Tuba, 58, 'bass'),
    'harp': (instrument.Harp, 46, 'treble'),
    # 人声（缺省 SATB 命名的兜底，避免落进 GM 0 号大钢琴）
    'soprano': (instrument.Soprano, 53, 'treble'),
    'alto': (instrument.Alto, 53, 'treble'),
    'tenor': (instrument.Tenor, 53, 'treble'),
    'bass': (instrument.Bass, 53, 'bass'),
    'voice': (instrument.Soprano, 53, 'treble'),
}

HELP_TEXT = """命令（可与自然语言混用；一行可写多个命令）。完整帮助（每个命令/功能都带示例）：

  输入 /help 查看全部命令与功能的说明 + 示例；
  输入 /help <命令或功能名>（如 /help /chords、/help 和弦顾问、/help 风格插值）查看单项详情。

  常用：/new /style 雨夜钢琴  /demo  /export  /analyze  /advice  /apply
  设置：/key Am  /tempo 72  /meter 3/4  /bars 16  /voices 3  /fugue auto|full|off
  生成：/skeleton 圆舞曲  /interpolate 雨夜钢琴 圆舞曲 40%  /system 十二音序列
  编辑：/continue /extend 8 /develop /variation /harmonize /orchestrate 弦乐四重奏
        /transpose D /shorter /longer /simplify /complexify /rewrite
  增强功能：/groove on 0.2 0.5  /refine 赋格  /import <MusicXML路径>  /chords I V vi IV
        /satb I V vi IV  /chords I V vi IV write
        /render piano reverb 0.3 velocity soft  /batch
        /reverse-prompt [路径]  /replay [score_description.md]  /brief-expand <短句>
  学进风格指纹：/learn-score chorale
  可解释语法（统一核心 learn_core）：/learn-grammar [chorale|folk|jazz|pop|madrigal|sonata|strings|symphony|chopin|all]
        /learn-all [--skip chorale|leadsheet|relation] [--style jazz pop] [--dry-run]
        （一次性跑完chorale + 8 体裁 leadsheet + 人工段落关系，全部编译入库）
        /compose-grammar [风格 子风格] [major|minor] [小节数] [弱起|强拍起] [乐器] [seed=…] [t=温度]
        例：/compose-grammar 民谣 jig  /compose-grammar 爵士 bebop  /compose-grammar 流行 minor  /compose-grammar 牧歌
            /compose-grammar 奏鸣曲 小步舞曲  /compose-grammar 四重奏 快板  /compose-grammar 肖邦 玛祖卡 minor
            /compose-grammar 交响曲 小步舞曲
        也可以直接说：作一曲小步舞曲 / 写一首海顿交响曲小步舞曲 / 来一首忧伤的玛祖卡

  创曲需配置 API；未配置时用 /demo。LLM 可能需 1–3 分钟，请等「创曲中」结束。
"""

# Commands with no argument (or arg ignored for session control)
_NO_ARG_CMDS = {
    '/help', '/?', '/config', '/clear', '/new', '/demo', '/export', '/analyze',
    '/advice', '/apply', '/learn-grammar', '/learn-all',
    '/continue', '/develop', '/variation', '/harmonize',
    '/shorter', '/longer', '/simplify', '/complexify', '/rewrite',
}
# Commands that consume a following argument token/phrase
_ARG_CMDS = {
    '/key', '/tempo', '/meter', '/bars', '/addbars', '/style', '/fugue', '/voices', '/skeleton',
    '/extend', '/orchestrate', '/transpose',
    '/learn-score', '/system', '/interpolate', '/groove', '/refine', '/import',
    '/chords', '/satb', '/render', '/batch', '/assist',
    '/reverse-prompt', '/replay', '/brief-expand', '/compose-grammar',
    '/addpart', '/addvoice', '/accomp', '/accompaniment',
}
# 只吃一个 token 的命令：后面的文字是自然语言，不能被参数吞掉。
# 否则「/fugue auto 欢快一些」会把「欢快一些」当成 /fugue 的参数，
# 既解析失败、又让整轮没有自然语言，结果整句只剩命令、不会触发作曲。
_ONE_TOKEN_CMDS = {'/bars', '/addbars', '/tempo', '/meter', '/key', '/voices', '/fugue'}
# 吃掉整行剩余作为主题的命令（/help 后跟命令或功能名，不应触发作曲）
_REST_CMDS = {'/help', '/?'}

# —— System prompts（作曲优先，MusicXML 由本地生成）——

PLANNER_PROMPT = """You are a professional composer and music theorist acting as an Intent Parser.

Transform the user's natural-language request into a Composition Plan JSON only.
Use the injected 风格目录 (learned packs only) to set genre_id.

Output ONE JSON object, no Markdown:
{
  "title": "string",
  "genre": "string",
  "genre_id": "family/pack_id from catalog or empty",
  "mood": ["..."],
  "tempo": 72,
  "meter": "4/4",
  "key_sharps": 0,
  "mode": "major|minor",
  "instrumentation": ["trumpet","piano","contrabass"],
  "form": ["A","B","A"],
  "bars": 16,
  "motif": {"character": "...", "development": ["repetition","sequence"]},
  "harmony": {"language": "...", "density": "sparse|moderate|dense"},
  "rhythm": {"character": "..."},
  "texture": "homophonic|polyphonic|sparse|orchestral|first-species",
  "species": false,
  "species_from_current": false,
  "learned_grammar": null,
  "want_fugue": false,
  "climax": {"location": "section B", "dynamic": "f"},
  "difficulty": "easy|medium",
  "summary": "one sentence for the user",
  "edit_intent": null
}
Rules:
- Original music only; no copyrighted melodies.
- genre_id MUST be a path from the injected learned-pack catalog (e.g. jazz/swing-standard). Technical terms are NOT styles.
- **species**: true when user wants 2-voice 1:1 / first-species counterpoint (一比一/1比1/第一类对位) and did NOT ask for a fugue. Then instrumentation=["piano","piano"], texture="first-species", genre_id="", want_fugue=false. Counterpoint ≠ fugue.
- **species_from_current**: true when user asks to write 1:1 counterpoint based on the currently open score (根据当前乐谱写1比1对位). Then also species=true, want_fugue=false; keep bars/key/tempo from existing_score; edit_intent=null.
- **learned_grammar**: DEPRECATED for new pieces — empty sessions never reach this Planner.
  New pieces use /compose-grammar (slash, keyword, or a dedicated grammar-route LLM). Keep null.
- **want_fugue**: true only if user explicitly asks 赋格/fugue/canon/invention; false for bare 对位/1:1.
- bars: 4–32; when genre_id is set prefer catalog default (often 16–32), not 8.
- Prefer kernel instrument names (violin/flute/trumpet/piano/contrabass/timpani); map fiddle→violin, double bass→contrabass.
- key_sharps: Bb major → -2. edit_intent null for new pieces.
"""

COMPOSER_PROMPT = """You are a professional composer and arranger.

Given a Composition Plan (and optional previous score state), write an ORIGINAL short piece as SCORE JSON — not MusicXML.

Think: MOTIF → PHRASE → HARMONY → RHYTHM → FORM → ARRANGEMENT, then encode notes.

Output ONE JSON object, no Markdown:
{
  "title": "string",
  "composer": "对话创曲",
  "key_sharps": 0,
  "mode": "major|minor",
  "time_signature": "4/4",
  "tempo": 72,
  "bars": 8,
  "parts": [
    {
      "name": "Violin",
      "program": 40,
      "clef": "treble",
      "events": [["C5","quarter"], [null,"eighth"], ["E5","half"]]
    }
  ],
  "notes": "brief user-facing description"
}
Rules:
- events: [pitch|null, duration]. pitch like C4, F#5, Bb3. duration: whole|half|quarter|eighth|16th.
- Each part's total duration MUST equal bars × measure length in quarter notes
  (4/4→4 per bar; 3/4→3; 6/8→3).
- When plan.bars is N, you MUST write enough events for all N bars (do not stop at 8 if bars=32).
- If previous_score exists and edit_intent is extend/longer: keep early material coherent, then continue through the new bars.
- 2–4 parts; respect ranges; coherent motifs/phrases/cadences.
- When plan.instrumentation is piano (or a piano reduction of chorale/hymn):
  use exactly 2 parts named "Piano RH" and "Piano LH" (grand staff). Write
  upper voices as chords on RH and lower voices as chords on LH
  (pitch may be a list, e.g. ["G4","E4"]). Do NOT emit 4 open SATB staves.
- Prefer musical coherence over complexity.
- Never copy copyrighted themes.
"""

SELECTION_MEASURE_PROMPT = """You rewrite ONLY the selected measures of an existing score.

Input includes selection.measures (0-based bar indices), current bar events per part,
and optional neighbor bars for context. User request describes the musical change.

Output ONE JSON object, no Markdown:
{
  "parts": [
    {"name": "same as input", "bar_events": {"0": [["C5","quarter"], ...], "3": [...]}}
  ],
  "notes": "brief description of the change"
}
Rules:
- bar_events keys are measure indices as strings; ONLY include selected measures.
- Each selected bar's events MUST sum exactly to one bar length in quarter notes.
- Keep part names identical. Do not change key/tempo/meter/total bars.
- Preserve style continuity with neighbors unless user asks otherwise.
- Never copy copyrighted themes.
"""

SELECTION_SYMBOL_PROMPT = """You rewrite ONLY the selected note/rest symbols in an existing score.

Input lists each symbol as {staff_idx, measure_idx, note_idx, pitch, duration, part_name}
plus local bar context. User request describes the change (e.g. raise pitch, make rest, shorten).

Output ONE JSON object, no Markdown:
{
  "replacements": [
    {"staff_idx": 0, "measure_idx": 2, "note_idx": 1, "event": ["E5","eighth"]}
  ],
  "notes": "brief description"
}
Rules:
- Return one replacement per selected symbol (same indices).
- event is [pitch|null, duration]; duration: whole|half|quarter|eighth|16th.
- Prefer keeping the bar's total duration valid; if you change duration, adjust only that event
  and the caller may pad/trim — keep duration the same when possible.
- Never copy copyrighted themes.
"""

REPAIR_PROMPT = """You are a music notation repair specialist.

You receive a SCORE JSON and a list of CRITICAL/MAJOR validation errors (usually wrong measure totals).

Repair ONLY what is needed so each part's event durations sum exactly to bars × quarters-per-bar.
Do not rewrite the musical idea unnecessarily. Keep pitches when possible; adjust durations/rests.

Output the full corrected SCORE JSON only (same schema), no Markdown.
"""


class CommandOnly(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


_LLM_CONFIG_CACHE: Optional[Tuple[float, Dict[str, str]]] = None


def load_llm_config(force: bool = False) -> Dict[str, str]:
    global _LLM_CONFIG_CACHE
    mtime = 0.0
    if os.path.exists(CONFIG_PATH):
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            mtime = 0.0
    if not force and _LLM_CONFIG_CACHE and _LLM_CONFIG_CACHE[0] == mtime:
        # 环境变量可能覆盖文件；合并时仍读 env
        cached = dict(_LLM_CONFIG_CACHE[1])
        if os.environ.get('MUSIC_LLM_API_KEY'):
            cached['api_key'] = os.environ['MUSIC_LLM_API_KEY']
        if os.environ.get('MUSIC_LLM_BASE_URL'):
            cached['base_url'] = os.environ['MUSIC_LLM_BASE_URL'].rstrip('/')
        if os.environ.get('MUSIC_LLM_MODEL'):
            cached['model'] = os.environ['MUSIC_LLM_MODEL']
        return cached

    cfg = {
        'api_key': os.environ.get('MUSIC_LLM_API_KEY', ''),
        'base_url': os.environ.get('MUSIC_LLM_BASE_URL', DEFAULT_BASE_URL),
        'model': os.environ.get('MUSIC_LLM_MODEL', DEFAULT_MODEL),
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                data = json.load(f)
            for k in ('api_key', 'base_url', 'model'):
                if data.get(k):
                    cfg[k] = str(data[k]).strip()
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    cfg['base_url'] = (cfg['base_url'] or DEFAULT_BASE_URL).rstrip('/')
    cfg['model'] = cfg['model'] or DEFAULT_MODEL
    _LLM_CONFIG_CACHE = (mtime, dict(cfg))
    return cfg


def config_summary() -> str:
    cfg = load_llm_config()
    key = cfg['api_key']
    masked = (key[:4] + '…' + key[-4:]) if len(key) > 8 else ('(未配置)' if not key else '****')
    return f"base_url={cfg['base_url']}\nmodel={cfg['model']}\napi_key={masked}\nfile={CONFIG_PATH}"


def _ql_per_bar(ts: str) -> float:
    num, den = map(int, ts.split('/'))
    return float(num) * (4.0 / float(den))


def _dur_ql(dur: str) -> float:
    d = str(dur)
    return DUR_QL.get(d, _EXTRA_DUR_QL.get(d, 1.0))


def _parse_meter_changes(raw, bars: int, base_ts: str) -> List[Tuple[int, str]]:
    """spec['meter_changes'] → [(bar1based, '3/4'), …]，去重/排序/截断到曲长内。"""
    out: List[Tuple[int, str]] = []
    for item in raw or []:
        try:
            if isinstance(item, dict):
                bar = int(item.get('bar') or item.get('measure') or 0)
                val = str(item.get('time_signature') or item.get('ts') or item.get('value') or '').strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                bar, val = int(item[0]), str(item[1]).strip()
            else:
                continue
        except Exception:
            continue
        try:
            _ql_per_bar(val)  # 非法拍号丢弃
        except Exception:
            continue
        if 2 <= bar <= bars and val:
            out.append((bar, val))
    out.sort()
    return out


def _bar_grid(bars: int, ts: str, meter_changes: Optional[List[Tuple[int, str]]] = None
              ) -> Tuple[List[float], List[str]]:
    """小节网格：返回 (每小节起始偏移，长度 bars+1) 与 (每小节拍号，长度 bars)。

    无 meter_changes 时退化为恒定拍号网格。
    """
    changes = [c for c in (meter_changes or []) if 2 <= c[0] <= bars]
    starts = [0.0]
    ts_list: List[str] = []
    cur_ts = ts
    for b in range(1, bars + 1):
        for cb, cts in changes:
            if cb == b:
                cur_ts = cts
        ts_list.append(cur_ts)
        starts.append(starts[-1] + _ql_per_bar(cur_ts))
    return starts, ts_list


def _total_ql(bars: int, ts: str, meter_changes: Optional[List[Tuple[int, str]]] = None) -> float:
    starts, _ = _bar_grid(bars, ts, meter_changes)
    return starts[-1] if starts else 0.0


def _offset_to_bar(offset: float, starts: List[float]) -> int:
    """偏移 → 1-based 小节号（用网格二分）。"""
    import bisect
    return max(1, min(len(starts) - 1, bisect.bisect_right(starts, offset + 1e-9)))


def _parse_tempo_marks(raw) -> List[Tuple[int, str, Optional[int]]]:
    """spec['tempo_marks'] → [(bar, text, bpm|None), …]。

    接受 [bar, text, bpm] / [bar, bpm] / [bar, text] / {'bar','text','bpm'}。
    text 为空但 bpm 在场时生成 "♩=bpm" 显示文本。
    """
    out: List[Tuple[int, str, Optional[int]]] = []
    for item in raw or []:
        try:
            if isinstance(item, dict):
                bar = int(item.get('bar') or item.get('measure') or 0)
                text = str(item.get('text') or item.get('mark') or '').strip()
                bpm = item.get('bpm') or item.get('tempo')
                try:
                    bpm = int(bpm) if bpm is not None else None
                except (TypeError, ValueError):
                    bpm = None   # bpm 非法（如 'Allegro'）只丢 bpm，保留文字标记
            elif isinstance(item, (list, tuple)):
                bar = int(item[0])
                if len(item) >= 3:
                    text, bpm = str(item[1]).strip(), _safe_int(item[2])
                elif len(item) == 2:
                    v = _safe_int(item[1])
                    if v is not None:
                        text, bpm = '', v
                    else:
                        text, bpm = str(item[1]).strip(), None
                else:
                    continue
            else:
                continue
        except Exception:
            continue
        if not (1 <= bar <= 64) or (not text and bpm is None):
            continue
        if not text and bpm is not None:
            text = f'♩={bpm}'
        if bpm is not None and not (20 <= bpm <= 300):
            bpm = None
        out.append((bar, text, bpm))
    out.sort(key=lambda x: x[0])
    seen = set()
    uniq = []
    for bar, text, bpm in out:
        if bar in seen:
            continue
        seen.add(bar)
        uniq.append((bar, text, bpm))
    return uniq


def _safe_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            raise ValueError('模型未返回可解析 JSON')
        return json.loads(m.group(0))


def _unwrap_spec(obj: Dict[str, Any]) -> Dict[str, Any]:
    """模型偶尔会把谱面包在 {"score": {...}} 等外层键里，自动解包。"""
    if not isinstance(obj, dict):
        return obj
    if obj.get('parts'):
        return obj
    for k in ('score', 'result', 'composition', 'music', 'spec'):
        inner = obj.get(k)
        if isinstance(inner, dict) and inner.get('parts'):
            merged = dict(inner)
            for kk, vv in obj.items():
                if kk not in merged:
                    merged[kk] = vv
            return merged
    return obj


def chat_completion(
    messages: List[Dict[str, str]],
    timeout: int = 180,
    *,
    json_mode: bool = True,
) -> str:
    cfg = load_llm_config()
    if not cfg['api_key']:
        raise RuntimeError(
            f'未配置 API Key。请设置 MUSIC_LLM_API_KEY 或编辑 {CONFIG_PATH}'
        )
    url = cfg['base_url'] + '/chat/completions'
    body: Dict[str, Any] = {
        'model': cfg['model'],
        'messages': messages,
        'temperature': 0.7,
        # 完整多声部谱面的 JSON 很长，用大输出上限防止截断
        'max_tokens': 8192,
    }
    if json_mode:
        body['response_format'] = {'type': 'json_object'}

    def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url, data=data, method='POST',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f"Bearer {cfg['api_key']}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    try:
        result = _post(body)
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8', errors='replace')
        if e.code in (400, 404, 422) and 'response_format' in body:
            body.pop('response_format', None)
            result = _post(body)
        else:
            raise RuntimeError(f'API HTTP {e.code}: {err[:500]}') from e
    except urllib.error.URLError as e:
        raise RuntimeError(f'无法连接 LLM API: {e}') from e

    try:
        content = result['choices'][0]['message'].get('content')
        return (content or '') if isinstance(content, str) else ('' if content is None else str(content))
    except Exception as e:
        raise RuntimeError(f'API 响应异常: {result!r}') from e


# ---------- session / state ----------

class ComposeSession:
    def __init__(self):
        self.prefs = {
            'key_sharps': 0,
            'mode': 'major',
            'tempo': 120,
            'bars': 8,
            'meter': '4/4',
            'style': '',
            'genre_id': '',
            'instrumentation': '',
            # 赋格：'auto'=每段乐曲都含一段赋格（2 声部即二声部创意曲）；
            # 'full'=整首写成赋格；'off'=不要赋格
            'fugue': 'auto',
            'voices': 0,          # 0=由风格自动推导（2 或 3/4）
            # 用户显式用命令设置过的键（/tempo /meter /key /bars）：
            # 用于区分「默认值」与「显式指定」——默认值不应压掉
            # LLM/情绪解析的推断，显式命令则优先
            '_explicit': {},
        }
        self.plan: Optional[Dict[str, Any]] = None
        self.score_spec: Optional[Dict[str, Any]] = None
        self.revision = 0
        self.composition_id = str(uuid.uuid4())[:8]
        self._pending_edit: Optional[str] = None
        self.last_skill_ids: List[str] = []
        self.last_advice: Optional[str] = None
        self.imported_score = None  # /import 导入的外部 MusicXML（music21 Score）
        self.last_reverse_program: Optional[Dict[str, Any]] = None
        self.last_reverse_prompt: Optional[str] = None
        self.last_brief_expand: Optional[Dict[str, Any]] = None

    def reset(self, keep_prefs: bool = True):
        prefs = dict(self.prefs) if keep_prefs else None
        self.__init__()
        if prefs:
            self.prefs = prefs

    def state_brief(self) -> Dict[str, Any]:
        return {
            'composition_id': self.composition_id,
            'revision': self.revision,
            'prefs': self.prefs,
            'plan': self.plan,
            'score_title': (self.score_spec or {}).get('title'),
            'has_score': self.score_spec is not None,
        }

    def save(self, path: str = STATE_PATH):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        payload = {
            'composition_id': self.composition_id,
            'revision': self.revision,
            'prefs': self.prefs,
            'plan': self.plan,
            'score_spec': self.score_spec,
        }
        _atomic_json_dump(path, payload)

    def load(self, path: str = STATE_PATH) -> bool:
        if not os.path.exists(path):
            return False
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        self.composition_id = data.get('composition_id') or self.composition_id
        self.revision = int(data.get('revision') or 0)
        self.prefs.update(data.get('prefs') or {})
        self.plan = data.get('plan')
        self.score_spec = data.get('score_spec')
        return True


def parse_command(text: str) -> Optional[Tuple[str, str]]:
    """First slash command only (compat). Prefer parse_user_turn."""
    cmds, rest = parse_user_turn(text)
    if not cmds:
        return None
    cmd, arg = cmds[0]
    if len(cmds) == 1 and rest:
        arg = f'{arg} {rest}'.strip() if arg else rest
    return cmd, arg


def parse_user_turn(text: str) -> Tuple[List[Tuple[str, str]], str]:
    """
    Split one chat line into leading slash-commands + leftover natural language.
    '/new /style 雨夜钢琴 快板小雨' → [('/new',''), ('/style','雨夜钢琴 快板小雨')], ''
    (style resolver later peels NL out of the /style argument)
    """
    raw = (text or '').strip()
    if not raw:
        return [], ''
    if not raw.startswith('/'):
        return [], raw

    known = _NO_ARG_CMDS | _ARG_CMDS
    cmds: List[Tuple[str, str]] = []
    i = 0
    n = len(raw)

    while i < n:
        while i < n and raw[i].isspace():
            i += 1
        if i >= n:
            break
        if raw[i] != '/':
            break
        m = re.match(r'/\S+', raw[i:])
        if not m:
            break
        cmd = m.group(0).lower().rstrip('，,。.;；')
        i += m.end()
        if cmd not in known:
            # unknown slash token → treat from here as NL (include the token)
            return cmds, raw[i - m.end():].strip()
        if cmd in _REST_CMDS:
            # /help 吃掉整行剩余作为主题，避免把后面文字当成作曲自然语言
            rest = raw[i:].strip()
            cmds.append((cmd, rest))
            return cmds, ''
        arg = ''
        if cmd in _ARG_CMDS:
            while i < n and raw[i].isspace():
                i += 1
            arg_start = i
            if cmd in _ONE_TOKEN_CMDS:
                # 只取第一个 token，其余留作自然语言
                while i < n and not raw[i].isspace():
                    i += 1
            else:
                while i < n:
                    if raw[i] == '/' and i > arg_start:
                        # possible next command
                        m2 = re.match(r'/\S+', raw[i:])
                        tok = (m2.group(0).lower().rstrip('，,。.;；') if m2 else '')
                        if tok in known:
                            break
                    i += 1
            arg = raw[arg_start:i].strip()
        cmds.append((cmd, arg))

    return cmds, raw[i:].strip()


def _exact_genre(arg: str):
    """仅当 arg 整段就是风格包正式名（中/英/pack_id）时返回。不用关键词表。"""
    return skill_lib.match_genre_by_name(
        arg, learned_only=False, allow_prefix=False)


def _peel_genre_prefix(arg: str) -> Tuple[Optional[dict], str]:
    """从 /style 参数前缀剥离正式风格名 → (genre, leftover_nl)。

    例：``摇摆标准曲（爵士） 写一首…Bb大调`` → (swing-standard, ``写一首…``)
    优先已学包；正式名匹配失败再试全部包（专家包可 /style 点名，但会提示弱）。
    """
    arg = (arg or '').strip()
    if not arg:
        return None, ''
    g = skill_lib.match_genre_by_name(arg, learned_only=True, allow_prefix=True)
    if not g:
        g = skill_lib.match_genre_by_name(arg, learned_only=False, allow_prefix=True)
    if not g:
        return None, arg
    leftover = arg
    for token in (g.get('name'), g.get('name_en'), g.get('pack_id'),
                  (g.get('id') or '').split(':', 1)[-1]):
        if not token:
            continue
        t = str(token)
        low = leftover.lower()
        tlow = t.lower()
        if leftover.startswith(t):
            leftover = leftover[len(t):]
            break
        idx = low.find(tlow)
        if idx == 0:
            leftover = leftover[len(t):]
            break
        if idx > 0:
            # 正式名夹在中间：少见，不剥离以免误伤
            continue
    leftover = re.sub(r'^[的一首做写请帮我，,。.\s/]+', '', leftover.strip())
    leftover = re.sub(r'\s+', ' ', leftover).strip(' ，,。.')
    if len(leftover) < 2:
        leftover = ''
    return g, leftover


def resolve_style_phrase(arg: str, llm_available: bool = False) -> Tuple[str, str, Optional[str]]:
    """Map /style argument → (label, leftover_nl, genre_id).

    主路径：按风格包**正式名**前缀剥离（不用关键词表）。多余自然语言进 leftover，
    触发创曲。有 LLM 时不再用 style_routing 关键词；无 LLM 才弱回退。
    """
    arg = (arg or '').strip()
    if not arg:
        return '', '', None
    g, leftover = _peel_genre_prefix(arg)
    if g:
        label = g.get('name') or g.get('pack_id') or g.get('id') or arg
        return str(label), leftover, g.get('id')
    # 整段不是正式风格名
    if llm_available:
        # 交给后续 LLM 目录解释；整段既当风格描述也当创作 brief（leftover=arg）
        # 这样 ``/style 写一首爵士摇摆 Bb大调`` 仍会开跑创曲
        return arg, arg, None
    try:
        import style_routing
        g2 = style_routing.best_match(arg)
    except Exception:
        g2 = skill_lib.match_genre(arg)
    if g2:
        label = g2.get('name') or g2.get('pack_id') or g2.get('id') or arg
        _g, leftover2 = _peel_genre_prefix(arg)
        return str(label), leftover2 or '', g2.get('id')
    if len(arg) <= 24 and not re.search(r'[，,。]|快板|慢板|做|写|一首', arg):
        return arg, '', None
    return arg[:24].rstrip('，,。 '), arg, None


def _cmd_assist(session: ComposeSession, arg: str) -> str:
    """辅助作曲：开关 / 风格 / 灵感重排 / 和声轨（程序化，不调 LLM）。

    用法：
      /assist on|off
      /assist style jazz/swing-standard
      /assist inspire stepwise|jazzy|angular|balanced|arpeggio
      /assist harmony 1=ii7 2=V7 3=Imaj7
      /assist harmony clear
      /assist status
    """
    from music_score.assist_engine import INSPIRATION_PROFILES
    a = (arg or '').strip()
    prefs = session.prefs
    if not a or a.lower() in ('status', '?', 'help'):
        on = prefs.get('assist_enabled', True)
        return (
            f"辅助作曲：{'ON' if on else 'OFF'} · "
            f"风格={prefs.get('genre_id') or '（未设）'} · "
            f"灵感={prefs.get('assist_inspiration') or 'balanced'} · "
            f"和声条数={len(prefs.get('assist_harmony') or {})}\n"
            f"子命令：on|off · style <包> · inspire <"
            f"{'|'.join(INSPIRATION_PROFILES)}> · "
            f"harmony <小节=级数…|clear>"
        )
    parts = a.split(None, 1)
    head = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ''
    if head in ('on', 'off', '1', '0', 'true', 'false'):
        on = head in ('on', '1', 'true')
        prefs['assist_enabled'] = on
        return f'辅助作曲 → {"ON" if on else "OFF"}'
    if head == 'style':
        if not rest:
            return '用法：/assist style jazz/swing-standard'
        # 复用风格解析
        try:
            from style_routing import resolve_style_phrase
            label, leftover, gid = resolve_style_phrase(rest)
            if gid:
                prefs['genre_id'] = gid
                prefs['style'] = label or rest
                return f'辅助风格 → {label or gid}'
        except Exception:
            pass
        prefs['genre_id'] = rest
        return f'辅助风格 → {rest}'
    if head in ('inspire', 'inspiration', 'mood'):
        key = (rest or 'balanced').lower().strip()
        if key not in INSPIRATION_PROFILES:
            return (f'未知灵感档 {rest!r}（可用：'
                    f'{", ".join(INSPIRATION_PROFILES)}）')
        prefs['assist_inspiration'] = key
        return f'灵感重排 → {key}'
    if head == 'harmony':
        if not rest or rest.lower() == 'clear':
            prefs['assist_harmony'] = {}
            return '已清空建议和声轨'
        harm = dict(prefs.get('assist_harmony') or {})
        import re as _re
        for tok in rest.replace(',', ' ').split():
            m = _re.match(r'^(\d+)\s*=\s*(\S+)$', tok)
            if not m:
                return f'无法解析和声标记 {tok!r}（例：1=ii7 2=V7）'
            harm[int(m.group(1))] = m.group(2)
        prefs['assist_harmony'] = harm
        shown = ' '.join(f'{b}={r}' for b, r in sorted(harm.items())[:8])
        return f'建议和声轨已更新：{shown}'
    return (f'未知 /assist 子命令 {head!r}。'
            f'试 /assist status')


def apply_command(session: ComposeSession, cmd: str, arg: str) -> str:
    """处理本地命令；返回提示文字。特殊标记：__DEMO__ / __PIPELINE__:<edit>"""
    if cmd in ('/help', '/?'):
        from help_doc import build_help
        return build_help(arg)
    if cmd == '/config':
        return config_summary()
    if cmd in ('/clear', '/new'):
        session.reset(keep_prefs=(cmd == '/clear'))
        if cmd == '/new':
            session.composition_id = str(uuid.uuid4())[:8]
            session.prefs['genre_id'] = ''
            session.prefs.pop('_style_leftover', None)
        return '已开始新会话。' if cmd == '/new' else '已清空对话与当前曲目状态。'
    if cmd == '/key':
        token = arg.replace(' ', '').lower()
        if token not in KEY_ALIASES:
            return '示例：/key Am  /key G  /key Dm'
        sharps, mode = KEY_ALIASES[token]
        session.prefs['key_sharps'] = sharps
        session.prefs['mode'] = mode
        _mark_explicit(session, 'key_sharps', 'mode')
        session._pending_edit = 'transpose' if session.score_spec else None
        return f'调性 → {arg}（sharps={sharps}, {mode}）'
    if cmd == '/tempo':
        try:
            bpm = int(arg)
            assert 40 <= bpm <= 240
        except Exception:
            return '示例：/tempo 72'
        session.prefs['tempo'] = bpm
        _mark_explicit(session, 'tempo')
        session._pending_edit = 'tempo' if session.score_spec else None
        return f'速度 → ♩={bpm}'
    if cmd == '/bars':
        try:
            n = int(arg)
            assert 4 <= n <= 32
        except Exception:
            return '示例：/bars 16（4–32）'
        session.prefs['bars'] = n
        _mark_explicit(session, 'bars')
        return f'小节数 → {n}'
    if cmd == '/addbars':
        try:
            n = int(arg)
            assert 1 <= n <= 32
        except Exception:
            return '示例：/addbars 4（在当前乐谱末尾追加 1–32 空小节）'
        return f'__ADDBARS__:{n}'
    if cmd in ('/addpart', '/addvoice'):
        tok = (arg or 'bass').strip() or 'bass'
        return f'__ADDPART__:{tok}'
    if cmd in ('/accomp', '/accompaniment'):
        pat = (arg or 'bass').strip() or 'bass'
        return f'__ACCOMP__:{pat}'
    if cmd == '/meter':
        m = arg.strip()
        if not re.match(r'^[1-9]\d*/[1-9]\d*$', m):
            return '示例：/meter 4/4  /meter 6/8（分母不能为 0）'
        num, den = m.split('/')
        if int(num) < 1 or int(den) not in (1, 2, 4, 8, 16, 32):
            return f'拍号 {m} 不合法（分母需为 1/2/4/8/16/32）'
        session.prefs['meter'] = m
        _mark_explicit(session, 'meter')
        return f'拍号 → {m}'
    if cmd == '/style':
        llm_available = bool((load_llm_config() or {}).get('api_key'))
        label, leftover, gid = resolve_style_phrase(arg, llm_available=llm_available)
        if not label and not arg:
            return '示例：/style 摇摆标准曲（爵士） 写一首Bb大调  或  /style 圆舞曲 快板轻盈'
        session.prefs['style'] = label or arg
        session.prefs['genre_id'] = gid or ''
        if leftover:
            session.prefs['_style_leftover'] = leftover
        else:
            session.prefs.pop('_style_leftover', None)
        msg = f'风格 → {session.prefs["style"]}'
        if gid:
            msg += f'（已绑定 {gid}）'
            if not skill_lib.is_learned(str(gid)):
                msg += ' ⚠ 该包尚未灌语料，生成可能偏弱；优先选已学包'
        else:
            msg += '（未绑定风格包：将由 LLM 按已学风格目录解释；也可 /style <正式中文名>）'
        if leftover:
            msg += f'\n创作描述已记下，将开始生成：{leftover}'
        return msg
    if cmd in _RETIRED_COMPOSITION_CMDS:
        return (f'{cmd} 已停用。{_RETIRED_COMPOSITION_CMDS[cmd]}')
    if cmd in _RETIRED_EDIT_CMDS:
        # 作曲主路径只保留 LLM → /compose-grammar；编辑类命令一律退役。
        # **刻意不删底层实现**：选区改写与编辑管线仍被 tests/ 与 studio 的
        # 历史会话引用，删除会连带破坏可复现性。这里只断入口。
        return (f'{cmd} 已停用。作曲请用 /compose-grammar；'
                '例：/compose-grammar 交响曲 快板')
    if cmd == '/groove':
        # 确定性 Groove / 人性化后处理（仅导出/回放 MIDI 时生效，不改符号谱）
        a = (arg or '').strip().lower()
        if a in ('off', '关', 'none', '0'):
            session.prefs['groove'] = {'on': False}
            return 'Groove → off：导出/回放不做人性化后处理'
        nums = []
        for x in (arg or '').split():
            try:
                nums.append(float(x))
            except ValueError:
                pass
        swing = max(0.0, min(0.6, nums[0])) if len(nums) >= 1 else 0.12
        humanize = max(0.0, min(1.0, nums[1])) if len(nums) >= 2 else 0.35
        session.prefs['groove'] = {
            'on': True, 'swing': swing, 'humanize': humanize,
            'seed': session.composition_id or 'groove',
        }
        return (f'Groove → on（swing={swing:.2f}, humanize={humanize:.2f}）；'
                f'导出/回放时生效')
    if cmd == '/system':
        return ('/system 已停用。新曲请用 /compose-grammar。')
    if cmd == '/refine':
        return ('/refine 已停用。新曲请用 /compose-grammar。')
    if cmd == '/import':
        # MusicXML 往返导入 + 再校验（对标 AIVA 的 DAW 内编辑闭环）
        a = (arg or '').strip()
        if not a:
            return ('用法：/import <MusicXML 路径>  —— 导入外部（如 DAW 编辑过的）'
                    'MusicXML，用确定性规则引擎再校验并重新导出规范化文件')
        return f'__IMPORT__:{a}'
    if cmd == '/reverse-prompt':
        a = (arg or '').strip()
        return f'__REVERSE__:{a}'
    if cmd == '/replay':
        a = (arg or '').strip()
        return f'__REPLAY__:{a}'
    if cmd == '/learn-grammar':
        return f'__LEARN_GRAMMAR__:{(arg or "").strip()}'
    if cmd == '/learn-all':
        return f'__LEARN_ALL__:{(arg or "").strip()}'
    if cmd == '/compose-grammar':
        return f'__COMPOSE_GRAMMAR__:{(arg or "").strip()}'
    if cmd == '/brief-expand':
        a = (arg or '').strip()
        if not a:
            return '用法：/brief-expand <简单提示词>  —— 确定性扩写为专业分段 brief（不生成乐谱）'
        return f'__BRIEF_EXPAND__:{a}'
    if cmd == '/chords':
        # 和弦进行顾问；末尾加 write → 写入钢琴大谱表四部和声
        a = (arg or '').strip()
        toks = a.split()
        if toks and toks[-1].lower() == 'write':
            body = ' '.join(toks[:-1]).strip()
            return f'__SATB__:{body}'
        return f'__CHORDS__:{a}'
    if cmd == '/satb':
        a = (arg or '').strip()
        if not a:
            return ('用法：/satb [调性] <进行>  —— 在钢琴大谱表上写出四部和声'
                    '（RH=S+A，LH=T+B）。例：/satb I V vi IV')
        return f'__SATB__:{a}'
    if cmd == '/assist':
        return _cmd_assist(session, arg)
    if cmd == '/render':
        # 渲染增强：soundfont / 混响 / 力度（确定性、表演层、不调 LLM）
        try:
            import json as _json
            from render_enhance import preset as _rpreset, VELOCITY_PRESETS
            kw: Dict[str, Any] = {}
            tokens = (arg or '').split()
            for t in tokens:
                tl = t.lower()
                if tl in ('piano',):
                    kw['piano'] = True
                elif tl in ('gm', 'auto'):
                    kw['piano'] = False   # gm/auto 走通用声源（该分支内 tl 必不是 piano）
                elif tl in VELOCITY_PRESETS:
                    kw['velocity'] = tl
                elif t.endswith('.sf2') or t.endswith('.sf3'):
                    kw['soundfont'] = t
                else:
                    try:
                        fv = float(t)
                        if 0.0 <= fv <= 0.9:
                            kw['reverb'] = fv
                    except ValueError:
                        pass
            p = _rpreset(**kw)
            return '__RENDER__:' + _json.dumps(p.to_dict(), ensure_ascii=False)
        except Exception as e:
            return f'渲染预设解析失败：{e}'
    if cmd == '/batch':
        # 风格包批量生成流水线（确定性、可审计；复用 score_learning + 风格插值内核）
        import json as _json
        arg_s = (arg or '').strip()
        packs: List[str] = []
        if arg_s:
            packs = [x.strip() for x in re.split(r'[,\s]+', arg_s) if x.strip()]
        return '__BATCH__:' + _json.dumps({'packs': packs}, ensure_ascii=False)
    if cmd == '/fugue':
        a = (arg or '').strip().lower()
        if a in ('auto', 'on', '开', '段', ''):
            session.prefs['fugue'] = 'auto'
            return '赋格 → auto：仅当 brief 明确提到赋格/卡农/创意曲等才加；未点名则不加'
        if a in ('full', '全', '整首'):
            session.prefs['fugue'] = 'full'
            return '赋格 → full：整首写成赋格'
        if a in ('off', '关', 'none', '0'):
            session.prefs['fugue'] = 'off'
            return '赋格 → off：不加赋格段'
        return '示例：/fugue auto（默认，brief 提到才加） | /fugue full（整首赋格） | /fugue off'
    if cmd == '/voices':
        try:
            v = int(str(arg).strip())
            assert v in (0, 2, 3, 4)
        except Exception:
            return '示例：/voices 3（0=由风格自动推导，2/3/4=声部数）'
        session.prefs['voices'] = v
        return f'声部数 → {v}（0=自动）'
    if cmd == '/demo':
        return '__DEMO__'
    if cmd == '/export':
        if not session.score_spec:
            return '当前没有可导出的曲目。'
        return '__EXPORT__'
    if cmd == '/analyze':
        if not session.plan and not session.score_spec:
            return '尚无可分析的曲目。'
        return '__ANALYZE__'
    if cmd == '/advice':
        return '/advice 已停用。作曲请用 /compose-grammar。'
    if cmd == '/apply':
        return '/apply 已停用。作曲请用 /compose-grammar。'
    if cmd == '/learn-score':
        return '__LEARN_SCORE__:' + (arg or '').strip()
    edit_map = {
        '/extend': 'extend',
        '/develop': 'develop',
        '/variation': 'variation',
        '/harmonize': 'harmonize',
        '/orchestrate': 'orchestrate',
        '/transpose': 'transpose',
        '/shorter': 'shorter',
        '/longer': 'longer',
        '/simplify': 'simplify',
        '/complexify': 'complexify',
    }
    if cmd in edit_map:
        if cmd == '/extend' and arg.isdigit():
            session.prefs['bars'] = min(32, max(4, int(arg)))
            _mark_explicit(session, 'bars')
        if cmd == '/transpose' and arg:
            token = arg.replace(' ', '').lower()
            if token in KEY_ALIASES:
                session.prefs['key_sharps'], session.prefs['mode'] = KEY_ALIASES[token]
                _mark_explicit(session, 'key_sharps', 'mode')
        if cmd == '/orchestrate' and arg:
            session.prefs['instrumentation'] = arg
        intent = edit_map[cmd]
        if not session.score_spec:
            return '还没有曲目。请先用自然语言创作，或 /demo。'
        session._pending_edit = intent
        return f'__PIPELINE__:{intent}'
    return f'未知命令 {cmd}。输入 /help。'


# ---------- score build / validate ----------

def _normalize_events(events: List[Any], bars: int, ts: str,
                      meter_changes: Optional[List[Tuple[int, str]]] = None,
                      extra_ql: float = 0.0) -> List[Tuple[Any, str, Optional[str]]]:
    target = _total_ql(bars, ts, meter_changes) + extra_ql
    out: List[Tuple[Any, str, Optional[str]]] = []
    total = 0.0
    for ev in events or []:
        if not isinstance(ev, (list, tuple)) or len(ev) < 2:
            continue
        pitch, dur = ev[0], str(ev[1])
        mark = str(ev[2]).strip() if len(ev) >= 3 and ev[2] else None
        if dur not in DUR_QL and dur not in _EXTRA_DUR_QL:
            dur = 'quarter'
        ql = _dur_ql(dur)
        if total + ql > target + 1e-6:
            break
        out.append((None if pitch in (None, 'rest', 'Rest', '') else pitch, dur, mark))
        total += ql
    while total + 1e-6 < target:
        remain = target - total
        if remain >= 1.0:
            out.append((None, 'quarter', None)); total += 1.0
        elif remain >= 0.5:
            out.append((None, 'eighth', None)); total += 0.5
        else:
            out.append((None, '16th', None)); total += 0.25
    return out


def _make_part(name: str, program: Optional[int], clef_name: str, part_id: str,
               part_cls=None, display_name: Optional[str] = None):
    key_name = (name or 'Violin').strip()
    low = key_name.lower()
    # 乐器匹配：先全名（'cello'），再首词（'Piano RH'→'piano'）
    factory_key = low if low in INSTRUMENT_FACTORY else low.split()[0]
    if factory_key in INSTRUMENT_FACTORY:
        cls, prog, default_clef = INSTRUMENT_FACTORY[factory_key]
        inst = cls()
        if program is None:
            program = prog
        if not clef_name:
            clef_name = default_clef
    else:
        inst = instrument.Instrument()
        if program is None:
            program = 0
    clef_obj = {
        'bass': m21clef.BassClef(),
        'alto': m21clef.AltoClef(),
        'tenor': m21clef.TenorClef(),
        'treble8vb': m21clef.Treble8vbClef(),
        'treble_8vb': m21clef.Treble8vbClef(),
    }.get((clef_name or 'treble').lower(), m21clef.TrebleClef())
    part = (part_cls or stream.Part)(id=part_id)
    if display_name:
        inst.instrumentName = display_name
        inst.partName = display_name
    else:
        inst.instrumentName = key_name
        inst.partName = key_name
    try:
        inst.midiProgram = int(program)
    except Exception:
        pass
    part.insert(0, inst)
    part.insert(0, clef_obj)
    return part


def _parse_part_dynamics(raw) -> Dict[int, str]:
    """[[bar,'pp'],…] 或 [{'bar':1,'value':'pp'},…] → {bar(1-based): value}。"""
    out: Dict[int, str] = {}
    for item in raw or []:
        try:
            if isinstance(item, dict):
                bar = int(item.get('bar') or item.get('measure') or 0)
                val = str(item.get('value') or item.get('dynamic') or '').strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                bar, val = int(item[0]), str(item[1]).strip()
            else:
                continue
        except Exception:
            continue
        if bar >= 1 and val:
            out.setdefault(bar, val)
    return out


def _restore_part_dynamics(spec: Dict[str, Any], backup: List[Any]):
    """修复 LLM 丢字段时，按声部序号回填原 dynamics。"""
    parts = spec.get('parts') or []
    for i, p in enumerate(parts):
        if not p.get('dynamics') and i < len(backup) and backup[i]:
            p['dynamics'] = backup[i]


def _restore_score_marks(spec: Dict[str, Any], tempo_backup, meter_backup):
    """修复 LLM 丢谱级字段时，回填 tempo_marks / meter_changes。"""
    if not spec.get('tempo_marks') and tempo_backup:
        spec['tempo_marks'] = tempo_backup
    if not spec.get('meter_changes') and meter_backup:
        spec['meter_changes'] = meter_backup


EVENT_MARKS = {
    # events 第三元素 → music21 表现
    'staccato': 'staccato',
    'accent': 'accent',
    'tenuto': 'tenuto',
    'fermata': 'fermata',
    'slur-start': 'slur-start',
    'slur-end': 'slur-end',
}


def _apply_event_marks(el, mark: Optional[str]):
    if not mark:
        return
    try:
        if mark == 'staccato':
            el.articulations.append(m21articulations.Staccato())
        elif mark == 'accent':
            el.articulations.append(m21articulations.Accent())
        elif mark == 'tenuto':
            el.articulations.append(m21articulations.Tenuto())
        elif mark == 'fermata':
            el.expressions.append(m21expressions.Fermata())
    except Exception:
        pass


def _add_events(part, events, ts: str = '4/4', dynamics=None,
                meter_changes: Optional[List[Tuple[int, str]]] = None,
                bars: Optional[int] = None):
    bar_ql = _ql_per_bar(ts)
    if bars:
        starts, _ts_list = _bar_grid(bars, ts, meter_changes)
    else:
        starts = None
    dyn_map = _parse_part_dynamics(dynamics)
    # 力度斜坡：相邻两个力度标记之间"保持段前半 + 后半线性过渡"，
    # 播放时渐强/渐弱自然，不再一刀切跳变
    dyn_points: List[Tuple[float, int]] = []
    for b in sorted(dyn_map):
        v = DYNAMIC_VELOCITIES.get(str(dyn_map[b]).lower())
        if v is None:
            continue
        try:
            off = starts[b - 1] if (starts is not None and 1 <= b <= len(starts)) \
                else (b - 1) * bar_ql
        except Exception:
            off = (b - 1) * bar_ql
        dyn_points.append((float(off), int(v)))
    dyn_points.sort()

    def _vel_at(t: float) -> Optional[int]:
        if not dyn_points:
            return None
        if t <= dyn_points[0][0]:
            return dyn_points[0][1]
        if t >= dyn_points[-1][0]:
            return dyn_points[-1][1]
        for i in range(len(dyn_points) - 1):
            t0, v0 = dyn_points[i]
            t1, v1 = dyn_points[i + 1]
            if t0 <= t < t1:
                span = t1 - t0
                if span < 1e-9:
                    return v1
                hold_end = t0 + span * 0.5
                if t < hold_end:
                    return v0
                f = (t - hold_end) / (t1 - hold_end)
                return int(round(v0 + (v1 - v0) * f))
        return dyn_points[-1][1]

    t = 0.0
    slur_notes = []          # 当前连音线内的音符
    all_slurs: List[List[Any]] = []
    for pitch, dur, mark in events:
        vel = _vel_at(t)
        # dotted_* → (基础时值, 附点数)：music21 的 type= 不认 dotted_ 拼写
        _base, _dots = _DUR_BASE_DOTS.get(str(dur), (str(dur), 0))
        if pitch is None:
            part.append(m21note.Rest(type=_base, dots=_dots) if _dots
                        else m21note.Rest(type=_base))
        else:
            if isinstance(pitch, (list, tuple)):
                el = (m21chord.Chord(list(pitch), type=_base, dots=_dots) if _dots
                      else m21chord.Chord(list(pitch), type=_base))
            else:
                try:
                    el = (m21note.Note(str(pitch), type=_base, dots=_dots) if _dots
                          else m21note.Note(str(pitch), type=_base))
                except Exception:
                    el = (m21note.Rest(type=_base, dots=_dots) if _dots
                          else m21note.Rest(type=_base))
            if vel is not None:
                try:
                    el.volume.velocity = vel
                except Exception:
                    pass
            # 重音的 velocity 抬升由 studio 导出 MIDI 后处理统一负责
            _apply_event_marks(el, mark)
            part.append(el)
            if mark == 'slur-start':
                slur_notes = [el]
            elif slur_notes:
                slur_notes.append(el)
                if mark == 'slur-end':
                    if len(slur_notes) >= 2:
                        all_slurs.append(slur_notes)
                    slur_notes = []
        t += _dur_ql(dur)
    # 未闭合的连音线：到末音符为止
    if len(slur_notes) >= 2:
        all_slurs.append(slur_notes)
    for notes in all_slurs:
        try:
            part.insert(0, m21spanner.Slur(notes))
        except Exception:
            pass


#: 最近一次 spec_to_music21 的记谱规则日志（可解释性：谁否掉了谁）
_LAST_NOTATION_LOG: Any = None


def last_notation_log() -> Any:
    """取最近一次 :func:`spec_to_music21` 的记谱规则日志（``notation.RuleLog``）。"""
    return _LAST_NOTATION_LOG


def _pickup_ql(spec: Dict[str, Any]) -> float:
    """spec 的弱起拍数；缺省、非法或不小于一小节时为 0（不弱起）。"""
    try:
        v = float(spec.get('pickup_ql') or 0.0)
    except (TypeError, ValueError):
        return 0.0
    bar_ql = _ql_per_bar(str(spec.get('time_signature') or '4/4'))
    return v if 0.0 < v < bar_ql else 0.0


def _strip_pickup_placeholder(part, placeholder, pad: float) -> None:
    measures = list(part.getElementsByClass(stream.Measure))
    if not measures:
        part.remove(placeholder, shiftOffsets=True)
        return
    m0 = measures[0]
    if placeholder in m0:
        m0.remove(placeholder)
    for el in list(m0.notesAndRests):
        m0.setElementOffset(el, m0.elementOffset(el) - pad)
    m0.paddingLeft = pad
    m0.number = 0
    try:
        m0.showNumber = stream.enums.ShowNumber.NEVER     # MusicXML implicit="yes"
    except Exception:
        pass
    for i, m in enumerate(measures[1:], start=1):
        part.setElementOffset(m, part.elementOffset(m) - pad)
        m.number = i
    for el in list(part.getElementsNotOfClass(stream.Measure)):
        off = part.elementOffset(el)
        if off >= pad:
            part.setElementOffset(el, off - pad)


def spec_to_music21(spec: Dict[str, Any], notation: bool = True,
                    notation_rules: Optional[Dict[str, bool]] = None
                    ) -> stream.Score:
    """spec → music21 分数。

    默认会走记谱层（等音拼写 / 符干 / 连符尾 / 演奏法 / 排版 / 可演奏性），
    规则留痕见 :func:`last_notation_log`；``notation=False`` 退回旧行为
    （扁平流、不排版）。单项规则可用 ``notation_rules={'beams': False}`` 关闭。
    """
    global _LAST_NOTATION_LOG
    bars = max(4, min(32, int(spec.get('bars') or 8)))
    ts = str(spec.get('time_signature') or '4/4')
    tempo_n = int(spec.get('tempo') or 120)
    sharps = int(spec.get('key_sharps') or 0)
    meter_changes = _parse_meter_changes(spec.get('meter_changes'), bars, ts)
    tempo_marks = _parse_tempo_marks(spec.get('tempo_marks'))
    starts, _ts_list = _bar_grid(bars, ts, meter_changes)
    # 弱起：第 0 小节只有 pickup_ql 拍。先用占位休止补成完整小节让 makeMeasures 正确
    # 划线，之后删掉占位并设 paddingLeft；第 1..bars 小节的起点整体后移一个小节
    pickup = _pickup_ql(spec)
    pad = (_ql_per_bar(ts) - pickup) if pickup else 0.0
    if pickup:
        starts = [s + _ql_per_bar(ts) for s in starts]
    # bar-1 速度标记：作为基础速度放第一个声部（带文字，谱面可见）；
    # 其余情况全局放一个纯数值基础速度（走 LCD，不画谱面）。
    bar1_mark = None
    part_level_marks = tempo_marks
    if tempo_marks and tempo_marks[0][0] == 1:
        bar1_mark = tempo_marks[0]
        part_level_marks = tempo_marks[1:]
    score = stream.Score(id='llm_compose')
    md = m21meta.Metadata()
    md.title = str(spec.get('title') or '对话创曲')
    md.composer = str(spec.get('composer') or '对话创曲')
    score.insert(0, md)
    score.insert(0, key.KeySignature(sharps))
    score.insert(0, meter.TimeSignature(ts))
    if bar1_mark is None:
        score.insert(0, tempo.MetronomeMark(number=tempo_n))
    parts_data = spec.get('parts') or []
    if not parts_data:
        raise ValueError('作曲结果 parts 为空')
    # 钢琴大谱表：staff_groups 声明的声部（如 Piano RH/LH）共用一件乐器，
    # 以 PartStaff + 花括号 StaffGroup 导出为单个 MusicXML part（<staves>2</staves>）
    staff_groups: List[Tuple[List[int], str, str]] = []
    grouped_idx: set = set()
    for g in (spec.get('staff_groups') or []):
        try:
            idxs = [int(i) for i in (g.get('parts') or [])]
        except (TypeError, ValueError):
            continue
        idxs = [i for i in idxs if 0 <= i < len(parts_data)]
        if len(idxs) >= 2:
            staff_groups.append((idxs, str(g.get('symbol') or 'brace'),
                                 str(g.get('name') or 'Piano')))
            grouped_idx.update(idxs)
    first_of_group = {idxs[0]: (idxs, sym, gname)
                      for idxs, sym, gname in staff_groups}
    # 段落级转调：[[bar, sharps, mode], …]（每个谱表都要写新调号）
    key_changes = []
    for kc in (spec.get('key_changes') or []):
        try:
            kb, ks_, km_ = int(kc[0]), int(kc[1]), str(kc[2])
        except (TypeError, ValueError, IndexError):
            continue
        if 1 <= kb <= bars and -7 <= ks_ <= 7 and km_ in ('major', 'minor'):
            key_changes.append((kb, ks_, km_))
    key_changes.sort()
    built = []
    for i, p in enumerate(parts_data):
        name = str(p.get('name') or f'Part{i + 1}')
        is_grouped = i in grouped_idx
        if i in first_of_group:
            _idxs, _sym, _gname = first_of_group[i]
            part = _make_part(name, p.get('program'), str(p.get('clef') or 'treble'),
                              f'p{i}', part_cls=stream.PartStaff,
                              display_name=_gname)
        elif is_grouped:
            # 合并导出时 MusicXML part 名取自第一行谱表，这里保留原始名
            # （Piano LH），供内部模型/报告/播放器识别声部
            part = _make_part(name, p.get('program'), str(p.get('clef') or 'treble'),
                              f'p{i}', part_cls=stream.PartStaff)
        else:
            part = _make_part(name, p.get('program'), str(p.get('clef') or 'treble'), f'p{i}')
        placeholder = None
        if pickup:
            placeholder = m21note.Rest(quarterLength=pad)
            part.append(placeholder)
        _add_events(part, _normalize_events(p.get('events') or [], bars, ts, meter_changes,
                                            extra_ql=pickup),
                    ts, p.get('dynamics'), meter_changes, bars)
        # 基础拍号必须插进每个声部：否则 makeMeasures 时声部内没有拍号，
        # music21 默认按 4/4 划小节（3/4 曲子会少小节、小节线错位）
        try:
            part.insert(0.0, meter.TimeSignature(ts))
        except Exception:
            pass
        # 基础调号同样必须逐声部插入：score 级 KeySignature 不会传播到
        # MusicXML <key>（music21 10.x 实测），非 C 调的调号会整段丢失
        try:
            part.insert(0.0, key.KeySignature(sharps))
        except Exception:
            pass
        # 曲中换拍号：在每个变化段首插入 TimeSignature
        for bar, tsx in meter_changes:
            try:
                part.insert(starts[bar - 1], meter.TimeSignature(tsx))
            except Exception:
                pass
        # 力度记号：插入到对应小节起始偏移（谱面显示 + MusicXML 导出）；
        # 大谱表只在第一行谱表写力度，避免两行谱表重复显示同一记号
        if not is_grouped or i in first_of_group:
            for bar, val in sorted(_parse_part_dynamics(p.get('dynamics')).items()):
                try:
                    off = starts[min(bar, bars) - 1]
                    part.insert(off, m21dynamics.Dynamic(val))
                except Exception:
                    pass
        # 速度标记（含曲中变速）：放第一个声部。数值走 MetronomeMark（驱动 MIDI
        # tempo），文字走 TextExpression（MusicXML <words> 双向保真），显示时合并。
        if i == 0:
            if bar1_mark is not None:
                _b, _t, _n = bar1_mark
                try:
                    if _n is not None:
                        part.insert(0.0, tempo.MetronomeMark(number=_n))
                    if _t:
                        part.insert(0.0, m21expressions.TextExpression(_t))
                except Exception:
                    pass
            for bar, text, bpm in part_level_marks:
                try:
                    off = starts[min(bar, bars) - 1]
                    if bpm is not None:
                        part.insert(off, tempo.MetronomeMark(number=bpm))
                    if text:
                        part.insert(off, m21expressions.TextExpression(text))
                except Exception:
                    pass
        # 段落级转调：在每个谱表的对应小节插入新调号（asKey 带 mode 写入 MusicXML）
        for kb, ks_, km_ in key_changes:
            try:
                part.insert(starts[kb - 1],
                            key.KeySignature(ks_).asKey(km_))
            except Exception:
                pass
        # 记谱层需要 Measure；扁平流在这里补齐小节（曲中换拍号由 makeMeasures
        # 依据已插入的 TimeSignature 自动处理）
        if notation:
            try:
                part.makeMeasures(inPlace=True)
            except Exception:
                pass
        if placeholder is not None:
            _strip_pickup_placeholder(part, placeholder, pad)
        built.append(part)
    for p in built:
        score.insert(0, p)
    # 大谱表：花括号组合（music21 导出时合并为单 part + <staves>2</staves>）
    for idxs, sym, gname in staff_groups:
        try:
            from music21 import layout as m21layout
            sg = m21layout.StaffGroup([built[i] for i in idxs if i < len(built)],
                                      name=gname, symbol=sym, barTogether=True)
            score.insert(0, sg)
        except Exception:
            pass
    log = None
    if notation:
        try:
            from notation import apply_notation, RuleLog
            log = apply_notation(score, spec, rules=notation_rules)
        except Exception:
            log = None
    _LAST_NOTATION_LOG = log
    return score


def _is_rest_ev(ev) -> bool:
    return not isinstance(ev, (list, tuple)) or len(ev) < 2 or ev[0] in (
        None, 'rest', 'Rest', 'REST', '',
    )


def _events_bar_map(events, bars: int, ts: str,
                    meter_changes: Optional[List[Tuple[int, str]]] = None) -> List[List[Any]]:
    """Split events into per-bar lists (no padding; 超出曲尾的事件按偏移夹进末小节)."""
    starts, _ts_list = _bar_grid(bars, ts, meter_changes)
    per: List[List[Any]] = [[] for _ in range(bars)]
    t = 0.0
    for ev in events or []:
        if not isinstance(ev, (list, tuple)) or len(ev) < 2:
            continue
        ql = _dur_ql(str(ev[1]))
        bi = _offset_to_bar(t, starts) - 1
        if 0 <= bi < bars:
            per[bi].append(ev)
        t += ql
    return per


def dead_tail_info(events, bars: int, ts: str,
                   meter_changes: Optional[List[Tuple[int, str]]] = None):
    """(first-half active bar count, trailing rest-only bar count, last active bar idx)."""
    per = _events_bar_map(events, bars, ts, meter_changes)
    active = [any(not _is_rest_ev(e) for e in b) for b in per]
    first_notes = sum(1 for a in active[:max(1, bars // 2)] if a)
    last = max((j for j, a in enumerate(active) if a), default=-1)
    dead = bars - 1 - last
    return first_notes, dead, last


def _dead_tail_min(bars: int) -> int:
    """尾段连续空/休止小节数达到该值才算塌陷（2 小节起步，长曲按 1/8）。"""
    return max(2, bars // 8)


def _fill_dead_tail(spec: Dict[str, Any]) -> List[str]:
    """Local fallback for collapsed tails: refill a part's trailing rest bars
    by repeating its own earlier active material (keeps meter, no rests-pad)."""
    fixed: List[str] = []
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    mc = _parse_meter_changes(spec.get('meter_changes'), bars, ts)
    for i, p in enumerate(spec.get('parts') or []):
        events = list(p.get('events') or [])
        if not events:
            continue
        first_notes, dead, last = dead_tail_info(events, bars, ts, mc)
        if dead < _dead_tail_min(bars) or first_notes < 1:
            continue
        per = _events_bar_map(events, bars, ts, mc)
        # collect active bars just before the dead zone as source material
        src: List[List[Any]] = []
        j = last
        while j >= 0 and len(src) < dead:
            if any(not _is_rest_ev(e) for e in per[j]):
                src.insert(0, [list(e) for e in per[j]])
            j -= 1
        if not src:
            continue
        k = 0
        for b in range(last + 1, bars):
            per[b] = [list(e) for e in src[k % len(src)]]
            k += 1
        p['events'] = _bars_to_events(per)
        fixed.append(str(p.get('name') or f'声部{i + 1}'))
    return fixed


# ---------- 过渡生硬：确定性兜底（速度抖动 / 缺力度 / 调号错 / 时值缺口） ----------

def _fit_duration_name(ql: float) -> Optional[str]:
    """不超过 ql 的最大可用时值名。"""
    best = None
    for name, v in DUR_QL.items():
        if v <= ql + 1e-9 and (best is None or v > DUR_QL[best]):
            best = name
    return best


def _compact_tempo_marks(spec: Dict[str, Any]) -> bool:
    """相邻速度标记间隔 <2 小节（一小节内来回摆动）→ 聚簇保留末位。

    播放里的速度是阶跃变化的，1 小节内的 più mosso → rit → a tempo
    抖动听起来非常生硬；保留簇内最后一个（最新意图）。
    """
    marks = _parse_tempo_marks(spec.get('tempo_marks'))
    if len(marks) < 3:
        return False
    marks = sorted(marks, key=lambda m: m[0])
    kept = [marks[0]]
    for m in marks[1:]:
        if m[0] - kept[-1][0] < 2 and kept[-1][0] != 1:
            kept[-1] = m      # 同簇：取末位
        elif m[1] == kept[-1][1] and m[2] == kept[-1][2]:
            continue           # 与前一标记文字+速度完全相同：冗余，丢弃
        else:
            kept.append(m)
    if len(kept) == len(marks):
        return False
    spec['tempo_marks'] = [[b, t, bpm] for b, t, bpm in kept]
    return True


_DYN_LEVELS = ['pp', 'p', 'mp', 'mf', 'f', 'ff']


def _dyn_step(cur: str, delta: int) -> str:
    i = _DYN_LEVELS.index(cur) if cur in _DYN_LEVELS else 3
    return _DYN_LEVELS[max(0, min(len(_DYN_LEVELS) - 1, i + delta))]


def _dyn_from_tempo_text(text, bpm, cur, held):
    """速度标记文字 → 力度档。返回 (新力度, 回退档 held)。"""
    t = (text or '').lower()
    if ('morendo' in t or 'smorz' in t or 'calando' in t
            or 'perdendo' in t or 'estinguendo' in t):
        return 'pp', None
    if 'dim' in t:
        return _dyn_step(cur, -3), None
    if ('appassionato' in t or 'con fuoco' in t or 'stretto' in t
            or 'furioso' in t or 'con forza' in t):
        return 'ff', None
    if 'agitato' in t or 'con brio' in t:
        return _dyn_step(cur, 2), None
    if 'a tempo' in t or 'tempo primo' in t or 'tempo i' in t:
        return (held or cur), None
    if ('rit' in t or 'rall' in t or 'men' in t):
        return _dyn_step(cur, -1), (held or cur)
    if ('mosso' in t or 'animato' in t or 'allegro' in t or 'vivo' in t
            or 'presto' in t or 'più' in t or 'piu ' in t or 'piu' == t.strip()):
        return _dyn_step(cur, 1), None
    if ('andante' in t or 'adagio' in t or 'lento' in t or 'cantabile' in t
            or 'dolce' in t or 'tranquillo' in t or 'notturno' in t
            or 'nocturne' in t):
        return 'mp', None
    if bpm:
        if bpm <= 66:
            d = 'p'
        elif bpm < 80:
            d = 'mp'
        elif bpm < 96:
            d = 'mf'
        elif bpm < 112:
            d = 'f'
        else:
            d = 'ff'
        return d, None
    return cur, held


def _ensure_dynamics(spec: Dict[str, Any]) -> Optional[str]:
    """兜底：声部缺 dynamics 时补力度阶梯。

    - 部分声部缺失 → 复制力度最全声部的阶梯
    - 全部缺失 → 由速度标记文字/BPM 推断（Andante→mp、Agitato→+2、
      appassionato→ff、rit→-1、a tempo→回退、dim→-2、morendo→pp），
      无速度标记且 ≥12 小节 → 默认弧 mp→mf→f→mp
    """
    parts = spec.get('parts') or []
    if not parts:
        return None
    have_dyn = [p for p in parts if _parse_part_dynamics(p.get('dynamics'))]
    if have_dyn:
        _i, best = _part_with_dynamics(spec)
        missing = [p for p in parts if not _parse_part_dynamics(p.get('dynamics'))]
        if best and missing:
            raw = [[b, v] for b, v in sorted(best.items())]
            for p in missing:
                p['dynamics'] = [list(x) for x in raw]
            return f"已为 {len(missing)} 个缺失力度的声部补抄力度阶梯"
        return None
    marks = sorted(_parse_tempo_marks(spec.get('tempo_marks')))
    arc: List[List] = []
    cur, held = 'mp', None
    for bar, text, bpm in marks:
        cur, held = _dyn_from_tempo_text(text, bpm, cur, held)
        arc.append([bar, cur])
    if not arc or arc[0][0] != 1:
        arc.insert(0, [1, 'mp'])
    dedup = [arc[0]]
    for b, v in arc[1:]:
        if v != dedup[-1][1]:
            dedup.append([b, v])
    if len(dedup) < 2 and int(spec.get('bars') or 0) >= 12:
        dedup = [[1, 'mp'], [int(spec['bars']) // 3 + 1, 'mf'],
                 [int(spec['bars']) // 2 + 1, 'f'],
                 [3 * int(spec['bars']) // 4 + 1, 'mp']]
    if len(dedup) < 2:
        return None
    for p in parts:
        p['dynamics'] = [list(x) for x in dedup]
    return ('已按速度标记推断力度阶梯：'
            + '，'.join(f"第{b}小节 {v}" for b, v in dedup))


def _fix_key_signature(spec: Dict[str, Any]) -> Optional[str]:
    """兜底：调号与实际音符严重不符时，按内容重推（如 Db 内容标成 0 升号）。"""
    try:
        from music21 import key as m21key
        from music21 import scale as m21scale
    except Exception:
        return None
    counts: Dict[str, int] = {}
    total = 0
    for p in spec.get('parts') or []:
        for ev in p.get('events') or []:
            if not isinstance(ev, (list, tuple)) or len(ev) < 2:
                continue
            pitches = ev[0] if isinstance(ev[0], (list, tuple)) else [ev[0]]
            for q in pitches:
                if not isinstance(q, str) or not q:
                    continue
                try:
                    n = m21pitch.Pitch(q).name
                except Exception:
                    continue
                counts[n] = counts.get(n, 0) + 1
                total += 1
    if total < 20:
        return None

    def allowed_names(sharps: int, mode: str):
        try:
            k = m21key.KeySignature(sharps).asKey(mode)
            if mode == 'major':
                scales = [m21scale.MajorScale(k.tonic)]
            else:
                scales = [m21scale.MinorScale(k.tonic),
                          m21scale.HarmonicMinorScale(k.tonic)]
            out = set()
            for sc in scales:
                out |= {pp.name for pp in sc.getPitches('C0', 'B0')}
            return out
        except Exception:
            return None

    def outside(sharps: int, mode: str) -> int:
        allowed = allowed_names(sharps, mode)
        if not allowed:
            return 10 ** 9
        return sum(c for n, c in counts.items() if n not in allowed)

    cur_s = int(spec.get('key_sharps') or 0)
    cur_m = str(spec.get('mode') or 'major')
    cur_out = outside(cur_s, cur_m)
    if cur_out < 10:
        return None
    cands = [(outside(s, m), s, m)
             for s in range(-7, 8) for m in ('major', 'minor')]
    best = min(cands)
    # 大小调外音相近时（≤2 个计数差）优先大调——同调号下大/小调共享音列，
    # 依赖和声小调导音才占优的小调判定通常不可靠
    best_major = min((c for c in cands if c[2] == 'major'))
    if best[2] == 'minor' and best_major[0] <= best[0] + 2:
        best = best_major
    if best[0] < cur_out * 0.5:
        spec['key_sharps'] = best[1]
        spec['mode'] = best[2]
        return (f"调号按内容重推：{cur_s}升/{cur_m} → {best[1]}升/{best[2]}"
                f"（谱面外音 {cur_out}→{best[0]}）")
    return None


def _fix_part_lengths(spec: Dict[str, Any]) -> List[str]:
    """兜底：声部时值溢出/不足的逐小节修正（不用休止填充结尾）。

    - 每小节超写 → 就近截断（改小时值）
    - 末小节超写 → 从尾部保留（终止/Cadence 在小节末尾，更重要）
    - 尾部空小节 → 重复该声部前面的活跃材料补齐
    - 末小节相邻同音合并为收尾长音
    """
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    if bars <= 0:
        return []
    mc = _parse_meter_changes(spec.get('meter_changes'), bars, ts)
    starts, ts_list = _bar_grid(bars, ts, mc)
    caps = [_ql_per_bar(t) for t in ts_list]
    fixed: List[str] = []
    for i, p in enumerate(spec.get('parts') or []):
        events = [e for e in (p.get('events') or [])
                  if isinstance(e, (list, tuple)) and len(e) >= 2]
        if not events:
            continue
        per: List[List[Any]] = [[] for _ in range(bars)]
        spill: List[Any] = []      # 起点已越过曲尾的音符（常是 LLM 多写的终止长音）
        t = 0.0
        old_total = 0.0
        for ev in events:
            ql = _dur_ql(str(ev[1]))
            old_total += ql
            if t >= starts[-1] - 1e-9:
                if not _is_rest_ev(ev):
                    spill.append(list(ev))
                continue
            bi = _offset_to_bar(t, starts) - 1
            if bi < 0 or bi >= bars:
                t += ql
                continue
            remain = caps[bi] - (t - starts[bi])
            if ql <= remain + 1e-9:
                per[bi].append(list(ev))
                t += ql
            else:
                name = _fit_duration_name(remain)
                if name:
                    ev2 = [ev[0], name] + ([ev[2]] if len(ev) >= 3 and ev[2] else [])
                    per[bi].append(ev2)
                    t += _dur_ql(name)
                else:
                    t += ql   # 本小节放不下，整个跳过
        # 末小节：从尾部保留（终止在末尾；溢出曲尾的音符也参与末位竞争）
        fb = per[bars - 1] + spill
        if fb:
            kept: List[Any] = []
            used = 0.0
            for ev in reversed(fb):
                ql = _dur_ql(str(ev[1]))
                remain = caps[bars - 1] - used
                if ql <= remain + 1e-9:
                    kept.insert(0, ev)
                    used += ql
                else:
                    name = _fit_duration_name(remain)
                    if name:
                        ev2 = [ev[0], name] + (
                            [ev[2]] if len(ev) >= 3 and ev[2] else [])
                        kept.insert(0, ev2)
                        used += _dur_ql(name)
            per[bars - 1] = kept
        # 尾部空小节 → 用前面活跃材料补齐
        active = [any(not _is_rest_ev(e) for e in b) for b in per]
        last_active = max((j for j, a in enumerate(active) if a), default=-1)
        if last_active >= 0 and last_active < bars - 1:
            src: List[List[Any]] = []
            j = last_active
            while j >= 0 and len(src) < (bars - 1 - last_active):
                if active[j]:
                    src.insert(0, [list(e) for e in per[j]])
                j -= 1
            if src:
                k = 0
                for b in range(last_active + 1, bars):
                    per[b] = [list(e) for e in src[k % len(src)]]
                    k += 1
        # 末小节同音相邻合并为长音（收尾）
        fb = per[bars - 1]
        j = 0
        while j + 1 < len(fb):
            a, b2 = fb[j], fb[j + 1]
            if (a[0] is not None and a[0] == b2[0]
                    and str(a[1]) == str(b2[1])
                    and str(a[1]) in _MERGE_DUR):
                fb[j] = [a[0], _MERGE_DUR[str(a[1])]] + (
                    [a[2]] if len(a) >= 3 and a[2] else [])
                del fb[j + 1]
                continue
            j += 1
        new_events = _bars_to_events(per)
        new_total = sum(_dur_ql(str(e[1])) for e in new_events)
        if abs(new_total - old_total) > 1e-6 or len(new_events) != len(events):
            p['events'] = new_events
            fixed.append(str(p.get('name') or f'声部{i + 1}'))
    return fixed


def _calm_quiet_sections(spec: Dict[str, Any]) -> List[str]:
    """安静段（力度 ≤ mp）所有    声部长音合并——避免弱段落里伴奏仍在轰鸣。目标平均时值 1.0：
    只压八分及更密的运动，四分旋律不受影响。"""
    try:
        spans = _section_spans_from_dynamics(spec)
    except Exception:
        return []
    quiet = [s for s in spans
             if str(s[2]).lower() in ('ppp', 'pp', 'p', 'mp')]
    if not quiet:
        return []
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    mc = _parse_meter_changes(spec.get('meter_changes'), bars, ts)
    changes: List[str] = []
    for i, p in enumerate(spec.get('parts') or []):
        events = list(p.get('events') or [])
        if not events:
            continue
        per = _events_bar_map(events, bars, ts, mc)
        merged = 0
        for b0, b1, _v in quiet:
            # 逐小节判定（整段平均会掩盖边界小节的忙碌）
            for b in range(max(0, b0 - 1), min(bars, b1)):
                merged += _coarsen_span_events([per[b]], 1.0)
        if merged:
            p['events'] = _bars_to_events(per)
            changes.append(f"声部{i + 1} 安静段合并 {merged} 处长音")
    return changes


# ---------- 织体段落对比：检测 + 确定性强制 ----------

def _part_with_dynamics(spec) -> Tuple[int, Dict[int, str]]:
    """找力度阶梯最全的声部（划段依据）。"""
    best_i, best_dyn = 0, {}
    for i, p in enumerate(spec.get('parts') or []):
        dyn = _parse_part_dynamics(p.get('dynamics'))
        if len(dyn) > len(best_dyn):
            best_i, best_dyn = i, dyn
    return best_i, best_dyn


def _section_spans_from_dynamics(spec) -> List[Tuple[int, int, str]]:
    """由力度标记划段：[(起始小节, 结束小节, 力度)]。"""
    _i, dyn = _part_with_dynamics(spec)
    marks = sorted(dyn.items())
    if len(marks) < 3:
        return []
    bars = int(spec.get('bars') or 0)
    spans: List[Tuple[int, int, str]] = []
    for k, (bar, val) in enumerate(marks):
        end = marks[k + 1][0] - 1 if k + 1 < len(marks) else bars
        end = min(max(end, bar), bars)
        if end >= bar:
            spans.append((bar, end, val))
    return spans


def _part_section_density(p: Dict[str, Any], spans, bars: int, ts: str, mc):
    """各段真实音符的平均时值（ql）。返回 {段序号: 平均值}。"""
    starts, _t = _bar_grid(bars, ts, mc)
    sums = {i: [0.0, 0] for i in range(len(spans))}
    t = 0.0
    for ev in p.get('events') or []:
        if not isinstance(ev, (list, tuple)) or len(ev) < 2:
            continue
        ql = _dur_ql(str(ev[1]))
        bar = _offset_to_bar(t, starts)
        for i, (b0, b1, _v) in enumerate(spans):
            if b0 <= bar <= b1:
                if ev[0] is not None:
                    sums[i][0] += ql
                    sums[i][1] += 1
                break
        t += ql
    return {i: (s / n if n else 0.0) for i, (s, n) in sums.items()}


def _span_has_chords(p: Dict[str, Any], span, bars: int, ts: str, mc) -> bool:
    start, end = span[0], span[1]
    starts, _t = _bar_grid(bars, ts, mc)
    t = 0.0
    for ev in p.get('events') or []:
        if not isinstance(ev, (list, tuple)) or len(ev) < 2:
            continue
        bar = _offset_to_bar(t, starts)
        if start <= bar <= end and isinstance(ev[0], (list, tuple)):
            return True
        t += _dur_ql(str(ev[1]))
    return False


def texture_flat_info(spec):
    """检测「各段织体密度相同 + 高潮无八度加厚」。
    返回 (flat, spans, 旋律声部密度, 旋律声部idx)；flat=False 时 spans 可能为空。"""
    parts = spec.get('parts') or []
    if len(parts) < 1:
        return False, [], {}, 0
    spans = _section_spans_from_dynamics(spec)
    if len(spans) < 3:
        return False, [], {}, 0
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    mc = _parse_meter_changes(spec.get('meter_changes'), bars, ts)
    # 旋律声部：高音谱号优先，否则第一声部
    mi = next((i for i, p in enumerate(parts)
               if (p.get('clef') or 'treble').lower() == 'treble'), 0)
    dens = _part_section_density(parts[mi], spans, bars, ts, mc)
    if len(dens) < 3 or any(d <= 0 for d in dens.values()):
        return False, spans, dens, mi
    climax_i = max(dens, key=lambda i: DYNAMIC_VELOCITIES.get(
        spans[i][2].lower(), 0) or 0)
    climax_chords = _span_has_chords(parts[mi], spans[climax_i], bars, ts, mc)
    # 安静段（力度 ≤ p 的最稀疏段）应显著比高潮段稀疏，否则听感是"一种织体写到底"
    quiet_candidates = [i for i in dens
                        if DYNAMIC_VELOCITIES.get(spans[i][2].lower(), 0) <= 70]
    quiet_i = min(quiet_candidates, key=lambda i: dens[i]) if quiet_candidates \
        else min(dens, key=lambda i: dens[i])
    flat = (not climax_chords) and dens[quiet_i] <= dens[climax_i] + 0.15
    return flat, spans, dens, mi


_MERGE_DUR = {'16th': 'eighth', 'eighth': 'quarter', 'quarter': 'half',
              'half': 'whole'}


def _coarsen_span_events(per_bar, target_avg: float):
    """把指定小节组内相邻、等时值、无记号的真实音符两两合并成长音，
    直到段内平均时值达到目标或无法再合并。小节时值守恒。"""
    merged = 0
    while True:
        total, n = 0.0, 0
        for bi in per_bar:
            for ev in bi:
                if ev[0] is not None:
                    total += _dur_ql(str(ev[1]))
                    n += 1
        if n == 0 or total / n >= target_avg:
            break
        did = False
        # 优先合并相同音高（重复音 → 保持音），再合并任意相邻音
        for need_same in (True, False):
            if did:
                break
            for bi in range(len(per_bar)):
                evs = per_bar[bi]
                j = 0
                while j + 1 < len(evs):
                    a, b = evs[j], evs[j + 1]
                    ma = a[2] if len(a) >= 3 else None
                    mb = b[2] if len(b) >= 3 else None
                    # 不切断连音线、不融合两个乐句：b 是连音线起止、
                    # a 是连音线终点（乐句边界）时跳过该对
                    if mb in ('slur-start', 'slur-end') or ma == 'slur-end':
                        j += 1
                        continue
                    if a[0] is None or b[0] is None:
                        j += 1
                        continue
                    da, db = str(a[1]), str(b[1])
                    if da != db or da not in _MERGE_DUR:
                        j += 1
                        continue
                    if need_same and a[0] != b[0]:
                        j += 1
                        continue
                    evs[j] = [a[0], _MERGE_DUR[da]] + ([ma] if ma else [])
                    del evs[j + 1]
                    merged += 1
                    did = True
                    break
                if did:
                    break
        if not did:
            break
    return merged


def _enforce_texture_contrast(spec: Dict[str, Any]) -> List[str]:
    """LLM 输出织体无段落对比时的本地确定性强制：
    - 安静段（首/末且力度 ≤ p）：相邻等时值音符合并成长音（织体变稀疏）
    - 高潮段（力度 ≥ f）：旋律声部八度加厚（单音 → 双音和弦）
    小节时值守恒，不动记号。返回变更说明列表。"""
    try:
        flat, spans, _dens, mi = texture_flat_info(spec)
    except Exception:
        return []
    if not flat or not spans:
        return []
    parts = spec.get('parts') or []
    if not parts:
        return []
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    mc = _parse_meter_changes(spec.get('meter_changes'), bars, ts)
    changes: List[str] = []
    # 安静段合并长音（所有声部）
    for i, p in enumerate(parts):
        events = list(p.get('events') or [])
        if not events:
            continue
        quiet_spans = [s for s in spans
                       if DYNAMIC_VELOCITIES.get(s[2].lower(), 0) <= 70
                       and (s is spans[0] or s is spans[-1])]
        per = _events_bar_map(events, bars, ts, mc)
        total_merged = 0
        for b0, b1, _v in quiet_spans:
            total_merged += _coarsen_span_events(
                [per[b] for b in range(max(0, b0 - 1), min(bars, b1))], 1.5)
        if total_merged:
            p['events'] = _bars_to_events(per)
            changes.append(f"声部{i + 1} 安静段合并 {total_merged} 处长音")
    # 高潮八度加厚（旋律声部，只加倍力度最响的段）
    climax_spans = []
    if spans:
        top = max(DYNAMIC_VELOCITIES.get(s[2].lower(), 0) or 0 for s in spans)
        climax_spans = [s for s in spans
                        if DYNAMIC_VELOCITIES.get(s[2].lower(), 0) == top]
    if climax_spans:
        p = parts[mi]
        starts, _t = _bar_grid(bars, ts, mc)
        doubled = 0
        t = 0.0
        for ev in p.get('events') or []:
            if not isinstance(ev, (list, tuple)) or len(ev) < 2:
                continue
            ql = _dur_ql(str(ev[1]))
            bar = _offset_to_bar(t, starts)
            t += ql
            pitch = ev[0]
            if not isinstance(pitch, str) or pitch in (None, ''):
                continue
            if not any(s[0] <= bar <= s[1] for s in climax_spans):
                continue
            try:
                mp = m21pitch.Pitch(pitch)
                if mp.midi + 12 <= 91:
                    hi = m21pitch.Pitch(pitch).transpose(12).nameWithOctave
                    ev[0] = [pitch, hi]
                elif mp.midi - 12 >= 40:
                    lo = mp.transpose(-12).nameWithOctave
                    ev[0] = [lo, pitch]
                else:
                    continue
                doubled += 1
            except Exception:
                continue
        if doubled:
            changes.append(f"高潮段八度加厚 {doubled} 个音（旋律声部）")
    return changes


def validate_score_spec(spec: Dict[str, Any]) -> List[Dict[str, str]]:
    """本地结构校验，返回 {level, message} 列表。"""
    issues = []
    bars = int(spec.get('bars') or 0)
    ts = str(spec.get('time_signature') or '4/4')
    try:
        _ql_per_bar(ts)
    except Exception:
        issues.append({'level': 'CRITICAL', 'message': f'非法拍号 {ts}'})
        return issues
    mc = _parse_meter_changes(spec.get('meter_changes'), bars, ts)
    target = _total_ql(bars, ts, mc) + _pickup_ql(spec)
    if not (4 <= bars <= 32):
        issues.append({'level': 'MAJOR', 'message': f'bars={bars} 超出 4–32'})
    parts = spec.get('parts') or []
    if not parts:
        issues.append({'level': 'CRITICAL', 'message': '无声部'})
        return issues
    for i, p in enumerate(parts):
        total = 0.0
        for ev in p.get('events') or []:
            if isinstance(ev, (list, tuple)) and len(ev) >= 2:
                dname = str(ev[1])
                # 未知时值名此前被 _dur_ql 静默当 1.0（music21 的 type= 也不认，
                # 会退化成休止符）——这里必须显式报出来
                if dname not in DUR_QL and dname not in _EXTRA_DUR_QL:
                    issues.append({
                        'level': 'CRITICAL',
                        'message': f"声部{i + 1}({p.get('name')}) 使用了未知时值 "
                                   f"{dname!r}（可用：{', '.join(sorted(DUR_QL))}）"})
                    continue
                total += _dur_ql(dname)
        if abs(total - target) > 0.05:
            issues.append({
                'level': 'CRITICAL',
                'message': (
                    f"声部{i + 1}({p.get('name')}) 时值合计 {total:.2f}，"
                    f"应为 {target:.2f}（{bars} 小节，含曲中拍号变化）"
                ),
            })
        # 末段休止塌陷：前半有音符、末尾连续 ≥1/4 小节几乎全休止 → 必须真实补写
        try:
            first_notes, dead, _last = dead_tail_info(p.get('events') or [], bars, ts, mc)
        except Exception:
            first_notes, dead = 1, 0
        if dead >= _dead_tail_min(bars) and first_notes >= 1:
            issues.append({
                'level': 'CRITICAL',
                'message': (
                    f"声部{i + 1}({p.get('name')}) 末段 {dead} 小节几乎全是休止符——"
                    f"每个声部必须为全部 {bars} 小节写出实际音符，"
                    f"用该声部自身材料延续/收束，禁止用休止符填充"
                ),
            })
    # 声部全部缺失力度记号：播放将没有强弱变化，段落过渡全部生硬
    if bars >= 8 and not any(_parse_part_dynamics(p.get('dynamics'))
                            for p in parts):
        issues.append({
            'level': 'CRITICAL',
            'message': (
                '所有声部均缺少 dynamics 力度记号——必须为每个声部按段落写出 '
                '[[小节, 力度]] 阶梯（如 [[1,"pp"],[5,"mf"],[13,"ff"],[19,"p"]]），'
                '与段落情绪对应，否则播放没有强弱变化'
            ),
        })
    # 段落织体无对比：有力度阶梯但各段音符密度相同、高潮无八度加厚
    try:
        flat, _spans, _dens, _mi = texture_flat_info(spec)
        # 赋格织体本就密度统一（对位靠声部进入而非织体对比），不适用该规则
        if flat and not spec.get('fugue'):
            issues.append({
                'level': 'CRITICAL',
                'message': (
                    '各段织体密度相同且高潮段无八度加厚——安静段（pp/p）须用 '
                    'half/whole 长音稀疏织体，冲突段用八分音符与休止的碎片化，'
                    '高潮段旋律以八度和弦（双 pitch 列表）加厚。'
                    '禁止全曲统一四分（或八分）音符'
                ),
            })
    except Exception:
        pass
    return issues


def voice_leading_issues_from_m21(m21_score) -> List[Dict[str, str]]:
    """外声部平行五度等（与工作室分析一致）。管弦织体仅作注意，不进 repair。"""
    try:
        from score_studio import analyze_composition, m21_to_model
        model = m21_to_model(m21_score, title='validate')
        issues: List[Dict[str, str]] = []
        for cat, status, desc in analyze_composition(model):
            if cat != '声部进行':
                continue
            if status.startswith('❌'):
                issues.append({'level': 'MAJOR', 'message': desc})
        return issues
    except Exception:
        return []

def parse_bars_from_text(text: str) -> Optional[int]:
    """Extract explicit target length like 扩展到32小节 / extend to 32 / /bars 32.

    Does NOT treat 「第4小节」/「第9-12小节」as a whole-piece length — those are
    location references inside a rewrite brief.
    """
    t = text or ''
    patterns = [
        r'(?:扩展到|延长到|加长到|加到|扩到|写到|写满|做到|改成|改到|做成|写成|缩到|缩短到)\s*(\d+)\s*小?节',
        r'(?:总共|一共|全曲|全长|长度(?:为|是)?)\s*(\d+)\s*小节',
        r'(?:extend(?:\s+to)?|/extend|/bars|/longer|/shorter)\s*(\d+)\b',
        r'\b(\d+)\s*bars?\b',
    ]
    for pat in patterns:
        m = re.search(pat, t, re.I)
        if m:
            n = int(m.group(1))
            if 4 <= n <= 32:
                return n
    return None


def _max_bar_ref_in_text(text: str) -> int:
    """Highest bar index referenced as a location (第16小节 / bars 13–16)."""
    t = text or ''
    nums = []
    for m in re.finditer(
        r'第\s*(\d{1,2})\s*(?:[–\-~至到]\s*(\d{1,2})\s*)?小节',
        t,
    ):
        nums.append(int(m.group(1)))
        if m.group(2):
            nums.append(int(m.group(2)))
    for m in re.finditer(r'\bbars?\s*(\d{1,2})\s*[–\-~to]+\s*(\d{1,2})\b', t, re.I):
        nums.extend([int(m.group(1)), int(m.group(2))])
    nums = [n for n in nums if 1 <= n <= 32]
    return max(nums) if nums else 0


def is_full_score_revision_request(text: str) -> bool:
    return skill_lib.is_full_score_revision_request(text)


def is_advice_affirmation(text: str) -> bool:
    """Short confirmations after /advice: 可以 / 执行 / 好的 / apply …"""
    t = re.sub(r'^\[选区[^\]]*\]\s*', '', (text or '')).strip()
    if not t:
        return False
    if re.search(r'按.{0,8}(建议|advice).{0,6}(改|修改|执行|应用)|执行建议|应用建议|照建议', t, re.I):
        return True
    return bool(re.match(
        r'^(可以|行|好的?|好|执行|确认|同意|照做|就这样|开始改|改吧|改掉|'
        r'apply|yes|y|ok|okay|go|do\s*it|proceed)[!！。.\s]*$',
        t, re.I,
    ))


def strip_advice_footer(advice: str) -> str:
    text = (advice or '').strip()
    text = re.sub(
        r'\n*\[Skill:.*?\]\s*(（仅建议，未修改乐谱）)?\s*'
        r'(回复「可以」.*?改谱。)?\s*$',
        '', text, flags=re.S,
    )
    text = re.sub(r'\n*（仅建议，未修改乐谱）\s*$', '', text)
    text = re.sub(r'\n*回复「可以」.*?改谱。\s*$', '', text)
    return text.strip()


def advice_apply_brief(session: ComposeSession) -> Optional[str]:
    """Short, routing-clean brief for applying advice (raw advice travels separately)."""
    raw = strip_advice_footer(session.last_advice or '')
    if not raw:
        return None
    title = (session.score_spec or {}).get('title') or '当前曲目'
    return (
        f'修订《{title}》：按上一轮 /advice 的修改建议，在现有乐谱基础上落实改进。'
        f'优先执行 🔴 高优先级条目，🟡 次之，🟢 酌情取舍；'
        f'标注「可能不适用」的条目忽略；'
        f'保持原曲标题、调性、拍号、速度、声部配置与主题动机，除非建议明确要求改变。'
    )


def infer_edit_intent(user_text: str, pending: Optional[str] = None) -> Optional[str]:
    """编辑意图识别。

    **当前恒返回 None**：作曲只保留 LLM → /compose-grammar，编辑类意图
    （重写/续写/移调/配器…）不再从自然语言路由进旧编辑管线。
    函数体保留是因为 studio 的历史会话仍可能带 ``edit_intent``，
    且 ``resolve_plan_bars`` 等下游会读它；保留可解析形状、但不再新产生意图。
    """
    if pending:
        return pending
    return None


def _retired_edit_intent(user_text: str, pending: Optional[str] = None
                         ) -> Optional[str]:
    """旧路由逻辑，保留供审计与回归对照（**不在主路径上调用**）。

    说明：这里曾经把「重写 / 修改建议 / 续写 / 移调…」等自然语言映射成
    edit_intent，从而绕开 `/compose-grammar` 走旧编辑管线——这正是
    「只有 LLM → /compose-grammar 一套」迟迟没有真正收口的原因：
    命令入口retire 了，自然语言入口还在。保留本函数以便日后需要时查回。
    """
    t = user_text or ''
    if skill_lib.is_full_score_revision_request(t) or re.search(
        r'/rewrite\b|重写|按.*改', t, re.I
    ):
        return 'rewrite'
    if re.search(
        r'/advice\b|修改建议|改进建议|有什么问题|如何更丰富|如何让乐谱更丰富|'
        r'分析乐谱|评价乐谱|\badvice\b|\bcritique\b',
        t, re.I,
    ):
        return 'advice'
    if re.search(r'/apply\b|执行建议|应用建议', t, re.I):
        return 'rewrite'
    if re.search(r'扩展|加长|续写|延长|加到|扩到|extend|longer', t, re.I):
        return 'extend'
    if re.search(r'发展|变奏|develop|variation', t, re.I):
        return 'develop'
    if re.search(r'配器|orchestrat', t, re.I):
        return 'orchestrate'
    if re.search(r'移调|transpose', t, re.I):
        return 'transpose'
    if re.search(r'和声|harmonize', t, re.I):
        return 'harmonize'
    if re.search(r'简化|简短|simplify|shorter', t, re.I):
        return 'simplify'
    if re.search(r'复杂|complexify', t, re.I):
        return 'complexify'
    return None


def _max_bar_mentioned(plan: Dict[str, Any]) -> int:
    """Highest bar index mentioned in form/summary/climax (e.g. 'bars 31–32')."""
    chunks = []
    for item in plan.get('form') or []:
        chunks.append(str(item))
    chunks.append(str(plan.get('summary') or ''))
    climax = plan.get('climax') or {}
    if isinstance(climax, dict):
        chunks.append(str(climax.get('location') or ''))
    else:
        chunks.append(str(climax))
    nums = []
    for c in chunks:
        nums.extend(int(x) for x in re.findall(r'\b(\d{1,2})\b', c))
    nums = [n for n in nums if 1 <= n <= 32]
    return max(nums) if nums else 0


def resolve_plan_bars(
    session: ComposeSession,
    plan: Dict[str, Any],
    user_text: str = '',
    edit_intent: Optional[str] = None,
) -> int:
    """Pick final bars count; never let stale prefs=8 squash an extend-to-32 plan.

    Also: rewrite briefs that mention 「第4小节」must NOT shrink the piece to 4 bars.
    """
    existing = int((session.score_spec or {}).get('bars') or 0)
    pref = int(session.prefs.get('bars') or 8)
    planned = int(plan.get('bars') or 0)
    asked = parse_bars_from_text(user_text) or 0
    from_form = _max_bar_mentioned(plan)
    from_user_refs = _max_bar_ref_in_text(user_text)
    intent = (edit_intent or plan.get('edit_intent') or session._pending_edit or '')
    intent = str(intent).lower()
    extending = intent in ('extend', 'longer') or bool(
        re.search(r'扩展|加长|续写|延长|extend|longer', user_text or '', re.I)
    )
    # Explicit /bars N or "扩展到N" — only when parse_bars_from_text matched
    shortening = intent in ('shorter', 'simplify') and asked and existing and asked < existing
    revising = intent in (
        'rewrite', 'complexify', 'develop', 'variation', 'harmonize',
        'orchestrate', 'transpose',
    ) or bool(re.search(r'/rewrite|重写|整曲', user_text or '', re.I))

    if asked and not (revising and existing and asked < existing and not shortening):
        # For rewrite of an existing score, ignore a tiny "asked" that came from
        # false positives (should be rare after parse_bars_from_text fix).
        target = asked
    elif revising and existing:
        # Keep at least the current length; grow if plan/form/user cite later bars
        target = max(existing, planned, from_form, from_user_refs)
    elif extending:
        target = max(existing, planned, from_form, from_user_refs, pref if pref > existing else 0)
        if target <= existing:
            target = min(32, max(existing + 8, existing * 2 if existing else 16))
    else:
        target = planned or pref or 8
        if from_form > target:
            target = from_form
        if from_user_refs > target:
            target = from_user_refs

    return max(4, min(32, int(target)))


def merge_prefs_into_plan(session: ComposeSession, plan: Dict[str, Any]) -> Dict[str, Any]:
    p = session.prefs
    # tempo 不再把默认值 120 注入 plan：没有显式 /tempo、也没有情绪/速度词命中时，
    # plan 里不写 tempo，translator 按基调（genre）区间自行取值——
    # genre 只做提示，默认值不能伪装成「明确约束」压掉风格基线。
    plan.setdefault('meter', p['meter'])
    plan.setdefault('bars', p['bars'])
    plan.setdefault('key_sharps', p['key_sharps'])
    plan.setdefault('mode', p['mode'])
    if p.get('style') and not plan.get('genre'):
        plan['genre'] = p['style']
    if p.get('instrumentation') and not plan.get('instrumentation'):
        plan['instrumentation'] = [x.strip() for x in p['instrumentation'].split(',') if x.strip()]
    # Session command prefs win for tempo/meter/key — 但只对**显式命令**生效；
    # 未显式指定时默认值（120/4/4/0/major）不得压掉 LLM/情绪解析的推断。
    # bars 同理单独解析，默认 8 不参与覆盖（/extend NL "到32" 不被打回）。
    ex = p.get('_explicit') or {}
    if ex.get('tempo'):
        plan['tempo'] = p['tempo']
    if ex.get('meter'):
        plan['meter'] = p['meter']
        plan['_user_meter'] = True
    if ex.get('key_sharps'):
        plan['key_sharps'] = p['key_sharps']
    if ex.get('mode'):
        plan['mode'] = p['mode']
    return plan


def _mark_explicit(session: ComposeSession, *keys: str) -> None:
    """记录用户显式用命令设置过的偏好键（区分默认值与显式指定）。"""
    try:
        ex = session.prefs.setdefault('_explicit', {})
        for k in keys:
            ex[k] = True
    except Exception:
        pass


def plan_to_score_defaults(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'title': plan.get('title') or '对话创曲',
        'key_sharps': plan.get('key_sharps', 0),
        'mode': plan.get('mode', 'major'),
        'time_signature': plan.get('meter', '4/4'),
        'tempo': plan.get('tempo', 120),
        'bars': plan.get('bars', 8),
    }


def demo_spec(session: ComposeSession) -> Dict[str, Any]:
    prefs = session.prefs
    ts, bars = prefs['meter'], prefs['bars']
    tempo_bpm = prefs['tempo']
    sharps, mode = prefs['key_sharps'], prefs['mode']
    # 用户显式命令（/tempo /meter /key）不被 genre 基础分覆盖——genre 只做提示
    _explicit = dict(prefs.get('_explicit') or {})
    if mode == 'minor':
        mel, bass, title = ['A4', 'C5', 'E5', 'A5', 'G5', 'E5', 'C5', 'A4'], \
            ['A2', 'E3', 'A2', 'E3', 'F2', 'C3', 'E2', 'A2'], '夜航小品'
    else:
        mel, bass, title = ['G4', 'B4', 'D5', 'G5', 'F#5', 'D5', 'B4', 'G4'], \
            ['G2', 'D3', 'G2', 'D3', 'C3', 'G2', 'D3', 'G2'], '晨光小品'

    g = None
    gid = (prefs.get('genre_id') or '').strip()
    if gid:
        g = skill_lib.get_genre(gid)
    if not g:
        try:
            import style_routing
            g = style_routing.best_match(prefs.get('style') or '')
        except Exception:
            g = skill_lib.match_genre(prefs.get('style') or '')
    if g:
        title = g.get('name') or title
        pack_id = g.get('pack_id') or ''
        form = (g.get('dimensions') or {}).get('form') or ''
        fs = (g.get('profile') or {}).get('foundation_score') or g.get('foundation_score') or {}
        if isinstance(fs, dict):
            if fs.get('meter') and not _explicit.get('meter'):
                ts = str(fs['meter'])
            if fs.get('tempo') is not None and not _explicit.get('tempo'):
                try:
                    tempo_bpm = int(fs['tempo'])
                except (TypeError, ValueError):
                    pass
            key_map = {
                'c_major': (0, 'major'), 'g_major': (1, 'major'), 'd_major': (2, 'major'),
                'a_major': (3, 'major'), 'f_major': (-1, 'major'),
                'a_minor': (0, 'minor'), 'd_minor': (-1, 'minor'),
                'e_minor': (1, 'minor'), 'g_minor': (-2, 'minor'),
            }
            kn = str(fs.get('key') or '').lower().replace(' ', '_')
            if kn in key_map and not (_explicit.get('key_sharps')
                                      or _explicit.get('mode')):
                sharps, mode = key_map[kn]
        if form in ('waltz', 'minuet', 'mazurka'):
            if ts == '4/4':
                ts = '3/4'
            mel, bass = ['C5', 'E5', 'G5', 'C6', 'B5', 'G5', 'E5', 'C5'], \
                ['C3', 'G3', 'C3', 'G3', 'F3', 'C3', 'G3', 'C3']
        elif pack_id == 'pirate-allegro' or form in ('chase', 'fanfare', 'epic', 'march'):
            if pack_id == 'pirate-allegro' and ts == '4/4':
                ts = '6/8'
            mode = 'minor' if pack_id == 'pirate-allegro' else mode
            mel, bass = ['A4', 'C5', 'E5', 'A5', 'F5', 'D5', 'E5', 'A4'], \
                ['A2', 'E3', 'A2', 'F3', 'D3', 'A2', 'E3', 'A2']
        elif pack_id == 'rain-piano' or form in ('nocturne', 'lullaby', 'air'):
            if pack_id == 'rain-piano':
                sharps, mode = -1, 'minor'
            mel, bass = ['D5', 'F5', 'A5', 'D5', 'C5', 'A4', 'F4', 'D4'], \
                ['D3', 'A3', 'D3', 'Bb2', 'F3', 'A2', 'D3', 'A2']

    bar_ql = _ql_per_bar(ts)

    def fill(pitches, prefer_eighth=True):
        events, total, i = [], 0.0, 0
        target = bars * bar_ql
        while total + 1e-6 < target:
            dur = 'eighth' if prefer_eighth and bar_ql >= 3 else 'quarter'
            events.append([pitches[i % len(pitches)], dur])
            total += _dur_ql(dur)
            i += 1
        return events

    return {
        'title': title,
        'composer': '对话创曲（本地示例）',
        'key_sharps': sharps,
        'mode': mode,
        'time_signature': ts,
        'tempo': tempo_bpm,
        'bars': bars,
        'parts': [
            {'name': 'Violin', 'program': 40, 'clef': 'treble', 'events': fill(mel, True)},
            {'name': 'Cello', 'program': 42, 'clef': 'bass', 'events': fill(bass, False)},
        ],
        'notes': '本地示例（未调用 LLM）。配置 API 后可自然语言创曲。',
    }


def _prompt(stage: str, skill_ids: List[str]) -> str:
    text = skill_lib.build_system_prompt(stage, skill_ids)
    if text:
        return text
    fallback = {
        'planner': PLANNER_PROMPT,
        'composer': COMPOSER_PROMPT,
        'selection_measure': SELECTION_MEASURE_PROMPT,
        'selection_symbol': SELECTION_SYMBOL_PROMPT,
        'repair': REPAIR_PROMPT,
    }
    return fallback.get(stage, '')


_FUGUE_WORDS = ('赋格', 'fugue', 'fugal', '卡农', 'canon', '创意曲', 'invention')


def _plan_indicates_species(plan: Optional[Dict[str, Any]]) -> bool:
    """由 Planner LLM 字段判定第一类 1:1 对位（不以 keyword 为主）。"""
    if not isinstance(plan, dict):
        return False
    if plan.get('want_fugue') is True:
        return False
    if plan.get('species') is True or plan.get('_species_hint'):
        return True
    if plan.get('species_from_current') is True:
        return True
    tex = str(plan.get('texture') or '').lower()
    if any(k in tex for k in (
        'first-species', 'first_species', 'species', '1:1', '1比1',
        '一比一', 'note-against-note', '第一类',
    )):
        return True
    genre = str(plan.get('genre') or '')
    if re.search(r'第一类|1\s*[:：比]\s*1|一比一|species', genre, re.I):
        if not re.search(r'赋格|fugue', genre, re.I):
            return True
    return False


def _wants_species_from_current(user_text: str,
                                plan: Optional[Dict[str, Any]] = None) -> bool:
    """根据当前乐谱写 1:1 对位。"""
    if isinstance(plan, dict) and plan.get('species_from_current') is True:
        return True
    try:
        from music_score.two_part_species import wants_species_from_current
        return bool(wants_species_from_current(user_text or ''))
    except Exception:
        return False


def _fugue_requested(plan: Optional[Dict[str, Any]], fugue_mode: str) -> bool:
    """是否要赋格：优先信 Planner 的 want_fugue；其次 /fugue full；brief 点名赋格。

    「对位 / 1:1」本身不等于赋格。species 计划直接排除。
    """
    mode = str(fugue_mode or 'auto').lower()
    if mode == 'full':
        return True
    if mode == 'off':
        return False
    p = plan or {}
    if p.get('want_fugue') is True:
        return True
    if p.get('want_fugue') is False:
        return False
    if p.get('_species_hint') or p.get('species') or _plan_indicates_species(p):
        return False
    text = ' '.join(str(p.get(k) or '') for k in
                    ('user_brief', 'summary', 'genre', 'texture', 'mood', 'title'))
    tl = text.lower()
    brief_says_fugue = any(w in tl for w in _FUGUE_WORDS)
    if str(p.get('form') or '').strip().lower() == 'fugue':
        return brief_says_fugue
    return brief_says_fugue


def _strip_unrequested_fugue(program: Dict[str, Any], plan: Optional[Dict[str, Any]],
                             fugue_mode: str) -> Tuple[Dict[str, Any], str]:
    """编配越权约束：brief/plan 未提赋格时，确定性摘除 translator 自加的 fugue 调用。

    摘除后若目标段落缺 melody/accompaniment，内核会报错并回喂重试。
    """
    if _fugue_requested(plan, fugue_mode):
        return program, ''
    calls = list((program or {}).get('calls') or [])
    if not any(str(c.get('script')) == 'fugue' for c in calls):
        return program, ''
    keep = [c for c in calls if str(c.get('script')) != 'fugue']
    return ({'calls': keep},
            '已摘除越权 fugue（brief 未要求赋格；如确需赋格请在需求中明确说明）')


_CHORALE_WORDS = ('众赞歌', '圣咏', '四部和声', '四部合唱', 'chorale', 'satb', '巴赫合唱')


def _strip_unrequested_grammar(program: Dict[str, Any], plan: Optional[Dict[str, Any]]
                               ) -> Tuple[Dict[str, Any], str]:
    """brief/plan 未提众赞歌时，摘除 translator 自加的 chorale_grammar（它会覆盖全部声部）；
    leadsheet_grammar 只由 learned_grammar 路由调用，translator 自加的一律摘除。"""
    calls = list((program or {}).get('calls') or [])
    if any(str(c.get('script')) == 'leadsheet_grammar' for c in calls):
        calls = [c for c in calls if str(c.get('script')) != 'leadsheet_grammar']
        program = {**(program or {}), 'calls': calls}
        if not any(str(c.get('script')) == 'chorale_grammar' for c in calls):
            return program, '已摘除越权 leadsheet_grammar（风格语法作曲只走 learned_grammar 路由）'
    if not any(str(c.get('script')) == 'chorale_grammar' for c in calls):
        return program, ''
    p = plan or {}
    text = ' '.join(str(p.get(k) or '') for k in
                    ('user_brief', 'summary', 'genre', 'texture', 'form', 'title')).lower()
    if any(w in text for w in _CHORALE_WORDS):
        return program, ''
    return ({'calls': [c for c in calls if str(c.get('script')) != 'chorale_grammar']},
            '已摘除越权 chorale_grammar（brief 未要求众赞歌）')


#: /compose-grammar 风格词 → (语法风格, 子风格)；子风格为 '' 时取该风格第一个子风格
_LEADSHEET_ALIASES = {
    'folk': ('folk', ''), '民谣': ('folk', ''), '民间舞曲': ('folk', ''), '英伦民谣': ('folk', ''),
    'jig': ('folk', 'jig'), '吉格': ('folk', 'jig'), 'reel': ('folk', 'reel'),
    '里尔': ('folk', 'reel'), 'hornpipe': ('folk', 'hornpipe'), '角笛舞': ('folk', 'hornpipe'),
    'waltz': ('folk', 'waltz'), '华尔兹': ('folk', 'waltz'), '圆舞曲': ('folk', 'waltz'),
    'jazz': ('jazz', ''), '爵士': ('jazz', ''), 'swing': ('jazz', 'swing'), '摇摆': ('jazz', 'swing'),
    'bebop': ('jazz', 'bebop'), '比波普': ('jazz', 'bebop'), 'cool': ('jazz', 'cool'),
    '酷派': ('jazz', 'cool'), 'hardbop': ('jazz', 'hardbop'), '硬波普': ('jazz', 'hardbop'),
    'postbop': ('jazz', 'postbop'), '后波普': ('jazz', 'postbop'),
    'traditional': ('jazz', 'traditional'), '传统爵士': ('jazz', 'traditional'),
    'pop': ('pop', 'pop'), '流行': ('pop', 'pop'), '流行歌': ('pop', 'pop'),
    'madrigal': ('madrigal', 'madrigal'), '牧歌': ('madrigal', 'madrigal'),
    'sonata': ('sonata', ''), '奏鸣曲': ('sonata', ''), '钢琴奏鸣曲': ('sonata', ''),
    '古典钢琴': ('sonata', ''),
    'strings': ('strings', ''), 'quartet': ('strings', ''), '四重奏': ('strings', ''),
    '弦乐四重奏': ('strings', ''), '弦乐': ('strings', ''),
    'symphony': ('symphony', ''), '交响曲': ('symphony', ''), '交响乐': ('symphony', ''),
    '交响': ('symphony', ''), '海顿交响曲': ('symphony', ''),
    '海顿': ('symphony', ''), '管弦乐': ('symphony', ''), 'orchestra': ('symphony', ''),
    'chopin': ('chopin', ''), '肖邦': ('chopin', ''),
    'mazurka': ('chopin', 'mazurka'), '玛祖卡': ('chopin', 'mazurka'),
    'prelude': ('chopin', 'prelude'), '前奏曲': ('chopin', 'prelude'),
    # 古典乐章子风格（奏鸣曲 / 四重奏共用；没写风格词时默认奏鸣曲）
    'allegro': ('sonata', 'allegro'), '快板': ('sonata', 'allegro'),
    'minuet': ('sonata', 'minuet'), '小步舞曲': ('sonata', 'minuet'),
    'scherzo': ('sonata', 'minuet'), '谐谑曲': ('sonata', 'minuet'),
    'duple': ('sonata', 'duple'), '二拍子': ('sonata', 'duple'),
    'compound': ('sonata', 'compound'), '复拍子': ('sonata', 'compound'),
}
_LEADSHEET_STYLES = ('folk', 'jazz', 'pop', 'madrigal', 'sonata', 'strings', 'symphony', 'chopin')


def _learn_all_report(arg: str = '') -> str:
    """`/learn-all` —— 统一学习：chorale + 8 体裁 leadsheet + 人工段落关系，编译入库。

    参数：`--skip chorale leadsheet relation` / `--style jazz pop` / `--dry-run`
    （与 `score_learning.relation.learn_all` 的命令行参数一致）
    """
    import shlex
    from score_learning.relation.learn_all import (
        learn_all, render_report, LEADSHOT_STYLES)
    argv = shlex.split(arg or '')
    skip: List[str] = []
    styles: Optional[List[str]] = None
    dry = False
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == '--dry-run':
            dry = True
        elif t == '--skip':
            i += 1
            while i < len(argv) and not argv[i].startswith('-'):
                skip.append(argv[i]); i += 1
            continue
        elif t == '--style':
            i += 1
            styles = []
            while i < len(argv) and not argv[i].startswith('-'):
                styles.append(argv[i]); i += 1
            continue
        elif t in LEADSHOT_STYLES:
            styles = (styles or []) + [t]
        i += 1
    stages = learn_all(skip=skip, styles=styles, dry=dry)
    return render_report(stages, dry=dry)


def _learn_grammar_report(arg: str = '') -> str:
    """/learn-grammar [chorale|folk|jazz|pop|madrigal|sonata|strings|symphony|chopin|all]：
    学习 → 写 grammar.yaml / grammar.md。不带参数 = 巴赫众赞歌。"""
    which = (arg or 'chorale').strip().lower()
    targets = ['chorale', *_LEADSHEET_STYLES] if which == 'all' else \
        ['chorale' if which in ('chorale', '众赞歌', 'bach', '巴赫') else
         _LEADSHEET_ALIASES.get(which, (which, ''))[0]]
    out = []
    for t in targets:
        if t == 'chorale':
            out.append(_learn_chorale_report())
        elif t in _LEADSHEET_STYLES:
            out.append(_learn_leadsheet_report(t))
        else:
            out.append(f'未知风格 {arg!r}：可选 chorale / {" / ".join(_LEADSHEET_STYLES)} / all')
    return '\n\n'.join(out)


def _learn_leadsheet_report(style: str) -> str:
    try:
        from score_learning.leadsheet import script as lscript
        from score_learning.leadsheet.analyze import analyze_style
        from score_learning.leadsheet.learn import learn as ls_learn
    except Exception as e:
        return f'语法学习模块不可用：{e}'
    pieces = analyze_style(style, progress=False)
    sc = ls_learn(pieces, style)
    path = lscript.save(sc, style)
    lines = [f"已从 {sc['meta']['pieces']} 首{sc['name']}学习可解释语法"
             f"（训练 {sc['meta']['train']} / 留出 {sc['meta']['heldout']}）",
             f'规则脚本：{path}', f'中文说明：{lscript.paths(style)[1]}']
    for sub, d in sc['subs'].items():
        ev = '；'.join(f"{'大调' if m == 'major' else '小调'}困惑度 {e['perplexity_script']}"
                      f"（只用频率 {e['perplexity_unigram']}）" for m, e in d['evaluation'].items())
        lines.append(f"{d['name']}（{sub}，{d['meter']}）：{ev or '无留出集'}；"
                     f"曲式 {d['form']['templates'][0][0] if d['form']['templates'] else '-'}")
    lines.append(f'用 /compose-grammar {style} [子风格] 按学到的语法作曲。')
    return '\n'.join(lines)


def _learn_chorale_report() -> str:
    """分析巴赫众赞歌语料 → 学习 → 写 grammar.yaml / grammar.md。"""
    try:
        from score_learning.grammar import script as gscript
        from score_learning.grammar.analyze import analyze_corpus
        from score_learning.grammar.learn import learn
    except Exception as e:
        return f'语法学习模块不可用：{e}'
    pieces = analyze_corpus(cache=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'runs', 'grammar_cache',
        'bach_chorales.json'), progress=False)
    sc = learn(pieces)
    gscript.save(sc)
    lines = [f"已从 {sc['meta']['pieces']} 首巴赫众赞歌学习可解释语法"
             f"（训练 {sc['meta']['train']} / 留出 {sc['meta']['heldout']}）",
             f'规则脚本：{gscript.DEFAULT_YAML}', f'中文说明：{gscript.DEFAULT_MD}']
    for mode, ev in sc['evaluation'].items():
        lines.append(f"{'大调' if mode == 'major' else '小调'}：词表 {ev['vocab_size']} 个和弦，"
                     f"留出集困惑度 {ev['perplexity_script']}（去掉位置条件 "
                     f"{ev['perplexity_no_position']}，只用频率 {ev['perplexity_unigram']}），"
                     f"二元组覆盖 {ev['bigram_coverage']:.0%}")
    lines.append('用 /compose-grammar 按学到的语法作曲。')
    return '\n'.join(lines)


def _contour_name_en(name: str) -> str:
    if not name:
        return ''
    s = str(name)
    s = re.sub(r'，幅度\s*(\d+)\s*半音', r', span \1 semitones', s)
    s = re.sub(r'幅度\s*(\d+)\s*半音', r'span \1 semitones', s)
    s = s.replace('拱形（高点在中段，落低', 'arch (peak in middle, falling')
    s = s.replace('拱形（高点在前段，落低', 'arch (peak early, falling')
    s = s.replace('拱形（高点在后段，落低', 'arch (peak late, falling')
    s = s.replace('拱形（高点在中段', 'arch (peak in middle')
    s = s.replace('拱形（高点在前段', 'arch (peak early')
    s = s.replace('拱形（高点在后段', 'arch (peak late')
    s = s.replace('谷形（低点在中段', 'valley (low in middle')
    s = s.replace('谷形（低点在前段', 'valley (low early')
    s = s.replace('谷形（低点在后段', 'valley (low late')
    s = s.replace('下行', 'downward')
    s = s.replace('上行', 'upward')
    s = s.replace('平稳波形', 'undulating')
    s = s.replace('（', '(').replace('）', ')')
    return s


def _compose_grammar_cmd(session, arg: str, overrides: Optional[Dict[str, Any]] = None):
    """/compose-grammar [major|minor] [小节数] [pickup|downbeat] [seed=…] [t=温度]：
    确定性 SATB 众赞歌。首个参数是风格词（folk / jig / 爵士 / bebop / 流行 / 牧歌 …）时
    改走旋律 + 钢琴的语法作曲。overrides 可带 key_sharps / tempo / title（LLM 路由用）。"""
    import compose_kernel as _K
    try:
        import i18n
        is_en = i18n.language() == 'en'
    except Exception:
        is_en = False
    toks = (arg or '').split()
    ov = overrides or {}
    style_hit = [t for t in toks if t.lower() in _LEADSHEET_ALIASES]
    if style_hit:
        return _compose_leadsheet_cmd(session, toks, ov)
    prefs = getattr(session, 'prefs', None)
    if prefs is None:
        prefs = {}
        if session is not None:
            session.prefs = prefs
    ex = prefs.setdefault('_explicit', {})
    mode = str(prefs.get('mode') or 'major') if ex.get('mode') else 'major'
    bars = int(prefs.get('bars') or 12) if ex.get('bars') else 12
    seed, temp, layout = '', 1.0, 'auto'
    for tok in (arg or '').split():
        tl = tok.lower().strip()
        tl_key = tl.replace('_', '-').strip()
        if tl_key in KEY_ALIASES:
            sharps, mode = KEY_ALIASES[tl_key]
            prefs['key_sharps'], prefs['mode'] = sharps, mode
            ex['key_sharps'] = True
            ex['mode'] = True
        elif tl_key in ('g-major', 'g-maj', 'gmaj'):
            sharps, mode = 1, 'major'
            prefs['key_sharps'], prefs['mode'] = sharps, mode
            ex['key_sharps'] = True
            ex['mode'] = True
        elif tl in ('major', '大调'):
            mode = 'major'
        elif tl in ('minor', '小调'):
            mode = 'minor'
        elif tl in ('pickup', '弱起', 'anacrusis'):
            layout = 'pickup'
        elif tl in ('downbeat', '强拍起', '强起'):
            layout = 'downbeat'
        elif tl.isdigit():
            bars = int(tl)
        elif tl.startswith('seed='):
            seed = tok.split('=', 1)[1]
        elif tl.startswith(('t=', 'temperature=')):
            try:
                temp = float(tok.split('=', 1)[1])
            except ValueError:
                pass
    bars = max(8, min(32, bars))
    sharps = int(prefs['key_sharps']) if ex.get('key_sharps') and \
        prefs.get('key_sharps') is not None else (1 if mode == 'major' else -1)
    tempo_bpm = int(prefs.get('tempo') or 66) if ex.get('tempo') else 66
    if ov.get('key_sharps') is not None:
        sharps = int(ov['key_sharps'])
    tempo_bpm = int(ov.get('tempo') or tempo_bpm)
    default_title = 'Chorale (Bach grammar)' if is_en else '众赞歌（巴赫语法）'
    score_title = str(ov.get('title') or prefs.get('title') or default_title)
    program = {'calls': [
        {'script': 'new_score', 'args': {
            'title': score_title,
            'key_sharps': sharps,
            'mode': mode, 'meter': '4/4', 'tempo': tempo_bpm,
            'bars': bars, 'voices': 4, 'instruments': ['soprano', 'alto', 'tenor', 'bass'],
            **({'seed': seed} if seed else {})}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'Chorale', 'bars': bars}]}},
        {'script': 'chorale_grammar', 'args': {'temperature': temp, 'layout': layout}},
        {'script': 'finalize', 'args': {}},
    ]}
    try:
        spec = _K.run_program(program)
    except Exception as e:
        fail_msg = f'Grammar composition failed: {e}' if is_en else f'语法作曲失败：{e}'
        raise CommandOnly(fail_msg)
    session.score_spec = spec
    session.plan = {'title': spec.get('title'), 'bars': spec.get('bars'),
                    'tempo': spec.get('tempo'), 'meter': '4/4',
                    'summary': ('Interpretable grammar composition (Bach chorale, deterministic, no LLM)'
                                if is_en else '可解释语法作曲（巴赫众赞歌，确定性，不调 LLM）')}
    session.revision += 1
    session.last_skill_ids = ['chorale-grammar']
    g = spec.get('grammar') or {}
    if is_en:
        lay_say = '1-beat pickup + ' if spec.get('pickup_ql') else 'downbeat · '
        lines = [f"Composed with learned Bach chorale grammar (deterministic, no LLM) · {spec.get('title')} · "
                 f"{lay_say}{bars} measures · seed={spec.get('seed')} · hard violations 0"]
        for p in g.get('phrases') or []:
            c_desc = _contour_name_en(p.get('contour_name') or '')
            lines.append(f"Phrase {p['phrase']} ({p['bars']} measures) ends on {p['target']}"
                         f" [{p['plan_rule']} / {p['formula']} / {p['contour']} {c_desc}]:"
                         f" {p['harmony']}")
    else:
        lay_say = '一拍弱起 + ' if spec.get('pickup_ql') else '强拍起 · '
        lines = [f"按学到的巴赫众赞歌语法作曲（确定性，不调 LLM）· {spec.get('title')} · "
                 f"{lay_say}{bars} 小节 · seed={spec.get('seed')} · 硬规则违例 0"]
        for p in g.get('phrases') or []:
            lines.append(f"第{p['phrase']}句（{p['bars']}小节）终止于 {p['target']}"
                         f" [{p['plan_rule']} / {p['formula']} / {p['contour']} {p['contour_name']}]："
                         f"{p['harmony']}")
    return spec_to_music21(spec), spec, '\n'.join(lines)


_LEADSHEET_INSTR = {'violin': 'violin', '小提琴': 'violin', 'fiddle': 'violin',
                    'flute': 'flute', '长笛': 'flute', 'trumpet': 'trumpet', '小号': 'trumpet',
                    'clarinet': 'clarinet', '单簧管': 'clarinet', 'voice': 'voice',
                    '人声': 'voice', 'soprano': 'soprano', '女高音': 'soprano'}
#: 未显式指定调号时的默认（调式 → 风格 → 升降号数）
_LEADSHEET_KEY = {'major': {'folk': 2, 'jazz': -1, 'pop': 0, 'madrigal': -1,
                            'sonata': 0, 'strings': 1, 'symphony': 2, 'chopin': -4},
                  'minor': {'folk': 1, 'jazz': -2, 'pop': 0, 'madrigal': -1,
                            'sonata': -3, 'strings': -1, 'symphony': -1, 'chopin': -2}}
#: 织体 → new_score 的声部乐器（melody_piano 的旋律乐器另行指定）
_TEXTURE_INSTRUMENTS = {'piano': ['piano', 'piano'],
                        'quartet': ['violin', 'violin', 'viola', 'cello'],
                        'orchestra': ['flute', 'oboe', 'horn', 'bassoon', 'timpani',
                                      'violin', 'violin', 'viola', 'cello']}


def _compose_leadsheet_cmd(session, toks: List[str], overrides: Optional[Dict[str, Any]] = None):
    """/compose-grammar <风格|子风格> [major|minor] [小节数] [弱起|强拍起] [乐器] [seed=…] [t=温度]"""
    import compose_kernel as _K
    from score_learning.leadsheet import script as lscript
    ov = overrides or {}
    prefs = getattr(session, 'prefs', None) or {}
    ex = prefs.get('_explicit') or {}
    style = sub = ''
    mode = inst = seed = ''
    bars, temp, layout = 0, 1.0, 'auto'
    style_word = False
    for tok in toks:
        tl = tok.lower()
        if tl in _LEADSHEET_ALIASES:
            st_, sb_ = _LEADSHEET_ALIASES[tl]
            # 风格词优先于子风格词（"四重奏 小步舞曲" 与 "小步舞曲 四重奏" 同义）
            if not sb_ and not style_word:
                style, style_word = st_, True
            elif not style:
                style = st_
            sub = sb_ or sub
        elif tl in ('major', '大调'):
            mode = 'major'
        elif tl in ('minor', '小调'):
            mode = 'minor'
        elif tl in ('pickup', '弱起', 'anacrusis'):
            layout = 'pickup'
        elif tl in ('downbeat', '强拍起', '强起'):
            layout = 'downbeat'
        elif tl in _LEADSHEET_INSTR:
            inst = _LEADSHEET_INSTR[tl]
        elif tl.isdigit():
            bars = int(tl)
        elif tl.startswith('seed='):
            seed = tok.split('=', 1)[1]
        elif tl.startswith(('t=', 'temperature=')):
            try:
                temp = float(tok.split('=', 1)[1])
            except ValueError:
                pass
    try:
        sc = lscript.load(style)
    except FileNotFoundError:
        raise CommandOnly(f'还没有 {style} 的规则脚本，先运行 /learn-grammar {style}')
    if sub and sub not in sc['subs']:
        raise CommandOnly(f"{sc['name']}没有学到子风格 {sub}（语料不足或拍号不统一）；"
                          f"可用：{'、'.join(sc['subs'])}")
    sub = sub or next(iter(sc['subs']))
    d = sc['subs'][sub]
    if not mode:
        mode = str(prefs.get('mode')) if ex.get('mode') and prefs.get('mode') in d['vocab'] else \
            ('major' if 'major' in d['vocab'] else 'minor')
    if mode not in d['vocab']:
        raise CommandOnly(f"{d['name']}的语料里{'大调' if mode == 'major' else '小调'}曲子不足，"
                          f"没有学到这个调式；可用：{'、'.join(d['vocab'])}")
    if not bars:
        bars = int(prefs.get('bars') or 0) if ex.get('bars') else 0
    bars = bars or (24 if style == 'madrigal' else 32)
    # 上限 32 → 96：原先 min(32, …) 会把 /compose-grammar hardbop 48 这类请求
    # 静默截成 32 小节，用户以为写的是 48 小节。上限放宽后仍按 8 小节对齐
    # （UNIT=8，段落以 8 小节为单位），并与 form_plan 的段数策略兼容。
    bars = max(8, min(96, 8 * round(bars / 8)))
    sharps = int(prefs['key_sharps']) if ex.get('key_sharps') and \
        prefs.get('key_sharps') is not None else _LEADSHEET_KEY[mode][style]
    tempo_bpm = int(prefs.get('tempo')) if ex.get('tempo') and prefs.get('tempo') else \
        int(d.get('tempo') or 120)
    if ov.get('key_sharps') is not None:
        sharps = int(ov['key_sharps'])
    tempo_bpm = int(ov.get('tempo') or tempo_bpm)
    inst = inst or sc.get('melody_instrument') or 'violin'
    instruments = _TEXTURE_INSTRUMENTS.get(d.get('texture', 'melody_piano'),
                                           [inst, 'Piano RH', 'Piano LH'])
    program = {'calls': [
        {'script': 'new_score', 'args': {
            'title': str(ov.get('title') or prefs.get('title') or f"{d['name']}（{sc['name']}语法）"),
            'key_sharps': sharps, 'mode': mode, 'meter': d['meter'], 'tempo': tempo_bpm,
            'bars': bars, 'voices': len(instruments), 'instruments': instruments,
            # 语法作曲允许写到 96 小节（form_plan 段数策略覆盖 12 段 × 8 小节），
            # 需显式打开 kernel 的 allow_long（默认上限 64）
            'allow_long': True,
            **({'seed': seed} if seed else {})}},
        {'script': 'set_sections', 'args': {'sections': [{'name': 'Tune', 'bars': bars}]}},
        {'script': 'leadsheet_grammar', 'args': {'style': style, 'sub': sub,
                                                 'temperature': temp, 'layout': layout}},
        {'script': 'finalize', 'args': {}},
    ]}
    try:
        spec = _K.run_program(program)
    except Exception as e:
        raise CommandOnly(f'语法作曲失败：{e}')
    session.score_spec = spec
    session.plan = {'title': spec.get('title'), 'bars': spec.get('bars'),
                    'tempo': spec.get('tempo'), 'meter': d['meter'],
                    'summary': f"可解释语法作曲（{sc['name']} · {d['name']}，确定性，不调 LLM）"}
    session.revision += 1
    session.last_skill_ids = [f'{style}-grammar']
    g = spec.get('grammar') or {}
    lay_say = '弱起 · ' if spec.get('pickup_ql') else '强拍起 · '
    lines = [f"按学到的{sc['name']}语法作曲（{d['name']}，确定性，不调 LLM）· {spec.get('title')} · "
             f"{lay_say}{bars} 小节 · 曲式 {g.get('form')}（{g.get('form_rule')}）· "
             f"seed={spec.get('seed')} · 硬规则违例 0"]
    for u in g.get('units') or []:
        mem = u.get('memory') or (f"再现第 {u['copy_of']} 段" if u.get('copy_of') else '')
        src = f'（{mem}）' if mem and mem != '新材料' else ''
        lines.append(f"第{u['unit']}段 {u.get('label', u['letter'])}{src} "
                     f"第{u['bars'][0]}–{u['bars'][1]}小节"
                     f" [{u['cadence']} {u['cadence_say']}]：{u['chords']}")
    return spec_to_music21(spec), spec, '\n'.join(lines)


def _compose_grammar_only_help(user_text: str = '') -> str:
    """新曲只允许 /compose-grammar（命令、关键词，或 LLM 路由到语法）。"""
    lines = [
        '新曲只走可解释语法作曲（/compose-grammar），已取消 Planner / translator / composer 写谱。',
        '可用：',
        '  /compose-grammar 交响曲 [小步舞曲|快板|二拍子|复拍子] [major|minor] [小节数]',
        '  /compose-grammar 奏鸣曲|四重奏|肖邦|民谣|爵士|流行|牧歌|众赞歌 …',
        '  或直接说：做一首交响 / 作一曲小步舞曲 / 来一首忧伤的玛祖卡',
        '已学风格：' + '、'.join(_LEADSHEET_STYLES) + '、chorale（众赞歌）',
    ]
    if (user_text or '').strip():
        lines.insert(1, f'未能把「{(user_text or "").strip()[:40]}」识别为已学风格。')
    return '\n'.join(lines)


_NEW_PIECE_MARKERS = re.compile(
    r'(做一首|作一曲|写一首|来一首|做一曲|写一曲|compose\s+(a|an|me)\b)', re.I)


def _is_new_piece_request(text: str) -> bool:
    return bool(_NEW_PIECE_MARKERS.search(text or ''))


_CHORALE_STYLE_WORDS = ('chorale', 'bach', '众赞歌', '巴赫', '圣咏')


_GRAMMAR_ROUTE_PROMPT = """You map a Chinese/English music request to /compose-grammar parameters.
Return ONE JSON object only, no markdown:
{"style":"symphony|sonata|strings|chopin|folk|jazz|pop|madrigal|chorale"|null,
 "sub":"allegro|minuet|duple|compound|mazurka|prelude|jig|reel|…"|null,
 "mode":"major"|"minor"|null,
 "bars":int|null, "key_sharps":int|null, "tempo":int|null}
Rules:
- style null if not a short NEW piece in a learned grammar (edits, fugue, free-form >32 bars, custom instrumentation).
- symphony: 交响/管弦/海顿/莫扎特交响/贝多芬交响; sonata: 奏鸣曲/钢琴; strings: 四重奏;
  chopin: 肖邦/玛祖卡; chorale: 众赞歌/巴赫圣咏; folk/jazz/pop/madrigal as usual.
- Bare 小步舞曲 → sonata/minuet; with 交响 → symphony/minuet; with 四重奏 → strings/minuet.
- Fill mode/key_sharps/tempo/bars ONLY from user text; else null.
"""


def _llm_route_compose_grammar(session, user_text: str) -> Optional[Dict[str, Any]]:
    """LLM 只负责把自然语言路由到 /compose-grammar 参数，不写音符。"""
    if not (user_text or '').strip():
        return None
    if not (load_llm_config() or {}).get('api_key'):
        return None
    try:
        raw = chat_completion([
            {'role': 'system', 'content': _GRAMMAR_ROUTE_PROMPT},
            {'role': 'user', 'content': (user_text or '').strip()[:500]},
        ], temperature=0.0, max_tokens=200)
    except Exception:
        return None
    try:
        data = json.loads(raw.strip().strip('`'))
        if isinstance(data, dict) and 'style' not in data and '{' in raw:
            # strip accidental fences
            m = re.search(r'\{.*\}', raw, re.S)
            data = json.loads(m.group(0)) if m else {}
    except Exception:
        try:
            m = re.search(r'\{[^{}]+\}', raw or '')
            data = json.loads(m.group(0)) if m else {}
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    style = str(data.get('style') or '').strip().lower()
    if not style or style in ('null', 'none'):
        return None
    if style in _CHORALE_STYLE_WORDS:
        style = 'chorale'
    else:
        style = _LEADSHEET_ALIASES.get(style, (style, ''))[0]
        if style not in _LEADSHEET_STYLES and style != 'chorale':
            return None
    out: Dict[str, Any] = {'style': style}
    sub = data.get('sub')
    if sub and str(sub).lower() not in ('null', 'none', ''):
        out['sub'] = str(sub).strip().lower()
    mode = str(data.get('mode') or '').strip().lower()
    if mode in ('major', 'minor'):
        out['mode'] = mode
    for k in ('bars', 'key_sharps', 'tempo'):
        v = data.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = int(v)
    _tr = getattr(session, 'compose_trace', None)
    if _tr is not None:
        _tr.setdefault('llm_calls', []).append({
            'stage': 'compose-grammar-route',
            'title': 'LLM → /compose-grammar 路由',
            'user': user_text,
            'assistant': raw,
            'parsed': out,
        })
    return out


def _detect_learned_grammar(text: str) -> Optional[Dict[str, Any]]:
    """无 LLM 时的兜底：按风格词识别「作一曲小步舞曲」这类短请求。
    同时命中多个风格词时取最长的（「海顿弦乐四重奏」→ 弦乐四重奏）。"""
    t = (text or '').strip().lower()
    if not t or len(t) > 80 or parse_command(t):
        return None
    if any(w in t for w in _FUGUE_WORDS + ('对位', 'counterpoint', '一比一', '1比1')):
        return None
    # 具名专业扩写（舒伯特快板等）或已是分段 brief → 不走短句语法
    if any(w in t for w in ('舒伯特', 'schubert', '【作品】', '呈示', '展开部', '副部')):
        return None

    def hits(words):
        return sorted((w for w in words if (re.search(rf'\b{w}\b', t) if w.isascii() else w in t)),
                      key=len, reverse=True)
    if any(w in t for w in _CHORALE_WORDS):
        style, sub = 'chorale', None
    else:
        sh = hits([w for w, (_s, sb) in _LEADSHEET_ALIASES.items() if not sb])
        bh = hits([w for w, (_s, sb) in _LEADSHEET_ALIASES.items() if sb])
        if not sh and not bh:
            return None
        style = _LEADSHEET_ALIASES[(sh or bh)[0]][0]
        sub = _LEADSHEET_ALIASES[bh[0]][1] if bh else None
    mode = 'minor' if re.search(r'小调|minor|忧伤|悲伤', t) else \
        ('major' if re.search(r'大调|major', t) else None)
    out: Dict[str, Any] = {'style': style, 'sub': sub, 'mode': mode}
    # 「A 大调 / Bb major」等显式调号 → 带进 /compose-grammar
    if re.search(r'[A-Ga-g][b#]?\s*(?:大调|小调|major|minor)', text or '', re.I):
        try:
            import brief_expand as _be
            sharps, km, _lab = _be._detect_key(text or '')
            out['key_sharps'] = sharps
            if not out['mode']:
                out['mode'] = km
        except Exception:
            pass
    return out


def _plan_learned_grammar(plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    lg = (plan or {}).get('learned_grammar')
    return lg if isinstance(lg, dict) and lg.get('style') else None


def _try_compose_learned_grammar(session, user_text: str, lg: Dict[str, Any],
                                 plan: Optional[Dict[str, Any]] = None):
    """Planner（或离线关键词）识别出已学风格 → 直接走 /compose-grammar 的确定性作曲。
    风格 / 子风格 / 调式没学到或要求超出语法能力（>32 小节）时返回 None，回到常规流程。"""
    style = str(lg.get('style') or '').strip().lower()
    sub = str(lg.get('sub') or '').strip().lower()
    mode = str(lg.get('mode') or '').strip().lower()
    mode = mode if mode in ('major', 'minor') else ''
    m = re.search(r'(?<![第\d])(\d{1,3})\s*个?小节', user_text or '')
    try:
        bars = int(lg.get('bars') or parse_bars_from_text(user_text) or (m and m.group(1)) or 0)
    except (TypeError, ValueError):
        bars = 0
    if bars > 32:
        return None
    ov: Dict[str, Any] = {}
    if isinstance(lg.get('key_sharps'), int) and -7 <= lg['key_sharps'] <= 7:
        ov['key_sharps'] = lg['key_sharps']
    if isinstance(lg.get('tempo'), (int, float)) and 30 <= lg['tempo'] <= 240:
        ov['tempo'] = int(lg['tempo'])
    if plan and plan.get('title'):
        ov['title'] = str(plan['title'])
    if style in _CHORALE_STYLE_WORDS:
        style, label = 'chorale', '巴赫众赞歌'
        args = ' '.join(x for x in ('chorale', mode, str(bars) if bars else '') if x)
    else:
        style = _LEADSHEET_ALIASES.get(style, ('', ''))[0]
        if style not in _LEADSHEET_STYLES:
            return None
        sub = _LEADSHEET_ALIASES.get(sub, ('', sub))[1] if sub else ''
        try:
            from score_learning.leadsheet import script as lscript
            sc = lscript.load(style)
        except Exception:
            return None
        if sub and sub not in sc['subs']:
            return None
        d = sc['subs'][sub or next(iter(sc['subs']))]
        if mode and mode not in d['vocab']:
            return None
        label = f"{sc['name']} · {d['name']}"
        args = ' '.join(x for x in (style, sub, mode, str(bars) if bars else '') if x)
    try:
        m21, spec, note = _compose_grammar_cmd(session, args, ov)
    except CommandOnly:
        return None
    src_tag = 'planner' if plan is not None else 'keyword-fallback'
    calls_text = f'/compose-grammar {args}' + (f'  overrides={ov}' if ov else '')
    _tr = getattr(session, 'compose_trace', None)
    if _tr is not None:
        _tr['pipeline'] = f'learned_grammar（{label}，意图来自 {src_tag}，确定性语法作曲，未写音符 LLM）'
        _tr['learned_grammar'] = {'style': style, 'sub': sub or None, 'mode': mode or None,
                                  'bars': bars or None, **ov}
        _tr['skill_ids'] = list(session.last_skill_ids or [])
        _tr['result'] = {
            'title': spec.get('title'), 'time_signature': spec.get('time_signature'),
            'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
            'key_sharps': spec.get('key_sharps'), 'bars_target': spec.get('bars'),
            'bars_actual': spec.get('bars'), 'voices': len(spec.get('parts') or []),
            'parts': [p.get('name') for p in (spec.get('parts') or [])],
            'has_fugue': False, 'composer': spec.get('composer'),
        }
    if plan:
        session.plan = {**plan, **(session.plan or {})}
    session.last_translate = _build_think_trace(
        session.plan, None, deterministic=True, calls_text=calls_text,
        reasoning_text=(f'意图来自 {src_tag}：用户要一首{label}，属于已学语法风格 → '
                        f'按学到的规则脚本确定性作曲（和声 / 旋律 / 曲式 / 织体都来自语料统计）。'),
        genre_fixes=[])
    return m21, spec, f'（识别为已学风格：{label} → {calls_text}）\n{note}'


# 乐器关键词 → 乐器名（按音域从高到低排列；匹配同时覆盖中英文）
_INSTRUMENT_KEYWORDS: List[Tuple[str, str]] = [
    ('短笛', 'piccolo'), ('piccolo', 'piccolo'),
    ('长笛', 'flute'), ('flute', 'flute'),
    ('锡笛', 'flute'), ('tin whistle', 'flute'), ('whistle', 'flute'),
    ('双簧管', 'oboe'), ('oboe', 'oboe'),
    ('单簧管', 'clarinet'), ('黑管', 'clarinet'), ('clarinet', 'clarinet'),
    ('巴松', 'bassoon'), ('大管', 'bassoon'), ('bassoon', 'bassoon'),
    ('小号', 'trumpet'), ('trumpet', 'trumpet'),
    ('圆号', 'horn'), ('法国号', 'horn'), ('horn', 'horn'),
    ('长号', 'trombone'), ('trombone', 'trombone'),
    ('大号', 'tuba'), ('tuba', 'tuba'),
    ('小提琴', 'violin'), ('violin', 'violin'),
    ('fiddle', 'violin'), ('富多', 'violin'),
    ('中提琴', 'viola'), ('viola', 'viola'),
    ('大提琴', 'cello'), ('cello', 'cello'),
    ('低音提琴', 'contrabass'), ('contrabass', 'contrabass'),
    ('double bass', 'contrabass'), ('doublebass', 'contrabass'),
    ('double_bass', 'contrabass'), ('upright bass', 'contrabass'),
    ('定音鼓', 'timpani'), ('timpani', 'timpani'),
    ('竖琴', 'harp'), ('harp', 'harp'),
    ('钢琴', 'piano'), ('piano', 'piano'),
]

# 编制别名 → 内核乐器名（plan / foundation 常写 jazz/folk 口语）
_INSTR_ALIASES = {
    'double bass': 'contrabass', 'doublebass': 'contrabass',
    'double_bass': 'contrabass', 'upright bass': 'contrabass',
    'bass': 'contrabass',  # jazz rhythm-section bass → 低音提琴
    'fiddle': 'violin', 'tin whistle': 'flute', 'whistle': 'flute',
    'synth pad': 'piano', 'pad': 'piano',
}
# 泛编制词（未点名具体乐器时按弦乐+低音+打击模板补）
_ENSEMBLE_KEYWORDS = ('管弦', '交响', '铜管', '木管', '弦乐组', '乐团', '乐队',
                      'orchestr', 'symphon', 'ensemble')
# 命中「交响/管弦」时确定性注入的缩编双管：
# **第一小提琴领奏（声部0）**，其余为衬托/伴奏——勿把长笛放第一（会听成全体乱响）。
# 泛编制词命中时的默认模板（按 voices，<=4 时生效）
_ENSEMBLE_TEMPLATES = {
    2: ['violin', 'cello'],
    3: ['flute', 'cello', 'timpani'],
    4: ['flute', 'violin', 'cello', 'timpani'],
}
# 长度不足时的补位池（保持高→低顺序）
_INSTRUMENT_FILL_POOL = ['violin', 'viola', 'cello', 'contrabass', 'timpani']


def _normalize_instrument(name: str) -> str:
    """把 plan/foundation 里的口语乐器名归一成内核认识的英文名。"""
    raw = str(name or '').strip()
    if not raw:
        return raw
    low = raw.lower().replace('-', ' ').replace('_', ' ')
    low = re.sub(r'\s+', ' ', low).strip()
    if low in _INSTR_ALIASES:
        return _INSTR_ALIASES[low]
    compact = low.replace(' ', '')
    for k, v in _INSTR_ALIASES.items():
        if k.replace(' ', '') == compact:
            return v
    # 已是内核名
    known = {n for _, n in _INSTRUMENT_KEYWORDS}
    if compact in known or low in known:
        return compact if compact in known else low
    return compact or raw


def _known_instrument_names() -> set:
    """内核可渲染的乐器名（INSTRUMENT_FACTORY + 别名目标）。"""
    return set(INSTRUMENT_FACTORY.keys()) | {v for v in _INSTR_ALIASES.values()}


def _normalize_instrument_list(items: Optional[List[Any]],
                               *, dedupe: bool = False) -> List[str]:
    """归一乐器名；丢弃内核尚不支持的名（如 drum_set），不顶替成定音鼓。

    编制以风格包 instruments_default 为准；未知名静默丢弃，避免鼓组→定音鼓。
    """
    known = _known_instrument_names()
    out: List[str] = []
    for x in items or []:
        n = _normalize_instrument(str(x))
        if not n:
            continue
        # 归一后仍不在工厂表 → 丢弃（不写死体裁替代乐器）
        if n not in known and n.replace(' ', '') not in known:
            continue
        if dedupe and n in out:
            continue
        out.append(n)
    return out


def _genre_ref(g: dict) -> str:
    """genre dict → ``family/pack_id``（供 new_score.style / plan.genre_id）。"""
    gid = str(g.get('id') or '')
    if gid.startswith('genre:'):
        return gid.split(':', 1)[-1]
    fam = str((g.get('dimensions') or {}).get('family') or '')
    pid = str(g.get('pack_id') or '')
    return f'{fam}/{pid}' if fam and pid else pid


def _bind_learned_genre(
    session: ComposeSession,
    plan: Dict[str, Any],
    skill_ids: List[str],
) -> List[str]:
    """把 LLM plan / prefs 接到已学风格包，写入 skill_ids + foundation 默认。

    顺序：prefs.genre_id → plan.genre_id → plan.genre 正式名。
    **不用关键词表**。未学包不强制 foundation。
    """
    ids = list(skill_ids or ['compose-full'])
    g = None
    # 1) 已由 /style 绑定
    pref_gid = (session.prefs.get('genre_id') or '').strip()
    if pref_gid:
        g = skill_lib.get_genre(pref_gid)
    # 2) LLM 写的 genre_id / genre
    if g is None:
        for key in ('genre_id', 'genre', 'style'):
            cand = str(plan.get(key) or '').strip()
            if not cand:
                continue
            # 允许 jazz/swing-standard 或 genre:jazz/swing-standard
            if not cand.startswith('genre:'):
                probe = cand if '/' in cand else cand
                g = skill_lib.get_genre(f'genre:{probe}') if '/' in probe else None
            else:
                g = skill_lib.get_genre(cand)
            if g is None:
                g = skill_lib.match_genre_by_name(
                    cand, learned_only=True, allow_prefix=True)
            if g:
                break
    if g is None:
        return ids

    gid = g.get('id') or ''
    ref = _genre_ref(g)
    plan['genre_id'] = gid
    plan['genre'] = g.get('name') or plan.get('genre') or ref
    session.prefs['genre_id'] = gid
    if not session.prefs.get('style'):
        session.prefs['style'] = g.get('name') or ref
    if gid and gid not in ids:
        ids.append(gid)

    # foundation：已学包默认小节/编制（用户显式 /bars 不覆盖）
    if not skill_lib.is_learned(str(gid)):
        return ids
    gen = g.get('generation') or {}
    fd = gen.get('foundation') or {}
    explicit = dict(session.prefs.get('_explicit') or {})
    bars_cfg = fd.get('bars') if isinstance(fd.get('bars'), dict) else {}
    default_bars = bars_cfg.get('default') or (gen.get('composition') or {}).get(
        'bars_default')
    min_bars = bars_cfg.get('min') if isinstance(bars_cfg, dict) else None
    try:
        default_bars = int(default_bars) if default_bars is not None else None
    except (TypeError, ValueError):
        default_bars = None
    try:
        min_bars = int(min_bars) if min_bars is not None else None
    except (TypeError, ValueError):
        min_bars = None
    cur_bars = int(plan.get('bars') or session.prefs.get('bars') or 8)
    if default_bars and not explicit.get('bars'):
        # LLM 常偷懒写 8；抬到该包 foundation 默认（如爵士 32、民谣 16）
        if cur_bars < default_bars and cur_bars <= 8:
            plan['bars'] = default_bars
            session.prefs['bars'] = default_bars
        elif min_bars and cur_bars < min_bars:
            plan['bars'] = default_bars or min_bars
            session.prefs['bars'] = plan['bars']
    insts = _normalize_instrument_list(fd.get('instruments_default') or [])
    # 已学包默认编制优先于 LLM 乱编的 Fiddle/Drum Kit（除非用户 /instrumentation）
    if insts and not explicit.get('instrumentation'):
        # 单 piano → 大谱表两行（众赞歌等缩谱）；开谱合唱留给用户显式编制
        plan['instrumentation'] = _expand_piano_to_grand(insts)
    elif plan.get('instrumentation'):
        plan['instrumentation'] = _expand_piano_to_grand(
            _normalize_instrument_list(plan.get('instrumentation') or []))
    return ids


def _detect_instruments(text: str) -> List[str]:
    """从用户文本中按音域高→低抽取提到过的乐器名（去重、稳定序）。"""
    if not text:
        return []
    low = text.lower()
    hits: List[str] = []
    for kw, name in _INSTRUMENT_KEYWORDS:
        # 中英文关键词统一在小写文本里找；中文不受 lower 影响
        if kw in low and name not in hits:
            hits.append(name)
    return hits


def _instruments_from_genre_pack(plan: Optional[Dict[str, Any]],
                                  skill_ids: Optional[List[str]] = None
                                  ) -> List[str]:
    """从已绑定风格包 generation.foundation.instruments_default 取编制。"""
    import composition_skills as skill_lib
    g = None
    gid = ''
    if isinstance(plan, dict):
        gid = str(plan.get('genre_id') or '').strip()
    if not gid:
        for sid in (skill_ids or []):
            if str(sid).startswith('genre:'):
                gid = str(sid)
                break
    if gid:
        g = skill_lib.get_genre(gid if gid.startswith('genre:') else f'genre:{gid}')
    if g is None:
        return []
    gen = g.get('generation') or {}
    fd = gen.get('foundation') or {}
    return _normalize_instrument_list(fd.get('instruments_default') or [])


def _expand_piano_to_grand(insts: Optional[List[str]]) -> List[str]:
    """单件 piano → 两行大谱表（内核再归一成 Piano RH/LH）。

    众赞歌等包的 instruments_default 常写 ``[piano]`` 表示钢琴缩谱，
    不能当成 voices=1，否则翻译失败回退到四行 SATB 开谱。
    """
    raw = [str(x).strip() for x in (insts or []) if str(x or '').strip()]
    if not raw:
        return []
    lows = [x.lower() for x in raw]
    if len(raw) == 1 and lows[0] in ('piano', 'pianoforte'):
        return ['piano', 'piano']
    if len(raw) == 2 and all(x == 'piano' for x in lows):
        return ['piano', 'piano']
    return raw


def _wants_open_satb(plan: Optional[Dict[str, Any]], user_text: str = '') -> bool:
    """用户明确要合唱开谱/四行人声时，不收成钢琴大谱表。"""
    blob = ' '.join([
        str(user_text or ''),
        str((plan or {}).get('user_brief') or ''),
        str((plan or {}).get('summary') or ''),
        str((plan or {}).get('genre') or ''),
        ' '.join(str(x) for x in ((plan or {}).get('instrumentation') or [])),
    ]).lower()
    keys = (
        '合唱', '人声', '四行', '开谱', '无伴奏合唱', 'a cappella', 'acappella',
        'choir', 'vocal quartet', 'satb 分谱', 'soprano, alto, tenor, bass',
    )
    return any(k in blob for k in keys)


def _wants_piano_grand(plan: Optional[Dict[str, Any]],
                       skill_ids: Optional[List[str]] = None,
                       user_text: str = '') -> bool:
    """编制意向是钢琴（含众赞歌钢琴缩谱）→ 大谱表两行。"""
    if _wants_open_satb(plan, user_text):
        return False
    if isinstance(plan, dict) and (
            plan.get('_species_hint') or plan.get('species')
            or _plan_indicates_species(plan)):
        return True
    # 无 LLM 离线兜底：keyword 检测
    llm_on = bool((load_llm_config() or {}).get('api_key'))
    if not llm_on:
        try:
            from music_score.two_part_species import detect_two_part_species
            blob = ' '.join([
                str(user_text or ''),
                str((plan or {}).get('user_brief') or ''),
            ])
            if detect_two_part_species(blob):
                return True
        except Exception:
            pass
    inst = []
    if isinstance(plan, dict):
        raw = plan.get('instrumentation')
        if isinstance(raw, str):
            inst = _normalize_instrument_list(
                [x.strip() for x in raw.split(',') if x.strip()])
        elif isinstance(raw, list):
            inst = _normalize_instrument_list(raw)
    if not inst:
        inst = _instruments_from_genre_pack(plan, skill_ids)
    inst = _expand_piano_to_grand(inst)
    return bool(inst) and all(x.lower() == 'piano' for x in inst)


def _is_satb_open_parts(parts: List[Dict[str, Any]]) -> bool:
    if len(parts) != 4:
        return False
    names = [str(p.get('name') or '').lower() for p in parts]
    def has(*keys):
        return any(any(k in n for k in keys) for n in names)
    return (has('soprano', '女高') and has('alto', '女低')
            and has('tenor', '男高') and has('bass', '男低'))


def _pitch_midi_safe(p) -> Optional[int]:
    if p is None:
        return None
    if isinstance(p, (list, tuple)):
        ms = [_pitch_midi_safe(x) for x in p]
        ms = [m for m in ms if m is not None]
        return max(ms) if ms else None
    try:
        from music21 import pitch as m21pitch
        return int(m21pitch.Pitch(str(p)).midi)
    except Exception:
        return None


def _merge_satb_pair_events(hi_events, lo_events, bars: int, ts: str) -> List[List]:
    """把两个声部按时间轴叠成和弦事件（同节奏直接 zip，否则按时值切片）。"""
    hi = _normalize_events(hi_events, bars, ts)
    lo = _normalize_events(lo_events, bars, ts)
    if (len(hi) == len(lo)
            and all(a[1] == b[1] for a, b in zip(hi, lo))):
        out = []
        for a, b in zip(hi, lo):
            ap, ad, am = a
            bp, _bd, bm = b
            mark = am or bm
            if ap is None and bp is None:
                ev = [None, ad]
            elif ap is None:
                ev = [bp, ad]
            elif bp is None:
                ev = [ap, ad]
            else:
                pair = [ap, bp]
                pair.sort(key=lambda x: _pitch_midi_safe(x) or 0, reverse=True)
                ev = [pair, ad]
            if mark:
                ev.append(mark)
            out.append(ev)
        return out

    # 不同节奏：按边界切片合并
    def timeline(evs):
        t = 0.0
        tl = []
        for pitch, dur, _mark in evs:
            ql = _dur_ql(dur)
            tl.append((t, t + ql, pitch))
            t += ql
        return tl

    thi, tlo = timeline(hi), timeline(lo)
    bounds = sorted({0.0, *[x for seg in thi + tlo for x in (seg[0], seg[1])]})
    out = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < 1e-9:
            continue
        mid = (a + b) / 2.0

        def sounding(tl):
            for s, e, p in tl:
                if s - 1e-9 <= mid < e - 1e-9:
                    return p
            return None

        hp, lp = sounding(thi), sounding(tlo)
        ql = b - a
        # 找最接近的标准时值名
        dur = min(DUR_QL.items(), key=lambda kv: abs(kv[1] - ql))[0]
        if abs(DUR_QL[dur] - ql) > 0.05:
            # 残余用多个更小的时值拼（简化：取 eighth 近似）
            dur = 'eighth' if ql <= 0.75 else 'quarter'
        if hp is None and lp is None:
            out.append([None, dur])
        elif hp is None:
            out.append([lp, dur])
        elif lp is None:
            out.append([hp, dur])
        else:
            pair = [hp, lp]
            pair.sort(key=lambda x: _pitch_midi_safe(x) or 0, reverse=True)
            out.append([pair, dur])
    # 用 normalize 再校准总时值
    return [list(x) for x in _normalize_events(out, bars, ts)]


def _collapse_satb_to_piano_grand(spec: Dict[str, Any]) -> Optional[str]:
    """四行 SATB 开谱 → 钢琴大谱表 RH(S+A) / LH(T+B)。成功返回说明，否则 None。"""
    parts = list(spec.get('parts') or [])
    if not _is_satb_open_parts(parts):
        return None
    # 按名定位
    def find(*keys):
        for i, p in enumerate(parts):
            n = str(p.get('name') or '').lower()
            if any(k in n for k in keys):
                return i
        return None
    si = find('soprano', '女高')
    ai = find('alto', '女低')
    ti = find('tenor', '男高')
    bi = find('bass', '男低')
    if None in (si, ai, ti, bi):
        return None
    bars = int(spec.get('bars') or 8)
    ts = str(spec.get('time_signature') or '4/4')
    rh_ev = _merge_satb_pair_events(
        parts[si].get('events') or [], parts[ai].get('events') or [], bars, ts)
    lh_ev = _merge_satb_pair_events(
        parts[ti].get('events') or [], parts[bi].get('events') or [], bars, ts)
    dyn = (parts[si].get('dynamics') or parts[0].get('dynamics'))
    spec['parts'] = [
        {'name': 'Piano RH', 'program': 0, 'clef': 'treble',
         'events': rh_ev, 'dynamics': dyn},
        {'name': 'Piano LH', 'program': 0, 'clef': 'bass',
         'events': lh_ev, 'dynamics': dyn},
    ]
    spec['staff_groups'] = [
        {'parts': [0, 1], 'symbol': 'brace', 'name': 'Piano'}]
    return 'SATB 开谱已收成钢琴大谱表（RH=S+A，LH=T+B）'


def _ensure_instruments(program: Dict[str, Any],
                        plan: Optional[Dict[str, Any]],
                        skill_ids: Optional[List[str]] = None
                        ) -> Tuple[Dict[str, Any], str]:
    """编制兜底：风格包 instruments_default > plan.instrumentation > 已有归一。

    不按「交响」等关键词写死编制——各风格用什么乐器写在包的 foundation 里。
    单件 piano 扩成两行大谱表（voices=2），避免众赞歌等回退成四行 SATB。
    """
    calls = list((program or {}).get('calls') or [])
    ns = next((c for c in calls if str(c.get('script')) == 'new_score'), None)
    if ns is None:
        return program, ''
    args = ns.setdefault('args', {})
    plan_inst = _expand_piano_to_grand(_normalize_instrument_list(
        (plan or {}).get('instrumentation') if isinstance(plan, dict) else None))
    pack_inst = _expand_piano_to_grand(
        _instruments_from_genre_pack(plan, skill_ids))
    # 用户明确要合唱开谱时，不要把包默认的 piano 强加进去
    if _wants_open_satb(plan, str((plan or {}).get('user_brief') or '')):
        preferred = plan_inst or []
    else:
        preferred = plan_inst or pack_inst

    existing = args.get('instruments')
    if preferred:
        # 风格包/plan 编制优先；若 LLM 已写且与包冲突，仍以包为准（已学参数）
        if list(existing or []) != preferred:
            args['instruments'] = list(preferred)
            args['voices'] = len(preferred)
            src = 'plan.instrumentation' if plan_inst else '风格包 foundation'
            note = f'按{src}注入 instruments={preferred}'
            if preferred == ['piano', 'piano']:
                note += '（钢琴大谱表 RH/LH）'
            return program, note
        args['voices'] = len(preferred)
        return program, ''

    # 无包编制：只做别名归一
    if isinstance(existing, list) and existing:
        norm = _expand_piano_to_grand(_normalize_instrument_list(existing))
        if norm and norm != list(existing):
            args['instruments'] = norm
            args['voices'] = len(norm)
            return program, f'编制别名归一：{existing} → {norm}'
        if not norm:
            args.pop('instruments', None)
            return program, f'丢弃不支持的乐器 {existing}'
        args['instruments'] = norm
        args['voices'] = len(norm)
        return program, ''

    # 末路：brief 点名乐器时补全（仍不写死交响模板）
    text = ' '.join(str(plan.get(k) or '') for k in (
        'user_brief', 'summary', 'genre', 'title')
                    ) if isinstance(plan, dict) else ''
    hits = _detect_instruments(text)
    if not hits and not any(k in text.lower() for k in _ENSEMBLE_KEYWORDS):
        return program, ''
    try:
        voices = int(args.get('voices') or 2)
    except (TypeError, ValueError):
        voices = 2
    if voices not in (2, 3, 4):
        return program, ''
    if not hits:
        hits = list(_ENSEMBLE_TEMPLATES[voices])
    for filler in _INSTRUMENT_FILL_POOL:
        if len(hits) >= voices:
            break
        if filler not in hits:
            hits.append(filler)
    if len(hits) > voices:
        hits = hits[:voices]
    while len(hits) < voices:
        hits.append(hits[-1])
    order = {name: i for i, (_kw, name) in enumerate(_INSTRUMENT_KEYWORDS)}
    hits.sort(key=lambda nm: order.get(nm, 99))
    args['instruments'] = hits
    return (program,
            f'LLM 未指定乐器，已按 brief 确定性补入 instruments={hits}')


def _ensure_style_contract(program: Dict[str, Any],
                           skill_ids: Optional[List[str]] = None,
                           plan: Optional[Dict[str, Any]] = None
                           ) -> Tuple[Dict[str, Any], str]:
    """若命中已学风格包，确定性注入 new_score.style（不依赖 LLM）。

    优先 skill_ids 中的 genre:*；其次 plan.genre_id。
    """
    calls = list((program or {}).get('calls') or [])
    ns = next((c for c in calls if str(c.get('script')) == 'new_score'), None)
    if ns is None:
        return program, ''
    args = ns.setdefault('args', {})
    if args.get('style'):
        return program, ''
    import composition_skills as skill_lib
    gid = None
    for sid in (skill_ids or []):
        if str(sid).startswith('genre:') and skill_lib.is_learned(str(sid)):
            gid = str(sid)
            break
    if gid is None and isinstance(plan, dict):
        cand = plan.get('genre_id') or ''
        if cand and skill_lib.is_learned(str(cand)):
            gid = str(cand)
    if not gid:
        return program, ''
    ref = gid.split(':', 1)[-1] if gid.startswith('genre:') else gid
    args['style'] = ref
    return program, f'风格契约：已注入 style={ref}'


#: 「某乐器独奏/主奏」标记词（中英文）
_SOLO_MARKERS = ('独奏', '主奏', '领奏', 'solo', '独唱')
#: 乐器名与独奏标记之间的最大字符距离。超过就不算「指定该乐器独奏」——
#: 避免长 brief 里把「提到某乐器」误判成「指定它独奏」。
_SOLO_WINDOW = 20


def _detect_solo_instrument(text: str) -> Optional[str]:
    """从 brief 识别「指定某乐器独奏」意图，返回规范乐器名（未识别返回 None）。

    判定依据是**乐器名与独奏标记必须在邻近窗口内共现**：「大提琴独奏」「由长笛主奏」
    「flute solo」会命中；「用小提琴写一段旋律」这类只提到乐器、无独奏意图的不会命中。
    多个候选取距离最近的一处（同样近时取编制表中靠前者，即音域高者优先）。
    """
    if not text:
        return None
    low = text.lower()
    best_d: Optional[int] = None
    best: Optional[str] = None
    for kw, name in _INSTRUMENT_KEYWORDS:
        kwl = kw.lower()
        start = 0
        while True:
            p = low.find(kwl, start)
            if p < 0:
                break
            for mk in _SOLO_MARKERS:
                mkl = mk.lower()
                q = low.find(mkl)
                while q >= 0:
                    d = abs(p - q)
                    if d <= _SOLO_WINDOW and (best_d is None or d < best_d):
                        best_d, best = d, name
                    q = low.find(mkl, q + 1)
            start = p + 1
    return best


def _apply_solo_routing(program: Dict[str, Any],
                        plan: Optional[Dict[str, Any]]
                        ) -> Tuple[Dict[str, Any], str]:
    """旋律路由注入：brief 指定某乐器独奏时，把该乐器写成 new_score 的 solo_instrument。

    真正的「提到旋律声部 + 旋律音区收敛到该乐器可奏范围」由内核确定性完成；本函数
    只做意图识别与参数注入，与 `_ensure_instruments` 同一思路——不依赖 LLM 自觉传参。
    独奏乐器必须已在本曲编制里（同一份关键词表保证它被补入），否则不擅自改写编制。
    """
    calls = list((program or {}).get('calls') or [])
    ns = next((c for c in calls if str(c.get('script')) == 'new_score'), None)
    if ns is None:
        return program, ''
    args = ns.setdefault('args', {})
    text = ' '.join(str(plan.get(k) or '') for k in ('user_brief', 'summary')
                    ) if isinstance(plan, dict) else ''
    solo = _detect_solo_instrument(text)
    if not solo:
        return program, ''
    insts = args.get('instruments')
    if not (isinstance(insts, list) and insts):
        return program, ''
    if solo.lower() not in [str(x).strip().lower() for x in insts]:
        return program, ''
    args['solo_instrument'] = solo
    return (program,
            f'旋律路由：brief 指定「{solo}」独奏 → 该乐器提到旋律声部，'
            f'并把旋律音区收进它的可奏音域')


def _ensure_fugue_section(program: Dict[str, Any], mode: str = 'auto',
                          voices: int = 0) -> Tuple[Dict[str, Any], str]:
    """确定性兜底：仅在用户明确要求整首赋格（mode='full'）时补 fugue 调用。

    mode: 'auto'=尊重 translator 的编排决定（brief 提到赋格时由 LLM 自己安排，
    不再强制「每首必赋格」）；'full'=整首写成赋格（/fugue full 或 brief 明确要求）；
    'off'=不加。越权自加的 fugue 由 _strip_unrequested_fugue 摘除。
    返回 (新 program, 说明文字)。改不动时原样返回。
    """
    calls = list((program or {}).get('calls') or [])
    if mode != 'full' or len(calls) < 3:
        return program, ''
    if any(str(c.get('script')) == 'fugue' for c in calls):
        return program, ''
    ns = next((c for c in calls if str(c.get('script')) == 'new_score'), None)
    sec = next((c for c in calls if str(c.get('script')) == 'set_sections'), None)
    if ns is None or sec is None:
        return program, ''
    ns_args = ns.setdefault('args', {})
    if voices in (2, 3, 4):
        ns_args['voices'] = voices
    else:
        try:
            cur_v = int(ns_args.get('voices') or 0)
        except (TypeError, ValueError):
            cur_v = 0
        # 赋格引擎只有 2–4 声部音域表：大编制整首赋格收成 4 声部缩谱
        if cur_v > 4:
            ns_args['voices'] = 4
            ns_args.pop('instruments', None)
    if not ((sec.get('args') or {}).get('sections') or []):
        return program, ''

    keep: List[Dict[str, Any]] = []
    for c in calls:
        nm = str(c.get('script') or '')
        if nm in ('set_harmony', 'accompaniment', 'melody', 'thicken', 'cadence'):
            continue          # 整首赋格：自带对位织体与终止
        keep.append(c)
    pos = next((i for i, c in enumerate(keep)
                if str(c.get('script')) == 'finalize'), len(keep))
    keep.insert(pos, {'script': 'fugue', 'args': {}})
    return {'calls': keep}, '赋格段（确定性）：整首'


def _run_translator(session, plan, skill_ids):
    """LLM 翻译器：需求 → 确定性脚本调用序列 → 内核生成 spec。

    LLM 不写任何音符；内核保证时值/调性/织体/终止正确。
    脚本违约时把错误回喂，重试一次；仍失败抛异常（调用方回退旧 composer）。
    """
    from compose_kernel import KernelError, kernel_docs, run_program
    payload = {
        'plan': {k: plan.get(k) for k in (
            'title', 'genre', 'mood', 'tempo', 'tempo_text', 'meter',
            'key_sharps', 'mode', '_emotion_hint', '_species_hint', 'species',
            'instrumentation', 'form', 'bars', 'motif', 'harmony', 'rhythm',
            'texture', 'climax', 'summary')},
        'user_brief': plan.get('user_brief'),
    }
    system = _prompt('translator', skill_ids) + '\n\n' + kernel_docs()
    if plan.get('_species_hint') or plan.get('species') or _plan_indicates_species(plan):
        try:
            from music_score.two_part_species import get_counterpoint_rules_doc
            system += '\n\n' + get_counterpoint_rules_doc()
        except Exception:
            system += (
                '\n\n## 硬约束：第一类二声部对位\n'
                '- new_score: voices=2, instruments=["piano","piano"]\n'
                '- 禁止调用 fugue；每小节每声部一个全音符\n'
                '- 中间 3/6 为主，可反向至 5/8；禁止纯 4 度\n'
                '- 结尾反向到八度；高音导→主、低音Ⅱ↓Ⅰ\n'
            )
        system += (
            '\n\n## Translator 执行要点（species）\n'
            '- new_score: voices=2, instruments=["piano","piano"]（钢琴大谱表）\n'
            '- 禁止调用 fugue\n'
            '- melody rhythm=sparse；accompaniment 与小节数等长的 whole 音符\n'
        )
    messages = [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ]
    fugue_mode = str(getattr(session, 'prefs', {}).get('fugue') or 'auto').lower()
    try:
        fugue_voices = int(getattr(session, 'prefs', {}).get('voices') or 0)
    except (TypeError, ValueError):
        fugue_voices = 0
    tr = getattr(session, 'compose_trace', None)
    kernel_trace = tr['kernel_calls'] if tr else None
    last_err = None
    for _attempt in range(2):
        raw = chat_completion(messages)
        program = extract_json_object(raw)
        program, _fugue_note = _ensure_fugue_section(program, fugue_mode,
                                                     fugue_voices)
        # 编配越权约束：brief/plan 未提赋格时，摘除 translator 自加的 fugue
        program, _strip_note = _strip_unrequested_fugue(program, plan, fugue_mode)
        program, _grammar_note = _strip_unrequested_grammar(program, plan)
        # 乐器编制兜底：brief 提到乐器/编制而 LLM 忘传 instruments 时确定性补入
        program, _inst_note = _ensure_instruments(program, plan, skill_ids)
        # 已学风格契约注入 new_score.style
        program, _style_note = _ensure_style_contract(program, skill_ids, plan)
        # 旋律路由：brief 指定某乐器独奏时，把该乐器提到旋律声部（音区随之收敛）
        program, _solo_note = _apply_solo_routing(program, plan)
        # 风格硬约束：在内核执行前把 genre profile 的 meter / 伴奏 / 小节数
        # 确定性写回 program（修复「软提示被 LLM 无视」导致 4/4 圆舞曲等）
        program, genre_fixes = _apply_genre_constraints(program, plan, skill_ids)
        program, species_fixes = _apply_species_program_constraints(program, plan)
        if species_fixes:
            genre_fixes = list(genre_fixes) + list(species_fixes)
        # 让「意图解析」面板反映被强制后的拍号（plan 是引用，会被 trace 读取）；
        # 用户显式 /meter 指定的拍号不被 genre 硬约束覆盖
        _genre_prof = _resolve_genre_profile(skill_ids)
        _g_primary = (_genre_prof or {}).get('meter', {}).get('primary')
        if _g_primary and not ((session.prefs or {}).get('_explicit') or {}).get('meter'):
            plan['meter'] = _g_primary
        # 追溯：捕获 LLM 翻译推理 + 最终脚本调用序列 + 风格硬约束，供 UI 展示
        session.last_translate = _build_think_trace(
            plan, program, deterministic=False, genre_fixes=genre_fixes)
        try:
            spec = run_program(program, trace=kernel_trace)
            # 速度钳进 genre 范围（只改 BPM，不影响时值）；
            # 用户显式 /tempo 直通谱面，不做 genre 钳制
            tempo_fixes = _clamp_tempo_to_genre(
                spec, _genre_prof,
                explicit_tempo=bool(((session.prefs or {}).get('_explicit')
                                     or {}).get('tempo')))
            if tempo_fixes:
                genre_fixes = list(genre_fixes) + tempo_fixes
                session.last_translate['genre_fixes'] = genre_fixes
            plan['tempo'] = spec.get('tempo')
            target = int(plan.get('bars') or 0)
            if target and int(spec.get('bars') or 0) != target:
                raise KernelError(
                    f'总小节数 {spec.get("bars")} ≠ plan.bars {target}')
            # 透明化：记录 translator 这次成功的 LLM I/O + 风格纠正
            if tr is not None:
                tr['llm_calls'].append({
                    'stage': 'translator',
                    'title': 'Translator（脚本翻译）',
                    'system': _truncate(system, 1400),
                    'user': json.dumps(payload, ensure_ascii=False),
                    'assistant': raw,
                    'parsed': {'reasoning': program.get('reasoning'),
                               'calls': program.get('calls')},
                })
                tr['translator_attempts'] = _attempt + 1
                tr['constraint_fixes'] = list(genre_fixes)
            spec.setdefault('composer', 'translated-kernel')
            return spec
        except KernelError as e:
            last_err = str(e)
            messages = messages[:2] + [
                {'role': 'assistant', 'content': raw},
                {'role': 'user', 'content': (
                    f'脚本执行失败：{last_err}\n'
                    '请修正后重新输出完整的 {"calls": [...]} JSON（只输出 JSON）。')},
            ]
    raise RuntimeError(f'翻译脚本执行失败：{last_err}')


def _run_planner(session: ComposeSession, user_text: str, edit_intent: Optional[str],
                 skill_ids: Optional[List[str]] = None,
                 advice_edits: Optional[str] = None) -> Dict[str, Any]:
    skill_ids = skill_ids or session.last_skill_ids or ['compose-full']
    payload = {
        'user_request': user_text,
        'preferences': session.prefs,
        'edit_intent': edit_intent or session._pending_edit,
        'skills': skill_ids,
        'existing_plan': session.plan,
        'revision_edits': advice_edits,
        'existing_score_summary': {
            'title': (session.score_spec or {}).get('title'),
            'bars': (session.score_spec or {}).get('bars'),
            'tempo': (session.score_spec or {}).get('tempo'),
            'parts': [p.get('name') for p in (session.score_spec or {}).get('parts') or []],
        } if session.score_spec else None,
    }
    system = _prompt('planner', skill_ids)
    try:
        from music_score.two_part_species import (
            get_counterpoint_rules_doc, counterpoint_rules_compact,
        )
        system += (
            '\n\n## 对位法规则（凡 species / 一比一 / 对位法 必遵）\n'
            + counterpoint_rules_compact()
            + '\n\n完整规则：\n'
            + get_counterpoint_rules_doc()
        )
    except Exception:
        pass
    raw = chat_completion([
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ])
    plan = extract_json_object(raw)
    _trace_planner(session, system, payload, raw, plan)
    plan = merge_prefs_into_plan(session, plan)
    bars = resolve_plan_bars(session, plan, user_text=user_text, edit_intent=edit_intent)
    plan['bars'] = bars
    session.prefs['bars'] = bars
    if not plan.get('edit_intent') and (edit_intent or session._pending_edit):
        plan['edit_intent'] = edit_intent or session._pending_edit
    return plan


def _run_composer(session: ComposeSession, plan: Dict[str, Any], edit_intent: Optional[str],
                  skill_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    skill_ids = skill_ids or session.last_skill_ids or ['compose-full']
    payload = {
        'plan': plan,
        'edit_intent': edit_intent,
        'skills': skill_ids,
        'previous_score': session.score_spec if edit_intent else None,
        'instruction': (
            'Preserve key/tempo/meter/motifs unless plan says otherwise. '
            f'Target length is exactly plan.bars={plan.get("bars")} measures — '
            'write full events for every bar. '
            'If plan.user_brief is present: follow its structural instructions '
            '(sections, texture/register/rhythm per section, climax, character) and '
            'express them through the NOTES, not just dynamics. Each section must '
            'differ in note values and density: quiet intro/ending = half/whole '
            'notes, sparse events; conflict = eighths with rests and syncopation, '
            'chromatic descending bass in low parts; climax = octave doubling '
            '(chord events with two pitches), higher register, wide leaps and '
            'eighth-note arpeggios in the accompaniment. Encode its per-section '
            '力度 via parts[].dynamics, 速度 via tempo_marks, and 拍号变化 via '
            'meter_changes. A uniform quarter-note texture for the whole piece is '
            'a defect — rewrite it. '
            'MANDATORY: every part MUST include a dynamics arc covering the '
            'sections (e.g. [[1,"pp"],[5,"mf"],[13,"ff"],[19,"p"]]) matching '
            'the brief\'s 力度 — a spec without dynamics is invalid. '
            'Transitions must be gradual, not hard cuts: at each section boundary '
            'the boundary bar should be intermediate in density (neither the '
            'previous section\'s busiest nor the next section\'s sparsest); '
            'accompaniment must calm down together with the melody when entering '
            'quiet sections (no busy arpeggios under pp); adjacent tempo_marks '
            'must be at least 2 bars apart (no one-bar più-mosso/rit/a-tempo '
            'churn); ending bars must contain real notes in EVERY part (no '
            'silence padding) and finish on tonic long notes. '
            'If plan.revision_edits is present (applying review advice): treat it as a '
            'priority-ordered edit list. Start from previous_score, change ONLY the bars '
            'and parts each edit names, and keep every other bar identical to '
            'previous_score. Protect anything the edits call 值得保留 / strengths. '
            '🔴 items are mandatory, 🟡 important, 🟢 optional; skip items marked '
            '可能不适用. Do NOT re-compose untouched bars. '
            'If edit_intent is rewrite/complexify: keep the same bar count as '
            'previous_score (unless plan.bars is larger); apply the revision_edits '
            'bar ranges in place — do NOT shrink to a short excerpt. '
            'If edit_intent is extend/longer, keep early material and continue through the new bars. '
            'If develop/variation, transform the motif. '
            'If orchestrate, change instrumentation per plan.'
        ),
    }
    if _plan_indicates_species(plan) or plan.get('species') or plan.get('_species_hint'):
        try:
            from music_score.two_part_species import get_counterpoint_rules_doc
            payload['instruction'] = (
                str(payload.get('instruction') or '')
                + '\n\n' + get_counterpoint_rules_doc()
            )
        except Exception:
            pass
    raw = chat_completion([
        {'role': 'system', 'content': _prompt('composer', skill_ids)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ])
    spec = _unwrap_spec(extract_json_object(raw))
    defaults = plan_to_score_defaults(plan)
    for k, v in defaults.items():
        spec.setdefault(k, v)
    # prefs win for explicit session settings：只有用户**显式命令**设置过的
    # 键才覆盖 spec；否则默认值（120 / 4/4 / 0 / major）不得打掉 plan/LLM 的推断
    ex = (session.prefs.get('_explicit') or {})
    if ex.get('tempo'):
        spec['tempo'] = session.prefs['tempo']
    if ex.get('meter'):
        spec['time_signature'] = session.prefs['meter']
    if ex.get('key_sharps'):
        spec['key_sharps'] = session.prefs['key_sharps']
    if ex.get('mode'):
        spec['mode'] = session.prefs['mode']
    spec['bars'] = int(plan.get('bars') or session.prefs['bars'])
    return spec


def _run_repair(spec: Dict[str, Any], issues: List[Dict[str, str]],
                skill_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    skill_ids = skill_ids or ['repair-timing']
    payload = {'score': spec, 'issues': issues}
    raw = chat_completion([
        {'role': 'system', 'content': _prompt('repair', skill_ids)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ])
    fixed = _unwrap_spec(extract_json_object(raw))
    for k in ('title', 'tempo', 'time_signature', 'bars', 'key_sharps', 'mode',
              'composer', 'key_changes', 'staff_groups'):
        if k in spec and k not in fixed:
            fixed[k] = spec[k]
    # 修复模型偶尔丢掉 parts：回退用原谱的 parts
    if not (fixed.get('parts') or []):
        fixed['parts'] = spec.get('parts') or []
    return fixed


def _events_to_bars(events: List[Any], bars: int, ts: str) -> List[List[List[Any]]]:
    """Split flat events into per-bar event lists."""
    bar_ql = _ql_per_bar(ts)
    out: List[List[List[Any]]] = [[] for _ in range(bars)]
    t = 0.0
    for ev in events or []:
        if not isinstance(ev, (list, tuple)) or len(ev) < 2:
            continue
        pitch, dur = ev[0], str(ev[1])
        ql = _dur_ql(dur)
        bi = int(t / bar_ql + 1e-9)
        if 0 <= bi < bars:
            out[bi].append([None if pitch in (None, 'rest', 'Rest', '') else pitch, dur])
        t += ql
    # pad empty bars with rests
    for bi in range(bars):
        total = sum(_dur_ql(e[1]) for e in out[bi])
        while total + 1e-6 < bar_ql:
            remain = bar_ql - total
            if remain >= 1.0:
                out[bi].append([None, 'quarter']); total += 1.0
            elif remain >= 0.5:
                out[bi].append([None, 'eighth']); total += 0.5
            else:
                out[bi].append([None, '16th']); total += 0.25
    return out


def _bars_to_events(bar_lists: List[List[List[Any]]]) -> List[List[Any]]:
    flat: List[List[Any]] = []
    for bar in bar_lists:
        for ev in bar:
            flat.append(list(ev))
    return flat


def _normalize_bar_events(events: List[Any], ts: str) -> List[List[Any]]:
    target = _ql_per_bar(ts)
    out: List[List[Any]] = []
    total = 0.0
    for ev in events or []:
        if not isinstance(ev, (list, tuple)) or len(ev) < 2:
            continue
        pitch, dur = ev[0], str(ev[1])
        if dur not in DUR_QL:
            dur = 'quarter'
        ql = _dur_ql(dur)
        if total + ql > target + 1e-6:
            break
        out.append([None if pitch in (None, 'rest', 'Rest', '') else pitch, dur])
        total += ql
    # 贪心取最大可容纳时值，避免 remain∈(0,0.25) 时强制塞 16th 过冲
    while total + 1e-6 < target:
        remain = target - total
        name = next((n for n in _FILL_DUR_NAMES
                     if _dur_ql(n) <= remain + 1e-9), None)
        if name is None:
            break
        out.append([None, name])
        total += _dur_ql(name)
    return out


def describe_selection(selection: Optional[Dict[str, Any]]) -> str:
    if not selection:
        return ''
    measures = selection.get('measures') or []
    symbols = selection.get('symbols') or []
    parts = []
    if measures:
        human = ', '.join(str(m + 1) for m in sorted(measures))
        parts.append(f'小节 {human}')
    if symbols:
        bits = []
        for s in symbols:
            bits.append(
                f"声部{s.get('staff_idx', 0) + 1} "
                f"第{s.get('measure_idx', 0) + 1}小节 "
                f"第{s.get('note_idx', 0) + 1}个符号"
                f"({s.get('pitch') or '休止'},{s.get('duration') or '?'})"
            )
        parts.append('符号: ' + '; '.join(bits))
    return '；'.join(parts)


def m21_to_score_spec(m21_score) -> Dict[str, Any]:
    """从 music21 Score 提炼可编辑 SCORE JSON（用于选区编辑已有曲目）。"""
    from music21 import note as n21
    from music21 import chord as c21
    md = m21_score.metadata
    title = '对话创曲'
    if md:
        for cand in (
            getattr(md, 'bestTitle', None),
            md.title,
            getattr(md, 'movementName', None),
        ):
            if cand and str(cand).strip():
                title = str(cand).strip()
                break
    composer = (md.composer if md else None) or ''
    ts_obj = m21_score.recurse().getElementsByClass(meter.TimeSignature).first()
    ts = ts_obj.ratioString if ts_obj else '4/4'
    ks = m21_score.recurse().getElementsByClass(key.KeySignature).first()
    sharps = int(ks.sharps) if ks is not None else 0
    mm = m21_score.recurse().getElementsByClass(tempo.MetronomeMark).first()
    tempo_n = int(mm.number) if mm and mm.number else 120
    parts_out = []
    max_bars = 0
    pickup = 0.0
    try:
        m0 = m21_score.parts[0].getElementsByClass('Measure').first()
        if m0 is not None and float(m0.paddingLeft or 0) > 0:
            pickup = _ql_per_bar(ts) - float(m0.paddingLeft)
    except Exception:
        pickup = 0.0
    pickup = _pickup_ql({'pickup_ql': pickup, 'time_signature': ts})
    for i, part in enumerate(m21_score.parts):
        name = part.partName or part.id or f'Part{i + 1}'
        clef_obj = part.recurse().getElementsByClass(m21clef.Clef).first()
        clef_name = 'treble'
        if clef_obj is not None:
            cn = clef_obj.__class__.__name__.lower()
            if 'bass' in cn:
                clef_name = 'bass'
            elif 'alto' in cn:
                clef_name = 'alto'
        program = 0
        try:
            inst = part.getInstrument()
            if inst and inst.midiProgram is not None:
                program = int(inst.midiProgram)
        except Exception:
            pass
        events = []
        measures = list(part.getElementsByClass('Measure'))
        max_bars = max(max_bars, len(measures))
        for m in measures:
            for el in m.notesAndRests:
                if isinstance(el, n21.Rest):
                    events.append([None, el.duration.type if el.duration.type in DUR_QL else 'quarter'])
                elif isinstance(el, c21.Chord):
                    pitches = [p.nameWithOctave for p in el.pitches]
                    dur = el.duration.type if el.duration.type in DUR_QL else 'quarter'
                    events.append([pitches, dur])
                elif isinstance(el, n21.Note):
                    dur = el.duration.type if el.duration.type in DUR_QL else 'quarter'
                    events.append([el.nameWithOctave, dur])
        parts_out.append({
            'name': str(name), 'program': program, 'clef': clef_name, 'events': events,
        })
    if pickup:
        max_bars -= 1
    bars = max(4, min(32, max_bars or 8))
    # normalize each part to bars
    for p in parts_out:
        p['events'] = _normalize_events(p.get('events') or [], bars, ts, extra_ql=pickup)
        p['events'] = [[e[0], e[1]] for e in p['events']]
    out = {
        'title': str(title),
        'composer': str(composer) or '对话创曲',
        'key_sharps': sharps,
        'mode': 'major',
        'time_signature': ts,
        'tempo': tempo_n,
        'bars': bars,
        'parts': parts_out,
        'notes': '从现有乐谱导入以便选区编辑',
    }
    if pickup:
        out['pickup_ql'] = pickup
    return out


def _ensure_score_spec(session: ComposeSession, m21_score=None) -> Dict[str, Any]:
    if session.score_spec:
        return session.score_spec
    if m21_score is not None:
        session.score_spec = m21_to_score_spec(m21_score)
        return session.score_spec
    raise RuntimeError('当前没有可编辑的创曲规格。请先对话生成曲目，或 /demo。')


# ============================================================
# 确定性选区重写（无 LLM Key 也能工作）
# 选中音符/小节 + 提示词 → 仅重写该区域，其余保持不变。复用内核音符级原语，
# 与「确定性内核」理念一致。生产环境有 LLM 时仍走 _compose_selection_*（更灵活）；
# 无 Key（本地/测试）时走本确定性路径。
# ============================================================

_DUR_QL_FULL = {**DUR_QL, **_EXTRA_DUR_QL}  # 含附点：附点音符按小节切分需精确时值

_DUR_ALIASES = {
    '全音符': 'whole', '二分音符': 'half', '四分音符': 'quarter',
    '八分音符': 'eighth', '十六分音符': '16th',
    '附点全音符': 'dotted_whole', '附点二分音符': 'dotted_half',
    '附点四分音符': 'dotted_quarter', '附点八分音符': 'dotted_eighth',
    '附点全': 'dotted_whole', '附点二分': 'dotted_half',
    '附点四分': 'dotted_quarter', '附点八分': 'dotted_eighth',
    '全': 'whole', '二分': 'half', '四分': 'quarter',
    '八分': 'eighth', '十六分': '16th',
}
#: 长词优先——否则「附点八分音符」会被「八分音符」抢先命中而丢掉附点
_DUR_ALIAS_ORDER = sorted(_DUR_ALIASES, key=len, reverse=True)

#: 和弦符号词。根音只认**大写** A–G（和弦记法惯例：C / Dm7 / G7 / Bb），
#: 小写会把英文词尾的 e/d 之类误判成和弦。尾部双 lookahead 断开：
#:   (?![A-Za-z0-9])  → 'Andante' 里的 'A' 不算和弦
#:   (?![#b]?\d)      → 'G4'/'F#5'/'Bb3' 是音名，不算和弦
_CHORD_SYM_WORD_RE = re.compile(
    r'[A-G][#b]?'
    r'(?:maj7|Maj7|M7|m7b5|m7-5|min7|dim7|°7|ø7|o7|aug|dim|°|m7|min|maj|'
    r'add9|add7|add6|sus4|sus2|m|\+|-|7|6)?'
    r'(?![A-Za-z0-9])(?![#b]?\d)')
#: 罗马数字词（含品质后缀：ii7 / Imaj7 / V7 / vii°7 / bII），同样用 lookahead 断开。
#: 品质后缀必须是**捕获组**——解析时按 (前缀, 罗马, 品质) 三段拼回 token，
#: 用 (?:…) 会丢掉 'ii7' 的 '7'，把属七/七和弦静默降级成三和弦。
_ROMAN_WORD_RE = re.compile(
    r'([b#]*)([IViv]+)'
    r'(maj7|Maj7|M7|m7b5|m7-5|min7|dim7|°7|ø7|o7|aug|dim|°|m7|min|maj|'
    r'add9|add7|add6|sus4|sus2|m|\+|-|7|6)?'
    r'(?![A-Za-z0-9])')
_NOTE_NAME_RE = re.compile(r'[A-Ga-g][#b]?\d')
#: 非音乐 token（小节引用「第3小节」、拍号「4/4」）——提取级数前先剔除，
#: 否则这些数字会被误当成音阶级数 token
_NON_MUSIC_REF_RE = re.compile(
    r'第\s*\d+\s*(?:[–\-~至到]\s*\d+\s*)?小节|\d+\s*/\s*\d+|\d+\s*小节')

#: 补齐小节余量时按「大→小」贪心选，含附点（渲染层已支持 dotted_*）
_FILL_DUR_NAMES = ('whole', 'dotted_half', 'half', 'dotted_quarter', 'quarter',
                   'dotted_eighth', 'eighth', '16th', '32nd')


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _ql_per_bar_full(ts: str) -> float:
    num, den = map(int, ts.split('/'))
    return float(num) * (4.0 / float(den))


def _split_events_to_bars(events, bars, ts):
    """扁平 events 按小节切分（支持附点时值）。返回 bar_lists[bar]（0-based）。

    保留第 3 元素（演奏法记号 staccato/accent/tenuto/slur-*）——丢掉会让整段
    的连音线/重音全部消失。
    """
    bar_ql = _ql_per_bar_full(ts)
    out = [[] for _ in range(bars)]
    t = 0.0
    for ev in events or []:
        if not isinstance(ev, (list, tuple)) or len(ev) < 2:
            continue
        pitch, dur = ev[0], str(ev[1])
        ql = _dur_ql(dur)
        bi = int(t / bar_ql + 1e-9)
        if 0 <= bi < bars:
            row = [None if pitch in (None, 'rest', 'Rest', '') else pitch, dur]
            if len(ev) >= 3 and ev[2]:
                row.append(ev[2])
            out[bi].append(row)
        t += ql
    return out


def _flatten_bars(bar_lists):
    flat = []
    for bar in bar_lists:
        for ev in bar:
            flat.append(list(ev))
    return flat


def _trim_bar(bar, ts):
    """裁掉超出小节容量的尾部事件（保留尽量多的材料）。"""
    target = _ql_per_bar_full(ts)
    out, total = [], 0.0
    for ev in bar:
        ql = _dur_ql(str(ev[1]))
        if total + ql > target + 1e-6:
            break
        out.append(list(ev))
        total += ql
    return out


def _pad_bar(bar, ts):
    """用休止符把小节补满到拍号容量（贪心取大时值；含附点以精确收口）。"""
    target = _ql_per_bar_full(ts)
    out = [list(e) for e in bar]
    total = sum(_dur_ql(str(e[1])) for e in out)
    remain = target - total
    while remain > 1e-6:
        name = next((n for n in _FILL_DUR_NAMES
                     if _dur_ql(n) <= remain + 1e-9), None)
        if name is None:
            break
        out.append([None, name])
        remain -= _dur_ql(name)
    return out


def _fit_bar(bar, ts):
    """改过音符后把该小节重新规整到恰好一拍号容量。

    扁平 events 列表隐含「每小节时值之和 == 拍号容量」，任何小节缺口/溢出都会
    让其后所有音符整体前移/后移（小节线错位）。这里裁尾 + 补休止，保证不位移。
    """
    return _pad_bar(_trim_bar(bar, ts), ts)


def _detect_voices(spec):
    """返回 (melody_idx, bass_idx)。melody=顶部非 bass 声部，bass=clef=='bass' 声部。"""
    parts = spec.get('parts') or []
    melody_idx, bass_idx = 0, None
    for i, p in enumerate(parts):
        if str(p.get('clef')) == 'bass':
            bass_idx = i
            break
    for i, p in enumerate(parts):
        if str(p.get('clef')) != 'bass':
            melody_idx = i
            break
    return melody_idx, bass_idx


def _parse_duration_word(w):
    if not w:
        return None
    w = w.strip()
    if w in _DUR_QL_FULL:
        return w
    return _DUR_ALIASES.get(w)


def _first_duration_word(text):
    t = text or ''
    for w in _DUR_ALIAS_ORDER:      # 长词优先，避免附点被基础时值抢先命中
        if w in t:
            return w
    for d in _DUR_QL_FULL:
        if d in t:
            return d
    return None


def _extract_chord_symbols(text):
    out = []
    for m in _CHORD_SYM_WORD_RE.finditer(text or ''):
        tok = m.group(0)
        if not tok or not re.match(r'^[A-G]', tok):
            continue
        if not _is_chord_symbol(tok):
            continue
        try:
            _parse_chord_symbol(tok, [])
        except Exception:
            continue
        out.append(tok)
    return _dedupe(out)


def _extract_roman(text):
    out = []
    for m in _ROMAN_WORD_RE.finditer(text or ''):
        tok = ''.join(g for g in m.groups() if g)
        if _is_chord_symbol(tok):
            continue  # bII 等罗马优先，不当和弦
        out.append(tok)
    return _dedupe(out)


def _extract_melody_tokens(text):
    """返回 [(kind,val)]，kind∈abs|chord|deg。

    先从文本剔除「非音乐 token」——和弦符号(Dm7/G7)、罗马数字(ii7/V7)、小节引用
    (第3小节)、拍号(4/4)——否则其中的 '7'/'3'/'4' 会被误当成音阶级数，
    导致「改成 Dm7 和弦」这种和声指令顺手把旋律也改了。
    """
    t = _NON_MUSIC_REF_RE.sub(' ', text or '')
    t = _CHORD_SYM_WORD_RE.sub(' ', t)
    t = _ROMAN_WORD_RE.sub(' ', t)
    tokens = [('abs', m.group(0)) for m in _NOTE_NAME_RE.finditer(t)]
    if tokens:
        return tokens
    tokens = [('chord', int(m.group(1))) for m in re.finditer(r'\^([1-7])', t)]
    if tokens:
        return tokens
    return [('deg', int(m.group(1)))
            for m in re.finditer(r'(?<![\d/])([1-7])(?![\d])', t)]


def _bass_events(chord_names, ts, bar_ql):
    """低声部一小节的根/五度音型 —— **时值之和必须恰好 == bar_ql**。

    （曾经的 4/4 分支写了 4 个 half = 8 ql，等于两小节的量塞进一个小节槽，
    导致其后所有音符整体后移、小节线全部错位。）
    """
    root = chord_names[0]
    fifth = next((n for n in chord_names if _pc(n) == (_pc(root) + 7) % 12), root)
    root_m = _midi(root + '3')
    fifth_m = _midi(fifth + '3')
    r = _name_oct(root_m, [root])
    f = _name_oct(fifth_m, [fifth])
    if abs(bar_ql - 3.0) < 1e-6:  # 3/4：根–五–高八度根（圆舞琴低音）
        r2 = _name_oct(root_m + 12, [root])
        return [[r, 'quarter'], [f, 'quarter'], [r2, 'quarter']]
    if abs(bar_ql - 2.0) < 1e-6:  # 2/4：根–五
        return [[r, 'quarter'], [f, 'quarter']]
    return [[r, 'half'], [f, 'half']]  # 4/4：根(半)–五(半)


def _build_melody_events(tokens, chord_names, scale_names, center, ts, bar_ql, dur):
    events = []
    for kind, val in tokens:
        if kind == 'abs':
            name = val
            try:
                _midi(val)
            except Exception:
                name = None
        elif kind == 'chord':
            midi = _resolve_melody_token(
                '^%d' % val, chord_names, scale_names,
                center - 9, center + 10, center, clamp=True)
            name = _name_oct(midi, scale_names + chord_names) if midi is not None else None
        else:
            midi = _resolve_melody_token(
                str(val), chord_names, scale_names,
                center - 9, center + 10, center, clamp=True)
            name = _name_oct(midi, scale_names + chord_names) if midi is not None else None
        events.append([name, dur])
    total = sum(_DUR_QL_FULL[d] for _, d in events)
    while total + 1e-6 < bar_ql:
        remain = bar_ql - total
        d = ('whole' if remain >= 4 else 'half' if remain >= 2 else
             'quarter' if remain >= 1 else 'eighth' if remain >= 0.5 else '16th')
        events.append([None, d])
        total += _DUR_QL_FULL[d]
    if total > bar_ql + 1e-6:
        acc = 0.0
        trimmed = []
        for e in events:
            q = _DUR_QL_FULL[e[1]]
            if acc + q > bar_ql + 1e-6:
                break
            trimmed.append(e)
            acc += q
        events = trimmed
    return events


def _deterministic_edit_measures(
    session: ComposeSession, user_text: str, selection: Dict[str, Any],
    m21_score=None,
) -> Tuple[stream.Score, Dict[str, Any], str]:
    """无 LLM 的选中小节重写：提示词 → 仅改选中段落的和声/旋律。"""
    spec = copy.deepcopy(_ensure_score_spec(session, m21_score))
    measures = sorted({int(m) for m in (selection.get('measures') or [])})
    if not measures:
        raise ValueError('未选中小节')
    bars = int(spec.get('bars') or 8)
    ts = str(spec.get('time_signature') or '4/4')
    bar_ql = _ql_per_bar_full(ts)
    measures = [m for m in measures if 0 <= m < bars]
    if not measures:
        raise ValueError('选中小节超出曲目范围')
    scale_names = _scale_names(int(spec.get('key_sharps') or 0),
                               spec.get('mode') or 'major')
    melody_idx, bass_idx = _detect_voices(spec)
    text = user_text or ''

    want_melody = any(k in text for k in
                      ('旋律', 'melody', '琶音', '音阶', '级进', '上行', '下行',
                       '节奏', '时值', 'line'))
    want_harmony = (any(k in text for k in ('和声', '和弦', 'harmony', 'chord', '级'))
                    and not want_melody)
    chord_syms = _extract_chord_symbols(text)
    roman_syms = _extract_roman(text)
    note_tokens = _extract_melody_tokens(text)
    arpeggio = '琶音' in text
    run = '上行' in text or '下行' in text

    do_harmony = want_harmony or (chord_syms and not note_tokens
                                  and not arpeggio and not run)
    do_melody = (want_melody or bool(note_tokens) or arpeggio or run
                 or (chord_syms and arpeggio))

    if do_harmony:
        chords_for_bars = []
        for i, m in enumerate(measures):
            if chord_syms:
                chords_for_bars.append(
                    _parse_chord_symbol(chord_syms[i % len(chord_syms)], scale_names))
            elif roman_syms:
                chords_for_bars.append(
                    parse_roman(roman_syms[i % len(roman_syms)], scale_names))
            else:
                chords_for_bars.append(
                    spec.get('harmony', {}).get(m) or ['C', 'E', 'G'])
        for i, m in enumerate(measures):
            ch = chords_for_bars[i]
            spec.setdefault('harmony', {})[m] = list(ch)
            if bass_idx is not None:
                bl = _split_events_to_bars(
                    spec['parts'][bass_idx].get('events') or [], bars, ts)
                bl[m] = _bass_events(ch, ts, bar_ql)
                spec['parts'][bass_idx]['events'] = _flatten_bars(bl)
    if do_melody:
        if arpeggio and chord_syms:
            chord = _parse_chord_symbol(chord_syms[0], scale_names)
            toks = [('chord', i + 1) for i in range(len(chord))]
        elif run:
            seq = list(range(1, 8)) if '上行' in text else list(range(7, 0, -1))
            toks = [('deg', d) for d in seq]
        elif note_tokens:
            toks = note_tokens
        else:
            toks = [('deg', 1), ('deg', 2), ('deg', 3), ('deg', 4)]
        dur = _parse_duration_word(_first_duration_word(text)) or 'quarter'
        center = 71
        ml = _split_events_to_bars(
            spec['parts'][melody_idx].get('events') or [], bars, ts)
        for m in measures:
            ch = spec.get('harmony', {}).get(m) or ['C', 'E', 'G']
            ml[m] = _build_melody_events(toks, ch, scale_names, center, ts, bar_ql, dur)
        spec['parts'][melody_idx]['events'] = _flatten_bars(ml)

    score = spec_to_music21(spec)
    session.score_spec = spec
    session.revision += 1
    session._pending_edit = None
    if not do_harmony and not do_melody:
        return score, spec, (
            f'已选中小节 {[m + 1 for m in measures]}，但未能从提示词识别到和声/旋律改动；'
            '可试试「改成 Dm7 和弦」「和声用 ii V7 I」「旋律上行音阶」'
            '「用 C 和弦琶音」「旋律写成 C4 E4 G4」')
    tag = ''.join(['(和声)' if do_harmony else '', '(旋律)' if do_melody else ''])
    note = f'已（确定性）更新小节 {[m + 1 for m in measures]}{tag}'
    return score, spec, note


def _deterministic_edit_symbols(
    session: ComposeSession, user_text: str, selection: Dict[str, Any],
    m21_score=None,
) -> Tuple[stream.Score, Dict[str, Any], str]:
    """无 LLM 的选中符号重写：提示词 → 仅改选中的单个音符（音高/时值/八度/休止）。"""
    spec = copy.deepcopy(_ensure_score_spec(session, m21_score))
    symbols = list(selection.get('symbols') or [])
    if not symbols:
        raise ValueError('未选中符号')
    bars = int(spec.get('bars') or 8)
    ts = str(spec.get('time_signature') or '4/4')
    parts = spec.get('parts') or []
    text = user_text or ''
    scale_names = _scale_names(int(spec.get('key_sharps') or 0),
                               spec.get('mode') or 'major')

    dur_word = _first_duration_word(text)
    new_dur = _parse_duration_word(dur_word) if dur_word else None
    transpose = 0
    if '升高八度' in text or '高八度' in text:
        transpose = 12
    elif '降低八度' in text or '低八度' in text:
        transpose = -12
    elif '升高半音' in text:
        transpose = 1
    elif '降低半音' in text:
        transpose = -1

    rest_req = ('休止' in text) or bool(re.search(r'\brest\b', text, re.I))
    m_abs = _NOTE_NAME_RE.search(text)
    if rest_req:
        new_pitch = None  # 显式休止
    elif m_abs:
        new_pitch = m_abs.group(0)
    else:
        new_pitch = None

    changed = 0
    for s in symbols:
        si, mi, ni = (int(s.get('staff_idx', 0)), int(s.get('measure_idx', 0)),
                      int(s.get('note_idx', 0)))
        if not (0 <= si < len(parts) and 0 <= mi < bars):
            continue
        bl = _split_events_to_bars(parts[si].get('events') or [], bars, ts)
        if ni >= len(bl[mi]):
            continue
        ev = list(bl[mi][ni])
        before = list(ev)
        pitch = ev[0]
        if new_pitch is not None or rest_req:
            ev[0] = new_pitch
        elif transpose != 0 and pitch is not None:
            if isinstance(pitch, (list, tuple)):
                ev[0] = [_name_oct(_midi(x) + transpose, scale_names) for x in pitch]
            else:
                ev[0] = _name_oct(_midi(pitch) + transpose, scale_names)
        if new_dur is not None:
            ev[1] = new_dur
        if list(ev) == before:
            continue
        bl[mi][ni] = ev
        # 改时值会破坏「小节内时值之和 == 拍号容量」这一隐含约束 → 其后音符整体
        # 位移、小节线错位。改完立刻把该小节裁尾/补休止规整回容量。
        bl[mi] = _fit_bar(bl[mi], ts)
        parts[si]['events'] = _flatten_bars(bl)
        changed += 1

    score = spec_to_music21(spec)
    session.score_spec = spec
    session.revision += 1
    session._pending_edit = None
    if changed == 0:
        return score, spec, (
            '已选中符号，但未能从提示词识别到改动；可试试「改成 G4」「升高八度」'
            '「升高半音」「改成二分音符」「改成休止符」')
    note = f'已（确定性）更新 {changed} 个符号'
    return score, spec, note


def _compose_selection_measures(
    session: ComposeSession, user_text: str, selection: Dict[str, Any],
    m21_score=None,
) -> Tuple[stream.Score, Dict[str, Any], str]:
    spec = copy.deepcopy(_ensure_score_spec(session, m21_score))
    measures = sorted({int(m) for m in (selection.get('measures') or [])})
    if not measures:
        raise ValueError('未选中小节')
    bars = int(spec.get('bars') or 8)
    ts = str(spec.get('time_signature') or '4/4')
    measures = [m for m in measures if 0 <= m < bars]
    if not measures:
        raise ValueError('选中小节超出曲目范围')

    parts_payload = []
    for p in spec.get('parts') or []:
        bar_lists = _events_to_bars(p.get('events') or [], bars, ts)
        sel_bars = {str(m): bar_lists[m] for m in measures}
        neighbors = {}
        for m in measures:
            for n in (m - 1, m + 1):
                if 0 <= n < bars and n not in measures:
                    neighbors[str(n)] = bar_lists[n]
        parts_payload.append({
            'name': p.get('name'),
            'clef': p.get('clef'),
            'selected_bars': sel_bars,
            'neighbor_bars': neighbors,
        })

    payload = {
        'user_request': user_text,
        'selection': {'measures': measures},
        'time_signature': ts,
        'bars': bars,
        'parts': parts_payload,
        'plan_summary': (session.plan or {}).get('summary'),
    }
    skill_ids, _ = skill_lib.resolve_skills(
        user_text, selection, None,
        style_pref=session.prefs.get('style') or '',
        genre_id=session.prefs.get('genre_id') or '',
    )
    session.last_skill_ids = skill_ids
    raw = chat_completion([
        {'role': 'system', 'content': _prompt('selection_measure', skill_ids)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ])
    result = extract_json_object(raw)
    by_name = {str(p.get('name')): p for p in (result.get('parts') or [])}

    new_parts = []
    for p in spec.get('parts') or []:
        p2 = dict(p)
        bar_lists = _events_to_bars(p.get('events') or [], bars, ts)
        patch = by_name.get(str(p.get('name'))) or {}
        bar_events = patch.get('bar_events') or {}
        for m in measures:
            key = str(m)
            if key in bar_events:
                bar_lists[m] = _normalize_bar_events(bar_events[key], ts)
            else:
                # fallback: keep original
                bar_lists[m] = _normalize_bar_events(bar_lists[m], ts)
        p2['events'] = _bars_to_events(bar_lists)
        new_parts.append(p2)
    spec['parts'] = new_parts
    # 局部重写后逐小节已 normalize；此处仅记录校验结果供追溯
    issues = validate_score_spec(spec)
    session.last_validate_issues = issues
    score = spec_to_music21(spec)
    session.score_spec = spec
    session.revision += 1
    session._pending_edit = None
    try:
        session.save()
    except Exception:
        pass
    note = result.get('notes') or f'已更新小节 {[m + 1 for m in measures]}'
    note += f"\n[Skill: {skill_lib.format_skill_label(skill_ids)}]"
    return score, spec, note


def _compose_selection_symbols(
    session: ComposeSession, user_text: str, selection: Dict[str, Any],
    m21_score=None,
) -> Tuple[stream.Score, Dict[str, Any], str]:
    spec = copy.deepcopy(_ensure_score_spec(session, m21_score))
    symbols = list(selection.get('symbols') or [])
    if not symbols:
        raise ValueError('未选中符号')
    bars = int(spec.get('bars') or 8)
    ts = str(spec.get('time_signature') or '4/4')
    parts = list(spec.get('parts') or [])

    # Enrich symbols with current pitch/duration from spec
    enriched = []
    for s in symbols:
        si = int(s.get('staff_idx', 0))
        mi = int(s.get('measure_idx', 0))
        ni = int(s.get('note_idx', 0))
        if not (0 <= si < len(parts) and 0 <= mi < bars):
            continue
        bar_lists = _events_to_bars(parts[si].get('events') or [], bars, ts)
        ev = bar_lists[mi][ni] if ni < len(bar_lists[mi]) else [None, 'quarter']
        enriched.append({
            'staff_idx': si,
            'measure_idx': mi,
            'note_idx': ni,
            'part_name': parts[si].get('name'),
            'pitch': ev[0],
            'duration': ev[1],
            'bar_events': bar_lists[mi],
        })
    if not enriched:
        raise ValueError('选中符号无法映射到当前曲目')

    payload = {
        'user_request': user_text,
        'symbols': enriched,
        'time_signature': ts,
    }
    skill_ids, _ = skill_lib.resolve_skills(
        user_text, selection, None,
        style_pref=session.prefs.get('style') or '',
        genre_id=session.prefs.get('genre_id') or '',
    )
    session.last_skill_ids = skill_ids
    raw = chat_completion([
        {'role': 'system', 'content': _prompt('selection_symbol', skill_ids)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ])
    result = extract_json_object(raw)
    replacements = result.get('replacements') or []
    # index by (staff, measure, note)
    rep_map = {
        (int(r['staff_idx']), int(r['measure_idx']), int(r['note_idx'])): r.get('event')
        for r in replacements
        if isinstance(r, dict) and 'event' in r
    }

    new_parts = []
    for si, p in enumerate(parts):
        p2 = dict(p)
        bar_lists = _events_to_bars(p.get('events') or [], bars, ts)
        for s in enriched:
            if s['staff_idx'] != si:
                continue
            mi, ni = s['measure_idx'], s['note_idx']
            key = (si, mi, ni)
            if key in rep_map and ni < len(bar_lists[mi]):
                ev = rep_map[key]
                if isinstance(ev, (list, tuple)) and len(ev) >= 2:
                    pitch = None if ev[0] in (None, 'rest', 'Rest', '') else ev[0]
                    bar_lists[mi][ni] = [pitch, str(ev[1])]
            bar_lists[mi] = _normalize_bar_events(bar_lists[mi], ts)
        p2['events'] = _bars_to_events(bar_lists)
        new_parts.append(p2)
    spec['parts'] = new_parts
    score = spec_to_music21(spec)
    session.score_spec = spec
    session.revision += 1
    session._pending_edit = None
    try:
        session.save()
    except Exception:
        pass
    note = result.get('notes') or f'已更新 {len(enriched)} 个符号'
    note += f"\n[Skill: {skill_lib.format_skill_label(skill_ids)}]"
    return score, spec, note


def learn_score_current(session: ComposeSession, genre: str = '',
                        m21_score=None, family: str = '') -> str:
    """Append current score features into genre/packs/<family>/<id>/ and update meta.yaml."""
    from score_learning.aggregate import aggregate_features
    from score_learning.analyzer import analyze_m21
    from score_learning.corpus import load_features_dir
    from score_learning.publish import genre_pack_dir, publish_to_genre_pack

    if m21_score is None and session.score_spec:
        m21_score = spec_to_music21(session.score_spec)
    if m21_score is None:
        raise CommandOnly('当前没有乐谱可学习。请先创作或打开乐谱。')

    pack_id = (genre or session.prefs.get('style') or '').strip()
    pack_id = re.sub(r'[^\w\-]+', '_', pack_id, flags=re.U).strip('_')
    family_map = {
        'chorale': 'sacred', 'hymn': 'sacred',
        'waltz': 'dance', 'minuet': 'dance', 'march': 'dance', 'polka': 'dance',
        'mazurka': 'dance', 'tango': 'dance',
        'fugue': 'academic', 'scherzo': 'academic', 'sonata-allegro': 'academic',
        'nocturne': 'intimate', 'lullaby': 'intimate',
        'pirate-allegro': 'cinematic',
    }
    fam = (family or family_map.get(pack_id) or '').strip()
    if not pack_id or not fam:
        raise CommandOnly(
            '请指定风格：/learn-score chorale\n'
            '（需对应已有风格包 genre/packs/<family>/<id>/）'
        )
    pack_dir = genre_pack_dir(fam, pack_id)
    meta_path = os.path.join(pack_dir, 'meta.yaml')
    if not os.path.isfile(meta_path):
        raise CommandOnly(f'风格包不存在：genre/packs/{fam}/{pack_id}/')

    feat_dir = os.path.join(pack_dir, 'features')
    os.makedirs(feat_dir, exist_ok=True)

    title = (session.score_spec or {}).get('title') or 'score'
    safe = re.sub(r'[^\w\-]+', '_', str(title), flags=re.U).strip('_') or 'score'
    feat = analyze_m21(m21_score, source=f'session:{session.composition_id}', genre_hint=pack_id)
    out_path = os.path.join(feat_dir, f'{safe}_{session.composition_id}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(feat, f, ensure_ascii=False, indent=2)

    features = load_features_dir(feat_dir)
    stats = aggregate_features(features)
    pub = publish_to_genre_pack(fam, pack_id, stats, None)

    g = feat.get('global') or {}
    mel = feat.get('melody') or {}
    rh = feat.get('rhythm') or {}
    lines = [
        f'已更新风格 Skill：`genre/packs/{fam}/{pack_id}/`',
        f'曲名：{feat.get("metadata", {}).get("title")} ｜ 调：{g.get("key")} ｜ '
        f'拍号：{g.get("meter")} ｜ 小节：{g.get("bars")}',
        f'特征：features/{os.path.basename(out_path)} ｜ 语料累计 {stats.get("total_scores")} 首',
        f'旋律级进比：{mel.get("stepwise_ratio")} ｜ '
        f'第一拍起音占比：{rh.get("beat1_onset_share")}',
        f'meta.yaml 已合并 empirical（{len(pub)} 文件）',
        '',
        '高频规则：',
    ]
    for r in (stats.get('rules') or [])[:6]:
        lines.append(
            f"  · {r['rule']}  freq={r['frequency']:.0%}  "
            f"{r['confidence']}/{r['universality']}"
        )
    return '\n'.join(lines)


def _load_parse_score_module():
    import importlib.util
    path = os.path.join(
        skill_lib.SKILL_ROOT, 'skills', 'score-analysis', 'scripts', 'parse_score.py',
    )
    spec = importlib.util.spec_from_file_location('score_analysis_parse_score', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'无法加载 parse_score.py: {path}')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_score_facts(session: ComposeSession, m21_score=None) -> Dict[str, Any]:
    """Build analysis facts from music21 score or session score_spec."""
    parse = _load_parse_score_module()
    if m21_score is not None:
        return parse.analyze_m21(m21_score)
    if session.score_spec:
        try:
            return parse.analyze_score_spec(session.score_spec)
        except Exception:
            # fall through to music21 rebuild
            pass
        try:
            return parse.analyze_m21(spec_to_music21(session.score_spec))
        except Exception as e:
            raise RuntimeError(f'解析乐谱失败：{e}') from e
    raise RuntimeError('当前没有可分析的乐谱')


def _advice_title(facts: Dict[str, Any]) -> str:
    return str(
        facts.get('title')
        or (facts.get('metadata') or {}).get('title')
        or (facts.get('global') or {}).get('title')
        or '当前曲目'
    )


def _normalize_advice_text(raw: str) -> str:
    """Accept markdown, or JSON wrappers like {\"advice\":\"...\"}."""
    text = (raw or '').strip()
    if not text:
        return ''
    if text.startswith('```'):
        text = re.sub(r'^```(?:json|markdown|md)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text).strip()
    if text.startswith('{'):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for k in ('advice', 'suggestions', 'markdown', 'text', 'content', 'report'):
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                # nested list of items → markdown
                items = obj.get('items') or obj.get('findings')
                if isinstance(items, list) and items:
                    lines = []
                    for it in items:
                        if isinstance(it, str):
                            lines.append(f'- {it}')
                        elif isinstance(it, dict):
                            pri = it.get('priority') or it.get('level') or ''
                            issue = it.get('issue') or it.get('problem') or it.get('问题') or ''
                            loc = it.get('location') or it.get('位置') or ''
                            fix = it.get('fix') or it.get('修改方向') or it.get('suggestion') or ''
                            head = f"**问题**：{issue}" if issue else str(it)
                            if pri:
                                head = f'{pri} {head}'
                            lines.append(f'- {head}')
                            if loc:
                                lines.append(f'  - **位置**：{loc}')
                            if fix:
                                lines.append(f'  - **修改方向**：{fix}')
                    if lines:
                        return '\n'.join(lines)
        except Exception:
            pass
    return text


def _format_advice_fallback(facts: Dict[str, Any], reason: str = '') -> str:
    """Always produce actionable local advice from measured facts."""
    title = _advice_title(facts)
    lines = [f'《{title}》修改建议' + (f'（{reason}）' if reason else ''), '']
    findings = list(facts.get('heuristic_findings') or [])
    parts = facts.get('parts') or []
    bars = int(facts.get('bars') or (facts.get('global') or {}).get('bars') or 0)

    if not findings and parts:
        for p in parts:
            if not isinstance(p, dict):
                continue
            name = p.get('name') or 'Part'
            lr = float(p.get('long_note_ratio') or 0)
            if lr > 0.8 and int(p.get('note_events') or 0) >= 4:
                findings.append({
                    'priority': 'high',
                    'issue': f'{name} 长音占比 {lr:.0%}，声部角色偏单一',
                    'location': f'{name} 全曲',
                    'fix': '在中段给该声部旋律或对位，勿全程长音铺底',
                })
            for st in p.get('density_streaks_ge4') or []:
                findings.append({
                    'priority': 'medium',
                    'issue': f'{name} 第{st.get("start")}–{st.get("end")}小节节奏密度过均一',
                    'location': f'{name} 第{st.get("start")}–{st.get("end")}小节',
                    'fix': '引入附点、切分或三连音打破机械重复',
                })
        if bars >= 8 and not facts.get('key_signature_changed'):
            mid = max(1, bars // 2 - 2)
            findings.append({
                'priority': 'high',
                'issue': '全曲调性对比不足（未检测到调号变化）',
                'location': f'建议第{mid}–{min(bars, mid + 6)}小节',
                'fix': '短暂离调到属调或关系小调后回归',
            })

    if findings:
        lines.append('### 检测结果')
        lines.append('')
        for fnd in findings[:10]:
            pri = fnd.get('priority') or 'medium'
            badge = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(pri, '🟡')
            lines.append(f"{badge} **问题**：{fnd.get('issue')}")
            if fnd.get('location'):
                lines.append(f"- **位置**：{fnd['location']}")
            if fnd.get('fix'):
                lines.append(f"- **修改方向**：{fnd['fix']}")
            lines.append('')
        tops = findings[:3]
        lines.append('### 最优先改的三件事')
        for i, fnd in enumerate(tops, 1):
            lines.append(f"{i}. {fnd.get('issue')}")
    else:
        # still give structure overview so user never sees empty
        g = facts.get('global') or {}
        lines += [
            '本地未触发强规则，仍建议从以下维度审视：',
            f"- 规模：{bars or g.get('bars') or '?'} 小节，拍号 {g.get('meter') or facts.get('meter') or '?'}",
            f"- 声部数：{len(parts)}",
            '- 声部是否分工（旋律 / 和声 / 低音）',
            '- 每 4 小节是否有节奏变化',
            '- 中段是否有调性或织体对比',
            '- 句尾终止与力度层次',
        ]
    return '\n'.join(lines).strip()


def _run_advice(session: ComposeSession, user_text: str,
                skill_ids: List[str],
                m21_score=None) -> str:
    """LLM score critique — returns markdown text; does not mutate the score."""
    if m21_score is None and not session.score_spec:
        raise CommandOnly('当前没有乐谱可分析。请先创作、/demo，或打开已有乐谱。')
    try:
        facts = _collect_score_facts(session, m21_score=m21_score)
    except Exception as e:
        raise CommandOnly(f'解析乐谱失败：{e}') from e

    checklist_path = os.path.join(
        skill_lib.SKILL_ROOT, 'skills', 'score-analysis', 'references', 'checklist.md',
    )
    checklist = ''
    if os.path.isfile(checklist_path):
        with open(checklist_path, encoding='utf-8') as f:
            checklist = f.read().strip()
    system = skill_lib.build_system_prompt('advice', skill_ids)
    parse = _load_parse_score_module()
    user_payload = {
        'user_focus': user_text or '/advice',
        'score_facts': facts,
        'checklist': checklist,
    }

    text = ''
    llm_err = ''
    try:
        # Advice must be markdown — do NOT force json_object (that caused empty replies)
        raw = chat_completion([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content':
             '根据以下乐谱事实与检测清单，输出中文 Markdown 修改建议（不要改谱，不要只返回 JSON）。\n\n'
             + parse.facts_to_prompt_block(user_payload)},
        ], timeout=180, json_mode=False)
        text = _normalize_advice_text(raw)
    except Exception as e:
        llm_err = str(e)

    if not text:
        reason = 'LLM 无有效正文，已用本地检测' if not llm_err else f'LLM 失败：{llm_err[:120]}'
        text = _format_advice_fallback(facts, reason=reason)

    label = skill_lib.format_skill_label(skill_ids)
    text += f"\n\n[Skill: {label}]\n（仅建议，未修改乐谱）\n回复「可以」「执行」或输入 /apply 将按上述建议改谱。"
    session.last_skill_ids = skill_ids
    session.last_advice = text
    session._pending_edit = None
    return text


def compose_pipeline(session: ComposeSession, user_text: str,
                     edit_intent: Optional[str] = None,
                     selection: Optional[Dict[str, Any]] = None,
                     m21_score=None,
                     advice_edits: Optional[str] = None,
                     ) -> Tuple[stream.Score, Dict[str, Any], str]:
    """意图计划 → 作曲 → 校验 →（可选）修复。有选区时只改选中小节/符号。"""
    # 流程痕迹（`session.compose_trace`）此前**只**由 `compose_from_text` 里的
    # `_init_compose_trace` 创建（见其定义）。直接调用本函数的调用方（脚本、
    # 测试、批处理）拿到的是没有该属性的 session，于是本函数下面所有
    # `_tr = getattr(session, 'compose_trace', None)` 全部落空：kernel_calls /
    # llm_calls 静默丢失，落盘的 `*_calls.json` 是空数组，程序员版报告缺
    # 第 4 节「作曲过程透明化」——事后无法补回（只能重跑）。
    # 这里兜一次：不存在才建，已存在不重置，保证同一轮内多次调用累积同一条痕迹。
    if getattr(session, 'compose_trace', None) is None:
        _init_compose_trace(session, user_text)

    # Structured full-score critique must ignore leftover staff selection
    if skill_lib.is_full_score_revision_request(user_text or ''):
        selection = None
        if not edit_intent:
            edit_intent = 'rewrite'

    edit_intent = infer_edit_intent(user_text, edit_intent or session._pending_edit)

    # Advice never mutates the score — ignore selection
    if edit_intent == 'advice':
        selection = None

    if selection and (selection.get('symbols') or selection.get('measures')):
        # 有 LLM Key → 走 LLM 选区重写（更灵活）；无 Key（本地/测试）→ 确定性重写
        llm_on = bool((load_llm_config() or {}).get('api_key'))
        if selection.get('symbols'):
            fn = _compose_selection_symbols if llm_on else _deterministic_edit_symbols
        else:
            fn = _compose_selection_measures if llm_on else _deterministic_edit_measures
        return fn(session, user_text, selection, m21_score=m21_score)

    asked = parse_bars_from_text(user_text)
    # Only apply when it is an explicit length command — never from「第N小节」
    if asked:
        session.prefs['bars'] = asked

    llm_on = bool((load_llm_config() or {}).get('api_key'))
    # 「根据当前乐谱写 1:1」——仅在已有谱时走对位（不属于 /compose-grammar）
    _from_cur = _wants_species_from_current(user_text)
    _has_score = bool(session.score_spec) or m21_score is not None
    if (not advice_edits and not edit_intent and not selection
            and _from_cur and _has_score):
        species_out = _try_compose_species_from_current(
            session, user_text, plan=None, m21_score=m21_score)
        if species_out is not None:
            return species_out

    # 新曲：只走 /compose-grammar（关键词 → LLM 路由 → 帮助）。已取消 Planner 写新曲。
    _want_new = (session.score_spec is None) or _is_new_piece_request(user_text)
    if not advice_edits and not edit_intent and not selection and _want_new:
        lg = _detect_learned_grammar(user_text)
        if lg is not None:
            lg_out = _try_compose_learned_grammar(session, user_text, lg)
            if lg_out is not None:
                return lg_out
        if llm_on:
            lg2 = _llm_route_compose_grammar(session, user_text)
            if lg2 is not None:
                lg_out = _try_compose_learned_grammar(session, user_text, lg2)
                if lg_out is not None:
                    return lg_out
        if session.score_spec is None and not llm_on:
            species_out = _try_compose_two_part_species(session, user_text)
            if species_out is not None:
                return species_out
        raise CommandOnly(_compose_grammar_only_help(user_text))

    # 已有乐谱上的修订 / 选区改写才继续走下方路径
    if session.score_spec is None and not advice_edits and not edit_intent and not selection:
        raise CommandOnly(_compose_grammar_only_help(user_text))

    # 简单古典提示词 → 专业分段 brief（仅修订路径可能用到；新曲已在上面截断）
    try:
        import brief_expand as _be
        expanded_text, expand_meta = _be.maybe_expand_user_text(user_text)
        if expand_meta is not None:
            session.last_brief_expand = expand_meta
            user_text = expanded_text
            if expand_meta.get('pack_hint') == 'symphony':
                session.prefs['style'] = session.prefs.get('style') or '交响曲'
                session.prefs['genre_id'] = (
                    session.prefs.get('genre_id')
                    or 'genre:classical/symphony')
            _tr = getattr(session, 'compose_trace', None)
            if _tr is not None:
                _tr['brief_expand'] = {
                    'template': expand_meta.get('template'),
                    'pack_hint': expand_meta.get('pack_hint'),
                    'original': expand_meta.get('original'),
                }
    except Exception:
        pass

    skill_ids, pipeline = skill_lib.resolve_skills(
        user_text, selection, edit_intent,
        slash_cmd=(parse_command(user_text) or (None, None))[0],
        style_pref=session.prefs.get('style') or '',
        genre_id=session.prefs.get('genre_id') or '',
        llm_available=bool((load_llm_config() or {}).get('api_key')),
    )
    session.last_skill_ids = skill_ids
    _tr = getattr(session, 'compose_trace', None)
    if _tr is not None:
        _tr['skill_ids'] = skill_ids
        _tr['pipeline'] = pipeline

    if pipeline == 'advice' or edit_intent == 'advice':
        advice = _run_advice(session, user_text, skill_ids, m21_score=m21_score)
        raise CommandOnly(advice)

    plan = _run_planner(session, user_text, edit_intent, skill_ids=skill_ids,
                        advice_edits=advice_edits)
    session.plan = plan
    # 原始用户需求直达 composer：planner 只产出标签化计划（form=["A","B"]），
    # 详细的分段结构/织体/情绪要求若不透传会被完全丢弃。
    # 原始用户需求 + /style 自然语言风格描述，直达 translator/planner：
    # 详细的分段结构/织体/情绪/风格要求若不透传会被 LLM 完全丢弃。
    style_pref = session.prefs.get('style') or ''
    if user_text or style_pref:
        plan.setdefault('user_brief', ' '.join(x for x in (user_text, style_pref) if x))
    # LLM plan.genre_id / /style 绑定 → 已学包 skill_ids + foundation 小节/编制
    skill_ids = _bind_learned_genre(session, plan, skill_ids)
    session.last_skill_ids = skill_ids
    _tr = getattr(session, 'compose_trace', None)
    if _tr is not None:
        _tr['skill_ids'] = skill_ids
        if plan.get('genre_id'):
            _tr['bound_genre'] = plan.get('genre_id')
    # 情绪/速度词法解析：确定性地把「欢快 / 快节奏」映射为 tempo / mode / mood，
    # 覆盖基调默认值（修复「欢快却出慢忧伤」）。仅新创作时生效。
    if (not advice_edits and not plan.get('revision_edits')
            and session.score_spec is None and user_text):
        _apply_mood_tempo_constraints(plan, user_text, session)
        species_fixes = _apply_two_part_species_constraints(plan, user_text, session)
        _tr = getattr(session, 'compose_trace', None)
        if _tr is not None:
            _tr['emotion'] = plan.get('_emotion_hint')
            _tr['emotion_applied'] = bool(plan.get('_emotion_hint'))
            if species_fixes:
                _tr['constraint_fixes'] = list(
                    _tr.get('constraint_fixes') or []) + list(species_fixes)

    # Planner 判定第一类 1:1 对位 → 确定性生成（LLM 只负责翻译意图，不写音符/不加赋格）
    # 含「根据当前乐谱」：在已有谱上抽定旋律写对位（即使用户已打开乐谱）
    _from_cur_req = _wants_species_from_current(user_text, plan)
    if (not advice_edits and not plan.get('revision_edits')
            and not selection
            and (_plan_indicates_species(plan) or _from_cur_req)
            and not _fugue_requested(plan, session.prefs.get('fugue') or 'auto')):
        from_cur = _from_cur_req
        has_score = bool(session.score_spec) or m21_score is not None
        if from_cur and has_score:
            gid = str(plan.get('genre_id') or '')
            if 'fugue' in gid.lower():
                plan['genre_id'] = ''
                skill_ids = [s for s in skill_ids if 'fugue' not in str(s).lower()]
                session.last_skill_ids = skill_ids
            plan['species'] = True
            plan['species_from_current'] = True
            plan['want_fugue'] = False
            species_out = _try_compose_species_from_current(
                session, user_text, plan=plan, m21_score=m21_score)
            if species_out is not None:
                return species_out
        elif session.score_spec is None and _plan_indicates_species(plan):
            # 误绑赋格包时摘掉
            gid = str(plan.get('genre_id') or '')
            if 'fugue' in gid.lower():
                plan['genre_id'] = ''
                skill_ids = [s for s in skill_ids if 'fugue' not in str(s).lower()]
                session.last_skill_ids = skill_ids
            plan['species'] = True
            plan['want_fugue'] = False
            species_out = _try_compose_two_part_species(session, user_text, plan=plan)
            if species_out is not None:
                return species_out
        elif from_cur and not has_score:
            # 用户说根据当前乐谱，但没有打开谱 → 友好提示
            raise CommandOnly(
                '当前没有打开的乐谱。请先创作或打开一首曲子，再发送'
                '「根据当前打开的乐谱写一个1比1对位」。'
                '若要从零写对位，可直接说「写一首2声部一比一对位的曲子」。'
            )

    # Planner 识别出已学风格的短曲（小步舞曲 / 玛祖卡 / 吉格 …）→ 确定性语法作曲
    lg = _plan_learned_grammar(plan)
    if (lg is not None and not advice_edits and not plan.get('revision_edits')
            and not selection and session.score_spec is None
            and not _plan_indicates_species(plan)):
        lg_out = _try_compose_learned_grammar(session, user_text, lg, plan=plan)
        if lg_out is not None:
            return lg_out

    # advice-apply: guarantee the concrete edit items reach the composer even
    # if the planner LLM drops them (plan schema has no field for them).
    if advice_edits:
        plan['revision_edits'] = advice_edits
    # keep composer edit_intent in sync with resolved plan
    edit_intent = plan.get('edit_intent') or edit_intent
    # 路由：新创作走「LLM 翻译器 + 确定性内核」（LLM 不写音符）；
    # 只有基于既有乐谱的修订（advice 应用/扩展/改写等）才走旧 composer 路径
    kernel_spec = None
    if (not advice_edits and not plan.get('revision_edits')
            and session.score_spec is None):
        try:
            kernel_spec = _run_translator(session, plan, skill_ids)
        except Exception as _e:
            print(f'[compose] kernel 翻译路径失败，回退 composer：{_e}',
                  file=sys.stderr)
            kernel_spec = None
    if kernel_spec is not None:
        spec = kernel_spec
        _tr = getattr(session, 'compose_trace', None)
        if _tr is not None:
            _tr['pipeline'] = 'translator（LLM 翻译 + 确定性内核）'
            _tr['constraint_fixes'] = list(
                (session.last_translate or {}).get('genre_fixes') or [])
    else:
        spec = _run_composer(session, plan, edit_intent, skill_ids=skill_ids)
        # 旧 composer 路径：LLM 直接写谱，无脚本调用序列
        session.last_translate = _build_think_trace(
            plan, None, deterministic=False,
            calls_text='（旧 composer 路径：LLM 直接输出乐谱 JSON，'
                      '不经过脚本调用序列）')
        _tr = getattr(session, 'compose_trace', None)
        if _tr is not None:
            _tr['pipeline'] = 'composer（LLM 直出乐谱，无脚本序列）'
    # 修复 LLM 重写 spec 时可能丢掉力度字段：备份，修完补回
    dyn_backup = [p.get('dynamics') for p in (spec.get('parts') or [])]
    tempo_backup = spec.get('tempo_marks')
    meter_backup = spec.get('meter_changes')
    # Force bars from plan after composer (some models ignore and write short)
    target_bars = int(plan.get('bars') or session.prefs.get('bars') or 8)
    spec['bars'] = target_bars
    if not (spec.get('parts') or []):
        # 模型偶发返回空 parts / 包了一层外壳：重试一次
        spec = _run_composer(session, plan, edit_intent, skill_ids=skill_ids)
        spec['bars'] = target_bars
    if not (spec.get('parts') or []):
        raise RuntimeError('作曲结果没有声部（模型连续两次返回不完整），请重发一次消息再试')
    # 钢琴众赞歌等：LLM 常误出四行 SATB → 收成大谱表两行
    piano_note = None
    if _wants_piano_grand(plan, skill_ids, user_text):
        piano_note = _collapse_satb_to_piano_grand(spec)
        if piano_note:
            # 同步 plan，避免后续报告仍写 4 声部
            plan['instrumentation'] = ['piano', 'piano']
    issues = validate_score_spec(spec)
    critical = [i for i in issues if i['level'] in ('CRITICAL', 'MAJOR')]
    if critical:
        _tr = getattr(session, 'compose_trace', None)
        if _tr is not None:
            _tr['repair'] = {'triggered': True,
                             'issues': [i.get('message') for i in critical[:8]]}
        try:
            spec = _run_repair(spec, critical, skill_ids=['repair-timing'])
            _restore_part_dynamics(spec, dyn_backup)
            _restore_score_marks(spec, tempo_backup, meter_backup)
            issues = validate_score_spec(spec)
        except Exception:
            pass
    spec['bars'] = target_bars
    score = spec_to_music21(spec)
    vl = voice_leading_issues_from_m21(score)
    if vl:
        try:
            spec = _run_repair(spec, vl, skill_ids=['repair-timing'])
            _restore_part_dynamics(spec, dyn_backup)
            _restore_score_marks(spec, tempo_backup, meter_backup)
            spec['bars'] = target_bars
            issues = validate_score_spec(spec) + voice_leading_issues_from_m21(
                spec_to_music21(spec),
            )
            score = spec_to_music21(spec)
        except Exception:
            issues = list(issues) + vl
    # 兜底（过渡生硬）：一小节内速度标记来回摆动 → 聚簇取末位
    _compact_tempo_marks(spec)
    # 兜底（过渡生硬）：声部缺力度记号 → 补抄/按速度标记推断阶梯
    dyn_note = _ensure_dynamics(spec)
    # 兜底：调号与实际音符严重不符 → 按内容重推
    key_note = _fix_key_signature(spec)
    # 兜底：声部时值溢出/不足 → 逐小节截断 + 尾部重复材料补齐
    len_fixes = _fix_part_lengths(spec)
    post_changed = bool(dyn_note or key_note or len_fixes)
    # 兜底：修复 LLM 仍未解决「末段休止塌陷」时，本地用该声部前段材料补齐
    fixed_parts = _fill_dead_tail(spec)
    if fixed_parts:
        issues = [i for i in issues if '休止' not in i.get('message', '')]
        post_changed = True
    # 兜底（过渡生硬）：安静段全声部长音合并（伴奏不轰鸣）
    calm_notes = _calm_quiet_sections(spec)
    # 兜底：LLM 织体无段落对比时，本地确定性强制
    # （安静段长音合并 + 高潮八度加厚，小节时值守恒）
    tex_changes = _enforce_texture_contrast(spec)
    if post_changed or calm_notes or tex_changes:
        issues = [i for i in issues if '织体' not in i.get('message', '')]
        score = spec_to_music21(spec)
    session.score_spec = spec
    session.revision += 1
    session._pending_edit = None
    try:
        session.save()
    except Exception:
        pass
    summary = plan.get('summary') or spec.get('notes') or f"已生成《{spec.get('title')}》"
    if fixed_parts:
        summary += f"\n（已本地补齐 {', '.join(fixed_parts)} 末段的休止塌陷：重复其前段材料）"
    if dyn_note:
        summary += f"\n（{dyn_note}）"
    if key_note:
        summary += f"\n（{key_note}）"
    if len_fixes:
        summary += f"\n（已本地修正 {', '.join(len_fixes)} 的时值溢出/不足）"
    if piano_note:
        summary += f"\n（{piano_note}）"
    if calm_notes:
        summary += f"\n（已本地平缓安静段织体：{'；'.join(calm_notes)}）"
    if tex_changes:
        summary += f"\n（已本地强化段落织体对比：{'；'.join(tex_changes)}）"
    summary += f"\n[Skill: {skill_lib.format_skill_label(skill_ids)}]"
    # 实际小节数：数渲染谱里的真实 measure，而不是盲信 plan 目标值
    # （此前曾出现 plan.bars=8 但谱子只生成 6 小节、报告却写 8 的错位）
    try:
        actual_measures = max(
            (len(p.getElementsByClass('Measure')) for p in score.parts),
            default=0,
        )
    except Exception:
        actual_measures = target_bars
    if actual_measures != target_bars:
        summary += f"\n实际小节数：{actual_measures}（目标 {target_bars}）"
    else:
        summary += f"\n实际小节数：{actual_measures}"
    remain = [i for i in issues if i['level'] == 'CRITICAL']
    vl_left = [i for i in issues if i['level'] == 'MAJOR' and '平行' in i.get('message', '')]
    if remain:
        summary += f"（已本地修正时值；原校验提示 {len(remain)} 条）"
    elif vl_left:
        summary += f"（声部进行仍有 {len(vl_left)} 处提示，可用 /advice）"
    else:
        summary += f"（规格：{plan.get('genre', '')} / {plan.get('form', [])}）"
    # 透明化：记录最终结果（谱面实际参数）
    _tr = getattr(session, 'compose_trace', None)
    if _tr is not None:
        _tr['result'] = {
            'title': spec.get('title'),
            'time_signature': spec.get('time_signature'),
            'tempo': spec.get('tempo'),
            'mode': spec.get('mode'),
            'key_sharps': spec.get('key_sharps'),
            'bars_target': target_bars,
            'bars_actual': actual_measures,
            'voices': spec.get('voices') or len(spec.get('parts') or []),
            'parts': [p.get('name') for p in (spec.get('parts') or [])],
            'has_fugue': bool(spec.get('fugue')),
            'composer': spec.get('composer'),
        }
    return score, spec, summary


# ---------------------------------------------------------------------------
# 情绪 / 节奏词法解析：把「欢快 / 快节奏」等词确定性地映射为 tempo / mode / mood
# ---------------------------------------------------------------------------
_MOOD_FAST = ['欢快', '快乐', '喜悦', '轻快', '活泼', '愉快', '高兴', '明朗',
              '喜庆', '欢腾', '雀跃', '明快', '欢畅', '开心', '愉悦']
_MOOD_SLOW = ['忧伤', '悲伤', '忧郁', '哀伤', '愁', '伤感', '凄凉', '沉重',
              '黯淡', '凄美', '落寞', '压抑', '阴郁']
_MOOD_HEROIC = ['激昂', '豪迈', '雄壮', '磅礴', '壮丽', '振奋', '澎湃',
                '气势', '英姿', '恢宏']
_MOOD_CALM = ['宁静', '安详', '平静', '静谧', '恬静', '祥和', '舒缓', '悠扬',
              '闲适']
_MOOD_TENSE = ['紧张', '不安', '焦虑', '悬疑', '诡异', '阴暗', '急切', '惊悚']
_TEMPO_FAST = ['快节奏', '快板', '快歌', '急板', '更快', '快一些', '欢快',
               '轻快', '活泼', '明快']
_TEMPO_SLOW = ['慢节奏', '慢板', '慢歌', '更慢', '慢一些', '舒缓', '悠长',
               '徐缓', '悠缓', '慢悠悠']


def _detect_mood_tempo(text: str) -> Dict[str, Any]:
    """从自然语言里词法解析情绪与快慢，返回参数化建议（不依赖 LLM）。

    优先级（决定 bpm）：fast > slow > heroic > tense > calm。
    返回 {} 表示未命中任何情绪/速度词。
    """
    if not text:
        return {}
    groups = {
        'fast': _MOOD_FAST + _TEMPO_FAST,
        'slow': _MOOD_SLOW + _TEMPO_SLOW,
        'heroic': _MOOD_HEROIC,
        'calm': _MOOD_CALM,
        'tense': _MOOD_TENSE,
    }
    matched: Dict[str, List[str]] = {}
    for g, words in groups.items():
        hits = [w for w in words if w in text]
        if hits:
            matched[g] = hits
    if not matched:
        return {}
    # 速度显式词（快板/慢板等）优先于情绪词决定快/慢
    explicit_fast = any(w in text for w in _TEMPO_FAST)
    explicit_slow = any(w in text for w in _TEMPO_SLOW)
    if explicit_fast and not explicit_slow:
        order = ['fast', 'heroic', 'tense', 'calm', 'slow']
    elif explicit_slow and not explicit_fast:
        order = ['slow', 'calm', 'tense', 'heroic', 'fast']
    else:
        order = ['fast', 'slow', 'heroic', 'tense', 'calm']
    group = next((g for g in order if g in matched), 'fast')

    table = {
        'fast':   dict(bpm=132, tempo_text='Allegro',  mode_bias='major',
                      mood_tags=['cheerful', 'bright'], rhythm='flowing',
                      dynamics='f',  contour='arch'),
        'slow':   dict(bpm=64,  tempo_text='Adagio',   mode_bias='minor',
                      mood_tags=['sad', 'melancholic'], rhythm='sparse',
                      dynamics='p',  contour='descending'),
        'heroic': dict(bpm=144, tempo_text='Allegro',  mode_bias='major',
                      mood_tags=['heroic', 'majestic'], rhythm='driving',
                      dynamics='ff', contour='ascending'),
        'calm':   dict(bpm=76,  tempo_text='Andante',  mode_bias=None,
                      mood_tags=['calm', 'peaceful'], rhythm='sustained',
                      dynamics='p',  contour='descending'),
        'tense':  dict(bpm=120, tempo_text='Allegro',  mode_bias='minor',
                      mood_tags=['tense', 'anxious'], rhythm='fragmented',
                      dynamics='f',  contour='wave'),
    }
    info = dict(table[group])
    info['group'] = group
    info['matched'] = sorted({w for hs in matched.values() for w in hs})
    return info


def _apply_mood_tempo_constraints(plan: Dict[str, Any], user_text: str,
                                 session: Any) -> Dict[str, Any]:
    """把词法解析出的情绪/速度写入 plan（覆盖基调默认值）。

    - 速度只在用户没用 /tempo 显式指定时才覆盖（显式命令优先）。
    - 调式（大/小调）按情绪偏向写入。
    - 情绪标签并入 plan.mood，并把结构化建议塞进 plan._emotion_hint 供翻译器用。
    """
    det = _detect_mood_tempo(user_text)
    if not det:
        # 仅「大调 / 小调」：模式偏向，不涉及速度（不写 emotion_hint，UI 不展示情绪块）
        if '小调' in user_text and '大调' not in user_text:
            plan['mode'] = 'minor'
        elif '大调' in user_text:
            plan['mode'] = 'major'
        return plan
    # 速度：未用 /tempo 显式锁定时才覆盖（默认 prefs['tempo']=120 不算锁定）
    if not ((session.prefs or {}).get('_explicit') or {}).get('tempo'):
        plan['tempo'] = det['bpm']
        plan['tempo_text'] = det['tempo_text']
    # 调式偏向
    if det.get('mode_bias'):
        plan['mode'] = det['mode_bias']
    # 情绪标签
    moods = list(plan.get('mood') or [])
    if isinstance(moods, str):
        moods = [moods]
    for t in det['mood_tags']:
        if t not in moods:
            moods.append(t)
    plan['mood'] = moods
    plan['_emotion_hint'] = {
        'group': det['group'], 'matched': det['matched'],
        'bpm': det['bpm'], 'tempo_text': det['tempo_text'],
        'mode_bias': det.get('mode_bias'), 'rhythm': det['rhythm'],
        'dynamics': det['dynamics'], 'contour': det['contour'],
    }
    return plan


def _apply_two_part_species_constraints(plan: Dict[str, Any], user_text: str,
                                        session: Any) -> List[str]:
    """二声部第一类对位：以 Planner 字段为准；无 LLM 时才用 keyword 兜底。"""
    fixes: List[str] = []
    hit = None
    if _plan_indicates_species(plan):
        hit = plan.get('_species_hint') or {
            'kind': 'two_part_species', 'from': 'planner',
        }
    else:
        llm_on = bool((load_llm_config() or {}).get('api_key'))
        if not llm_on:
            try:
                from music_score.two_part_species import detect_two_part_species
            except Exception:
                return fixes
            blob = ' '.join(x for x in (
                user_text, plan.get('user_brief'), plan.get('summary')) if x)
            hit = detect_two_part_species(blob)
    if not hit:
        return fixes
    plan['species'] = True
    plan['want_fugue'] = False
    plan['_species_hint'] = hit
    plan['instrumentation'] = ['piano', 'piano']
    plan['texture'] = plan.get('texture') or 'first-species'
    plan['genre'] = plan.get('genre') or '第一类二声部对位'
    if 'fugue' in str(plan.get('genre_id') or '').lower():
        plan['genre_id'] = ''
        fixes.append('已清除误绑 genre_id=fugue（1:1 对位不是赋格）')
    if str(plan.get('form') or '').lower() in ('fugue', 'fugal'):
        plan['form'] = 'species'
        fixes.append('已清除误标 form=fugue（1:1 对位不是赋格）')
    if not plan.get('title'):
        plan['title'] = '二声部对位'
    prefs = getattr(session, 'prefs', None) or {}
    if not (prefs.get('_explicit') or {}).get('voices'):
        prefs['voices'] = 2
    if not (prefs.get('_explicit') or {}).get('fugue'):
        prefs['fugue'] = 'off'
    fixes.append('二声部对位：编制强制钢琴大谱表 RH/LH；赋格关闭（Planner）')
    fixes.append('约束：1 小节 1 音 · 中间仅 3/6 度 · 禁纯四 · '
                 '结尾反向八度 · 最高音错开 · 低音级进为主')
    return fixes


def _try_compose_species_from_current(
    session: ComposeSession, user_text: str,
    plan: Optional[Dict[str, Any]] = None,
    m21_score=None,
) -> Optional[Tuple[Any, Dict[str, Any], str]]:
    """根据当前打开的乐谱抽定旋律，写第一类 1:1 对位。"""
    try:
        from music_score.two_part_species import (
            wants_species_from_current, detect_two_part_species,
            extract_cantus_from_spec, extract_cantus_from_m21,
            infer_cf_role_from_text, build_two_part_species_from_cantus,
            species_score_to_spec,
        )
        from music_score.exporter import model_to_m21
    except Exception:
        return None

    if plan is not None and not (
            _plan_indicates_species(plan)
            or plan.get('species_from_current')
            or wants_species_from_current(user_text)):
        return None
    if plan is None and not (
            wants_species_from_current(user_text)
            or detect_two_part_species(user_text)):
        return None

    prefs = getattr(session, 'prefs', None) or {}
    if not (prefs.get('_explicit') or {}).get('fugue'):
        prefs['fugue'] = 'off'

    cf = None
    cf_role = 'upper'
    meta_src: Dict[str, Any] = {}
    err = None
    # 优先 score_spec（与会话一致），否则 m21
    if session.score_spec:
        try:
            cf, cf_role, meta_src = extract_cantus_from_spec(session.score_spec)
        except Exception as e:
            err = e
    if cf is None and m21_score is not None:
        try:
            cf, cf_role, meta_src = extract_cantus_from_m21(m21_score)
            err = None
        except Exception as e:
            err = e
    if cf is None:
        # 无可用乐谱
        return None

    cf_role = infer_cf_role_from_text(user_text, default=cf_role)
    src = plan or {}
    tempo = int(src.get('tempo') or meta_src.get('tempo')
                or prefs.get('tempo') or 88)
    meter = str(src.get('meter') or meta_src.get('meter')
                or prefs.get('meter') or '4/4')
    sharps = int(src.get('key_sharps') if src.get('key_sharps') is not None
                 else (meta_src.get('key_sharps') if meta_src.get('key_sharps') is not None
                       else (prefs.get('key_sharps') or 0)))
    mode = str(src.get('mode') or meta_src.get('mode')
               or prefs.get('mode') or 'major')
    title = str(src.get('title') or meta_src.get('title')
                or prefs.get('title') or '二声部对位（基于当前乐谱）')
    if title and '对位' not in title:
        title = f'{title} · 1:1对位'
    seed = int(prefs.get('seed') or 0)
    if seed <= 0:
        mix = time.time_ns() ^ ((session.revision + 1) * 0x9E3779B9)
        mix ^= (hash(str(session.composition_id or '')) & 0xFFFFFFFF)
        seed = abs(int(mix)) % (2**31 - 1) or int(time.time()) % 100000

    try:
        model, report, meta = build_two_part_species_from_cantus(
            cf, cf_role=cf_role, title=title, tempo=tempo, meter=meter,
            sharps=sharps, mode=mode, seed=seed)
    except Exception as e:
        # 让上层走普通路径或报错；此处返回带说明的 CommandOnly 更清晰
        raise CommandOnly(
            f'无法根据当前乐谱生成 1:1 对位：{e}\n'
            '请确认乐谱至少有 4 小节可听音符；也可先用「写一首2声部一比一对位」从零生成。'
        ) from e

    spec = species_score_to_spec(model, meta)
    spec['fugue'] = False
    spec['from_current'] = True
    spec['fixed_cf'] = meta.get('fixed_cf')
    bars = int(spec.get('bars') or len(cf))
    m21 = model_to_m21(model)
    out_plan = {
        'title': spec.get('title'),
        'genre': '第一类二声部对位（基于当前乐谱）',
        'genre_id': '',
        'mood': list(src.get('mood') or ['清晰', '平稳']),
        'tempo': tempo,
        'meter': meter,
        'key_sharps': sharps,
        'mode': mode,
        'instrumentation': ['piano', 'piano'],
        'form': 'species',
        'bars': bars,
        'texture': 'first-species',
        'species': True,
        'species_from_current': True,
        'want_fugue': False,
        'summary': src.get('summary') or (
            f'基于当前乐谱定旋律（{meta.get("fixed_cf")}）写 1:1 对位'),
        'user_brief': user_text,
        '_species_hint': {
            'kind': 'two_part_species', 'from_current': True,
            'fixed_cf': meta.get('fixed_cf'),
        },
    }
    session.plan = out_plan
    session.score_spec = spec
    session.revision += 1
    session.last_skill_ids = ['compose-full']
    _tr = getattr(session, 'compose_trace', None)
    if _tr is not None:
        _tr['pipeline'] = (
            'two_part_species_from_current（抽定旋律 + 确定性对位，未写音符 LLM）')
        _tr['constraint_fixes'] = [
            f'定旋律来自当前乐谱（role={meta.get("fixed_cf")}）',
            '另一声部按对位法硬约束生成；对位≠赋格',
        ]
        _tr['skill_ids'] = ['compose-full']
        _tr['species'] = True
        _tr['result'] = {
            'title': spec.get('title'),
            'time_signature': spec.get('time_signature'),
            'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
            'key_sharps': spec.get('key_sharps'),
            'bars_target': bars, 'bars_actual': bars,
            'voices': 2,
            'parts': [p.get('name') for p in (spec.get('parts') or [])],
            'has_fugue': False,
            'species': True,
            'from_current': True,
        }
    session.last_translate = _build_think_trace(
        out_plan, None, deterministic=True,
        calls_text=(
            f"two_part_species.from_cantus(bars={bars}, cf_role={meta.get('fixed_cf')}, "
            f"sharps={sharps}, mode={mode}, seed={seed})"),
        reasoning_text=(
            '根据当前乐谱抽取每小节一音作定旋律，按对位法规则写另一声部；'
            '不加赋格。'),
        genre_fixes=list((_tr or {}).get('constraint_fixes') or []),
    )
    note = report + '\n[Skill: 基于当前乐谱的二声部对位（确定性）]'
    return m21, spec, note


def _try_compose_two_part_species(
    session: ComposeSession, user_text: str,
    plan: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[Any, Dict[str, Any], str]]:
    """第一类二声部对位确定性生成。

    有 plan 时信 Planner（species/texture）；无 plan 时才 keyword 检测（离线兜底）。
    """
    try:
        from music_score.two_part_species import (
            detect_two_part_species, build_two_part_species_score,
            species_score_to_spec,
        )
        from music_score.exporter import model_to_m21
    except Exception:
        return None
    hit = None
    if plan is not None and _plan_indicates_species(plan):
        hit = plan.get('_species_hint') or {
            'kind': 'two_part_species', 'from': 'planner',
        }
    else:
        hit = detect_two_part_species(user_text)
    if not hit:
        return None
    prefs = getattr(session, 'prefs', None) or {}
    if not (prefs.get('_explicit') or {}).get('fugue'):
        prefs['fugue'] = 'off'
    src = plan or {}
    bars = int(src.get('bars') or prefs.get('bars') or 8)
    bars = max(4, min(32, bars))
    tempo = int(src.get('tempo') or prefs.get('tempo') or 88)
    meter = str(src.get('meter') or prefs.get('meter') or '4/4')
    sharps = int(src.get('key_sharps') if src.get('key_sharps') is not None
                 else (prefs.get('key_sharps') or 0))
    mode = str(src.get('mode') or prefs.get('mode') or 'major')
    title = str(src.get('title') or prefs.get('title') or '二声部对位')
    seed = int(prefs.get('seed') or 0)
    if seed <= 0:
        # 每次作曲新随机：时间 ⊕ revision ⊕ composition_id
        mix = time.time_ns() ^ ((session.revision + 1) * 0x9E3779B9)
        mix ^= (hash(str(session.composition_id or '')) & 0xFFFFFFFF)
        seed = abs(int(mix)) % (2**31 - 1) or int(time.time()) % 100000
    model, report, meta = build_two_part_species_score(
        title=title,
        bars=bars, tempo=tempo, meter=meter,
        sharps=sharps, mode=mode, seed=seed,
    )
    spec = species_score_to_spec(model, meta)
    spec['fugue'] = False
    m21 = model_to_m21(model)
    out_plan = {
        'title': spec.get('title'),
        'genre': '第一类二声部对位',
        'genre_id': '',
        'mood': list(src.get('mood') or ['清晰', '平稳']),
        'tempo': tempo,
        'meter': meter,
        'key_sharps': sharps,
        'mode': mode,
        'instrumentation': ['piano', 'piano'],
        'form': 'species',
        'bars': bars,
        'texture': 'first-species',
        'species': True,
        'want_fugue': False,
        'summary': src.get('summary') or (
            '钢琴大谱表二声部，1 小节 1 音，中间 3/6 度，结尾反向八度'),
        'user_brief': user_text,
        '_species_hint': hit,
    }
    session.plan = out_plan
    session.score_spec = spec
    session.revision += 1
    session.last_skill_ids = ['compose-full']
    src_tag = 'planner' if plan is not None else 'keyword-fallback'
    _tr = getattr(session, 'compose_trace', None)
    if _tr is not None:
        _tr['pipeline'] = (
            f'two_part_species（确定性第一类对位，意图来自 {src_tag}，未写音符 LLM）')
        _tr['constraint_fixes'] = [
            '二声部对位：编制强制钢琴大谱表 RH/LH',
            '1 小节 1 音 · 中间仅 3/6 度 · 禁纯四 · 结尾反向八度 · 最高音错开',
        ]
        _tr['skill_ids'] = ['compose-full']
        _tr['species'] = True
        _tr['result'] = {
            'title': spec.get('title'),
            'time_signature': spec.get('time_signature'),
            'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
            'key_sharps': spec.get('key_sharps'),
            'bars_target': bars, 'bars_actual': bars,
            'voices': 2,
            'parts': [p.get('name') for p in (spec.get('parts') or [])],
            'has_fugue': False,
            'composer': spec.get('composer'),
            'species': True,
        }
    session.last_translate = _build_think_trace(
        out_plan, None, deterministic=True,
        calls_text=(
            f"two_part_species.build(bars={bars}, meter={meter}, "
            f"sharps={sharps}, mode={mode}, seed={seed})"),
        reasoning_text=(
            f'意图来自 {src_tag}：第一类二声部对位（对位法规则）——钢琴大谱表、'
            '每小节一音、中间3/6为主可反向至5/8、禁纯四、禁同时跳进、'
            '结尾反向八度且导→主/Ⅱ↓Ⅰ。确定性写谱，不加赋格。'),
        genre_fixes=list((_tr or {}).get('constraint_fixes') or []),
    )
    note = report + '\n[Skill: 二声部对位（确定性）]'
    return m21, spec, note


# ---------------------------------------------------------------------------
# 风格（genre）硬约束执行器：把 genre profile 的确定性规则写回 program / spec
# ---------------------------------------------------------------------------
def _map_genre_acc_pattern(acc_default: Optional[str]) -> Optional[str]:
    """风格包 accompaniment.default → 内核 ACC_PATTERNS（统一表，不按体裁分支）。"""
    if not acc_default:
        return None
    try:
        from style_skeleton import ACC_MAP
        from compose_kernel import ACC_PATTERNS
        pat = ACC_MAP.get(str(acc_default))
        if pat and pat in ACC_PATTERNS:
            return pat
    except Exception:
        pass
    return None


def _resolve_genre_profile(skill_ids: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    """从 skill_ids 里取 genre: 开头的风格，返回其结构化 profile（或 None）。"""
    if not skill_ids:
        return None
    gid = next((s for s in skill_ids if str(s).startswith('genre:')), None)
    if not gid:
        return None
    try:
        import composition_skills as _cs
        g = _cs.get_genre(gid)
        return (g or {}).get('profile')
    except Exception:
        return None


def _apply_genre_constraints(program: Dict[str, Any], plan: Dict[str, Any],
                             skill_ids: Optional[List[str]]) -> Tuple[Dict[str, Any], List[str]]:
    """在内核执行前，确定性地把 genre profile 的硬约束写回 program。

    修复「genre 只是软提示、LLM 自由发挥」导致的 4/4 圆舞曲、缺失 oom-pah-pah、
    小节数不符等问题：
    - meter：new_score / 各段 set_sections 的 meter 若不在 allowed，强制 primary
    - accompaniment：genre 默认伴奏可映射为内核 pattern 时强制改写
    - bars：set_sections 各段 bars 之和对齐 plan.bars
    返回 (program, 应用的约束说明列表)。
    """
    fixes: List[str] = []
    if not isinstance(program, dict):
        return program, fixes
    calls = program.get('calls')
    if not isinstance(calls, list):
        return program, fixes
    prof = _resolve_genre_profile(skill_ids)
    if not prof:
        return program, fixes
    meter = prof.get('meter') or {}
    primary = meter.get('primary')
    allowed = meter.get('allowed') or []
    rhythm = prof.get('rhythm') or {}
    acc_default = (rhythm.get('accompaniment') or {}).get('default')
    acc_pat = _map_genre_acc_pattern(acc_default)
    target = int(plan.get('bars') or 0)
    for c in calls:
        s = str(c.get('script') or '')
        a = c.get('args') or {}
        if s == 'new_score':
            cur = a.get('meter')
            # 用户显式 /meter 指定的拍号不被 genre 硬约束覆盖
            if primary and not plan.get('_user_meter') \
                    and cur not in allowed and cur != primary:
                a['meter'] = primary
                fixes.append(f'new_score 拍号 {cur!r} → {primary}（genre 硬约束）')
            if target:
                cb = int(a.get('bars') or 0)
                if cb != target:
                    a['bars'] = target
                    if cb:
                        fixes.append(f'new_score 小节 {cb} → {target}')
        elif s == 'set_sections':
            secs = a.get('sections') or []
            for sec in secs:
                if not isinstance(sec, dict):
                    continue
                cur = sec.get('meter')
                if primary and cur not in allowed and cur != primary:
                    sec['meter'] = primary
                    fixes.append(f"段落 {sec.get('name')} 拍号 {cur!r} → {primary}")
            if target and secs:
                tot = sum(int(x.get('bars', 0)) for x in secs
                          if isinstance(x, dict))
                if tot and tot != target:
                    diff = target - tot
                    last = secs[-1]
                    last['bars'] = max(1, int(last.get('bars', 1)) + diff)
                    fixes.append(f'小节数 {tot} → {target}（对齐 plan.bars）')
        elif s == 'accompaniment' and acc_pat:
            if a.get('pattern') != acc_pat:
                old = a.get('pattern')
                a['pattern'] = acc_pat
                fixes.append(f"accompaniment pattern {old!r} → {acc_pat}")
    return program, fixes


def _apply_species_program_constraints(
    program: Dict[str, Any], plan: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """第一类二声部对位：强制 piano×2、摘除 fugue、旋律 sparse。"""
    fixes: List[str] = []
    if not (plan.get('_species_hint') or plan.get('species')):
        return program, fixes
    if not isinstance(program, dict):
        return program, fixes
    calls = list(program.get('calls') or [])
    if not calls:
        return program, fixes
    keep = []
    for c in calls:
        s = str(c.get('script') or '')
        a = c.setdefault('args', {}) if isinstance(c.get('args'), dict) \
            else {}
        if not isinstance(c.get('args'), dict):
            c['args'] = a
        if s == 'fugue':
            fixes.append('已摘除 fugue（第一类对位不是赋格）')
            continue
        if s == 'new_score':
            a['voices'] = 2
            a['instruments'] = ['piano', 'piano']
            fixes.append('new_score → piano×2 大谱表')
        elif s == 'melody':
            if a.get('rhythm') != 'sparse':
                a['rhythm'] = 'sparse'
                fixes.append(f"melody rhythm → sparse（1 小节 1 音）")
        keep.append(c)
    program['calls'] = keep
    return program, fixes


def _clamp_tempo_to_genre(spec: Dict[str, Any],
                          profile: Optional[Dict[str, Any]],
                          explicit_tempo: bool = False) -> List[str]:
    """把 spec 速度钳进 genre 的 tempo 范围（只改 BPM，不影响音符时值）。

    用户显式 /tempo 设定的速度**直通谱面**，不做 genre 钳制——
    genre 只做提示，不得覆盖用户明确给定的速度。
    """
    fixes: List[str] = []
    if explicit_tempo or not isinstance(spec, dict) or not profile:
        return fixes
    tempo = profile.get('tempo') or {}
    lo = tempo.get('min')
    hi = tempo.get('max')
    df = tempo.get('default')
    if lo is None and hi is None:
        return fixes
    lo = lo if lo is not None else 0
    hi = hi if hi is not None else 10 ** 9

    def _clamp(v: float) -> int:
        if v < lo:
            return int(df if df is not None else lo)
        if v > hi:
            return int(df if df is not None else hi)
        return int(v)

    t = spec.get('tempo')
    if isinstance(t, (int, float)) and (t < lo or t > hi):
        new = _clamp(t)
        fixes.append(f'基础速度 ♩={int(t)} → ♩={new}（genre 范围 {lo}–{hi}）')
        spec['tempo'] = new
    marks = spec.get('tempo_marks')
    if isinstance(marks, list):
        import re as _re
        for m in marks:
            if isinstance(m, (list, tuple)) and len(m) >= 2 and isinstance(m[1], str):
                mo = _re.search(r'♩\s*=\s*(\d+)', m[1])
                if mo:
                    bpm = int(mo.group(1))
                    if bpm < lo or bpm > hi:
                        nb = _clamp(bpm)
                        m[1] = _re.sub(r'♩\s*=\s*\d+', f'♩={nb}', m[1])
                        # 同步更新数值型 bpm（列表第 3 项），否则渲染时
                        # spec_to_music21 仍会按旧数值 120 写 MetronomeMark
                        if len(m) >= 3 and isinstance(m[2], (int, float)):
                            m[2] = nb
                        elif len(m) == 2:
                            m.append(nb)
                        fixes.append(f'速度标记 ♩={bpm} → ♩={nb}')
    return fixes


def _format_calls(calls: List[Dict[str, Any]]) -> str:
    """把脚本调用序列渲染成易读的多行文本。"""
    out: List[str] = []
    for c in (calls or []):
        s = str(c.get('script') or '?')
        a = c.get('args') or {}
        if s == 'set_sections':
            secs = a.get('sections') or []
            names = '/'.join(
                f"{x.get('name')}({x.get('bars')})" for x in secs)
            out.append(f"set_sections([{names}])")
        elif s == 'new_score':
            out.append(f"new_score(♩={a.get('tempo')}, {a.get('meter')}, "
                       f"voices={a.get('voices')})")
        else:
            sec = a.get('section') or ''
            rest = ', '.join(f"{k}={v}" for k, v in a.items()
                             if k != 'section')
            if sec and rest:
                out.append(f"{s}({sec}, {rest})")
            elif sec:
                out.append(f"{s}({sec})")
            elif rest:
                out.append(f"{s}({rest})")
            else:
                out.append(s)
    return '\n'.join(out)


def _build_think_trace(plan: Dict[str, Any], program: Optional[Dict[str, Any]] = None,
                       deterministic: bool = False, calls_text: Optional[str] = None,
                       reasoning_text: Optional[str] = None,
                       genre_fixes: Optional[List[str]] = None) -> Dict[str, Any]:
    """构造一次生成的「思考/翻译追溯」信息，存到 session.last_translate。"""
    p = {k: plan.get(k) for k in
         ('genre', 'mood', 'tempo', 'tempo_text', 'meter', 'mode', 'form', 'bars')}
    if program:
        reasoning = program.get('reasoning')
        calls = _format_calls(program.get('calls') or [])
    else:
        reasoning = reasoning_text
        calls = calls_text
    return {
        'plan': p,
        'emotion_hint': plan.get('_emotion_hint'),
        'reasoning': reasoning,
        'calls': calls,
        'genre_fixes': genre_fixes or [],
        'deterministic': deterministic,
    }


# ---------------------------------------------------------------------------
# 作曲过程透明化：把「需求 → 大模型 → 脚本 → 谱面」全链路记录下来，
# 存到 session.compose_trace，供分析报告的「第 4 节」展开，也便于把报告整体
# 喂回大模型定位「哪里写错了、错到什么程度」。
# ---------------------------------------------------------------------------
def _init_compose_trace(session, user_text):
    """初始化（或重置）一次生成的流程痕迹。"""
    tr = {
        'user_text': user_text,
        'pipeline': None,
        'skill_ids': [],
        'llm_calls': [],          # [{stage, title, system, user, assistant, parsed}]
        'emotion': None,
        'emotion_applied': False,
        'constraint_fixes': [],   # 风格硬约束 + 速度钳制合并后的修正说明
        'kernel_calls': [],       # 逐脚本「输入 + 输出」
        'repair': None,           # {'triggered': bool, 'issues': [...]}
        'result': {},
        'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    session.compose_trace = tr
    return tr


def _truncate(s, n=1500):
    if s is None:
        return ''
    s = str(s)
    if len(s) <= n:
        return s
    return (s[:n] + f'\n…（已截断，完整 {len(s)} 字；固定指令模板见 '
            f'skills/composition/base/ 与内核 kernel_docs）')


def _compact_args(args):
    """把脚本 args 压成无竖线、适合放进 Markdown 表格的字符串。"""
    if not isinstance(args, dict):
        return str(args)
    parts = []
    for k, v in args.items():
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        parts.append(f'{k}={v}')
    return '，'.join(parts)


def _trace_planner(session, system, payload, raw, plan):
    tr = getattr(session, 'compose_trace', None)
    if tr is None:
        return
    tr['llm_calls'].append({
        'stage': 'planner',
        'title': 'Planner（意图解析）',
        'system': _truncate(system, 1400),
        'user': json.dumps(payload, ensure_ascii=False),
        'assistant': raw,
        'parsed': plan,
    })


def compose_from_text(session: ComposeSession, user_text: str,
                      selection: Optional[Dict[str, Any]] = None,
                      m21_score=None,
                      ) -> Tuple[stream.Score, Dict[str, Any], str]:
    cmds, nl = parse_user_turn(user_text)
    notes: List[str] = []
    pipeline_intent: Optional[str] = None
    pipeline_cmd = ''
    apply_advice = False
    session.last_translate = None
    # 透明化：初始化本次生成的流程痕迹（命令分支也会用，放在最前）
    _init_compose_trace(session, user_text)

    for name, arg in cmds:
        msg = apply_command(session, name, arg)
        if msg == '__DEMO__':
            spec = demo_spec(session)
            score = spec_to_music21(spec)
            session.score_spec = spec
            session.plan = {
                'title': spec['title'], 'genre': session.prefs.get('style') or 'demo',
                'tempo': spec['tempo'], 'meter': spec['time_signature'],
                'bars': spec['bars'], 'summary': spec.get('notes'),
            }
            session.revision += 1
            session.last_skill_ids = ['compose-full']
            _tr = session.compose_trace
            _tr['pipeline'] = 'demo（本地示例，未调 LLM）'
            _tr['result'] = {
                'title': spec.get('title'),
                'time_signature': spec.get('time_signature'),
                'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
                'key_sharps': spec.get('key_sharps'),
                'bars_target': spec.get('bars'), 'bars_actual': spec.get('bars'),
                'voices': spec.get('voices') or len(spec.get('parts') or []),
                'parts': [p.get('name') for p in (spec.get('parts') or [])],
                'has_fugue': bool(spec.get('fugue')),
                'composer': spec.get('composer'),
            }
            session.last_translate = _build_think_trace(
                session.plan, None, deterministic=True,
                calls_text='本地示例曲（demo_spec，确定性，不调 LLM）')
            return score, spec, (spec.get('notes') or '本地示例曲') + '\n[Skill: 本地示例]'
        if msg.startswith('__SKELETON__:'):
            want = msg.split(':', 1)[1].strip()
            try:
                import style_skeleton
            except Exception as e:
                raise CommandOnly(f'骨架生成器不可用：{e}')
            genre = want or session.prefs.get('genre_id') \
                or session.prefs.get('style') or ''
            if not genre:
                names = '、'.join(
                    s['name'] for s in style_skeleton.list_styles()[:12])
                raise CommandOnly(
                    '骨架需要指定风格基调。用法：/skeleton 圆舞曲  '
                    '（或先 /style 设定基调，再 /skeleton）\n可用风格：' + names + ' …')
            g = style_skeleton.resolve_genre(genre)
            if g is None:
                raise CommandOnly(f'未匹配到风格包：{genre}（试试 /style 列出）')
            kw = {'bars': int(session.prefs.get('bars') or 16),
                  'fugue': session.prefs.get('fugue') or 'auto'}
            if session.prefs.get('voices'):
                kw['voices'] = int(session.prefs['voices'])
            if session.prefs.get('tempo'):
                kw['tempo'] = int(session.prefs['tempo'])
            if session.prefs.get('meter'):
                kw['meter'] = str(session.prefs['meter'])
            spec = style_skeleton.skeleton_spec(
                genre, trace=session.compose_trace['kernel_calls'], **kw)
            score = spec_to_music21(spec)
            session.score_spec = spec
            gid = g.get('id') or genre
            session.plan = {
                'title': spec['title'], 'genre': genre,
                'tempo': spec['tempo'], 'meter': spec['time_signature'],
                'bars': spec['bars'], 'summary': '确定性骨架',
            }
            session.revision += 1
            session.last_skill_ids = ['compose-full', f'genre:{gid}']
            _tr = session.compose_trace
            _tr['pipeline'] = 'skeleton（确定性骨架，未调 LLM）'
            _tr['skill_ids'] = ['compose-full', f'genre:{gid}']
            _tr['result'] = {
                'title': spec.get('title'),
                'time_signature': spec.get('time_signature'),
                'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
                'key_sharps': spec.get('key_sharps'),
                'bars_target': spec.get('bars'), 'bars_actual': spec.get('bars'),
                'voices': spec.get('voices') or len(spec.get('parts') or []),
                'parts': [p.get('name') for p in (spec.get('parts') or [])],
                'has_fugue': bool(spec.get('fugue')),
                'composer': spec.get('composer'),
            }
            session.last_translate = _build_think_trace(
                session.plan, None, deterministic=True,
                calls_text=(f"style_skeleton.skeleton_spec(genre='{gid}', "
                            f"bars={kw['bars']}, fugue={kw['fugue']}, "
                            f"voices={kw.get('voices')}, tempo={kw.get('tempo')}, "
                            f"meter={kw.get('meter')})"))
            note = (f"骨架（确定性脚本，不调 LLM）· 风格 {g.get('name') or genre}"
                    f"（{gid}）\n声部 {len(spec.get('parts') or [])} · "
                    f"{spec['bars']} 小节 · ♩={spec['tempo']} · "
                    f"赋格 {'有' if spec.get('fugue') else '无'} · seed={spec.get('seed')}")
            return score, spec, note
        if msg.startswith('__INTERPOLATE__:'):
            want = msg.split(':', 1)[1].strip()
            try:
                import style_interpolate as _si
            except Exception as e:
                raise CommandOnly(f'风格插值模块不可用：{e}')
            # 解析：A B [t]，t 可为 0-1 或 0-100%
            toks = want.split()
            t = 0.5
            if toks:
                raw = toks[-1].strip()
                is_pct = raw.endswith('%')
                last = raw[:-1].strip() if is_pct else raw
                try:
                    v = float(last)
                    if is_pct:
                        # 显式百分号：1% = 0.01（不能落入 0–1 分支变成 1.0）
                        if 0 <= v <= 100:
                            t, toks = v / 100.0, toks[:-1]
                    elif 0 <= v <= 1:
                        t, toks = v, toks[:-1]
                    elif 1 < v <= 100:
                        t, toks = v / 100.0, toks[:-1]
                except ValueError:
                    pass
            if len(toks) < 2:
                raise CommandOnly(
                    '用法：/interpolate <风格A> <风格B> [t]\n'
                    '例：/interpolate 圆舞曲 夜曲 0.5  （t=0 偏 A，t=1 偏 B）')
            a, b = toks[0], ' '.join(toks[1:])
            kw = {'fugue': session.prefs.get('fugue') or 'auto'}
            if session.prefs.get('voices'):
                kw['voices'] = int(session.prefs['voices'])
            # tempo/meter/bars 由混合 profile 决定（风格插值的核心价值），
            # 不强行用 session 默认偏好（120/4/4/8）覆盖；用户可生成后用
            # /tempo /meter /bars 再改写。
            try:
                spec = _si.interpolate_spec(
                    a, b, t=t, trace=session.compose_trace['kernel_calls'], **kw)
            except Exception as e:
                raise CommandOnly(f'风格插值失败：{e}')
            score = spec_to_music21(spec)
            session.score_spec = spec
            session.plan = {
                'title': spec['title'], 'genre': f'{a}×{b}',
                'tempo': spec['tempo'], 'meter': spec['time_signature'],
                'bars': spec['bars'], 'summary': '风格插值（确定性，不调 LLM）',
            }
            session.revision += 1
            session.last_skill_ids = ['compose-full', 'interpolate']
            _tr = session.compose_trace
            _tr['pipeline'] = 'interpolate（风格插值，确定性，不调 LLM）'
            _tr['skill_ids'] = ['compose-full', 'interpolate']
            _tr['result'] = {
                'title': spec.get('title'),
                'time_signature': spec.get('time_signature'),
                'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
                'key_sharps': spec.get('key_sharps'),
                'bars_target': spec.get('bars'), 'bars_actual': spec.get('bars'),
                'voices': spec.get('voices') or len(spec.get('parts') or []),
                'parts': [p.get('name') for p in (spec.get('parts') or [])],
                'has_fugue': bool(spec.get('fugue')),
                'composer': spec.get('composer'),
            }
            session.last_translate = _build_think_trace(
                session.plan, None, deterministic=True,
                calls_text=(f"style_interpolate.interpolate_spec('{a}','{b}', "
                            f"t={t}, fugue={kw['fugue']}, "
                            f"voices={kw.get('voices')})"))
            note = (f"风格插值（确定性，不调 LLM）· {a} × {b}（t={t:.2f}）\n"
                    f"声部 {len(spec.get('parts') or [])} · {spec['bars']} 小节 · "
                    f"♩={spec['tempo']} · 赋格 {'有' if spec.get('fugue') else '无'} · "
                    f"seed={spec.get('seed')}")
            return score, spec, note
        if msg.startswith('__REFINE__:'):
            want = msg.split(':', 1)[1].strip()
            try:
                import refine_style as _rs
            except Exception as e:
                raise CommandOnly(f'风格保真度精炼模块不可用：{e}')
            # 目标包：参数 > 当前 session 风格 > 报错
            pack_id = want or session.prefs.get('genre_id') \
                or session.prefs.get('style') or ''
            if not pack_id:
                raise CommandOnly(
                    '请先 /style 设定目标风格，或 /refine <风格名>。'
                    '例：/refine 圆舞曲')
            import style_skeleton
            g = style_skeleton.resolve_genre(pack_id)
            if g is None:
                raise CommandOnly(f'未匹配到风格包：{pack_id}（试试 /style 列出）')
            gid = g.get('id') or pack_id
            # pack_id（文件夹名，如 'fugue'）才是 pack_fingerprint 匹配用的键；
            # genre id（如 'genre:academic/fugue'）不能直接喂给 refine_style。
            pid = g.get('pack_id') or gid
            kw = {'bars': int(session.prefs.get('bars') or 16),
                  'fugue': session.prefs.get('fugue') or 'auto'}
            if session.prefs.get('voices'):
                kw['voices'] = int(session.prefs['voices'])
            if session.prefs.get('tempo'):
                kw['tempo'] = int(session.prefs['tempo'])
            if session.prefs.get('meter'):
                kw['meter'] = str(session.prefs['meter'])
            try:
                res = _rs.refine_style(
                    pid, kw, trace=session.compose_trace['kernel_calls'])
            except Exception as e:
                raise CommandOnly(f'风格保真度精炼失败：{e}')
            spec = res['spec']
            score = spec_to_music21(spec)
            session.score_spec = spec
            session.prefs['genre_id'] = pid
            session.prefs['style'] = g.get('name') or pid
            session.plan = {
                'title': spec['title'], 'genre': gid,
                'tempo': spec['tempo'], 'meter': spec['time_signature'],
                'bars': spec['bars'], 'summary': '风格保真度闭环精炼（确定性）',
            }
            session.revision += 1
            session.last_skill_ids = ['compose-full', 'refine']
            _tr = session.compose_trace
            _tr['pipeline'] = 'refine（风格保真度闭环精炼，确定性，不调 LLM）'
            _tr['skill_ids'] = ['compose-full', 'refine']
            _tr['result'] = {
                'title': spec.get('title'),
                'time_signature': spec.get('time_signature'),
                'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
                'key_sharps': spec.get('key_sharps'),
                'bars_target': spec.get('bars'), 'bars_actual': spec.get('bars'),
                'voices': spec.get('voices') or len(spec.get('parts') or []),
                'parts': [p.get('name') for p in (spec.get('parts') or [])],
                'has_fugue': bool(spec.get('fugue')),
                'composer': spec.get('composer'),
                'style_overlap': res.get('overlap'),
                'refine_converged': res.get('converged'),
            }
            session.last_translate = _build_think_trace(
                session.plan, None, deterministic=True,
                calls_text=(f"refine_style.refine_style('{gid}', "
                            f"bars={kw['bars']}, fugue={kw['fugue']}, "
                            f"voices={kw.get('voices')})"))
            note = (f"风格保真度闭环精炼（确定性，不调 LLM）· 目标 "
                    f"{g.get('name') or gid}（{gid}）\n"
                    f"风格重叠率 {res['overlap']:.3f}（{'已收敛' if res['converged'] else '未收敛'}）· "
                    f"声部 {len(spec.get('parts') or [])} · {spec['bars']} 小节 · "
                    f"♩={spec['tempo']} · seed={spec.get('seed')}\n\n"
                    f"{_rs.refine_report(res)}")
            return score, spec, note
        if msg.startswith('__IMPORT__:'):
            path = msg.split(':', 1)[1].strip()
            if not path or not os.path.exists(path):
                raise CommandOnly(f'文件不存在：{path}')
            try:
                import musicxml_roundtrip as _mr
            except Exception as e:
                raise CommandOnly(f'MusicXML 往返模块不可用：{e}')
            try:
                score = _mr.import_musicxml(path)
            except Exception as e:
                raise CommandOnly(f'MusicXML 解析失败：{e}')
            pid = session.prefs.get('genre_id') or session.prefs.get('style') or ''
            pid = pid.rsplit('/', 1)[-1] if str(pid).startswith('genre:') else str(pid)
            try:
                res = _mr.revalidate(score, pack_id=pid or None, title=os.path.basename(path))
            except Exception as e:
                raise CommandOnly(f'再校验失败：{e}')
            # 往返：重新导出规范化 MusicXML + MIDI（证明导入→导出闭环可用）
            tmpd = tempfile.gettempdir()
            xml_out = os.path.join(tmpd, 'imported_canonical.musicxml')
            mid_out = os.path.join(tmpd, 'imported_canonical.mid')
            try:
                _mr.export_canonical(score, xml_out, mid_out)
            except Exception:
                xml_out = mid_out = None
            session.imported_score = score
            meta = res.get('meta') or {}
            session.score_spec = {
                'title': meta.get('title') or os.path.basename(path),
                'time_signature': meta.get('meter'),
                'tempo': meta.get('tempo'), 'key_sharps': meta.get('key_sharps'),
                'bars': meta.get('bars'), 'parts': [{'name': p} for p in (meta.get('parts') or [])],
            }
            session.plan = {
                'title': meta.get('title') or os.path.basename(path),
                'genre': pid or 'imported',
                'tempo': meta.get('tempo'), 'meter': meta.get('meter'),
                'bars': meta.get('bars'), 'summary': 'MusicXML 往返导入 + 再校验',
            }
            session.revision += 1
            session.last_skill_ids = ['import', 'roundtrip']
            note = (f"MusicXML 往返导入 + 再校验（确定性规则引擎，不调 LLM）· "
                    f"来源 `{path}`\n"
                    + _mr.revalidate_report(res)
                    + (f"\n\n规范化文件已导出：\n- MusicXML: {xml_out}\n- MIDI: {mid_out}"
                       if xml_out else ""))
            return score, session.score_spec, note
        if msg.startswith('__REVERSE__:'):
            path = msg.split(':', 1)[1].strip()
            try:
                import score_reverse as _sr
            except Exception as e:
                raise CommandOnly(f'逆向模块不可用：{e}')
            if path:
                if not os.path.exists(path):
                    raise CommandOnly(f'文件不存在：{path}')
                result = _sr.reverse_path(path)
            else:
                if not session.score_spec or not (session.score_spec.get('parts')):
                    raise CommandOnly(
                        '当前没有可逆向的乐谱。请先创作，或 '
                        '/reverse-prompt <MusicXML|JSON路径>')
                # 需要完整 events；stub import 不够
                parts = session.score_spec.get('parts') or []
                if not any((p.get('events') for p in parts)):
                    raise CommandOnly(
                        '当前 score_spec 缺少音符事件。请对完整生成谱使用 '
                        '/reverse-prompt，或传入 MusicXML/JSON 路径。')
                result = _sr.reverse_score(session.score_spec)
            session.last_reverse_program = result['program']
            session.last_reverse_prompt = result['prompt']
            out = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'runs',
                f"reverse_{result['manifest'].get('source_hash') or session.composition_id}")
            os.makedirs(out, exist_ok=True)
            desc = result.get('description') or result['prompt']
            with open(os.path.join(out, 'score_description.md'),
                      'w', encoding='utf-8') as f:
                f.write(desc)
            with open(os.path.join(out, 'regeneration_prompt.md'),
                      'w', encoding='utf-8') as f:
                f.write(desc)
            with open(os.path.join(out, 'manifest.json'),
                      'w', encoding='utf-8') as f:
                json.dump(result['manifest'], f, ensure_ascii=False, indent=2)
            raise CommandOnly(
                f"乐谱文字描述已生成（MusicXML→文字，无 JSON，不调 LLM）\n"
                f"- voices={result['manifest'].get('voices')} · "
                f"bars={result['manifest'].get('bars')}\n"
                f"- 主产物：{out}/score_description.md\n"
                f"- 用 /replay <score_description.md> 可还原为 MusicXML\n\n"
                f"{desc[:1200]}"
                + ('…\n(全文见 score_description.md)'
                   if len(desc) > 1200 else ''))
        if msg.startswith('__REPLAY__:'):
            path = msg.split(':', 1)[1].strip()
            try:
                import score_reverse as _sr
                import compose_kernel as _K
            except Exception as e:
                raise CommandOnly(f'复现模块不可用：{e}')
            program = None
            from_text = False
            if path:
                if not os.path.exists(path):
                    raise CommandOnly(f'文件不存在：{path}')
                low = path.lower()
                if low.endswith('.md') or low.endswith('.txt'):
                    with open(path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    try:
                        spec = _sr.replay_description(text)
                    except Exception as e:
                        raise CommandOnly(f'文字描述复现失败：{e}')
                    from_text = True
                else:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    program = data if 'calls' in data else data.get('program')
            elif session.last_reverse_prompt and '【声部' in (
                    session.last_reverse_prompt or ''):
                try:
                    spec = _sr.replay_description(session.last_reverse_prompt)
                except Exception as e:
                    raise CommandOnly(f'文字描述复现失败：{e}')
                from_text = True
            elif session.last_reverse_program:
                program = session.last_reverse_program
            else:
                raise CommandOnly(
                    '没有可复现的文字描述。请先 /reverse-prompt，'
                    '或 /replay <score_description.md>')
            if not from_text:
                if not program or not program.get('calls'):
                    raise CommandOnly('program 无效（需要 {"calls":[...]}）')
                try:
                    spec = _K.run_program(program)
                except Exception as e:
                    raise CommandOnly(f'复现失败：{e}')
            session.score_spec = spec
            session.plan = {
                'title': spec.get('title'), 'bars': spec.get('bars'),
                'tempo': spec.get('tempo'), 'meter': spec.get('time_signature'),
                'summary': ('文字描述还原（MusicXML←文字）' if from_text
                            else '逆向 program 复现（finalize preserve）'),
            }
            session.revision += 1
            session.last_skill_ids = ['reverse-replay']
            score = spec_to_music21(spec)
            note = (
                f"已按{'文字描述' if from_text else 'regeneration_program'}还原"
                f"（确定性，不调 LLM）· {spec.get('title')} · "
                f"{spec.get('bars')} 小节 · "
                f"voices={len(spec.get('parts') or [])}")
            return score, spec, note
        if msg.startswith('__BRIEF_EXPAND__:'):
            raw = msg.split(':', 1)[1].strip()
            try:
                import brief_expand as _be
            except Exception as e:
                raise CommandOnly(f'提示词扩写模块不可用：{e}')
            meta = _be.expand_brief(raw)
            session.last_brief_expand = meta
            raise CommandOnly(
                f"确定性 brief 扩写（模板={meta['template']}，"
                f"pack={meta['pack_hint']}）\n\n{meta['expanded']}")
        if msg.startswith('__LEARN_GRAMMAR__:'):
            raise CommandOnly(_learn_grammar_report(msg.split(':', 1)[1].strip()))
        if msg.startswith('__LEARN_ALL__:'):
            raise CommandOnly(_learn_all_report(msg.split(':', 1)[1].strip()))
        if msg.startswith('__COMPOSE_GRAMMAR__:'):
            return _compose_grammar_cmd(session, msg.split(':', 1)[1].strip())
        if msg.startswith('__CHORDS__:'):
            want = msg.split(':', 1)[1].strip()
            try:
                import chord_advisor as _ca
            except Exception as e:
                raise CommandOnly(f'和弦顾问模块不可用：{e}')
            try:
                report = _ca.advisor(want)
            except Exception as e:
                raise CommandOnly(f'和弦顾问分析失败：{e}')
            raise CommandOnly(report)
        if msg.startswith('__SATB__:'):
            want = msg.split(':', 1)[1].strip()
            try:
                from music_score.satb_piano import build_satb_piano_score
                from music_score.exporter import model_to_m21
            except Exception as e:
                raise CommandOnly(f'四部和声模块不可用：{e}')
            prefs = getattr(session, 'prefs', None) or {}
            try:
                model, report, meta = build_satb_piano_score(
                    want,
                    title=str(prefs.get('title') or '四部和声'),
                    tempo=int(prefs.get('tempo') or 72),
                    meter=str(prefs.get('meter') or '4/4'),
                    sharps=(int(prefs['key_sharps'])
                            if prefs.get('key_sharps') is not None else None),
                    mode=prefs.get('mode'),
                )
            except Exception as e:
                raise CommandOnly(f'四部和声写入失败：{e}')
            m21 = model_to_m21(model)
            spec = {
                'title': meta.get('title') or model.title,
                'composer': '四部和声',
                'tempo': meta.get('tempo') or model.tempo,
                'time_signature': meta.get('time_signature') or '4/4',
                'key_sharps': meta.get('key_sharps', 0),
                'mode': meta.get('mode') or 'major',
                'bars': meta.get('bars') or len(model.staves[0].measures),
                'staff_groups': meta.get('staff_groups') or list(model.staff_groups),
                'parts': [
                    {'name': 'Piano RH', 'clef': 'treble'},
                    {'name': 'Piano LH', 'clef': 'bass'},
                ],
            }
            session.score_spec = spec
            return m21, spec, report
        if msg.startswith('__RENDER__:'):
            import json as _json
            try:
                from render_enhance import RenderPreset, render_to_wav, describe
            except Exception as e:
                raise CommandOnly(f'渲染引擎不可用：{e}')
            try:
                pdict = _json.loads(msg.split(':', 1)[1].strip() or '{}')
            except Exception:
                pdict = {}
            p = RenderPreset.from_dict(pdict)
            if m21_score is None:
                raise CommandOnly('当前没有乐谱可渲染。请先创作、/demo，或打开已有乐谱。')
            try:
                fd, mp = tempfile.mkstemp(suffix='.mid', prefix='render_')
                os.close(fd)
                m21_score.write('midi', fp=mp)
                out_dir = os.path.join('scores', 'render')
                os.makedirs(out_dir, exist_ok=True)
                wav_path = os.path.join(
                    out_dir,
                    f'llm_compose_{re.sub(r"[^A-Za-z0-9]", "_", session.composition_id)}.wav')
                info = render_to_wav(mp, wav_path, p)
                os.remove(mp)
            except Exception as e:
                raise CommandOnly(f'渲染失败：{e}')
            if not info['ok']:
                raise CommandOnly(f'渲染未完成：{info.get("error")}'
                                  f'（缺少可用 SoundFont 或合成后端；'
                                  f'可放一个 .sf2 到 soundfonts/ 目录）')
            note = (f"渲染增强（确定性、表演层、不调 LLM）· 已出 WAV\n"
                    f"配置：{describe(p)}\n"
                    f"产物：{wav_path}（{info['duration']} 秒，{info['sample_rate']} Hz）\n"
                    f"说明：仅动音色/空间混响/力度曲线，未改动符号谱音高·时值·和声。")
            raise CommandOnly(note)
        if msg.startswith('__BATCH__:'):
            import json as _json
            try:
                from batch_generate import run_batch
            except Exception as e:
                raise CommandOnly(f'批量生成流水线不可用：{e}')
            try:
                bdict = _json.loads(msg.split(':', 1)[1].strip() or '{}')
                packs = bdict.get('packs') or []
            except Exception:
                packs = []
            try:
                report = run_batch(packs if packs else None)
            except Exception as e:
                raise CommandOnly(f'批量生成失败：{e}')
            # report 已是完整 markdown；同时落盘到 runs/
            try:
                os.makedirs('runs', exist_ok=True)
                with open(os.path.join('runs', 'batch_report.md'), 'w',
                          encoding='utf-8') as _f:
                    _f.write(report)
            except Exception:
                pass
            raise CommandOnly(report)
        if msg.startswith('__SYSTEM__:'):
            want = msg.split(':', 1)[1].strip()
            try:
                import systems as _systems
            except Exception as e:
                raise CommandOnly(f'体系层不可用：{e}')
            # 先做中英文别名归一，再校验（否则中文写法会被当成未知体系）
            alias = {'板腔': 'banqiang', '曲牌': 'qupai', '拉格': 'raga',
                     '序列': 'serial', '十二音': 'serial',
                     '板腔体': 'banqiang', '曲牌体': 'qupai'}.get(
                         str(want), str(want or '').lower())
            if alias not in _systems.SYSTEM_BUILDERS:
                avail = '、'.join(f'{k}（{v}）' for k, v in _systems.SYSTEM_LABELS.items())
                raise CommandOnly(
                    '体系层用法：/system 板腔  |  /system 曲牌  |  /system 拉格  '
                    '|  /system 序列\n可用体系：' + avail)
            kw = {'bars': int(session.prefs.get('bars') or 16)}
            if session.prefs.get('seed'):
                kw['seed'] = str(session.prefs['seed'])
            spec = _systems.build_system(alias, **kw)
            score = spec_to_music21(spec)
            session.score_spec = spec
            session.plan = {
                'title': spec['title'], 'genre': _systems.SYSTEM_LABELS.get(alias, alias),
                'tempo': spec['tempo'], 'meter': spec['time_signature'],
                'bars': spec['bars'], 'summary': '体系层（确定性）',
            }
            session.revision += 1
            session.last_skill_ids = ['compose-full', f'system:{alias}']
            _tr = session.compose_trace
            _tr['pipeline'] = 'system（体系层确定性，未调 LLM）'
            _tr['skill_ids'] = ['compose-full', f'system:{alias}']
            _tr['result'] = {
                'title': spec.get('title'),
                'time_signature': spec.get('time_signature'),
                'tempo': spec.get('tempo'), 'mode': spec.get('mode'),
                'key_sharps': spec.get('key_sharps'),
                'bars_target': spec.get('bars'), 'bars_actual': spec.get('bars'),
                'voices': spec.get('voices') or len(spec.get('parts') or []),
                'parts': [p.get('name') for p in (spec.get('parts') or [])],
                'has_fugue': bool(spec.get('fugue')),
                'composer': spec.get('composer'),
            }
            session.last_translate = _build_think_trace(
                session.plan, None, deterministic=True,
                calls_text=(f"systems.build_system('{alias}', "
                            f"bars={kw['bars']}, seed={kw.get('seed')})"))
            note = (f"体系层（确定性）· {_systems.SYSTEM_LABELS.get(alias, alias)}\n"
                    f"声部 {len(spec.get('parts') or [])} · {spec['bars']} 小节 · "
                    f"♩={spec['tempo']} · seed={spec.get('seed')}")
            return score, spec, note
        if msg == '__EXPORT__':
            spec = session.score_spec or {}
            if not spec.get('parts'):
                raise CommandOnly('当前没有可导出的曲目。先创一首曲（或 /demo）再导出。')
            # 真实导出：MusicXML + MIDI 写入 runs/（不依赖 Studio UI）
            out_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'runs')
            os.makedirs(out_dir, exist_ok=True)
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            title = str(spec.get('title') or 'untitled')
            safe = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', title).strip('-') or 'untitled'
            base = os.path.join(out_dir, f'{stamp}-{safe}')
            xml_path, mid_path = f'{base}.musicxml', f'{base}.mid'
            try:
                score = spec_to_music21(spec)
                score.write('musicxml', fp=xml_path)
                score.write('midi', fp=mid_path)
            except Exception as e:
                raise CommandOnly(f'导出失败：{e}')
            raise CommandOnly(
                f"已导出《{title}》：\n{xml_path}\n{mid_path}")
        if msg == '__ANALYZE__':
            plan = session.plan or {}
            spec = session.score_spec or {}
            lines = [
                f"曲名：{spec.get('title') or plan.get('title')}",
                f"体裁/风格：{plan.get('genre')}",
                f"情绪：{plan.get('mood')}",
                f"调性 sharps={spec.get('key_sharps')} mode={spec.get('mode')}",
                f"速度 ♩={spec.get('tempo')}  拍号 {spec.get('time_signature')}  小节 {spec.get('bars')}",
                f"曲式：{plan.get('form')}",
                f"动机：{(plan.get('motif') or {}).get('character')}",
                f"和声：{plan.get('harmony')}",
                f"高潮：{plan.get('climax')}",
                f"声部：{[p.get('name') for p in spec.get('parts') or []]}",
                f"revision={session.revision} id={session.composition_id}",
                f"skills={session.last_skill_ids}",
            ]
            raise CommandOnly('\n'.join(lines))
        if msg == '__APPLY_ADVICE__':
            apply_advice = True
            pipeline_intent = 'rewrite'
            pipeline_cmd = 'Command /apply'
            continue
        if msg.startswith('__LEARN_SCORE__:'):
            genre = msg.split(':', 1)[1].strip()
            raise CommandOnly(learn_score_current(session, genre=genre, m21_score=m21_score))
        if msg.startswith('__PIPELINE__:'):
            pipeline_intent = msg.split(':', 1)[1]
            pipeline_cmd = f'Command {name} {arg}'.strip()
            continue
        # 界面动作标记由工作室原样解析，不能混入偏好提示（否则 int('4\n…') 失败）
        if msg.startswith(('__ADDBARS__:', '__ADDPART__:', '__ACCOMP__:')):
            raise CommandOnly(msg)
        notes.append(msg)

    leftover = (session.prefs.pop('_style_leftover', None) or '').strip()
    if leftover:
        nl = f'{leftover} {nl}'.strip() if nl else leftover

    # Affirmation after /advice → apply stored suggestions as full rewrite
    probe = (nl or user_text or '').strip()
    has_advice = bool((session.last_advice or '').strip())
    wants_apply = apply_advice or (has_advice and is_advice_affirmation(probe))
    if wants_apply and not (pipeline_intent and pipeline_intent not in ('rewrite', 'advice')):
        brief = advice_apply_brief(session)
        if not brief:
            raise CommandOnly('没有可执行的修改建议。请先 /advice。')
        if not session.score_spec and m21_score is None:
            raise CommandOnly('当前没有乐谱。请先创作或打开乐谱。')
        raw_edits = strip_advice_footer(session.last_advice or '')
        score, spec, summary = compose_pipeline(
            session, brief,
            edit_intent='rewrite', selection=None, m21_score=m21_score,
            advice_edits=raw_edits or None,
        )
        # advice consumed — avoid accidental re-apply of stale suggestions
        session.last_advice = None
        prefix = '\n'.join(notes)
        note = '已按上一轮 /advice 建议整曲修改。\n' + summary
        if prefix:
            note = f'{prefix}\n{note}'
        return score, spec, note

    if pipeline_intent and not nl:
        return compose_pipeline(
            session, pipeline_cmd or user_text,
            edit_intent=pipeline_intent, selection=selection, m21_score=m21_score,
        )

    if nl:
        # 编辑快捷：添加声部 / 设置配乐（确定性，不走创曲）
        try:
            from music_score.part_accomp import (
                detect_add_part_request, detect_accomp_request,
            )
            part_tok = detect_add_part_request(nl)
            if part_tok is not None:
                raise CommandOnly(f'__ADDPART__:{part_tok}')
            acc_pat = detect_accomp_request(nl)
            if acc_pat is not None:
                raise CommandOnly(f'__ACCOMP__:{acc_pat}')
        except CommandOnly:
            raise
        except Exception:
            pass
        prefix = '\n'.join(notes)
        result = compose_pipeline(
            session, nl,
            edit_intent=pipeline_intent or session._pending_edit,
            selection=selection,
            m21_score=m21_score,
        )
        if prefix:
            score, spec, summary = result
            return score, spec, f'{prefix}\n{summary}'
        return result

    if notes:
        # 只改偏好的命令（/key /tempo /fugue …）不会动当前乐谱，
        # 不加这句说明用户会以为「命令没执行」。
        msg = '\n'.join(notes)
        msg += '\n（以上只设定基调参数，下一次生成时生效；'
        msg += '想立刻出谱：/skeleton 风格名 或写一句创作描述）'
        raise CommandOnly(msg)

    return compose_pipeline(
        session, user_text,
        edit_intent=session._pending_edit,
        selection=selection,
        m21_score=m21_score,
    )
