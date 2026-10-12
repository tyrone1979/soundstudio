# -*- coding: utf-8 -*-
"""作曲库管理器：诊断 / 任务 Skill / 风格 Genre / 内核脚本 / 规则库 / 维度 taxonomy。

设计原则
--------
1. **每一样东西都要能回答「它到底有没有生效」。** 诊断页把任务 Skill 的
   「注入矩阵」、风格包的契约覆盖率、内核脚本清单摊开。历史上这里出过一次
   静默失效：菜单里 13 个任务 Skill 有 10 个在任何阶段都注入 0 字符，UI 却
   只显示它们的文件名，看不出问题。
2. **真相源永远是磁盘上的文件。** 编辑面保持纯文本，视图层不发明数据。
3. **只读视图（契约数值 / 脚本文档）由 composition_skills 的 API 生成**，
   UI 只负责排版。
4. **规则库**统一登记对位权威、教学规则与全部确定性脚本，可同时看文档与源码。
"""
from __future__ import annotations

import os
import subprocess
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import composition_skills as skill_lib
import rule_library as rule_lib

SKILL_ROOT = skill_lib.SKILL_ROOT
GENRE_ROOT = skill_lib.GENRE_ROOT
RULES_ROOT = rule_lib.RULES_ROOT

# 与 score_studio._APP_STYLESHEET 同一套深色令牌（FL Studio 式近黑底盘 + 橙色强调）
C_BG = '#1a1c21'
C_PANEL = '#202227'
C_PANEL2 = '#26282e'
C_BORDER = '#34373f'
C_TEXT = '#d7dae0'
C_MUTED = '#8a8f98'
C_DIM = '#5d626b'
C_ACCENT = '#ff8c1a'
C_OK = '#30d158'
C_WARN = '#ffb020'

_MONO = "Menlo, Consolas, 'Courier New', monospace"

_CARD_QSS = (
    f'background:{C_PANEL}; border:1px solid {C_BORDER}; '
    f'border-radius:6px; padding:8px 10px; color:{C_TEXT};'
)
_HEAD_QSS = f'color:{C_ACCENT}; font-size:12px; font-weight:bold; background:transparent;'
_NOTE_QSS = f'color:{C_MUTED}; font-size:12px; background:transparent;'


def _fmt_ratio(v) -> str:
    try:
        return f'{float(v):.0%}'
    except (TypeError, ValueError):
        return '—'


def _fmt_num(v, nd: int = 2, suf: str = '') -> str:
    if v is None:
        return '—'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f) and nd == 0:
        return f'{int(f)}{suf}'
    return f'{f:.{nd}f}{suf}'


def _bar(pct: float, width: int = 18) -> str:
    n = max(0, min(width, int(round(pct * width))))
    return '█' * n + '·' * (width - n)


def format_contract(c) -> str:
    """把 StyleContract 渲染成可读的数值契约（只读视图）。"""
    if c is None:
        return (
            '本风格包还没有 style_contract.yaml。\n\n'
            '它意味着：风格只由 meta.yaml → profile 的**专家画像**驱动（仍然是确定性\n'
            '的，但没有「从真实语料学到的数值」）。要补上实证契约：\n\n'
            '    python -m score_learning fetch <source> --accept-license\n'
            '    python -m score_learning learn scores/corpus/<dir> '
            '--family <family> --pack-id <pack>\n\n'
            'learn 结束时会自动写出 style_contract.yaml（本页即会刷新）。'
        )

    out: list[str] = []
    act = c.active_sections()
    out.append(f'包 id      {c.pack_id or "—"}')
    out.append(f'家族       {c.family or "—"}')
    out.append(f'schema     {c.schema}')
    out.append(f'语料样本   evidence_n = {c.evidence_n}')
    out.append(f'证据权重   {_fmt_num(c.evidence_weight, 2)}'
               '   （0=纯专家画像，1=纯语料实证）')
    out.append(f'生效分区   {" · ".join(act) if act else "（无 —— 契约不会约束生成）"}')
    prov = c.provenance or {}
    out.append(f'来源       built_from={prov.get("built_from", "—")}　'
               f'分析 {prov.get("scores_analyzed", "—")} 首'
               f'（其中有和声 {prov.get("scores_with_harmony", "—")} 首）　'
               f'min_evidence={prov.get("min_evidence", "—")}')
    extra = {k: v for k, v in prov.items()
             if k not in ('built_from', 'scores_analyzed',
                          'scores_with_harmony', 'min_evidence')}
    if extra:
        out.append('           ' + '　'.join(f'{k}={v}' for k, v in extra.items()))
    out.append('')

    def sec(title: str, usable: bool, rows: list[tuple[str, str]]) -> None:
        flag = '生效' if usable else '不生效（样本不足或字段缺失）'
        out.append(f'── {title} ' + '─' * max(0, 46 - len(title)) + f' [{flag}]')
        for k, v in rows:
            out.append(f'   {k:<14}{v}')
        out.append('')

    t = c.tempo
    sec('速度 tempo', t.is_usable(), [
        ('默认', _fmt_num(t.default, 0, ' BPM')),
        ('区间', f'{_fmt_num(t.min, 0)} – {_fmt_num(t.max, 0)} BPM'),
        ('来源', t.source),
        ('样本数', str(t.evidence_n)),
    ])

    m = c.meter
    meter_rows = [('主拍号', m.primary or '—'),
                  ('候选', ', '.join(m.allowed) or '—'),
                  ('样本数', str(m.evidence_n))]
    if m.weights:
        meter_rows.append(('分布', ''))
        for k, v in sorted(m.weights.items(), key=lambda x: -float(x[1]))[:6]:
            meter_rows.append((f'  {k}', f'{_bar(float(v))} {_fmt_ratio(v)}'))
    sec('拍号 meter', m.is_usable(), meter_rows)

    h = c.harmony
    h_rows = [
        ('七和弦占比', f'{_fmt_ratio(h.seventh_ratio())}   ← 体裁分野的关键单一指标'),
        ('和弦/小节', _fmt_num(h.chords_per_bar, 2)),
        ('独立根音数', _fmt_num(h.unique_roots_mean, 2)),
        ('样本数', str(h.evidence_n)),
    ]
    if h.quality_weights:
        h_rows.append(('品质分布', ''))
        total = sum(float(v) for v in h.quality_weights.values()) or 1.0
        for k, v in sorted(h.quality_weights.items(), key=lambda x: -float(x[1])):
            pct = float(v) / total
            h_rows.append((f'  {k}', f'{_bar(pct)} {pct:.1%}'))
    sec('和声 harmony', h.is_usable(), h_rows)

    mel = c.melody
    sec('旋律 melody', mel.is_usable(), [
        ('级进比', _fmt_num(mel.stepwise_ratio, 2)),
        ('大跳比', _fmt_num(mel.leap_ratio, 2)),
        ('同音重复', _fmt_num(mel.repeated_note_ratio, 2)),
        ('音域中心', _fmt_num(mel.range_center, 1)),
        ('音域宽度', _fmt_num(mel.range_width, 1)),
        ('平均音高', _fmt_num(mel.mean_midi, 1)),
        ('样本数', str(mel.evidence_n)),
    ])

    r = c.rhythm
    sec('节奏 rhythm', r.is_usable(), [
        ('密度 音/小节', _fmt_num(r.density_notes_per_bar, 2)),
        ('正拍起音比', _fmt_num(r.beat1_onset_share, 2)),
        ('切分度', _fmt_num(r.syncopation, 2)),
        ('swing 比', _fmt_num(r.swing_ratio, 2)),
        ('不等分八分占比', _fmt_num(r.swing_pair_share, 3)),
        ('三连音占比', _fmt_num(r.triplet_ratio, 3)),
        ('样本数', str(r.evidence_n)),
    ])

    tex = c.texture
    sec('织体 texture', tex.is_usable(), [
        ('声部数', _fmt_num(tex.voice_count, 1)),
        ('样本数', str(tex.evidence_n)),
    ])

    f = c.form
    sec('曲式 form', f.is_usable(), [
        ('乐句长度', _fmt_num(f.phrase_len, 1)),
        ('动机重复比', _fmt_num(f.repetition_ratio, 2)),
        ('独立动机数', _fmt_num(f.unique_motifs, 1)),
        ('样本数', str(f.evidence_n)),
        ('说明', '这里没有 A/A2/B 这类写死的段落名 —— 只记录可测量的统计量。'),
    ])

    out.append('数值低于 min_evidence 的分区不会生效；判据见 style_contract.py。')
    out.append('内核在 new_score / set_harmony / melody 等脚本里确定性地消费本契约。')
    return '\n'.join(out)


class SkillLibraryDialog(QDialog):
    """作曲库管理器。tab：0 诊断 / 1 Skill / 2 Genre / 3 脚本 / 4 规则库 / 5 维度。"""

    TAB_DIAG, TAB_SKILL, TAB_GENRE, TAB_SCRIPT, TAB_RULES, TAB_TAXONOMY = range(6)

    def __init__(self, parent=None, tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle("作曲库 — Skill / Genre / 脚本 / 规则库")
        self.setMinimumSize(1160, 740)
        self._current_id = None
        self._current_kind = 'skill'   # skill | genre
        self._rules: list = []
        self._build_ui()
        self._reload_all()
        if 0 <= int(tab) < self.main_tabs.count():
            self.main_tabs.setCurrentIndex(int(tab))

    # ------------------------------------------------------------------ 骨架
    def _build_ui(self):
        self.setStyleSheet(self._stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        head = QLabel(
            '每个条目都对应磁盘上的真实文件；改动立即写盘并在下次创曲时生效。'
            '「诊断」页看是否生效；「规则库」页统一查看对位/教学规则与确定性脚本的文档与源码。'
        )
        head.setWordWrap(True)
        head.setStyleSheet(_NOTE_QSS)
        layout.addWidget(head)

        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(self._build_diag_page(), "诊断")
        self.main_tabs.addTab(self._build_skill_page(), "任务 Skill")
        self.main_tabs.addTab(self._build_genre_page(), "风格 Genre")
        self.main_tabs.addTab(self._build_script_page(), "内核脚本")
        self.main_tabs.addTab(self._build_rules_page(), "规则库")
        self.main_tabs.addTab(self._build_taxonomy_page(), "维度 taxonomy")
        layout.addWidget(self.main_tabs)

        bottom = QHBoxLayout()
        refresh_btn = QPushButton("全部刷新")
        refresh_btn.setToolTip("清空缓存后重新扫描 skills/、genre/packs/、rules/")
        refresh_btn.clicked.connect(self._reload_all)
        bottom.addWidget(refresh_btn)
        for text, path in (
            ("打开 skills 文件夹", os.path.join(SKILL_ROOT, 'skills')),
            ("打开 genre/packs", os.path.join(GENRE_ROOT, 'packs')),
            ("打开规则库文件夹", RULES_ROOT),
        ):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, p=path: self._open_path(p))
            bottom.addWidget(b)
        bottom.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    @staticmethod
    def _stylesheet() -> str:
        return f"""
        QDialog {{ background:{C_BG}; color:{C_TEXT}; }}
        QLabel {{ background:transparent; color:{C_TEXT}; }}
        QTabWidget::pane {{ border:1px solid {C_BORDER}; background:{C_PANEL}; }}
        QTabBar::tab {{ background:{C_PANEL2}; color:{C_MUTED}; padding:6px 16px;
                        border-top-left-radius:6px; border-top-right-radius:6px;
                        margin-right:2px; }}
        QTabBar::tab:selected {{ background:{C_PANEL}; color:{C_ACCENT}; }}
        QTabBar::tab:hover:!selected {{ color:{C_TEXT}; }}
        QListWidget, QTreeWidget, QTableWidget, QTextEdit, QLineEdit {{
            background:{C_PANEL2}; color:{C_TEXT}; border:1px solid {C_BORDER};
            border-radius:4px; }}
        QListWidget::item:selected, QTreeWidget::item:selected {{
            background:{C_ACCENT}; color:{C_BG}; }}
        QTableWidget {{ gridline-color:{C_BORDER}; }}
        QHeaderView::section {{ background:{C_PANEL}; color:{C_MUTED};
            border:none; border-right:1px solid {C_BORDER};
            border-bottom:1px solid {C_BORDER}; padding:4px 6px; }}
        /* 表格左上角与标签页溢出时的滚动按钮：不显式上色会露出浅色系统底 */
        QTableCornerButton::section {{ background:{C_PANEL}; border:none; }}
        QTabBar QToolButton {{ background:{C_PANEL2}; border:none;
            border-radius:3px; margin:1px; }}
        QTabBar QToolButton:hover {{ background:#31343c; }}
        QScrollBar:horizontal, QScrollBar:vertical {{ background:{C_BG}; border:none; }}
        QScrollBar::handle {{ background:#3a3d45; border-radius:3px; }}
        QPushButton {{ background:{C_PANEL2}; color:{C_TEXT};
            border:1px solid {C_BORDER}; border-radius:4px; padding:4px 12px; }}
        QPushButton:hover {{ background:#31343c; border-color:#3f434d; }}
        QPushButton:pressed {{ background:#3a3d45; }}
        QCheckBox {{ background:transparent; color:{C_TEXT}; }}
        QSplitter::handle {{ background:{C_BG}; }}
        """

    # ------------------------------------------------------------- 诊断页
    def _build_diag_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)

        self.diag_headline = QLabel("")
        self.diag_headline.setWordWrap(True)
        self.diag_headline.setStyleSheet(
            f'background:{C_PANEL2}; border:1px solid {C_BORDER}; border-radius:6px;'
            f'padding:10px; color:{C_TEXT}; font-size:13px;')
        lay.addWidget(self.diag_headline)

        lay.addWidget(self._head('任务 Skill × 提示词阶段 —— 注入的字符数（「·」= 不注入）'))
        self.diag_matrix = QTableWidget()
        self.diag_matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.diag_matrix.setSelectionMode(QAbstractItemView.NoSelection)
        self.diag_matrix.verticalHeader().setDefaultSectionSize(22)
        lay.addWidget(self.diag_matrix, 2)

        lay.addWidget(self._head('风格包 —— 实证契约覆盖（有契约才会由数值约束生成）'))
        self.diag_genres = QTableWidget()
        self.diag_genres.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.diag_genres.setSelectionMode(QAbstractItemView.NoSelection)
        self.diag_genres.verticalHeader().setDefaultSectionSize(22)
        lay.addWidget(self.diag_genres, 2)

        lay.addWidget(self._head('结论与待办'))
        self.diag_notes = QTextEdit()
        self.diag_notes.setReadOnly(True)
        self.diag_notes.setFont(QFont(_MONO.split(',')[0], 11))
        lay.addWidget(self.diag_notes, 3)
        return page

    @staticmethod
    def _head(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet(_HEAD_QSS)
        return lb

    def _reload_diag(self):
        stages = list(skill_lib.INJECT_STAGES)
        matrix = skill_lib.injection_matrix()
        skills = skill_lib.list_skills(force=True)
        by_id = {s.get('id'): s for s in skills}

        # --- 注入矩阵 ---
        self.diag_matrix.clear()
        self.diag_matrix.setColumnCount(len(stages) + 1)
        self.diag_matrix.setHorizontalHeaderLabels(['任务 Skill'] + stages)
        self.diag_matrix.setRowCount(len(matrix))
        dead: list[str] = []
        for r, (sid, row) in enumerate(matrix.items()):
            name = by_id.get(sid, {}).get('name') or sid
            total = sum(row.values())
            if total == 0:
                dead.append(sid)
            first = QTableWidgetItem(f'{name}   ({sid})')
            first.setForeground(QColor(C_TEXT if total else C_DIM))
            if total == 0:
                first.setToolTip('本 skill 的 SKILL.md 没有注入任何阶段 —— '
                                 '在 index.yaml 加 inject: [<stage>] 可开启。')
            self.diag_matrix.setItem(r, 0, first)
            for c, st in enumerate(stages, start=1):
                n = row.get(st, 0)
                cell = QTableWidgetItem(f'{n}' if n else '·')
                cell.setTextAlignment(Qt.AlignCenter)
                cell.setForeground(QColor(C_OK if n else C_DIM))
                if n:
                    cell.setToolTip(f'注入 {st} 阶段 {n} 字符')
                self.diag_matrix.setItem(r, c, cell)
        hh = self.diag_matrix.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(stages) + 1):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)

        # --- 风格契约覆盖 ---
        genres = skill_lib.list_genres(force=True)
        # 一次性把契约读出来（每包只解析一次 YAML，顺便做排序键）
        contracts = {g['id']: skill_lib.genre_contract(g['id'])[0] for g in genres}
        # 有实证契约的排前面 —— 这张表是「覆盖情况」，空行不该占据首屏
        genres = sorted(
            genres,
            key=lambda g: (contracts[g['id']] is None,
                           str((g.get('dimensions') or {}).get('family') or ''),
                           str(g.get('name') or '')),
        )
        self.diag_genres.clear()
        cols = ['风格包', 'family', '样本数', '七和弦', '♩', '拍号', '生效分区', '特征数']
        self.diag_genres.setColumnCount(len(cols))
        self.diag_genres.setHorizontalHeaderLabels(cols)
        self.diag_genres.setRowCount(len(genres))
        no_contract, bad_tax = [], []
        for r, g in enumerate(genres):
            c = contracts[g['id']]
            if c is None:
                no_contract.append(g)
            errs = g.get('taxonomy_errors') or []
            if errs:
                bad_tax.append((g, errs))
            dims = g.get('dimensions') or {}
            vals = [
                g.get('name') or g.get('pack_id') or '',
                str(dims.get('family') or ''),
                str(c.evidence_n) if c else '—',
                _fmt_ratio(c.harmony.seventh_ratio()) if c else '—',
                _fmt_num(c.tempo.default, 0) if c else '—',
                (c.meter.primary or '—') if c else '—',
                ('·'.join(c.active_sections()) if c and c.active_sections() else '—'),
                str(skill_lib.genre_feature_count(g['id'])),
            ]
            for cc, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setForeground(QColor(C_TEXT if c else C_MUTED))
                if cc == 0 and errs:
                    cell.setText('⚠ ' + v)
                    cell.setForeground(QColor(C_WARN))
                    cell.setToolTip('；'.join(errs))
                self.diag_genres.setItem(r, cc, cell)
        gh = self.diag_genres.horizontalHeader()
        gh.setSectionResizeMode(0, QHeaderView.Stretch)
        for cc in range(1, len(cols)):
            gh.setSectionResizeMode(cc, QHeaderView.ResizeToContents)

        # --- 结论 ---
        scripts = skill_lib.script_library()
        snap = rule_lib.rule_library_snapshot()
        alive = len(matrix) - len(dead)
        with_c = [g for g in genres if contracts[g['id']] is not None]
        self.diag_headline.setText(
            f'任务 Skill <b>{len(matrix)}</b> 个（<span style="color:{C_OK}">{alive} 个真正注入提示词</span>'
            f'，<span style="color:{C_WARN}">{len(dead)} 个不注入</span>）'
            f'　　风格包 <b>{len(genres)}</b> 个（有实证契约 {len(with_c)} 个）'
            f'　　内核脚本 <b>{len(scripts)}</b> 个'
            f'　　规则库 <b>{snap["total"]}</b> 条'
            f'（手工 {snap["manual"]} + 脚本 {snap["kernel_script"]}）'
        )

        L: list[str] = []
        if dead:
            L.append(f'【{len(dead)} 个任务 Skill 不注入任何提示词】')
            for sid in dead:
                item = by_id.get(sid) or {}
                L.append(f'  · {item.get("name") or sid}（{sid}）')
            L.append('')
            L.append('  这不影响路由 —— 选区、关键词、斜杠命令仍然照常命中；')
            L.append('  但它们目录里的 SKILL.md 只是给人看的索引，从不进入提示词。')
            L.append('  若要让它真正影响生成：在 skills/composition/index.yaml 里给它加')
            L.append('       inject: [translator]     # 新创作主路径（翻译器 + 确定性内核）')
            L.append('       inject: [planner]        # 只影响计划')
            L.append('  或者直接写一个 <阶段>.md（translator.md/planner.md/composer.md），')
            L.append('  该文件存在时优先生效，不需要 inject 声明。')
            L.append('')
        if no_contract:
            L.append(f'【{len(no_contract)} 个风格包没有实证契约，只靠 meta.yaml 的专家画像】')
            facts = [(g.get('dimensions') or {}).get('family') for g in no_contract]
            fams = sorted({f for f in facts if f})
            L.append('  涉及 family：' + ('、'.join(fams) or '—'))
            L.append('  补法：python -m score_learning fetch <source> --accept-license')
            L.append('        python -m score_learning learn scores/corpus/<dir> '
                     '--family <f> --pack-id <p>')
            L.append('')
        if bad_tax:
            L.append(f'【{len(bad_tax)} 个风格包未通过 taxonomy 校验（在风格页显示为 ⚠）】')
            for g, errs in bad_tax[:12]:
                L.append(f'  · {g.get("id")}：{"; ".join(errs)}')
            L.append('  校验不通过的包**不会**参与关键词路由（_enabled_genres 会剔除）。')
            L.append('')
        L.append('【怎么读上面的注入矩阵】')
        L.append('  行 = 任务 Skill，列 = 提示词的 7 个阶段，格子里的数字是实际注入的字符数。')
        L.append('  translator 是新创作（翻译器 → 确定性内核）的主入口；')
        L.append('  planner 决定计划；composer / selection_* / repair / advice 走修订与建议路径。')
        L.append('  整行都是「·」= 这个 Skill 对 LLM 完全不可见，只剩路由作用。')
        self.diag_notes.setPlainText('\n'.join(L))

    # ---------------------------------------------------------- 任务 Skill 页
    def _build_skill_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel('任务 Skill（决定 pipeline 与路由；正文注入见右栏）'))
        self.skill_list = QListWidget()
        self.skill_list.currentItemChanged.connect(self._on_skill_select)
        ll.addWidget(self.skill_list)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.skill_enabled = QCheckBox("启用（关闭后不再参与路由）")
        self.skill_enabled.stateChanged.connect(self._on_enabled_toggle)
        head.addWidget(self.skill_enabled)
        head.addStretch(1)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_current)
        head.addWidget(save_btn)
        rl.addLayout(head)

        self.skill_inject = QLabel("")
        self.skill_inject.setWordWrap(True)
        self.skill_inject.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.skill_inject.setStyleSheet(_CARD_QSS)
        rl.addWidget(self.skill_inject)

        self.skill_tabs = QTabWidget()
        self.edit_skill_md = self._editor()
        self.edit_skill_translator = self._editor()
        self.edit_skill_planner = self._editor()
        self.edit_skill_composer = self._editor()
        self.skill_tabs.addTab(self.edit_skill_md, "SKILL.md")
        self.skill_tabs.addTab(self.edit_skill_translator, "translator.md")
        self.skill_tabs.addTab(self.edit_skill_planner, "planner.md")
        self.skill_tabs.addTab(self.edit_skill_composer, "composer.md")
        rl.addWidget(self.skill_tabs)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        outer.addWidget(split)
        return page

    @staticmethod
    def _editor() -> QTextEdit:
        ed = QTextEdit()
        ed.setFont(QFont(_MONO.split(',')[0], 11))
        ed.setLineWrapMode(QTextEdit.NoWrap)
        return ed

    def _reload_skills(self):
        self.skill_list.blockSignals(True)
        self.skill_list.clear()
        for sk in skill_lib.list_skills(force=True):
            sid = sk.get('id', '')
            name = sk.get('name', sid)
            live = skill_lib.live_stages(sid)
            badge = f'  注入: {"·".join(live)}' if live else '  注入: 无'
            item = QListWidgetItem(f"{name}  ({sid}){badge}")
            item.setData(Qt.UserRole, sid)
            item.setForeground(QColor(C_TEXT if sk.get('enabled', True) else C_DIM))
            item.setToolTip(f"pipeline: {sk.get('pipeline', '')}"
                            f"\ninject: {list(sk.get('inject') or [])}")
            self.skill_list.addItem(item)
        self.skill_list.blockSignals(False)
        if self.skill_list.count():
            self.skill_list.setCurrentRow(0)

    def _on_skill_select(self, current, _previous):
        if not current:
            return
        sid = current.data(Qt.UserRole)
        self._current_id = sid
        self._current_kind = 'skill'
        sk = skill_lib.get_skill(sid, force=True) or {}
        self.skill_enabled.blockSignals(True)
        self.skill_enabled.setChecked(bool(sk.get('enabled', True)))
        self.skill_enabled.blockSignals(False)

        row = skill_lib.skill_injection_stages(sid)
        live = {k: v for k, v in row.items() if v}
        if live:
            txt = '注入阶段：' + '　'.join(f'<b>{k}</b> {v} 字' for k, v in live.items())
        else:
            txt = (f'<span style="color:{C_WARN}">不注入任何提示词</span> —— '
                   '该 Skill 只参与路由（选区 / 关键词 / 斜杠命令）。'
                   '要让它影响生成，见「诊断」页的说明。')
        decl = sk.get('inject') or []
        txt += (f'<br><span style="color:{C_MUTED}">pipeline: '
                f'{sk.get("pipeline") or "—"}　'
                f'index.yaml inject: {decl if decl else "（未声明）"}</span>')
        txt += (f'<br><span style="color:{C_MUTED}">目录：'
                f'{skill_lib.skill_dir(sid)}</span>')
        self.skill_inject.setText(txt)

        paths = skill_lib.skill_file_paths(sid)
        self.edit_skill_md.setPlainText(self._read(paths.get('SKILL.md')))
        self.edit_skill_translator.setPlainText(
            self._read(os.path.join(skill_lib.skill_dir(sid), 'translator.md')))
        self.edit_skill_planner.setPlainText(self._read(paths.get('planner.md')))
        self.edit_skill_composer.setPlainText(self._read(paths.get('composer.md')))

    # ---------------------------------------------------------- 风格 Genre 页
    def _build_genre_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel('按语汇家族 (family) 分组；维度见 taxonomy.yaml'))
        self.genre_tree = QTreeWidget()
        self.genre_tree.setHeaderLabels(['风格包', '维度', '实证'])
        self.genre_tree.currentItemChanged.connect(self._on_genre_select)
        ll.addWidget(self.genre_tree)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.genre_enabled = QCheckBox("启用此风格包")
        self.genre_enabled.stateChanged.connect(self._on_enabled_toggle)
        head.addWidget(self.genre_enabled)
        head.addStretch(1)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_current)
        head.addWidget(save_btn)
        rl.addLayout(head)

        self.genre_dims = QLabel("")
        self.genre_dims.setWordWrap(True)
        self.genre_dims.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.genre_dims.setStyleSheet(_CARD_QSS)
        rl.addWidget(self.genre_dims)

        self.genre_tabs = QTabWidget()
        self.view_genre_contract = self._editor()
        self.view_genre_contract.setReadOnly(True)
        self.edit_genre_meta = self._editor()
        self.edit_genre_md = self._editor()
        self.view_genre_empirical = self._editor()
        self.view_genre_empirical.setReadOnly(True)
        self.edit_genre_planner = self._editor()
        self.edit_genre_planner.setPlaceholderText(
            "（可选）仅当有「只影响计划、写不进 profile」的特例时填写；一般留空。")
        self.edit_genre_composer = self._editor()
        self.edit_genre_composer.setPlaceholderText(
            "（可选）仅当有「只影响写谱」的特例时填写；一般留空。"
            "音乐画像请写在 meta.yaml → profile。")
        self.genre_tabs.addTab(self.view_genre_contract, "实证契约 ★")
        self.genre_tabs.addTab(self.edit_genre_meta, "meta.yaml ★")
        self.genre_tabs.addTab(self.edit_genre_md, "GENRE.md")
        self.genre_tabs.addTab(self.view_genre_empirical, "EMPIRICAL_SKILL.md")
        self.genre_tabs.addTab(self.edit_genre_planner, "planner.md 可选")
        self.genre_tabs.addTab(self.edit_genre_composer, "composer.md 可选")
        rl.addWidget(self.genre_tabs)
        split.addWidget(right)

        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([560, 600])   # 左树比右panel略窄，维度和 n= 才不至于被截
        outer.addWidget(split)
        return page

    def _reload_genres(self):
        keep = self._current_id if self._current_kind == 'genre' else None
        self.genre_tree.blockSignals(True)
        self.genre_tree.clear()
        tax = skill_lib.load_taxonomy(force=True)
        fam_defs = ((tax.get('dimensions') or {}).get('family') or {}).get('values') or {}
        grouped = skill_lib.genres_by_family(force=True)
        families = list(fam_defs.keys()) if fam_defs else sorted(grouped.keys())
        for extra in grouped:
            if extra not in families:
                families.append(extra)
        for fam in families:
            label = skill_lib.dimension_label('family', fam)
            parent = QTreeWidgetItem([f"{label}  ({fam})", "", ""])
            parent.setData(0, Qt.UserRole, None)
            parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
            parent.setForeground(0, QColor(C_ACCENT))
            packs = grouped.get(fam) or []
            for g in packs:
                c, _p = skill_lib.genre_contract(g['id'])
                err = g.get('taxonomy_errors') or []
                title = g.get('name') or g.get('pack_id')
                if err:
                    title = f"⚠ {title}"
                ev = f'n={c.evidence_n}' if (c and c.evidence_n) else '—'
                child = QTreeWidgetItem([title, skill_lib.format_genre_dims(g), ev])
                child.setData(0, Qt.UserRole, g.get('id'))
                if err:
                    child.setForeground(0, QColor(C_WARN))
                    child.setToolTip(0, '；'.join(err))
                elif c:
                    child.setForeground(2, QColor(C_OK))
                else:
                    child.setForeground(2, QColor(C_DIM))
                if not g.get('enabled', True):
                    for cc in range(3):
                        child.setForeground(cc, QColor(C_DIM))
                parent.addChild(child)
            if not packs:
                empty = QTreeWidgetItem(["（尚无风格包）", "", ""])
                empty.setDisabled(True)
                parent.addChild(empty)
            self.genre_tree.addTopLevelItem(parent)
            parent.setExpanded(bool(packs) and fam in ('jazz', 'classical', 'pop', 'folk'))
        for cc in (0, 2):
            self.genre_tree.resizeColumnToContents(cc)
        # 中间那列（维度）吃掉剩余宽度，否则长标签被截成「均衡…」
        hdr = self.genre_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.genre_tree.blockSignals(False)

        # 恢复/默认选中：不选的话右栏一片空白，看起来像功能坏了
        first = restore = None
        for i in range(self.genre_tree.topLevelItemCount()):
            parent = self.genre_tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                cid = child.data(0, Qt.UserRole)
                if not cid:
                    continue
                if first is None:
                    first = child
                if keep and cid == keep:
                    restore = child
        target = restore or first
        if target is not None:
            self.genre_tree.setCurrentItem(target)
            self.genre_tree.scrollToItem(target)

    def _on_genre_select(self, current, _previous):
        if not current:
            return
        sid = current.data(0, Qt.UserRole)
        if not sid:
            return
        self._current_id = sid
        self._current_kind = 'genre'
        g = skill_lib.get_genre(sid, force=True) or {}
        self.genre_enabled.blockSignals(True)
        self.genre_enabled.setChecked(bool(g.get('enabled', True)))
        self.genre_enabled.blockSignals(False)

        dims = g.get('dimensions') or {}
        err = g.get('taxonomy_errors') or []
        c, cpath = skill_lib.genre_contract(sid)
        nfeat = skill_lib.genre_feature_count(sid)
        lines = [
            f'<b>{sid}</b>',
            f'分类：{skill_lib.format_genre_dims(g)}',
            f'<span style="color:{C_MUTED}">'
            f'family={dims.get("family")}　form={dims.get("form")}　'
            f'tempo_energy={dims.get("tempo_energy")}　mood={dims.get("mood")}　'
            f'medium={dims.get("medium")}</span>',
            f'实证契约：{"✔ " + c.summarize() if c else "（无 style_contract.yaml，仅专家画像）"}'
            f'　　逐曲特征：{nfeat} 个',
            f'<span style="color:{C_MUTED}">目录：{g.get("path")}</span>',
        ]
        if err:
            lines.append(f'<span style="color:{C_WARN}">校验错误：{"; ".join(err)}'
                         f'（该包不参与关键词路由）</span>')
        if g.get('summary'):
            lines.append(f'<span style="color:{C_MUTED}">{g["summary"]}</span>')
        self.genre_dims.setText('<br>'.join(lines))

        self.view_genre_contract.setPlainText(format_contract(c))
        paths = skill_lib.skill_file_paths(sid)
        self.edit_genre_meta.setPlainText(self._read(paths.get('meta.yaml')))
        self.edit_genre_md.setPlainText(self._read(paths.get('GENRE.md')))
        self.edit_genre_planner.setPlainText(self._read(paths.get('planner.md')))
        self.edit_genre_composer.setPlainText(self._read(paths.get('composer.md')))
        self.view_genre_empirical.setPlainText(
            self._read(os.path.join(skill_lib.skill_dir(sid), 'EMPIRICAL_SKILL.md'))
            or '（本风格包没有 EMPIRICAL_SKILL.md —— 它由 score_learning 学习后写出。）')

    # ----------------------------------------------------------- 内核脚本页
    def _build_script_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel('确定性脚本（LLM 只能选脚本 + 给参数，音符由内核写）'))
        self.script_filter = QLineEdit()
        self.script_filter.setPlaceholderText("过滤脚本名 / 说明 / 参数…")
        self.script_filter.textChanged.connect(self._apply_script_filter)
        ll.addWidget(self.script_filter)
        self.script_list = QListWidget()
        self.script_list.currentItemChanged.connect(self._on_script_select)
        ll.addWidget(self.script_list)
        tip = QLabel('完整规则（含对位/教学）见「规则库」页，可同时看文档与源码。')
        tip.setWordWrap(True)
        tip.setStyleSheet(_NOTE_QSS)
        ll.addWidget(tip)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        right_split = QSplitter(Qt.Vertical)
        doc_box = QWidget()
        dl = QVBoxLayout(doc_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(QLabel('脚本文档（= 翻译器提示词里那份，唯一事实来源）'))
        self.script_view = self._editor()
        self.script_view.setReadOnly(True)
        dl.addWidget(self.script_view)
        right_split.addWidget(doc_box)
        code_box = QWidget()
        cl = QVBoxLayout(code_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QLabel('实现源码（compose_kernel.SCRIPT_IMPL）'))
        self.script_code_view = self._editor()
        self.script_code_view.setReadOnly(True)
        cl.addWidget(self.script_code_view)
        right_split.addWidget(code_box)
        right_split.setStretchFactor(0, 2)
        right_split.setStretchFactor(1, 3)
        rl.addWidget(right_split)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        outer.addWidget(split)
        return page

    def _reload_scripts(self):
        self._scripts = skill_lib.script_library()
        if not self._scripts:
            self.script_view.setPlainText(
                '读不到 compose_kernel（依赖未安装？）。\n'
                '脚本清单由 compose_kernel.SCRIPT_DOCS 提供。')
            if hasattr(self, 'script_code_view'):
                self.script_code_view.setPlainText('')
        else:
            notes = skill_lib.script_notes()
            tail = '\n'.join(x for x in (notes.get('call_order'),
                                         notes.get('texture_map')) if x)
            self.script_view.setPlainText(
                '选择左侧脚本查看说明与参数。\n\n' + ('─' * 72) + '\n\n' + tail)
            if hasattr(self, 'script_code_view'):
                self.script_code_view.setPlainText('选择脚本后显示实现源码。\n')
        self._apply_script_filter(self.script_filter.text())

    def _apply_script_filter(self, text: str):
        self.script_list.clear()
        needle = (text or '').strip().lower()
        for d in getattr(self, '_scripts', []):
            blob = f"{d['script']} {d['desc']} {d['args']}".lower()
            if needle and needle not in blob:
                continue
            item = QListWidgetItem(d['script'])
            item.setData(Qt.UserRole, d['script'])
            item.setToolTip((d['desc'] or '')[:160])
            self.script_list.addItem(item)
        if self.script_list.count():
            self.script_list.setCurrentRow(0)

    def _on_script_select(self, current, _previous):
        if not current:
            return
        name = current.data(Qt.UserRole)
        d = next((x for x in getattr(self, '_scripts', []) if x['script'] == name), None)
        if not d:
            return
        notes = skill_lib.script_notes()
        total = len(getattr(self, '_scripts', []))
        pos = [x['script'] for x in self._scripts].index(name) + 1
        body = [
            f'# {d["script"]}',
            f'（第 {pos} / {total} 个脚本）',
            '',
            '## 说明',
            d['desc'] or '—',
            '',
            '## 参数',
            d['args'] or '—',
            '',
            '─' * 72,
            '',
            '## 通用调用顺序',
            notes.get('call_order') or '—',
            '',
            '## 段落织体映射参考',
            notes.get('texture_map') or '—',
        ]
        self.script_view.setPlainText('\n'.join(body))
        rule = rule_lib.get_rule(f'kernel_script_{name}')
        if rule:
            self.script_code_view.setPlainText(rule_lib.format_rule_code(rule))
        else:
            self.script_code_view.setPlainText('（无对应规则条目）\n')

    # ---------------------------------------------------------- 规则库页
    def _build_rules_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(QLabel(
            '统一规则库：对位权威 · 和声/声部/乐句教学规则 · 全部确定性内核脚本。'
            '左侧按分类浏览；右侧同时查看文档与实现代码。'
        ))

        top = QHBoxLayout()
        self.rule_filter = QLineEdit()
        self.rule_filter.setPlaceholderText("过滤规则 id / 标题 / 标签 / 摘要…")
        self.rule_filter.textChanged.connect(self._apply_rule_filter)
        top.addWidget(self.rule_filter)
        open_btn = QPushButton("打开规则文件…")
        open_btn.setToolTip("在系统文件管理器中打开当前规则的文档或源码路径")
        open_btn.clicked.connect(self._open_current_rule_path)
        top.addWidget(open_btn)
        outer.addLayout(top)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.rule_tree = QTreeWidget()
        self.rule_tree.setHeaderLabels(['规则', '摘要'])
        self.rule_tree.setColumnWidth(0, 260)
        self.rule_tree.currentItemChanged.connect(self._on_rule_select)
        ll.addWidget(self.rule_tree)
        self.rule_status = QLabel('')
        self.rule_status.setStyleSheet(_NOTE_QSS)
        ll.addWidget(self.rule_status)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        right_split = QSplitter(Qt.Vertical)

        doc_box = QWidget()
        dl = QVBoxLayout(doc_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(QLabel('文档'))
        self.rule_doc_view = self._editor()
        self.rule_doc_view.setReadOnly(True)
        dl.addWidget(self.rule_doc_view)
        right_split.addWidget(doc_box)

        code_box = QWidget()
        cl = QVBoxLayout(code_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QLabel('代码'))
        self.rule_code_view = self._editor()
        self.rule_code_view.setReadOnly(True)
        cl.addWidget(self.rule_code_view)
        right_split.addWidget(code_box)
        right_split.setStretchFactor(0, 2)
        right_split.setStretchFactor(1, 3)
        rl.addWidget(right_split)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        outer.addWidget(split)
        return page

    def _reload_rules(self):
        rule_lib.invalidate_cache()
        self._rules = rule_lib.list_rules(include_scripts=True)
        snap = rule_lib.rule_library_snapshot()
        self.rule_status.setText(
            f'共 {snap["total"]} 条 · 手工 {snap["manual"]} · '
            f'内核脚本 {snap["kernel_script"]} · 已过期 {snap.get("deprecated", 0)} · '
            f'目录 {RULES_ROOT}'
        )
        self.rule_doc_view.setPlainText(
            '选择左侧规则查看文档。\n\n'
            '手工规则来自 skills/composition/rules/；\n'
            '内核脚本由 compose_kernel.SCRIPT_DOCS / SCRIPT_IMPL 自动登记；\n'
            '已过期代码登记在 rule_library.DEPRECATED_CODE（不注入提示词）。'
        )
        self.rule_code_view.setPlainText('选择规则后显示绑定的实现源码。\n')
        self._apply_rule_filter(self.rule_filter.text())

    def _apply_rule_filter(self, text: str):
        self.rule_tree.clear()
        needle = (text or '').strip().lower()
        by_cat: dict = {}
        for r in getattr(self, '_rules', []):
            blob = ' '.join([
                r.get('id') or '',
                r.get('title') or '',
                r.get('summary') or '',
                ' '.join(r.get('tags') or []),
                r.get('category_label') or '',
            ]).lower()
            if needle and needle not in blob:
                continue
            by_cat.setdefault(r.get('category') or 'other', []).append(r)

        cat_meta = {c['id']: c for c in rule_lib.categories()}
        # 保持 catalog 顺序，内核脚本靠后
        order = [c['id'] for c in rule_lib.categories()]
        for cid in list(by_cat.keys()):
            if cid not in order:
                order.append(cid)

        first_item = None
        for cid in order:
            rules = by_cat.get(cid) or []
            if not rules:
                continue
            meta = cat_meta.get(cid) or {
                'label': rules[0].get('category_label') or cid,
                'desc': '',
            }
            parent = QTreeWidgetItem([
                f"{meta.get('label') or cid}  ({len(rules)})",
                meta.get('desc') or '',
            ])
            parent.setData(0, Qt.UserRole, None)
            parent.setExpanded(True)
            self.rule_tree.addTopLevelItem(parent)
            for r in rules:
                deprecated = r.get('status') == 'deprecated'
                title = r.get('title') or r.get('id') or ''
                child = QTreeWidgetItem([
                    ('⚠️ ' + title) if deprecated else title,
                    (r.get('summary') or '')[:80],
                ])
                child.setData(0, Qt.UserRole, r.get('id'))
                child.setToolTip(0, (r.get('id') or '')
                                 + ('（已过期）' if deprecated else ''))
                if deprecated:
                    for col in (0, 1):
                        child.setForeground(col, QColor(140, 140, 140))
                parent.addChild(child)
                if first_item is None:
                    first_item = child
        if first_item is not None:
            self.rule_tree.setCurrentItem(first_item)

    def _on_rule_select(self, current, _previous):
        if not current:
            return
        rid = current.data(0, Qt.UserRole)
        if not rid:
            return
        rule = next((x for x in getattr(self, '_rules', []) if x.get('id') == rid), None)
        if not rule:
            rule = rule_lib.get_rule(rid)
        if not rule:
            return
        self.rule_doc_view.setPlainText(rule_lib.format_rule_doc(rule))
        self.rule_code_view.setPlainText(rule_lib.format_rule_code(rule))

    def _open_current_rule_path(self):
        item = self.rule_tree.currentItem() if hasattr(self, 'rule_tree') else None
        if not item:
            return
        rid = item.data(0, Qt.UserRole)
        rule = rule_lib.get_rule(rid) if rid else None
        if not rule:
            QMessageBox.information(self, "规则库", "请先选择一条规则。")
            return
        path = (rule.get('doc_path') or '').strip()
        if not path or not os.path.isfile(path):
            codes = rule.get('code') or []
            path = (codes[0].get('path') if codes else '') or ''
        if path and os.path.exists(path):
            target = path if os.path.isdir(path) else os.path.dirname(path)
            self._open_path(target or RULES_ROOT)
        else:
            self._open_path(RULES_ROOT)

    # -------------------------------------------------------- taxonomy 页
    def _build_taxonomy_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(QLabel(
            '维度权威定义（只读预览）。修改请编辑 genre/taxonomy.yaml 后点「全部刷新」。'))
        self.taxonomy_view = self._editor()
        self.taxonomy_view.setReadOnly(True)
        lay.addWidget(self.taxonomy_view)
        return page

    # ------------------------------------------------------------- 公共
    def _reload_all(self):
        skill_lib.invalidate_cache()
        rule_lib.invalidate_cache()
        self._reload_skills()
        self._reload_genres()
        self._reload_scripts()
        self._reload_rules()
        self._reload_diag()
        path = skill_lib.GENRE_TAXONOMY_PATH
        self.taxonomy_view.setPlainText(
            self._read(path) if os.path.isfile(path) else '（无 taxonomy.yaml）')

    @staticmethod
    def _read(path) -> str:
        if path and os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                return f.read()
        return ''

    def _on_enabled_toggle(self, state):
        if not self._current_id:
            return
        try:
            skill_lib.set_skill_enabled(self._current_id, state == Qt.Checked)
        except Exception as e:
            QMessageBox.warning(self, "作曲库", str(e))
            return
        if self._current_kind == 'genre':
            self._reload_genres()
        else:
            self._reload_skills()
        self._reload_diag()

    def _save_current(self):
        if not self._current_id:
            return
        sid = self._current_id
        try:
            if self._current_kind == 'genre':
                skill_lib.save_skill_text(sid, 'GENRE.md',
                                          self.edit_genre_md.toPlainText())
                skill_lib.save_skill_text(sid, 'planner.md',
                                          self.edit_genre_planner.toPlainText())
                skill_lib.save_skill_text(sid, 'composer.md',
                                          self.edit_genre_composer.toPlainText())
                skill_lib.save_skill_text(sid, 'meta.yaml',
                                          self.edit_genre_meta.toPlainText())
            else:
                skill_lib.save_skill_text(sid, 'SKILL.md',
                                          self.edit_skill_md.toPlainText())
                skill_lib.save_skill_text(sid, 'planner.md',
                                          self.edit_skill_planner.toPlainText())
                skill_lib.save_skill_text(sid, 'composer.md',
                                          self.edit_skill_composer.toPlainText())
                # translator.md：空内容与「文件不存在」等价（_addon_text 检查真值后
                # 走 inject 回退），所以空着就别往仓库里落一个空文件。
                tpath = os.path.join(skill_lib.skill_dir(sid), 'translator.md')
                ttext = self.edit_skill_translator.toPlainText()
                if ttext.strip():
                    skill_lib.save_skill_text(sid, 'translator.md', ttext)
                elif os.path.isfile(tpath):
                    os.remove(tpath)
            skill_lib.invalidate_cache()
            QMessageBox.information(self, "作曲库", "已保存。")
            self._reload_all()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _open_path(self, path):
        os.makedirs(path, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(path)  # noqa: S606
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
