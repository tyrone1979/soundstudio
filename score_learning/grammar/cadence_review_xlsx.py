# -*- coding: utf-8 -*-
"""生成终止式人工评审 Excel 工作簿。

读 runs/experiments/cadence_occurrences.json（由 cadence_score_export 产出），
写 docs/cadence_review/终止式评审表.xlsx：
  Sheet1 填写说明   一页看完的操作规则
  Sheet2 评审表     每行一条待裁定的 C 规则，下拉框打分
  Sheet3 谱例索引   每次真实出现的定位（作品 / 调 / 句 / 小节）

条数由 cadence_occurrences.json 决定（判定表升级后会变，不要写死）。
2026-10-04 判定表补入副调终止三类后，待裁定由 35 条降到 12 条。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

SRC = os.path.join(ROOT, 'runs', 'experiments', 'cadence_occurrences.json')
OUT = os.path.join(ROOT, 'docs', 'cadence_review', '终止式评审表.xlsx')

HDR_FILL = PatternFill('solid', fgColor='1F3864')
HDR_FONT = Font(color='FFFFFF', bold=True, size=10)
TIP_FILL = PatternFill('solid', fgColor='FFF2CC')
ALT_FILL = PatternFill('solid', fgColor='F2F2F2')
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical='top')
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)

D4_OPTS = ['REAL_CADENCE', 'SUSPECT-低', 'SUSPECT-中', 'SUSPECT-高', 'NOT_CADENCE']
VERDICT_OPTS = ['KEEP_OTHER', 'RECODE_PAC', 'RECODE_IAC', 'RECODE_PC',
                'RECODE_HC', 'RECODE_DC', 'RECODE_SECONDARY', 'RECODE_TONIC',
                'MODULATED', 'NOT_CADENCE', 'UNDETERMINED']
GRADE_OPTS = ['正确', '部分正确', '错误', '无法判定']
GRADE3_OPTS = ['部分正确', '错误', '无法判定']
CONF_OPTS = ['高', '中', '低']
YN_OPTS = ['是', '否']

HEADERS = [
    ('C 编号', 9), ('调式', 6), ('终止窗口和声', 20), ('女高音落音', 10),
    ('出现次数 n', 9), ('谱例位置（乐谱 PDF 中的标签）', 46),
    ('① 是否真实终止位置', 15), ('② 终止式类型判定', 17),
    ('③ 类型等级', 11), ('④ 和声/低音匹配', 13), ('⑤ 位置合理性', 11),
    ('置信度', 8), ('判定依据（必须引用谱面，如「低音 2→4→6 上行」）', 44),
    ('备注 / 触发的规则号', 26), ('需补谱例?', 9),
]


def dv(ws, opts, col, first, last):
    v = DataValidation(type='list', formula1='"%s"' % ','.join(opts),
                       allow_blank=True, showDropDown=False)
    ws.add_data_validation(v)
    v.add('%s%d:%s%d' % (col, first, col, last))


def _pitfalls(data):
    """「常见的坑」只列本批数据里实际出现的情形，条数现算。"""
    last = {c: d['roman'].split(' – ')[-1] for c, d in data.items()}
    minor_iii = [c for c, d in data.items()
                 if d['form']['mode'] == 'minor' and last[c].startswith('III')]
    sec = [c for c in data if last[c].startswith('V/')]
    thin = [c for c, d in data.items() if d['form']['n'] <= 2]
    rows = []
    if minor_iii:
        rows.append(('转调', '小调终止在 III（关系大调主和弦）的有 %d 条（%s）—— 可能是转到关系大调的终止，'
                     '是则判 MODULATED 并写新调，别硬套原调。' % (len(minor_iii), '、'.join(minor_iii))))
    if sec:
        rows.append(('副属没落地', '终止在副属和弦上、目标和弦没出现的有 %d 条（%s）—— 不能判正格/离调终止，'
                     '判 KEEP_OTHER 并备注「副属悬空」。' % (len(sec), '、'.join(sec))))
    rows.append(('属七结尾', '判定程序只认大三和弦的属，属七结尾会掉进「其他」—— 属七结尾可以判半终止 HC。'))
    if thin:
        rows.append(('样本太薄', '%d 条只有 n=2 次，最高只能给「部分正确」，除非你在依据里说明为什么 2 次足够。'
                     % len(thin)))
    by_skel = {}
    for c, d in data.items():
        by_skel.setdefault((d['form']['mode'], d['roman']), []).append(c)
    pairs = ['/'.join(v) for v in by_skel.values() if len(v) > 1]
    return [('kv', '%s %s' % ('①②③④⑤'[i], a), b) for i, (a, b) in enumerate(rows)], pairs


def sheet_intro(wb, data):
    ws = wb.create_sheet('填写说明')
    ws.column_dimensions['A'].width = 4
    ws.column_dimensions['B'].width = 26
    ws.column_dimensions['C'].width = 92
    n_rules = len(data)
    n_occ = sum(len(d['occurrences']) for d in data.values())
    pit, pairs = _pitfalls(data)
    rows = [
        ('title', '终止式人工评审 · 一页说明'),
        ('h', '你要做的事'),
        ('kv', '评审对象', 'docs/cadence_review/终止式评审乐谱.pdf 里的 %d 个谱例'
                       '（%d 条规则 × 各自全部真实出现）。' % (n_occ, n_rules)),
        ('kv', '试听', 'midi/ 文件夹里每个谱例一个 MIDI，文件名 <C编号>_<第几次>.mid'
                     '（如 C108_1.mid，♩=60），与乐谱标题【C 编号 · 第 i/N 次】对应；判定以谱面为准。'),
        ('kv', '操作', '按 C 编号在乐谱里找到该条的全部谱例 → 看谱 → 在「评审表」为该 C 编号填一行。'),
        ('kv', '时间', '每条约 2–5 分钟，全程约 %d 分钟。' % (n_rules * 5)),
        ('h', '看谱要点'),
        ('kv', '谱例标题', '【C 编号 · 第 i/N 次】作品 · 调 · 拍号 · 第几句 · 小节 · 和声骨架。'),
        ('kv', '终止窗口', '末尾 3 个和弦（低音谱下方标了罗马数字 ① ② ③），最后一带延长记号的音就是句尾。'),
        ('kv', '女高音落音', '标签里给出的两个数字是相对主音的音级（1=主音，5=属音），只到音级、不含八度。'),
        ('h', '怎么填（评审表 7 个下拉列）'),
        ('kv', '① 是否真实终止位置', 'REAL_CADENCE=正常句尾 / SUSPECT-低中高=存疑 / NOT_CADENCE=根本不是终止（如三拍同和弦、延长记号误读）。'),
        ('kv', '② 终止式类型判定', '给这条规则一个独立结论：RECODE_xxx=改判为该类型；KEEP_OTHER=确认「其他终止」是对的；MODULATED=已转调（在备注写新调）；NOT_CADENCE / UNDETERMINED。'),
        ('kv', '③④⑤ 三个等级', '一律四选一：正确 / 部分正确 / 错误 / 无法判定。⑤ 位置一栏**没有「正确」**（数据里本来就没有位置标注，只能对照先验）。'),
        ('kv', '置信度', '高=三条准入都满足；中=满足两条或有轻度存疑；低=任一「无法判定」或 n=2 且无特别理由。'),
        ('kv', '判定依据', '**必须引用谱面事实**，例如「低音 6→4→3 下行、女高音落 3、非属→主」。只写「听起来像」无效。'),
        ('h', '常见的坑'),
        *pit,
        ('h', '多人怎么合'),
        ('kv', '流程', '两人各自独立评完全部 %d 条 → 对照；同族内不同（如 PAC vs IAC）取约束更松者；'
                     '跨族不同（如 HC vs NOT_CADENCE）交第三人仲裁。' % n_rules),
        *([('kv', '一致性锚', '%s 和声骨架相同，判定必须自洽，不一致要写明理由。' % '、'.join(pairs))]
          if pairs else []),
        ('h', '交付'),
        ('kv', '产出', '填好的 终止式评审表.xlsx（评审表 sheet 每行必填，依据列不得留空）。'),
    ]
    r = 1
    for item in rows:
        if item[0] == 'title':
            c = ws.cell(row=r, column=2, value=item[1])
            c.font = Font(bold=True, size=14)
            r += 2
        elif item[0] == 'h':
            c = ws.cell(row=r, column=2, value=item[1])
            c.font = Font(bold=True, size=11, color='1F3864')
            r += 1
        else:
            ws.cell(row=r, column=2, value=item[1]).font = Font(bold=True, size=10)
            c = ws.cell(row=r, column=3, value=item[2])
            c.alignment = WRAP
            ws.row_dimensions[r].height = 30
            r += 1
    ws.sheet_view.showGridLines = False
    return ws


def sheet_review(wb, data):
    ws = wb.create_sheet('评审表')
    for j, (name, width) in enumerate(HEADERS, 1):
        c = ws.cell(row=1, column=j, value=name)
        c.fill, c.font, c.alignment, c.border = HDR_FILL, HDR_FONT, CENTER, BORDER
        ws.column_dimensions[get_column_letter(j)].width = width
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = 'C2'

    r = 2
    for cid in sorted(data, key=lambda x: int(x[1:])):
        d = data[cid]
        form, occs = d['form'], d['occurrences']
        zh = '大调' if form['mode'] == 'major' else '小调'
        romans = d.get('roman') or ''
        locs = '；'.join('第%d次: %s %s%s 第%s句 %s小节' % (
            i, o['piece'], o['tonic'],
            '大调' if o['mode'] == 'major' else '小调',
            o['phrase_kth'],
            '第%d' % o['m_start'] if o['m_start'] == o['m_end']
            else '第%d–%d' % (o['m_start'], o['m_end']))
            for i, o in enumerate(occs, 1)) or '（未还原到谱例）'
        vals = [cid, zh, romans,
                '%s → %s' % (form['soprano'][0], form['soprano'][1]),
                form['n'], locs, None, None, None, None, None, None, None, None, None]
        for j, v in enumerate(vals, 1):
            c = ws.cell(row=r, column=j, value=v)
            c.border = BORDER
            c.alignment = WRAP if j in (3, 6, 13, 14) else CENTER
            if r % 2 == 0:
                c.fill = ALT_FILL
        ws.row_dimensions[r].height = 44
        r += 1
    last = r - 1
    dv(ws, D4_OPTS, 'G', 2, last)
    dv(ws, VERDICT_OPTS, 'H', 2, last)
    dv(ws, GRADE_OPTS, 'I', 2, last)
    dv(ws, GRADE_OPTS, 'J', 2, last)
    dv(ws, GRADE3_OPTS, 'K', 2, last)
    dv(ws, CONF_OPTS, 'L', 2, last)
    dv(ws, YN_OPTS, 'O', 2, last)
    ws.sheet_view.showGridLines = False
    return ws


def sheet_index(wb, data):
    ws = wb.create_sheet('谱例索引')
    cols = [('C 编号', 9), ('第几次', 7), ('作品', 12), ('调', 10), ('拍号', 7),
            ('第几句/共', 11), ('起止小节', 12), ('乐句拍数', 9),
            ('终止窗口和声', 20), ('女高音落音', 11)]
    for j, (name, width) in enumerate(cols, 1):
        c = ws.cell(row=1, column=j, value=name)
        c.fill, c.font, c.alignment, c.border = HDR_FILL, HDR_FONT, CENTER, BORDER
        ws.column_dimensions[get_column_letter(j)].width = width
    r = 2
    for cid in sorted(data, key=lambda x: int(x[1:])):
        d = data[cid]
        form = d['form']
        zh = '大调' if form['mode'] == 'major' else '小调'
        for i, o in enumerate(d['occurrences'], 1):
            vals = [cid, i, o['piece'],
                    '%s%s' % (o['tonic'], zh), o['meter'],
                    '%d/%d' % (o['phrase_kth'], o['phrase_total']),
                    '第%d' % o['m_start'] if o['m_start'] == o['m_end']
                    else '第%d–%d' % (o['m_start'], o['m_end']),
                    o['end'] - o['start'] + 1, d.get('roman', ''),
                    '%s → %s' % (form['soprano'][0], form['soprano'][1])]
            for j, v in enumerate(vals, 1):
                c = ws.cell(row=r, column=j, value=v)
                c.border = BORDER
                c.alignment = CENTER
                if r % 2 == 0:
                    c.fill = ALT_FILL
            r += 1
    ws.freeze_panes = 'A2'
    ws.sheet_view.showGridLines = False
    return ws


def main():
    data = json.load(open(SRC, encoding='utf-8'))
    # 补充罗马数字骨架（与乐谱标签一致）
    from score_learning.grammar.theory import parse_chord_key, roman
    for cid, d in data.items():
        f = d['form']
        d['roman'] = ' – '.join(roman(parse_chord_key(c), f['mode'])
                                for c in f['chords'])

    wb = Workbook()
    wb.remove(wb.active)
    sheet_intro(wb, data)
    sheet_review(wb, data)
    sheet_index(wb, data)
    wb.save(OUT)
    print('→ %s（%d 条待裁定规则 / %d 次出现索引）'
          % (OUT, len(data), sum(len(d['occurrences']) for d in data.values())))


if __name__ == '__main__':
    main()
