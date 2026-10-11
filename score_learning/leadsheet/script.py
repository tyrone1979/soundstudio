# -*- coding: utf-8 -*-
"""规则脚本读写：<风格包>/grammar.yaml（机器执行）+ grammar.md（人读的中文说明）。"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import yaml

from .analyze import ROOT, STYLES

_CLS_ZH = {'CT': '和弦音', 'ST': '调内非和弦音', 'CH': '半音'}
_ST_ZH = {'S': '强拍', 'M': '次强拍（其余拍首）', 'W': '拍内弱位置'}
_RCLS_ZH = {'mid': '段中小节', 'cad': '乐句末小节（段内第 4、8 小节）', 'final': '全曲末小节'}
_DEG = {0: '1', 1: 'b2', 2: '2', 3: 'b3', 4: '3', 5: '4', 6: '#4', 7: '5', 8: 'b6', 9: '6',
        10: 'b7', 11: '7'}


def paths(style: str):
    fam, pack = STYLES[style]['pack']
    d = os.path.join(ROOT, 'skills', 'composition', 'genre', 'packs', fam, pack)
    return os.path.join(d, 'grammar.yaml'), os.path.join(d, 'grammar.md')


_HEADER = ('# 由 score_learning.leadsheet 从语料自动学习生成；作曲器只读本文件。\n'
           '# 中文说明见同目录 grammar.md。\n')


def _dump(script: Dict[str, Any]) -> str:
    return _HEADER + yaml.safe_dump(script, allow_unicode=True, sort_keys=False, width=110)


def save(script: Dict[str, Any], style: str) -> str:
    yml, md = paths(style)
    os.makedirs(os.path.dirname(yml), exist_ok=True)
    with open(yml, 'w', encoding='utf-8') as f:
        f.write(_dump(script))
    with open(md, 'w', encoding='utf-8') as f:
        f.write(describe(script))
    return yml


def round_trips(style: str) -> bool:
    """读入再写出与原文件逐字相同（没有手工 / 注入的格式），才能安全地局部重写。"""
    yml, _ = paths(style)
    with open(yml, encoding='utf-8') as f:
        raw = f.read()
    return _dump(yaml.safe_load(raw)) == raw


def load(style: str) -> Dict[str, Any]:
    yml, _ = paths(style)
    with open(yml, encoding='utf-8') as f:
        return yaml.safe_load(f)


def available() -> List[str]:
    return [s for s in STYLES if os.path.isfile(paths(s)[0])]


def _pct(p: float) -> str:
    return f'{p * 100:.0f}%'


def describe(s: Dict[str, Any], top: int = 15) -> str:
    L: List[str] = []
    m = s['meta']
    L += [f'# 学到的作曲语法：{s["name"]}（{s["grammar"]}）', '',
          f'语料：{s["source"]}，有效 {m["pieces"]} 首（训练 {m["train"]}，留出评测 {m["heldout"]}）。'
          f'每个子风格单独学习。', '',
          '本文件由 `grammar.yaml` 自动生成。作曲器生成的每个和弦、每小节节奏都在追溯里写出引用的'
          '规则编号：H = 和声转移，C = 终止公式，R = 节奏模板，M = 乐句轮廓，R0x = 硬规则。', '',
          '学到的是规律而不是曲子：规则里没有任何一条记录具体的音符序列。伴奏织体（低音 + 和弦）'
          '是固定模板，不是从语料学来的——这几份语料只有旋律和和弦标记。', '']
    for sub, d in s['subs'].items():
        L += [f'# {d["name"]}（{sub}）', '',
              f'- 拍号 {d["meter"]}，默认速度 ♩={d["tempo"]}；训练 {d["pieces"]["train"]} 首，'
              f'留出 {d["pieces"]["heldout"]} 首；调式 ' +
              '，'.join(f'{"大调" if k == "major" else "小调"} {v} 首' for k, v in d['modes'].items()), '']
        L += ['## 硬规则（作曲搜索时不可违反）', '']
        L += [f'- **{r["id"]}** {r["rule"]}' for r in d['hard_rules']]
        fm = d['form']
        L += ['', '## 曲式层', '',
              '- 8 小节一段，段落字母曲式：' + '，'.join(
                  f'{k} {_pct(p)}' for k, p, _n in fm['templates'][:8]),
              f'- 段末带反复记号的比例：{_pct(fm["section_repeat_prob"])}']
        pk = d['rhythm']['pickup']
        L.append(f'- 弱起开头：{_pct(pk["prob"])}' + ('；弱起长度 ' + '，'.join(
            f'{k:g} 拍 {_pct(p)}' for k, p, _n in pk['ql']) if pk['ql'] else ''))
        L.append('')
        for mode, levels in d['harmony'].items():
            zh = '大调' if mode == 'major' else '小调'
            ev = d['evaluation'].get(mode, {})
            vocab = d['vocab'][mode]
            L += [f'## 和声（{zh}）', '',
                  f'词表 {len(vocab)} 个和弦：' + '、'.join(list(vocab.values())[:24]) +
                  ('…' if len(vocab) > 24 else ''), '']
            if ev:
                L += [f'留出集困惑度：脚本 {ev["perplexity_script"]}（不剪枝 {ev["perplexity_unpruned"]}，'
                      f'去掉位置条件 {ev["perplexity_no_position"]}，只用频率 {ev["perplexity_unigram"]}）；'
                      f'留出集和弦落在词表内 {_pct(ev["coverage"])}。困惑度越低，规则越能预测下一个和弦。', '']
            L.append('首和弦：' + '，'.join(f'{vocab.get(c, c)} {_pct(p)}'
                                          for c, p, _n in d['first_chord'][mode][:4]))
            cad = d['cadences'][mode]
            L += ['', '### 终止公式（C 规则：段末两个和弦）', '']
            L += [f'- **{c["id"]}** 全曲结尾 {c["say"]} {_pct(c["p"])}（{c["n"]} 例）'
                  for c in cad['final'][:5]]
            L += [f'- **{c["id"]}** 段落结尾 {c["say"]} {_pct(c["p"])}（{c["n"]} 例）'
                  for c in cad['mid'][:5]]
            if cad['half']:
                L.append('- 段内第 4 小节（半句）落在：' + '，'.join(
                    f'{vocab.get(c, c)} {_pct(p)}' for c, p, _n in cad['half'][:4]))
            L += ['', '### 和声转移（H 规则：前一个和弦 + 段内位置 → 下一个和弦）', '',
                  '位置 = 段首小节 / 半句末小节（第 4）/ 段末小节（第 8）/ 全曲末小节 / 段中小节，'
                  '再分拍首和小节中途；越具体的规则（L3 看前两个和弦）优先，数据不足时回退。', '']
            for r in levels.get('L2', [])[:top]:
                L.append(f'- **{r["id"]}**（{r["n"]} 例）{r["say"]}')
            L.append('')
        L += ['## 和声节奏（每小节在第几拍换和弦）', '']
        for pos, dist in d['harmonic_rhythm'].items():
            nm = '末小节' if pos == 'F' else f'段内第 {int(pos) + 1} 小节'
            L.append(f'- {nm}：' + '，'.join(
                f'第 {"、".join(str(int(x) + 1) for x in k.split(","))} 拍 {_pct(p)}'
                for k, p, _n in dist[:3]))
        rh = d['rhythm']
        L += ['', '## 节奏（R 规则：每小节的节奏模板）', '']
        if rh['mode'] == 'cell':
            L += ['整小节模板覆盖率太低（即兴独奏几乎每小节都不同），作曲时改用**按拍的节奏单元链**：'
                  '每拍的节奏单元由"上一拍的单元 + 这是第几拍"决定（下表为各拍最常见的单元）。', '']
            for key, dist in rh['cells']['by_beat'].items():
                nm = ('末小节' if key.startswith('F') else '') + f'第 {int(key.lstrip("F")) + 1} 拍'
                L.append(f'- {nm}：' + '，'.join(
                    f'{"延续" if c == "-" else c} {_pct(p)}' for c, p, _n in dist[:6]))
            L += ['', '整小节模板（仅供参考）：', '']
        for cls, t in rh['templates'].items():
            L.append(f'### {_RCLS_ZH.get(cls, cls)}（{t["bars"]} 小节，前 {len(t["items"])} 个模板覆盖 '
                     f'{_pct(t["coverage"])}）')
            L += [f'- **{x["id"]}** {_pct(x["p"])}：{x["say"]}' for x in t['items'][:8]]
            L.append('')
        L.append('小节间的逻辑——照抄前面某小节节奏的概率（段内位置：前 1 / 2 / 4 小节）：')
        for pos, lags in rh['repeat'].items():
            L.append(f'- 第 {int(pos) + 1} 小节：' + '，'.join(
                f'抄前 {lag} 小节 {_pct(p)}' for lag, p in lags.items()))
        L.append('- 节奏相同时，旋律音程也相同（模进 / 重复）的比例：' + '，'.join(
            f'前 {lag} 小节 {_pct(v["interval_same"])}' for lag, v in rh['melodic_copy'].items()))
        mr = rh.get('motif_recall')
        if mr:
            L.append(f'- 跨段动机回忆（新段落第 1–7 小节照搬前面段落某小节的节奏与音高，'
                     f'{mr["bars"]} 小节）：原样 {_pct(mr["p_same"])}，模进 {_pct(mr["p_shift"])}')
        mel = d['melody']
        L += ['', '## 旋律', '']
        for st, dist in mel['class_by_strength'].items():
            L.append(f'- {_ST_ZH.get(st, st)}：' + '，'.join(
                f'{_CLS_ZH.get(k, k)} {_pct(p)}' for k, p, _n in dist))
        L += [f'- 非和弦音级进进入 {_pct(mel["nct_step_in"])}，级进离开 {_pct(mel["nct_step_out"])}，'
              f'二者之一 {_pct(mel["nct_step_either"])}；强拍非和弦音级进解决 '
              f'{_pct(mel["appoggiatura_resolve"])}',
              f'- 大跳（≥ 纯四度）后反向或级进回补：{_pct(mel["leap_recovery_prob"])}',
              f'- 最大跳进 {mel["max_leap"]} 半音；音域 MIDI {mel["range"]["low"]}–{mel["range"]["high"]}，'
              f'单曲音域中位数 {mel["range"]["span_median"]} 半音',
              '- 首音音级：' + '，'.join(f'{_DEG[int(k)]} {_pct(p)}' for k, p, _n in mel['first_degree']),
              '- 末音音级：' + '，'.join(f'{_DEG[int(k)]} {_pct(p)}' for k, p, _n in mel['final_degree']),
              '- 小节第一拍落在和弦的：' + '，'.join(
                  f'{ {"R": "根音", "3": "三音", "5": "五音", "7": "七音", "N": "非和弦音"}[k]} {_pct(p)}'
                  for k, p, _n in mel['downbeat_role']), '',
              '乐句轮廓模板（M 规则，4 小节，k-means 聚类，8 点相对音高）：']
        L += [f'- **{t["id"]}** {t["name"]} {_pct(t["p"])}：' + ' '.join(f'{v:+.1f}' for v in t['shape'])
              for t in mel['contour_templates']]
        L.append('')
    return '\n'.join(L) + '\n'
