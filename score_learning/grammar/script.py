# -*- coding: utf-8 -*-
"""规则脚本的读写：grammar.yaml（机器执行）+ grammar.md（人读的中文说明）。"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import numpy as np
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DIR = os.path.join(ROOT, 'skills', 'composition', 'genre', 'packs', 'sacred', 'chorale')
DEFAULT_YAML = os.path.join(DEFAULT_DIR, 'grammar.yaml')
DEFAULT_MD = os.path.join(DEFAULT_DIR, 'grammar.md')

_MODE_ZH = {'major': '大调', 'minor': '小调'}
_POS_ZH = {'start': '句首', 'mid_s': '句中强拍', 'mid_w': '句中弱拍', 'cad3': '终止前第3拍',
           'cad2': '终止前第2拍', 'cad1': '终止和弦前一拍', 'cad0': '终止和弦'}
_ROLE_ZH = {'R': '根音', '3': '三音', '5': '五音', '7': '七音', 'N': '非和弦音'}


def save(script: Dict[str, Any], path: str = DEFAULT_YAML, md_path: str = DEFAULT_MD) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# 由 score_learning.grammar 从语料自动学习生成；作曲器只读本文件。\n')
        f.write('# 中文说明见同目录 grammar.md。\n')
        yaml.safe_dump(script, f, allow_unicode=True, sort_keys=False, width=110)
    if md_path:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(describe(script))


def load(path: str = DEFAULT_YAML) -> Dict[str, Any]:
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def _pct(p: float) -> str:
    return f'{p * 100:.0f}%'


def describe(s: Dict[str, Any], top_rules: int = 25) -> str:
    """把规则脚本翻译成可读的中文文档。"""
    L: List[str] = []
    m = s['meta']
    L += [f'# 学到的作曲语法：{s["grammar"]}', '',
          f'语料 {m["pieces"]} 首（训练 {m["train"]}，留出评测 {m["heldout"]}），'
          f'{m["beats"]} 拍，{m["phrases"]} 个乐句。', '',
          '本文件由 `grammar.yaml` 自动生成。每条规则有编号，作曲器生成的每一拍都会在'
          '追溯里写出它引用了哪条规则（H=和声转移，C=终止公式，F=句终规划，M=旋律轮廓，'
          'R=硬规则）。', '']
    L += ['## 硬规则（作曲搜索时不可违反）', '']
    L += [f'- **{r["id"]}** {r["rule"]}' for r in s['hard_rules']]
    L.append('')
    if s.get('calibration'):
        L += ['## 校准项', '', f'- 女高音同音偏置 {s["calibration"]["sop_repeat_bias"]}：'
              f'{s["calibration"]["say"]}', '']
    fm = s['form']
    L += ['## 全曲层', '',
          f'- 带反复（Barform：第 1–2 句与第 3–4 句相同）的众赞歌占 {_pct(fm["barform_prob"])}',
          '- 强拍起句的乐句长度（拍）：' + '，'.join(
              f'{k} 拍 {_pct(p)}' for k, p, _n in fm['downbeat_phrase_slots'])]
    if fm.get('layout'):
        lay = fm['layout']
        L += [f'- 全曲写法（4/4，{lay["n"]} 首）：弱起（第 4 拍起句，终止落第 3 拍）'
              f'{_pct(lay["pickup"])}，强拍起 {_pct(lay["downbeat"])}',
              '- 弱起乐句长度（拍）：' + '，'.join(
                  f'{k} 拍 {_pct(p)}' for k, p, _n in fm.get('pickup_phrase_slots', []))]
    L.append('')
    for mode in ('major', 'minor'):
        if mode not in s['vocab']:
            continue
        zh = _MODE_ZH[mode]
        ev = s['evaluation'][mode]
        L += [f'# {zh}', '',
              f'和弦词表 {ev["vocab_size"]} 个；留出集困惑度：脚本 {ev["perplexity_script"]}'
              f'（不剪枝 {ev["perplexity_unpruned"]}，去掉位置条件 {ev["perplexity_no_position"]}，'
              f'只用频率 {ev["perplexity_unigram"]}）；留出集和弦二元组覆盖率 '
              f'{_pct(ev["bigram_coverage"])}。困惑度越低说明规则越能预测巴赫下一拍用什么和弦。', '']
        f = fm[mode]
        L += ['## 句终规划（F 规则：这一句终止在哪个和弦上）', '',
              '乐句数分布：' + '，'.join(f'{k} 句 {_pct(p)}' for k, p, _n in f['phrase_count'][:6]),
              '', '首和弦：' + '，'.join(f'{s["vocab"][mode].get(c, c)} {_pct(p)}'
                                        for c, p, _n in f['first_chord'][:4]), '']
        L += [f'- **{r["id"]}**（{r["n"]} 例）{r["say"]}' for r in f['cadence_plan'][:top_rules]]
        L.append('')
        L += ['## 终止公式（C 规则：每句最后三拍的和弦 + 女高音最后两音）', '']
        for tgt, c in s['cadences'][mode].items():
            nm = s['vocab'][mode].get(tgt + ':0', tgt)
            L.append(f'### 终止在 {nm}（{c["n"]} 例）')
            L += [f'- **{x["id"]}** {_pct(x["p"])}：{x["say"]}' for x in c['forms'][:6]]
            L.append('')
        L += ['## 和声转移（H 规则：按“前两拍和弦 + 句中位置”预测下一和弦）', '',
              '位置是相对终止的距离：句首 / 句中强拍 / 句中弱拍 / 终止前第3、2、1拍 / 终止和弦。'
              '越具体的规则（L3 看前两拍，L2 看前一拍 + 位置）优先，数据不足时回退到更一般的规则。', '']
        for lv, title in (('L2', '前一拍和弦 + 位置'), ('L3', '前两拍和弦 + 位置')):
            rules = s['harmony'][mode].get(lv, [])
            L.append(f'### {lv}：{title}（共 {len(rules)} 条，列出最常用的 {min(top_rules, len(rules))} 条）')
            for r in rules[:top_rules]:
                say = r['say']
                for k, v in _POS_ZH.items():
                    say = say.replace(f'+ {k} →', f'+ {v} →').replace(f'{k} →', f'{v} →')
                L.append(f'- **{r["id"]}**（{r["n"]} 例）{say}')
            L.append('')
        mel = s['melody'][mode]
        L += ['## 旋律（女高音）', '',
              f'- 跳进（≥ 纯四度）后反向级进回补的比例：{_pct(mel["leap_recovery_prob"])}',
              f'- 平均音高 MIDI {mel["abs_mean"]}（标准差 {mel["abs_std"]}）',
              '- 全曲音区弧线（各段乐句相对全曲平均的高低，半音）：' + '，'.join(
                  f'第{int(k) + 1}段 {v:+.1f}' for k, v in mel['register_arc'].items()), '']
        L.append('乐句轮廓模板（M 规则，k-means 聚类，8 点相对音高）：')
        for t in mel['contour_templates']:
            L.append(f'- **{t["id"]}** {t["name"]} {_pct(t["p"])}：' +
                     ' '.join(f'{v:+.1f}' for v in t['shape']))
        L += ['', '各位置的旋律音级角色：']
        for pos, d in mel['chord_role_by_pos'].items():
            L.append(f'- {_POS_ZH.get(pos, pos)}：' + '，'.join(
                f'{_ROLE_ZH.get(k, k)} {_pct(p)}' for k, p, _n in d[:4]))
        L.append('')
        vo = s['voicing'][mode]
        L += ['## 声部配置', '',
              f'- 内声部保持共同音的比例：{_pct(vo["common_tone_keep_prob"])}',
              '- 内声部导音（V→I）：' + '，'.join(
                  f'{ {"up_to_tonic": "上行到主音", "down_to_fifth": "下行到五音", "other": "其他"}.get(k, k)} {_pct(p)}'
                  for k, p, _n in vo['inner_leading_tone']),
              '- 重复音（按和弦类型:转位）：']
        for k, d in vo['doubling'].items():
            L.append(f'  - {k}：' + '，'.join(f'重复{_ROLE_ZH.get(r, r)} {_pct(p)}' for r, p, _n in d[:3]))
        L.append('')
        # ↓ 声部**之间**的关系：这一段以前完全没有，生成器也没用
        if vo.get('pair_motion'):
            _PAIR_ZH = {'SA': '女高-女低', 'AT': '女低-男高',
                        'TB': '男高-男低', 'SB': '女高-男低（外声部）'}
            _MOT_ZH = {'similar': '同向', 'contrary': '反向',
                       'oblique': '一动一静', 'static': '同静止'}
            L += ['### 声部之间的运动关系', '',
                  '「同向」即四声部一起动——那是织体单一的直接来源；'
                  '「一动一静」与「反向」才是巴赫织体的常态。', '',
                  '| 声部对 | ' + ' | '.join(_MOT_ZH.values()) + ' |',
                  '|---' * (len(_MOT_ZH) + 1) + '|']
            for pair in ('SA', 'AT', 'TB', 'SB'):
                d = vo['pair_motion'].get(pair)
                if not d:
                    continue
                m = {k: p for k, p, _n in d}
                L.append(f'| {_PAIR_ZH.get(pair, pair)} | ' +
                         ' | '.join(_pct(m.get(k, 0)) for k in _MOT_ZH) + ' |')
            L.append('')
        if vo.get('pair_span'):
            L += ['- 声部间距分布（半音，语料实测）：' + '，'.join(
                f'{ {"SA": "女高-女低", "AT": "女低-男高", "TB": "男高-男低", "SB": "外声部"}.get(k, k)} '
                f'中位 {int(np.median([kk for kk, _p, _n in d]))}'
                for k, d in sorted(vo['pair_span'].items())), '']
        # 条件运动分布：这一组是「声部之间关系」真正能被生成端使用的载体
        if vo.get('cond_motion'):
            _DIR_ZH = {'up': '上行', 'down': '下行', 'stay': '不动'}
            _V_ZH = {'A': '女低', 'T': '男高', 'B': '低音'}
            L += ['### 条件运动分布 P(声部方向 | 女高方向)', '',
                  '边缘分布只记「某声部自己走了多远」，记不下「它与女高是同向还是反向」'
                  '——那是个相关性，只能用条件分布表达。行=女高走向，列=该声部走向，'
                  '括号内为条件概率。', '',
                  '| 女高 ＼ 声部 | ' + ' | '.join(
                      f'{_V_ZH.get(v, v)}' for v in ('A', 'T', 'B')) + ' |',
                  '|---' * 4 + '|']
            for sd in ('up', 'down', 'stay'):
                cells = []
                for v in ('A', 'T', 'B'):
                    tab = vo['cond_motion'].get(v) or {}
                    n, p = tab.get(f'{sd}|up', [0, 0])
                    n2, p2 = tab.get(f'{sd}|down', [0, 0])
                    n3, p3 = tab.get(f'{sd}|stay', [0, 0])
                    tot = n + n2 + n3
                    cells.append(f'↑{p:.2f} ↓{p2:.2f} ·{p3:.2f}')
                L.append(f'| {_DIR_ZH[sd]} | ' + ' | '.join(cells) + ' |')
            L += ['',
                  '读法：**外声部强反向**（女高上行时低音 57% 下行、下行时 53% 上行，'
                  '且低音「不动」只有 7%），这是巴赫织体的核心手法；'
                  '女高动时女低有 32–36% 保持不动，即「一动一静」。', '']
        L.append('')
    rg = s['voicing']['ranges']
    L += ['## 音域与装饰', '',
          '- 音域（1%–99% 分位，MIDI）：' + '，'.join(
              f'{k} {v["low"]}–{v["high"]}' for k, v in rg.items()),
          '- 八分经过音 / 辅助音概率：' + '，'.join(
              f'{k} {_pct(v["passing_prob"])}/{_pct(v["neighbor_prob"])}'
              for k, v in s['ornament'].items()), '']
    return '\n'.join(L) + '\n'
