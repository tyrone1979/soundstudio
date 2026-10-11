# -*- coding: utf-8 -*-
"""生成质量双盲评审包：模型作品 vs 人工作品（同一体裁、同长度、同调池）。

设计要点（写在这里是为了可审计）
--------------------------------
1. **机器组**：走真正的 ``/compose-grammar`` 内核
   ``llm_compose.ComposeSession`` + ``llm_compose._compose_grammar_cmd``，
   全程不调 LLM（该函数本身就是确定性的语法作曲）。每首 SATB，
   seed 固定，可逐首重算复现。
2. **人类组**：从 music21 自带 bach 语料取**没有进过训练**的**整首**众赞歌
   （训练用的是 ``scores/corpus/bach_chorales_10/`` 那 10 首）。
   用训练外的曲子，是为了让评审员无法靠「听过这段旋律」识别来源。
2b. **完整性（本版修复的泄密点）**：早期版本把人组按固定 12 小节**截断**，
   于是人组结尾常常没有终止式、开头也不是乐句起点，而机组是完整生成的——
   评审员只要看「结尾站不站得住」就能识破来源。现在两组都必须是**完整的曲子**：
   开头从第一个完整小节起、结尾停在原位主和弦、全篇无缺拍小节、无反复记号。
   机组还会额外剥掉内核自带的 1 拍弱起小节（否则「开头有个单音弱起」
   又成了机组的暗号）。长度上机组逐首跟随人组，两组的长度分布按构造相同。
3. **配平**：两组从上到下共用同一份调池计划（mode × 调号），因此调式与调号的
   分布按构造相同，不是碰巧接近。人类组转调到最近的可用调，移动不超过三全音。
4. **去表面痕迹**：所有片段一律剥掉标题/作曲家、延长记号、歌词，统一声部名与谱号。
   否则「有没有 fermata」这种排版特征会直接泄露来源，测的就不是音乐本身了。
5. **匿名**：A01–A20 随机编号随机次序；真值存放在单独文件，**不随评审材料给出**。

产物
----
``runs/exploratory/blind_review/生成质量盲评乐谱.pdf``      20 个匿名片段
``runs/exploratory/blind_review/生成质量盲评表.xlsx``        评分表（维度取自 runs/listening_rubric.yaml）
``runs/exploratory/blind_review/_答案_评审后开封.json``      真值 + 复现用 seed（**评审后开封**）
``runs/exploratory/blind_review/audio/A*.mid|.wav``         供试听

用法
----
::

    venv/bin/python blind_review_export.py            # 默认 12 机器 / 8 人类
    venv/bin/python blind_review_export.py --no-wav
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, 'runs', 'exploratory', 'blind_review')
AUDIO_DIR = os.path.join(OUT_DIR, 'audio')
RUBRIC = os.path.join(ROOT, 'runs', 'listening_rubric.yaml')
CORPUS_DIR = os.path.join(ROOT, 'scores', 'corpus', 'bach_chorales_10')
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

VOICE_NAMES = ('Soprano', 'Alto', 'Tenor', 'Bass')
#: 试听音色。GM program 19 = Church Organ（管风琴）。
#: 必须两组统一：不指定时 MIDI 无 PROGRAM_CHANGE，落到 GM 默认 program 0（大钢琴），
#: 钢琴音色下四个声部难以听辨，而「声部进行正确性」是评分维度之一。
TIMBRE_PROGRAM = 19
#: 片段长度范围（完整小节数）。**不再定死统一长度**——长度由人组的整首长度
#: 决定，机组逐首跟随（见 main），否则「都是 12 小节」本身就是来源线索。
#: 下限 12：内核在 10 小节时往往收不到原位主和弦（实测 6/6 首结尾不解决），
#: 与其拿「没有终止」去换长度多样性，不如把下限抬到终止式必然出现的长度。
#: 上限受人类对照组限制：music21 的众赞歌整首多在 12–24 小节。
BAR_MIN, BAR_MAX = 12, 24
SHUFFLE_SEED = 20261004          # 编号打乱的固定种子 -> 顺序可复现
KEY_POOL = (-2, -1, 0, 1, 2)     # 调号（升降号数）；两组共用

#: 训练用过的 10 首，人类组必须回避
TRAINED_BWV = {f.split('_bwv')[1].replace('.musicxml', '')
               for f in os.listdir(CORPUS_DIR) if f.startswith('bach_bwv')}

AUTHOR_OPTS = ['人工作品', '机器作品', '不确定']

CHORALE_HARD_DIMS = [
    {'id': 'cadence_persuasion', 'label': '终止式说服力'},
    {'id': 'voice_leading', 'label': '声部进行正确性'},
]

DIM_ANCHOR = {
    'melody_memorable': '1=完全无印象 3=有一句顺口 5=哼得出来',
    'phrase_breath': '1=通篇平铺无句读 3=句读可辨但不自然 5=呼吸自然、句法分明',
    'harmony_clear': '1=功能和声崩坏 3=大体可行但有怪处 5=功能清晰、进行可信',
    'texture_balance': '1=四声部各自为政 3=主次基本清楚 5=主旋律与内声部层次分明',
    'genre_identity': '1=不像众赞歌 3=勉强像 5=一听就是十八世纪四部和声众赞歌',
    'cadence_persuasion': '1=句尾站不住、看不出终止 3=有终止但说服力弱 5=终止式明确可信',
    'voice_leading': '1=多处平行五八、声部交叉或超八度 3=偶有瑕疵 5=通篇合规',
}


# --------------------------------------------------------------- 调池计划
def make_slots(n_machine, n_human):
    """两组共用的 (mode, 调号) 计划：按索引取，保证两组分布一致。"""
    total = n_machine + n_human
    return [('major' if i % 2 == 0 else 'minor', KEY_POOL[i % len(KEY_POOL)])
            for i in range(total)]


# --------------------------------------------------------------- 完整性
def strip_partial_bars(sc):
    """只保留完整小节：丢掉首小节的弱起残拍与尾小节的残拍。

    必须两组都做。内核生成的众赞歌开头恒带 1 拍弱起（measureNumber 0），
    人组语料里也有带弱起的曲子；不剥掉的话「开头是不是有个孤零零的单音」
    就成了来源暗号。同样，末尾残拍会让人组看起来「话没说完」。
    """
    from music21 import meter
    ts = sc.recurse().getElementsByClass(meter.TimeSignature)
    exp = ts[0].barDuration.quarterLength if ts else 4.0
    nums = [m.measureNumber for m in sc.parts[0].getElementsByClass('Measure')
            if m.duration.quarterLength >= exp - 1e-6]
    if not nums or (nums[0] == sc.parts[0].getElementsByClass('Measure')[0].measureNumber
                    and nums[-1] == sc.parts[0].getElementsByClass('Measure')[-1].measureNumber):
        return sc                                   # 本来就完整
    try:
        return sc.measures(nums[0], nums[-1])
    except Exception:
        return sc


def completeness(sc, sharps, mode, tonic_pc=None):
    """完整性自检：一首能不能当成「一首完整的曲子」交给评审员。

    四项：
      1. 无缺拍小节（首/中/末都要满拍）——残拍小节会让人组像被截断；
      2. 末和弦是本调主和弦（根音+五度+任意三度，容忍 Picardy third）
         ——否则结尾不解决，看着像「没写完」；
      3. 末和弦低音是本调主音（原位主和弦）——终止式完整的最低要求；
      4. 四个声部都唱到最后——末小节有空声部同样是「不全」的信号。

    末和弦按**四个声部各自的末音**判定，不走 ``chordify()``：末小节里声部时值
    错开时，chordify 会切出只含一两个音的末片段，root 判定会整片错掉
    （实测表现为「所有小调都被判成结尾大三和弦」）。

    ``tonic_pc`` 优先于调号：music21 语料的 KeySignature 不可靠（实测有 E 大调
    的曲子标着 3 个升号，即按 A 大调解释），用调号反推主音会整片错一格，
    结果是所有大调候选被误杀。主音以 ``analyze('key')`` 为准。
    """
    from music21 import key, meter
    ts = sc.recurse().getElementsByClass(meter.TimeSignature)
    exp = ts[0].barDuration.quarterLength if ts else 4.0
    ms = list(sc.parts[0].getElementsByClass('Measure'))
    short = [m.measureNumber for m in ms if m.duration.quarterLength < exp - 1e-6]
    info = {'measures': len(ms), 'short_bars': short}
    try:
        tonic = (int(tonic_pc) % 12 if tonic_pc is not None
                 else key.KeySignature(int(sharps)).asKey(mode).tonic.pitchClass)
        tails = [list(p.recurse().notes)[-1].pitch.pitchClass for p in sc.parts]
        # 允许大三度或小三度（4 或 3）：巴赫小调众赞歌大量以 Picardy third
        # （平行大调主和弦）收尾，若锁死小三度会把大半人类作品误杀，
        # 反过来又制造「只有人组会这样」的新线索。只要求根音+五度+任意三度。
        chord_pcs = {0, 3, 4, 7}
        info['final_chord'] = '-'.join(
            p.recurse().getElementsByClass('Note')[-1].nameWithOctave
            for p in sc.parts)
        info['final_root_tonic'] = bool(tails and
                                        all((t - tonic) % 12 in chord_pcs for t in tails))
        info['final_bass_tonic'] = bool(tails and tails[-1] == tonic)
    except Exception as e:
        info['final_root_tonic'] = info['final_bass_tonic'] = None
        info['final_chord'] = 'n/a (%s)' % e
    try:
        voices = [p.getElementsByClass('Measure')[-1] for p in sc.parts]
        info['voices_to_end'] = sum(
            1 for m in voices if m.recurse().notes) == len(voices)
    except Exception:
        info['voices_to_end'] = None
    info['ok'] = bool(not short and info['final_root_tonic']
                      and info['final_bass_tonic'] and info['voices_to_end'])
    return info


# --------------------------------------------------------------- 机器组
def gen_machine(idx, mode, sharps, bars):
    """一首完整众赞歌：真正的 /compose-grammar 内核，确定性、不调 LLM。

    ``bars`` 取自人组整首的长度（长度配平，不让「都是 12 小节」泄露机组）。
    返回前剥掉内核自带的弱起小节，因此实际小节数以 ``strip_partial_bars``
    之后为准。

    生成后当场做完整性复核，不通过就换 seed 重来（seed 仍固定，可复现），
    避免机组里混进一首「结尾不解决」的——那正是评审员抓的来源线索。
    """
    import llm_compose as L
    from music21 import key as _k

    def KeySignatureTonic(sh, md):
        return _k.KeySignature(sh).asKey(md).tonic.pitchClass
    last = None
    for t in range(6):
        seed = 'blind%02d' % idx if t == 0 else 'blind%02d_r%d' % (idx, t)
        sess = L.ComposeSession()
        m21, spec, note = L._compose_grammar_cmd(
            sess, f'chorale {mode} {bars} seed={seed}',
            overrides={'key_sharps': sharps, 'tempo': 72})
        m21 = strip_partial_bars(m21)
        n = len(list(m21.parts[0].getElementsByClass('Measure')))
        info = completeness(m21, sharps, mode)
        last = {
            'code': None, 'truth': 'machine', 'seed': seed,
            'mode': mode, 'sharps': sharps,
            'tonic_pc': KeySignatureTonic(sharps, mode),
            'bars': n, 'requested_bars': bars,
            'voices': len(spec.get('parts') or []),
            'completeness': info,
            'score': m21,
        }
        if info['ok'] and n == bars:
            return last
    return last


# --------------------------------------------------------------- 人类组
def _key_sig_sharps(sc):
    from music21 import key
    ks = sc.recurse().getElementsByClass(key.KeySignature)
    return int(ks[0].sharps) if ks else 0


def _pick_human_candidates():
    """训练外的候选 Bach 众赞歌文件名（不含扩展名），随机但固定种子。

    返回**全部**候选（约 320 首）而不是只取一小撮：完整性过滤（整首、无反复、
    末和弦是本调原位主和弦）会刷掉九成，取一小撮会凑不齐对照组。
    """
    from music21 import corpus
    names = []
    for f in corpus.search('bach', 'composer'):
        n = str(f.sourcePath).split('/')[-1]
        if not n.startswith('bwv'):
            continue
        bwv = n.replace('.mxl', '')
        if bwv in TRAINED_BWV:
            continue
        names.append(bwv)
    names = sorted(set(names))
    rnd = random.Random(424242)
    rnd.shuffle(names)
    return names


def scan_human(candidates, limit=64):
    """预扫描：找出**整首就完整**的训练外众赞歌（先不存 score，按需再解析）。

    早期版本是「随机截 12 小节」，结果人组结尾常停在乐句半途、没有终止式，
    而机组是完整生成的——评审员看结尾就能分辨。所以这里只收**整首**成立的作品：

      * 4 声部；
      * 无反复记号（机组不会写反复，谱面上出现就是暗号）；
      * 剥掉弱起后剩余小节全部满拍；
      * 长度落在 [BAR_MIN, BAR_MAX]；
      * 末和弦是本调原位主和弦（完整终止，结尾站得住）。
    """
    from music21 import bar, corpus, meter
    out = []
    for bwv in candidates:
        if len(out) >= limit:
            break
        try:
            sc = corpus.parse(f'bach/{bwv}')
        except Exception:
            continue
        if len(list(sc.parts)) != 4:
            continue
        if len(list(sc.recurse().getElementsByClass(bar.Repeat))) > 0:
            continue
        ks_orig = _key_sig_sharps(sc)
        # 注意：KeySignature.asKey() 恒按大调解释，调式必须另问 analyze('key')
        try:
            kk = sc.analyze('key')
            kmode, kpc = kk.mode, kk.tonic.pitchClass
        except Exception:
            continue
        ex = strip_partial_bars(sc)
        ts = sc.recurse().getElementsByClass(meter.TimeSignature)
        exp = ts[0].barDuration.quarterLength if ts else 4.0
        ms = list(ex.parts[0].getElementsByClass('Measure'))
        if not (BAR_MIN <= len(ms) <= BAR_MAX):
            continue
        if any(m.duration.quarterLength < exp - 1e-6 for m in ms):
            continue                       # 中间/末尾有残拍 -> 看着像被截断
        info = completeness(ex, ks_orig, kmode, tonic_pc=kpc)
        if not info['ok']:
            continue                       # 结尾不解决 -> 同样像被截断
        out.append({'bwv': bwv, 'bars': len(ms), 'mode': kmode,
                    'sharps': ks_orig, 'tonic_pc': kpc,
                    'completeness': info})
    return out


def gen_human(idx, slot_mode, slot_sharps, pool, used, target_bars=None):
    """取一整首训练外的众赞歌（**不截断**），转调到指定调。

    ``target_bars`` 用于长度配平：优先挑长度最接近的一首，让两组长度分布一致。
    """
    from music21 import corpus
    rnd = random.Random(9000 + idx)
    cands = [c for c in pool if c['mode'] == slot_mode and c['bwv'] not in used]
    if not cands:
        return None
    cands.sort(key=lambda c: (abs(c['bars'] - target_bars) if target_bars else 0,
                              rnd.random()))
    # 目标主音：按「调号 → 调 → 主音」推，不能拿调号直接乘 7（小调会偏）
    from music21 import key as m21key
    tgt_pc = m21key.KeySignature(slot_sharps).asKey(slot_mode).tonic.pitchClass
    for c in cands[:8]:
        bwv = c['bwv']
        try:
            ex = strip_partial_bars(corpus.parse(f'bach/{bwv}'))
        except Exception:
            continue
        # 按主音差转调，取最小移动（-6..+6 半音）
        delta = (tgt_pc - c['tonic_pc']) % 12
        if delta > 6:
            delta -= 12
        try:
            ex = ex.transpose(delta) if delta else ex
        except Exception:
            continue
        # 转调后复核：判据必须用转调后的实际调号，否则人组会混进结尾不解决的曲子
        info = completeness(ex, _key_sig_sharps(ex), slot_mode,
                           tonic_pc=(c['tonic_pc'] + delta) % 12)
        if not info['ok']:
            continue
        used.add(bwv)
        return {
            'code': None, 'truth': 'human', 'seed': None,
            'mode': slot_mode, 'sharps': _key_sig_sharps(ex),
            'tonic_pc': (c['tonic_pc'] + delta) % 12,
            'source': bwv, 'transpose': int(delta),
            'measures': '整首',
            'bars': c['bars'], 'voices': 4,
            'completeness': info,
            'score': ex,
        }
    return None


# --------------------------------------------------------------- 统一处理
def normalize(sc):
    """剥掉一切会泄露来源的表面痕迹，统一记谱规格。

    关键点：机器组带 ♩=72 的节拍机标记而人组没有，这本身就是一个
    「一秒识破」的暗号，必须两边都剥。同理剥掉歌词、fermata、标题。
    另外剥掉反复记号：内核从不写反复，语料里有，谱面上出现就是暗号。
    """
    from music21 import bar, clef, dynamics, expressions, tempo
    for rp in list(sc.recurse().getElementsByClass(bar.Repeat)):
        try:
            rp.activeSite.remove(rp)
        except Exception:
            pass
    # 速度 / 文本表情 / 力度（两组统一剥，防止「有没有节拍机或力度记号」泄露来源：
    # 机器内核会写 mf/pp，music21 语料里的众赞歌没有）
    for obj in list(sc.recurse().getElementsByClass(
            (tempo.MetronomeMark, tempo.TempoIndication,
             expressions.TextExpression,
             dynamics.Dynamic, dynamics.DynamicWedge))):
        try:
            obj.activeSite.remove(obj)
        except Exception:
            pass
    for i, part in enumerate(sc.parts):
        part.id = VOICE_NAMES[i]
        part.partName = VOICE_NAMES[i]
        part.partAbbreviation = VOICE_NAMES[i][:3]
        want = (clef.BassClef() if i == 3
                else clef.Treble8vbClef() if i == 2
                else clef.TrebleClef())
        for m in part.getElementsByClass('Measure'):
            m.clef = want
            break
        for n in part.recurse().notes:
            if getattr(n, 'expressions', None):
                n.expressions = []
            if getattr(n, 'lyrics', None):
                n.lyrics = []
    if sc.metadata is not None:
        sc.metadata.title = ''
        sc.metadata.composer = ''
        sc.metadata.movementName = ''


def set_uniform_timbre(sc, program=19):
    """给四个声部写入同一个 GM 音色，避免落到 GM 默认 program 0（大钢琴）。

    不写 PROGRAM_CHANGE 时所有声部共用一个通道的钢琴音色，而评分维度里
    「声部进行正确性」要靠听辨，钢琴上四个声部几乎分不开。两组用同一音色，
    盲性不受影响（音色不是线索，音色差异才是）。
    """
    from music21 import instrument
    try:
        ins = instrument.instrumentFromMidiProgram(program)
    except Exception:
        ins = instrument.ChurchOrgan()
    for part in sc.parts:
        # 清掉已有乐器声明，避免机器组/人组各带一个默认值造成音色不一致
        for old in list(part.recurse().getElementsByClass(instrument.Instrument)):
            try:
                old.activeSite.remove(old)
            except Exception:
                pass
        part.insert(0, ins)
    return sc


# --------------------------------------------------------------- 渲染
def _printable_svg(svg):
    """verovio 的 CSS（stroke:currentColor）在 Chrome 打印管线里不生效，
    必须显式补描边/填充并去掉 <style>，否则整页空白。"""
    import re
    svg = re.sub(r'<style.*?</style>', '', svg, flags=re.S)
    svg = re.sub(r'<path (d="[^"]+" stroke-width="\d+" />)',
                 r'<path fill="none" stroke="black" \1', svg)
    svg = re.sub(r'(<g id="E[0-9A-F]+-[^"]*">\s*)<path ',
                 r'\1<path fill="black" ', svg)
    return svg


def render_pdf(items, pdf_path):
    import verovio
    tk = verovio.toolkit()
    tk.setOptions({
        'adjustPageHeight': True, 'breaks': 'auto', 'scale': 40,
        'pageWidth': 1900, 'pageMarginTop': 4, 'pageMarginBottom': 4,
        'pageMarginLeft': 8, 'pageMarginRight': 8,
        'header': 'none', 'footer': 'none', 'font': 'Bravura',
    })
    parts = ["""<!doctype html><meta charset="utf-8"><style>
@page { size: A4; margin: 12mm 12mm; }
body { font-family: "Songti SC","STSong",serif; }
.ex { break-inside: avoid; page-break-inside: avoid; margin-bottom: 8px; }
.hd { font-size: 15px; font-weight: 600; letter-spacing: 1px;
      border-left: 4px solid #333; padding-left: 8px; margin-bottom: 2px; }
svg { width: 100%; height: auto; display: block; margin: 0 auto; }
</style><body>"""]
    for it in items:
        tmp = it['score'].write('musicxml')
        tk.loadData(open(tmp, encoding='utf-8').read())
        parts.append(f'<div class="ex"><div class="hd">{it["code"]}</div>'
                     f'{_printable_svg(tk.renderToSVG(1, False))}</div>')
    parts.append('</body>')
    html = ''.join(parts)
    html_path = pdf_path.replace('.pdf', '.html')
    open(html_path, 'w', encoding='utf-8').write(html)
    if os.path.exists(CHROME):
        subprocess.run([CHROME, '--headless', '--disable-gpu', '--no-sandbox',
                        '--no-pdf-header-footer',
                        '--run-all-compositor-stages-before-draw',
                        '--virtual-time-budget=25000',
                        f'--print-to-pdf={pdf_path}', html_path],
                       capture_output=True)
    else:
        print('未找到 Chrome，跳过 PDF 合成')
    return html_path


# --------------------------------------------------------------- 量表
def ensure_chorale_dims():
    """把众赞歌专项维度写回共享量表（单一来源），已存在则不动。"""
    import yaml
    with open(RUBRIC, encoding='utf-8') as f:
        rb = yaml.safe_load(f)
    hard = rb.setdefault('pack_hard', {})
    if 'chorale' not in hard:
        hard['chorale'] = CHORALE_HARD_DIMS
        with open(RUBRIC, 'w', encoding='utf-8') as f:
            yaml.safe_dump(rb, f, allow_unicode=True, sort_keys=False)
    return rb


def rubric_dims(rb):
    common = [(d['id'], d['label']) for d in rb['common_dimensions']]
    hard = [(d['id'], d['label'])
            for d in (rb.get('pack_hard', {}).get('chorale') or [])]
    return common + hard


# --------------------------------------------------------------- Excel
def write_xlsx(items, dims, rb, out_path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    hdr_fill = PatternFill('solid', fgColor='1F3864')
    hdr_font = Font(color='FFFFFF', bold=True, size=10)
    alt_fill = PatternFill('solid', fgColor='F2F2F2')
    thin = Side(style='thin', color='BFBFBF')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical='top')
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    n_dim = len(dims)
    wb = Workbook()
    wb.remove(wb.active)

    # ---- 说明
    ws = wb.create_sheet('填写说明')
    ws.column_dimensions['A'].width = 4
    ws.column_dimensions['B'].width = 24
    ws.column_dimensions['C'].width = 96
    rows = [
        ('title', '生成质量双盲评审 · 一页说明'),
        ('h', '你在评什么'),
        ('kv', '评审对象', f'runs/exploratory/blind_review/生成质量盲评乐谱.pdf 里的 {len(items)} 个匿名片段，'
                          f'每首都是**完整的**四声部众赞歌（SATB、无歌词），'
                          f'长度 {min(x["bars"] for x in items)}–{max(x["bars"] for x in items)} 小节不等'
                          f'（长度不构成线索，两组的长度分布相同）。'),
        ('kv', '混合来源', '这批片段里有本系统生成��作品，也有人工作品（同体裁、同风格的时代作品），'
                          '**配比不公布**。编号 A01–Axx 与来源无关，次序已随机打乱。'),
        ('kv', '你要做两件事', f'① 按 {n_dim} 个维度给每个片段打 1–5 分；② 猜它是不是人工作品，并给置信度。'),
        ('kv', '时间', '每条约 2–4 分钟，全程约 1 小时。可分工：按编号段拆分，但每人必须独立盲评。'),
        ('kv', '配套音频', 'runs/exploratory/blind_review/audio/ 下有同名 MIDI/WAV，可以先听再看谱，或只看谱。'
                          '两种模态各评一次会更稳。'),
        ('h', f'{n_dim} 个打分维度（1–5 分）'),
    ]
    for i, (dim_id, label) in enumerate(dims, 1):
        rows.append(('kv', f'D{i} {label}', DIM_ANCHOR.get(dim_id, '1=差　3=一般　5=好')))
    rows += [
        ('h', '猜来源'),
        ('kv', '是否人工作品', '人工作品 / 机器作品 / 不确定。为了使这一列有信息量，'
                              '没把握就选「不确定」，不要每题都猜。'),
        ('kv', '置信度', '1=纯蒙　3=有点感觉　5=非常有把握。'),
        ('h', '四条规矩'),
        ('kv', '① 不要试图查出处', '不要去搜这段旋律、不要翻乐谱集、不要问别人。凭耳朵和眼睛。'),
        ('kv', '② 备注必须引用谱面', '写明具体的成败之处，例如「第 5–6 小节女高与男高平行五度」'
                                     '「第 8 小节 V–i 但女高音落 5，不是完全正格终止」。'
                                     '只写「听起来怪」无效。'),
        ('kv', '③ 质量与来源要分开', '打分评的是音乐本身好不好；猜来源评的是像不像人写的。'
                                     '两件事分开——机器作品也可能写得很好。'),
        ('kv', '④ 别被编排痕迹误导', '所有片段已统一去掉标题、延长记号、歌词、反复记号，'
                                      '统一了谱号、声部名与音色。'
                                      '这些痕迹不能用来判断来源。'),
        ('kv', '⑤ 每首都是完整的一首', '没有中途截断的片段：开头是第一完整小节，'
                                        '结尾停在原位主和弦，四声部都唱到最后。'
                                        '「看不完整」不是判断来源的依据。'),
        ('h', '多人怎么合'),
        ('kv', '流程', f'两人各自盲评全部 {len(items)} 条 → 逐维度算平均绝对差；'
                       '差值 >1 分的维度交换理由后重评；仍不一致则取均值并在备注标注分歧。'),
        ('kv', '怎么读结果', '① 机器组的七个维度均分是否显著低于人类组；'
                             '② 「猜来源」的命中率是否高于随机。'
                             '若两者都接近随机，说明该体裁上机器产出已难以与人工作品区分。'),
        ('kv', '交付', '填好的 生成质量盲评表.xlsx（每行必填；备注列不得留空）。'),
    ]
    r = 1
    for kind, *rest in rows:
        if kind == 'title':
            ws.cell(row=r, column=2, value=rest[0]).font = Font(bold=True, size=14)
            r += 2
        elif kind == 'h':
            ws.cell(row=r, column=2, value=rest[0]).font = Font(bold=True, size=11, color='1F3864')
            r += 1
        else:
            ws.cell(row=r, column=2, value=rest[0]).font = Font(bold=True, size=10)
            c = ws.cell(row=r, column=3, value=rest[1])
            c.alignment = wrap
            ws.row_dimensions[r].height = 30
            r += 1
    ws.sheet_view.showGridLines = False

    # ---- 评分表
    ws = wb.create_sheet('评分表')
    heads = [('编号', 8)] + [(f'D{i} {lab}', 11) for i, (_, lab) in enumerate(dims, 1)]
    heads += [('平均分', 8), ('是否人工作品', 13), ('置信度', 8),
              ('备注（必须引用谱面具体小节）', 52)]
    for j, (name, w) in enumerate(heads, 1):
        c = ws.cell(row=1, column=j, value=name)
        c.fill, c.font, c.alignment, c.border = hdr_fill, hdr_font, center, border
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.row_dimensions[1].height = 40
    ws.freeze_panes = 'B2'
    for i, it in enumerate(items):
        r = i + 2
        for j, v in enumerate([it['code']] + [None] * (n_dim + 3), 1):
            c = ws.cell(row=r, column=j, value=v)
            c.border = border
            c.alignment = center
            if i % 2:
                c.fill = alt_fill
        avg_col = get_column_letter(n_dim + 2)
        first, last = 'B', get_column_letter(1 + n_dim)
        c = ws.cell(row=r, column=n_dim + 2,
                    value=f'=IF(COUNT({first}{r}:{last}{r})={n_dim},'
                          f'ROUND(AVERAGE({first}{r}:{last}{r}),2),"")')
        c.border, c.alignment = border, center
        ws.row_dimensions[r].height = 30
    last_row = len(items) + 1
    for col in range(2, 2 + n_dim):
        letter = get_column_letter(col)
        v = DataValidation(type='list', formula1='"1,2,3,4,5"',
                           allow_blank=True, showDropDown=False)
        ws.add_data_validation(v)
        v.add(f'{letter}2:{letter}{last_row}')
    author_col = get_column_letter(n_dim + 3)
    conf_col = get_column_letter(n_dim + 4)
    v = DataValidation(type='list', formula1='"%s"' % ','.join(AUTHOR_OPTS),
                       allow_blank=True, showDropDown=False)
    ws.add_data_validation(v)
    v.add(f'{author_col}2:{author_col}{last_row}')
    v = DataValidation(type='list', formula1='"1,2,3,4,5"',
                       allow_blank=True, showDropDown=False)
    ws.add_data_validation(v)
    v.add(f'{conf_col}2:{conf_col}{last_row}')
    ws.sheet_view.showGridLines = False

    # ---- 汇总
    ws = wb.create_sheet('汇总')
    ws.column_dimensions['A'].width = 28
    ws.column_dimensions['B'].width = 14
    for j, t in enumerate(('指标', '值'), 1):
        c = ws.cell(row=1, column=j, value=t)
        c.font = Font(bold=True, size=11)
        c.border, c.alignment = border, center
    n = len(items)
    avg_col = get_column_letter(n_dim + 2)
    author_col = get_column_letter(n_dim + 3)
    conf_col = get_column_letter(n_dim + 4)
    rows = [
        ('片段总数', n),
        ('打分维度数', n_dim),
        ('通过线（量表版本 %s）' % rb.get('version'), rb.get('pass_avg')),
        ('平均分 ≥ 通过线的片段数',
         f'=COUNTIF(评分表!{avg_col}2:{avg_col}{n+1},">="&B4)'),
        ('猜「人工作品」的片段数',
         f'=COUNTIF(评分表!{author_col}2:{author_col}{n+1},"人工作品")'),
        ('猜「不确定」的片段数',
         f'=COUNTIF(评分表!{author_col}2:{author_col}{n+1},"不确定")'),
        ('平均置信度',
         f'=ROUND(AVERAGE(评分表!{conf_col}2:{conf_col}{n+1}),2)'),
    ]
    r = 2
    for k, v in rows:
        ws.cell(row=r, column=1, value=k).border = border
        c = ws.cell(row=r, column=2, value=v)
        c.border, c.alignment = border, center
        r += 1
    r += 1
    ws.cell(row=r, column=1, value='各维度均值').font = Font(bold=True, size=11)
    r += 1
    for i, (_dim, lab) in enumerate(dims):
        col = get_column_letter(2 + i)
        ws.cell(row=r, column=1, value=lab).border = border
        c = ws.cell(row=r, column=2,
                    value=f'=ROUND(AVERAGE(评分表!{col}2:{col}{n+1}),2)')
        c.border, c.alignment = border, center
        r += 1
    r += 1
    c = ws.cell(row=r, column=1,
                value='说明：本表只做描述统计。要回答「机器是否达到人类水平」，'
                      '还需开封 _答案_评审后开封.json 后按来源分两组做配对检验。')
    c.font = Font(size=10, color='666666')
    ws.sheet_view.showGridLines = False

    wb.save(out_path)
    return out_path


# --------------------------------------------------------------- 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description='生成质量双盲评审包')
    ap.add_argument('--machine', type=int, default=12)
    ap.add_argument('--human', type=int, default=8)
    ap.add_argument('--no-wav', action='store_true')
    a = ap.parse_args(argv)

    os.makedirs(AUDIO_DIR, exist_ok=True)
    rb = ensure_chorale_dims()

    slots = make_slots(a.machine, a.human)
    print('调池计划（前 %d 机器 / 后 %d 人类）：%s' % (a.machine, a.human, slots))

    items = []
    # 先挑人组：整首的长度决定机组的长度（长度配平），顺序不能反。
    print('挑选训练外的人类对照组（整首、完整终止、无反复）…')
    pool = scan_human(_pick_human_candidates())
    print('  可用完整曲子 %d 首：major %d / minor %d，长度 %s'
          % (len(pool),
             sum(1 for c in pool if c['mode'] == 'major'),
             sum(1 for c in pool if c['mode'] == 'minor'),
             sorted(c['bars'] for c in pool)))
    human_bars = []
    used = set()
    for i in range(a.human):
        mode, sharps = slots[a.machine + i]
        it = gen_human(i, mode, sharps, pool, used)
        if it is None:
            print('  !! 无符合条件的对照组（%s, %+d），跳过' % (mode, sharps))
            continue
        items.append(it)
        human_bars.append(it['bars'])
        print('  human   %02d  %-5s sharps=%+d source=%s 小节=%d transp=%+d'
              % (i, mode, sharps, it['source'], it['bars'], it['transpose']))

    if not human_bars:
        human_bars = [12]                # 兜底：人组为空时退回固定长度
    print('生成机器作品（/compose-grammar 内核，确定性；长度跟随人组 %s）…'
          % human_bars)
    machine_items = []
    for i in range(a.machine):
        mode, sharps = slots[i]
        it = gen_machine(i, mode, sharps, human_bars[i % len(human_bars)])
        machine_items.append(it)
        print('  machine %02d  %-5s sharps=%+d seed=%s bars=%d(req %d)'
              % (i, mode, sharps, it['seed'], it['bars'], it['requested_bars']))
    items = machine_items + items

    rnd = random.Random(SHUFFLE_SEED)
    codes = ['A%02d' % (k + 1) for k in range(len(items))]
    shuffled = codes[:]
    rnd.shuffle(shuffled)
    for it, code in zip(items, shuffled):
        it['code'] = code
    items.sort(key=lambda x: x['code'])
    print('片段数 %d，匿名编号已随机绑定' % len(items))

    # 完整性自检：两组都必须「是一首完整的曲子」，否则评审员靠结尾就能识破。
    print('完整性自检（缺拍小节 / 末和弦=本调原位主和弦 / 四声部唱到末尾）：')
    bad = []
    for it in items:
        normalize(it['score'])
        set_uniform_timbre(it['score'], TIMBRE_PROGRAM)
        it['sharps'] = _key_sig_sharps(it['score'])
        info = completeness(it['score'], it['sharps'], it['mode'],
                           tonic_pc=it.get('tonic_pc'))
        it['completeness'] = info
        if not info['ok']:
            bad.append(it['code'])
        print('  %s %-4s %-7s bars=%-3d short=%-6s root=%-5s bass=%-5s voices=%-5s'
              % ('OK ' if info['ok'] else 'BAD', it['code'], it['truth'],
                 info['measures'], info['short_bars'] or '-',
                 info['final_root_tonic'], info['final_bass_tonic'],
                 info['voices_to_end']))
    print('  通过 %d/%d%s' % (len(items) - len(bad), len(items),
                             '，未通过：' + ','.join(bad) if bad else ''))

    for it in items:
        try:
            mid = os.path.join(AUDIO_DIR, it['code'] + '.mid')
            it['score'].write('midi', fp=mid)
            if not a.no_wav:
                from render_enhance import render_to_wav, preset
                wav = os.path.join(AUDIO_DIR, it['code'] + '.wav')
                info = render_to_wav(mid, wav, preset())
                if not info.get('ok'):
                    print('   ! %s WAV 渲染失败：%s' % (it['code'], info.get('error')))
        except Exception as e:
            print('   ! %s 音频导出失败：%s' % (it['code'], e))

    pdf_path = os.path.join(OUT_DIR, '生成质量盲评乐谱.pdf')
    render_pdf(items, pdf_path)
    print('→ %s (%.1f KB)' % (pdf_path, os.path.getsize(pdf_path) / 1024))

    try:
        from music21 import stream
        comb = None
        for it in items:
            # MusicXML 导出写的是 partName，不是 id——编号必须落在 partName 上，
            # 否则 20 首在合并谱里全叫 Soprano/Alto/…，事后无法按 Axx 拆开。
            for pr in it['score'].parts:
                pr.partName = f'{it["code"]}_{pr.partName}'
                pr.partAbbreviation = pr.partName
            if comb is None:
                comb = stream.Score(it['score'].parts)
            else:
                for pr in it['score'].parts:
                    comb.append(pr)
        comb.write('musicxml', fp=os.path.join(OUT_DIR, '生成质量盲评乐谱.musicxml'))
        print('→ %s' % os.path.join(OUT_DIR, '生成质量盲评乐谱.musicxml'))
    except Exception as e:
        print('   ! MusicXML 合并失败：%s' % e)

    key_path = os.path.join(OUT_DIR, '_答案_评审后开封.json')
    json.dump({
        'note': '评审完成后方可开封。machine=本仓库 /compose-grammar 内核生成'
                '（seed 见 items[].seed，可用 blind_review_export.py 重跑复现）；'
                'human=music21 bach 语料中未参与训练的作品（bwv 编号见 items[].source）。',
        'shuffle_seed': SHUFFLE_SEED,
        'items': [{k: v for k, v in x.items() if k != 'score'} for x in items],
    }, open(key_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('→ %s（**评审后再开封**）' % key_path)

    dims = rubric_dims(rb)
    xlsx = write_xlsx(items, dims, rb, os.path.join(OUT_DIR, '生成质量盲评表.xlsx'))
    print('→ %s（%d 行 × %d 维度）' % (xlsx, len(items), len(dims)))
    return items


if __name__ == '__main__':
    main()
