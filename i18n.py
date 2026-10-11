"""界面中英文切换（运行时翻译 UI 外壳文字）。

源码里的界面文字保持中文；切到 English 时由 :class:`Retranslator` 遍历控件树，
把标签/按钮/菜单/提示/标题/下拉项/表头等替换为英文，原文存于控件动态属性中，
切回中文即可还原。乐谱内容、对话正文、输入框内容、LLM 输出等数据不翻译。

翻译顺序：整句精确匹配 → 正则模式 → 按片段（最长优先）替换；若替换后仍残留
汉字，则视为无法完整翻译，保留原文（避免中英夹杂的半句）。
"""
from __future__ import annotations

import glob
import os
import re
from functools import lru_cache

_ROOT = os.path.dirname(os.path.abspath(__file__))
_ENV = 'COMPOSERCHAT_LANG'
_SETTINGS = ('ComposerChat', 'ScoreStudio')
_KEY = 'ui/language'
LANGS = ('zh', 'en')

_lang = 'zh'


def language() -> str:
    return _lang


def initial_language() -> str:
    env = (os.environ.get(_ENV) or '').strip().lower()
    if env in LANGS:
        return env
    try:
        from PyQt5.QtCore import QSettings
        v = str(QSettings(*_SETTINGS).value(_KEY, 'zh') or 'zh').lower()
        return v if v in LANGS else 'zh'
    except Exception:
        return 'zh'


def set_language(lang: str, persist: bool = True) -> None:
    global _lang
    _lang = lang if lang in LANGS else 'zh'
    if persist:
        try:
            from PyQt5.QtCore import QSettings
            QSettings(*_SETTINGS).setValue(_KEY, _lang)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 词典
# ---------------------------------------------------------------------------

EXACT = {
    # 窗口 / 菜单
    '乐谱工作室': 'Score Studio',
    '文件': 'File', '导出': 'Export', '视图': 'View', '工具': 'Tools', '帮助': 'Help',
    '作曲库': 'Library', 'MIDI 键盘': 'MIDI Keyboard',
    '体系层（非功能和声）': 'Systems (non-functional harmony)',
    '不走功能和声的另一套组织规则；确定性生成，不调 LLM':
        'Alternative organising systems without functional harmony; deterministic, no LLM',
    '接入外部 MIDI 键盘步进输入（macOS / Windows）':
        'Step input from an external MIDI keyboard (macOS / Windows)',
    '新建钢琴四部和声谱...': 'New piano four-part score...',
    '新建钢琴四部和声谱': 'New piano four-part score',
    '增加小节...': 'Add measures...', '增加小节': 'Add measures',
    '在当前乐谱末尾追加空小节': 'Append empty measures to the end of the score',
    '添加声部...': 'Add part...', '添加声部': 'Add part',
    '为当前乐谱追加空声部（低音/钢琴左手等）': 'Append an empty part (bass, piano left hand, ...)',
    '设置配乐...': 'Accompaniment...', '设置配乐': 'Accompaniment',
    '根据旋律声部为低音写入伴奏': 'Write a bass accompaniment from the melody part',
    '打开乐谱 (MusicXML/MIDI)...': 'Open score (MusicXML/MIDI)...',
    '打开乐谱 (MusicXML/MIDI)': 'Open score (MusicXML/MIDI)',
    '打开音频...': 'Open audio...', '打开音频': 'Open audio', '恢复乐谱音频': 'Restore score audio',
    '分析报告...': 'Analysis report...', '分析报告': 'Analysis report',
    '程序员报告（查看）...': 'Developer report (view)...', '程序员报告（查看）': 'Developer report (view)',
    '程序员报告': 'Developer report',
    '归档到 runs/ ...': 'Archive to runs/ ...', '归档到 runs/': 'Archive to runs/',
    '退出': 'Quit',
    '工作区：谱面': 'Workspace: Score', '工作区：编排': 'Workspace: Arrange',
    '工作区：钢琴卷帘': 'Workspace: Piano roll',
    '循环播放': 'Loop playback', '节拍器': 'Metronome', '跟随播放头': 'Follow playhead',
    '风格骨架生成器…': 'Style skeleton generator…',
    '用确定性脚本按风格包生成乐谱骨架（含赋格段），不调 LLM':
        'Generate a score skeleton (with fugal section) from a style pack with deterministic scripts; no LLM',
    '启用 MIDI 输入': 'Enable MIDI input', '选择输入设备…': 'Select input device…',
    '八度 +1': 'Octave +1', '八度 −1': 'Octave −1',
    '和弦模式（抬键后写入）': 'Chord mode (write on key release)',
    '左右手分谱（钢琴大谱表）': 'Split hands (piano grand staff)',
    '按分手点把音分到右手高音谱表 / 左手低音谱表；单谱表时自动补左手':
        'Route notes to right-hand treble / left-hand bass staff by split point; adds a left hand on single-staff scores',
    '另一只手补休止（两手对齐）': 'Fill the other hand with rests (keep hands aligned)',
    '分手点…': 'Split point…', 'MIDI 状态…': 'MIDI status…',
    '板腔体（上下句 · 板式变化）': 'Banqiang (couplet phrases · metre changes)',
    '板腔体：上下句 + 板式变化 + 过门，五声音阶。（确定性生成，不调 LLM）':
        'Banqiang: couplet phrases + metre changes + interludes, pentatonic. (Deterministic, no LLM)',
    '曲牌体（联曲 · 字位填腔）': 'Qupai (tune suites · syllable-slot filling)',
    '曲牌体：固定字位网格填腔，多牌联套。（确定性生成，不调 LLM）':
        'Qupai: fixed syllable-slot grid filling, chained tunes. (Deterministic, no LLM)',
    '拉格（调式 · 塔拉循环）': 'Raga (mode · tala cycle)',
    '拉格：调式 + 特征乐句 + 塔拉循环，低声部持续音。（确定性生成，不调 LLM）':
        'Raga: mode + characteristic phrases + tala cycle, drone bass. (Deterministic, no LLM)',
    '十二音序列（P/R/I/RI）': 'Twelve-tone row (P/R/I/RI)',
    '十二音序列：音列 P/R/I/RI 四形态轮换，无调中心。（确定性生成，不调 LLM）':
        'Twelve-tone: rotates P/R/I/RI row forms, no tonal centre. (Deterministic, no LLM)',
    '作曲库管理器…': 'Library manager…',
    '任务 Skill、风格 Genre、确定性脚本、规则库的查看与编辑':
        'View and edit task skills, style genres, deterministic scripts and the rule library',
    '诊断：库里哪些东西真正生效': 'Diagnostics: what in the library is actually used',
    '任务 Skill（路由规则与提示词）': 'Task skills (routing rules and prompts)',
    '风格 Genre（含实证契约）': 'Style genres (with evidence contracts)',
    '内核脚本库（确定性脚本与参数）': 'Kernel scripts (deterministic scripts and parameters)',
    '规则库（对位 / 教学 / 脚本 · 文档+代码）': 'Rule library (counterpoint / pedagogy / scripts · docs + code)',
    '维度 taxonomy': 'Dimension taxonomy', '重新加载库': 'Reload library',
    '帮助文档（完整 · 每条带示例）': 'Help (complete · with examples)',
    '查看单项说明（命令/功能）…': 'Look up a command/feature…',
    '设计理念 · 护城河': 'Design rationale',
    '编辑教程 · 莫扎特小夜曲（16小节）': 'Editing tutorial · Mozart serenade (16 bars)',
    '专业文档（命令/功能实测验证版）': 'Reference manual (verified commands/features)',
    # 走带 / 工具条
    '回到开头': 'Go to start', '停止': 'Stop',
    '播放 / 暂停（乐谱区获得焦点后可按空格）': 'Play / pause (Space when the score has focus)',
    '五线谱编辑模式：谱面上直接插入/拖拽/删除音符与记号。\n智能提示「下一个符号」，违反乐理规则实时禁止（红）/警告（黄）。':
        'Staff edit mode: insert/drag/delete notes and marks directly on the score.\n'
        'Suggests the "next symbol"; rule violations are blocked (red) / warned (yellow) live.',
    '✎ 编辑': '✎ Edit', '主音量': 'Master volume',
    '播放增益（自本次播放开始生效）': 'Playback gain (applies from this playback)',
    '缩小乐谱': 'Zoom out', '放大乐谱': 'Zoom in',
    '当前缩放比例（点击恢复 100%）': 'Current zoom (click to reset to 100%)',
    '横向铺满：整页宽度适应窗口': 'Fit width to window', '⤢ 宽度': '⤢ Width',
    '跳到正在播放的小节（未播放时回开头）': 'Jump to the playing measure (start if stopped)',
    '◎ 聚焦': '◎ Focus',
    '打开 MusicXML / MIDI': 'Open MusicXML / MIDI', '🎼 打开乐谱': '🎼 Open score',
    '打开音频文件': 'Open audio file', '📂 打开音频': '📂 Open audio',
    '切回乐谱原始音频': 'Switch back to the score audio', '↩ 乐谱音频': '↩ Score audio',
    '导出 MusicXML': 'Export MusicXML', '导出 MIDI': 'Export MIDI', '导出 WAV': 'Export WAV',
    '导出 LilyPond': 'Export LilyPond',
    '重新生成分析报告': 'Regenerate analysis report', '刷新报告': 'Refresh report',
    '另存分析报告': 'Save analysis report as', '保存报告': 'Save report',
    '循环播放（Ctrl+Shift+L）：播到出点/结尾自动回到入点':
        'Loop (Ctrl+Shift+L): return to the in-point at the out-point/end',
    '把当前播放位置设为循环入点': 'Set loop in-point at the playhead', '⟦入': '⟦In',
    '把当前播放位置设为循环出点': 'Set loop out-point at the playhead', '出⟧': 'Out⟧',
    '节拍器：随播放打拍（每小节第一拍重音）': 'Metronome: click along (accent on beat 1)',
    '跟随播放头：播放时自动滚动乐谱/卷帘': 'Follow playhead: auto-scroll score/piano roll',
    '钢琴卷帘/编排视图的网格吸附粒度': 'Grid snap for piano roll / arrange view',
    '吸附 1/4': 'Snap 1/4', '吸附 1/8': 'Snap 1/8', '吸附 1/16': 'Snap 1/16',
    '关': 'Off', '开': 'On', '有': 'yes', '无': 'none',
    '🎛 混音台': '🎛 Mixer', 'Mel 频谱': 'Mel spectrogram', '波形': 'Waveform',
    '线性频谱': 'Linear spectrogram', '色度图': 'Chromagram', '能量包络': 'Energy envelope',
    '（不显示可视化）': '(no visualisation)',
    '静音': 'Mute', '独奏': 'Solo',
    '谱面': 'Score', '编排': 'Arrange', '钢琴卷帘': 'Piano roll',
    '钢琴卷帘：点击可定位播放头': 'Piano roll: click to move the playhead',
    '时间线': 'Timeline', '时间线：点击小节块可选中声部': 'Timeline: click a block to select the part',
    '声部': 'Parts', '名称': 'Name', '可见 / 静音 / 独奏': 'Visible / Mute / Solo',
    # 编辑面板
    '选择（Esc）· 拖拽改音高 · 方向键微调': 'Select (Esc) · drag to change pitch · arrows to nudge',
    '音符输入（N）· 点击谱表插入': 'Note input (N) · click staff to insert',
    '休止符 · 点击谱表插入': 'Rest · click staff to insert',
    '擦除 · 点击删除音符': 'Erase · click to delete a note',
    '全音符（1）': 'Whole note (1)', '二分音符（2）': 'Half note (2)', '四分音符（3）': 'Quarter note (3)',
    '八分音符（4）': 'Eighth note (4)', '十六分音符（5）': 'Sixteenth note (5)',
    '三十二分音符（6）': '32nd note (6)', '附点（.）': 'Dot (.)',
    '还原（0 / =）': 'Natural (0 / =)', '升号（+）': 'Sharp (+)', '降号（-）': 'Flat (-)',
    '重升（++）': 'Double sharp (++)', '重降（--）': 'Double flat (--)',
    '延音 Tie（T）': 'Tie (T)', 'Staccato 跳音': 'Staccato', 'Accent 重音': 'Accent',
    '三连音 3:2': 'Triplet 3:2', '五连音': 'Quintuplet', '六连音': 'Sextuplet', '七连音': 'Septuplet',
    '撤销（Ctrl+Z）': 'Undo (Ctrl+Z)', '重做（Ctrl+Y）': 'Redo (Ctrl+Y)',
    '💡 可能性': '💡 Possibilities', '下一音': 'Next note',
    '悬停在谱表上，或写入音符后显示多种可能性': 'Hover over the staff or write a note to see possibilities',
    '搜索符号…': 'Search symbols…',
    '四四拍': '4/4 time', '三四拍': '3/4 time', '二四拍': '2/4 time', '六八拍': '6/8 time',
    '九八拍': '9/8 time', '十二八拍': '12/8 time', '二二拍': '2/2 time', '三八拍': '3/8 time',
    '五四拍': '5/4 time', '六四拍': '6/4 time', '七四拍': '7/4 time',
    '高音谱号 Treble': 'Treble clef', '低音谱号 Bass': 'Bass clef', '中音谱号 Alto': 'Alto clef',
    '次中音 Tenor': 'Tenor clef', '高音 8va bassa': 'Treble 8va bassa', '低音 8va bassa': 'Bass 8va bassa',
    'Tenuto 保持': 'Tenuto', 'Fermata 延长': 'Fermata', 'Trill 颤音': 'Trill',
    'Mordent 波音': 'Mordent', '逆波音': 'Inverted mordent', 'Turn 回音': 'Turn',
    '渐强 Hairpin': 'Crescendo hairpin', '渐弱 Hairpin': 'Diminuendo hairpin',
    '延音线 Tie（T）': 'Tie (T)', '连音线 Slur': 'Slur', '倚音 Grace': 'Grace note',
    '单小节线': 'Single barline', '复纵线 Double': 'Double barline', '终止线 Final': 'Final barline',
    '虚线 Dashed': 'Dashed barline', '反复开始': 'Repeat start', '反复结束': 'Repeat end',
    '广板': 'Largo', '柔板': 'Adagio', '行板': 'Andante', '中板': 'Moderato', '快板': 'Allegro',
    '急板': 'Presto', '渐慢': 'Ritardando', '渐快': 'Accelerando', '回原速': 'A tempo',
    'Arpeggio 琶音': 'Arpeggio', 'Breath 换气': 'Breath mark',
    '谱末增加空小节': 'Append empty measures', '+小节': '+Measure',
    '添加空声部': 'Add an empty part', '+声部': '+Part',
    '根据旋律写伴奏': 'Write accompaniment from the melody', '配乐': 'Accomp.',
    'MIDI 键盘步进输入': 'MIDI keyboard step input', 'MIDI 输入': 'MIDI input',
    '辅助作曲': 'Assist', '严格校验': 'Strict check',
    '音符：选中后点符号\n小节：选中后点调号/拍号等':
        'Notes: select, then click a symbol\nMeasures: select, then click key/time signature etc.',
    '暂无铺满可能性（先写至少一个音）': 'No fill-in possibilities yet (write at least one note)',
    # 对话面板
    '对话': 'Chat', '新对话': 'New chat', '新建': 'New', '删除': 'Delete', '风格': 'Style',
    '新乐谱': 'New Score', '对话创曲': 'Chat composition',
    '发送': 'Send',
    '点选小节/音符后对话只改选区；无选区则整曲。可用下方「风格」下拉选择，或输入 /style 弹出列表，选完后继续写描述。创曲需等 LLM（约1–3分钟）。Ctrl多选 · Esc清除 · /help':
        'With measures/notes selected, chat edits only the selection; otherwise the whole piece. '
        'Pick a style below or type /style, then describe the piece. Composing waits for the LLM '
        '(about 1–3 minutes). Ctrl multi-select · Esc clear · /help',
    '（选择风格包，填入后可再写描述）': '(choose a style pack, then add a description)',
    '选区：无（对话将作用于整曲）': 'Selection: none (chat applies to the whole piece)',
    '选区：无': 'Selection: none', '本次 Skill：—': 'Skill: —',
    '做一首圆舞曲…  或选风格后写描述  ·  /style  ·  /help\nEnter 发送 · Shift+Enter 换行 · ↑/↓ 翻命令履历':
        'Write a waltz…  or pick a style and describe it  ·  /style  ·  /help\n'
        'Enter send · Shift+Enter newline · ↑/↓ command history',
    '删除对话': 'Delete chat', '确定删除当前对话记录？': 'Delete the current chat?',
    '对话记录格式无效。': 'Invalid chat file format.', '至少保留一个对话。': 'At least one chat must remain.',
    # 对话框 / 状态
    '提示': 'Notice', '失败': 'Failed', '已保存': 'Saved', '已保存。': 'Saved.', '保存': 'Save',
    '保存失败': 'Save failed', '关闭': 'Close', '打开乐谱': 'Open score',
    '暂无乐谱': 'No score', '当前没有可导出的乐谱': 'There is no score to export',
    '当前没有乐谱。请先新建或打开乐谱。': 'No score open. Create or open a score first.',
    '导出成功': 'Export succeeded', '导出失败': 'Export failed', '请稍候': 'Please wait',
    '音频尚未就绪，请等待 MIDI 合成完成': 'Audio not ready yet; wait for MIDI rendering to finish',
    '当前没有可归档的生成结果': 'Nothing generated to archive', '已归档': 'Archived',
    '打开失败': 'Open failed', '文件中没有音频数据': 'The file contains no audio data',
    '乐谱音频尚未合成完成': 'Score audio is still rendering', '播放中…': 'Playing…',
    '播放完成': 'Playback finished', '已停止': 'Stopped',
    '体系层生成失败': 'Systems generation failed', '命令已执行': 'Command executed',
    '载入失败': 'Load failed', '未找到': 'Not found', '刷新失败': 'Refresh failed',
    '打开乐谱失败': 'Failed to open score', '归档失败': 'Archive failed',
    '正在合成音频，合成后自动播放…': 'Rendering audio; playback starts when ready…',
    '尚无可播放内容（请先写入音符）': 'Nothing to play yet (write some notes first)',
    '程序员报告缺失，已尝试重新导出': 'Developer report missing; tried to re-export',
    '报告生成失败': 'Report generation failed', '出点必须在入点之后': 'Out-point must be after in-point',
    '生成骨架': 'Generate skeleton', '骨架生成失败': 'Skeleton generation failed',
    '请先用对话选择风格：例如 `/style 圆舞曲` 或 `/skeleton 圆舞曲`。':
        'Choose a style in the chat first, e.g. `/style waltz` or `/skeleton waltz`.',
    '在对话中用 /skeleton 或 /style 生成骨架': 'Use /skeleton or /style in the chat to generate a skeleton',
    '正在用确定性脚本生成骨架（含赋格段）…': 'Generating skeleton with deterministic scripts (with fugal section)…',
    '（当前无可分轨声部：导入外部音频时仅主音量生效）':
        '(no separable parts: only master volume applies to imported audio)',
    'MIDI 键盘已关闭': 'MIDI keyboard disconnected', 'MIDI：左右手分谱': 'MIDI: split hands',
    '关（写入当前谱表）': 'Off (write to current staff)', '开（两手每拍对齐）': 'On (align hands each beat)',
    '分手点': 'Split point', '此音及以上进右手，以下进左手：': 'This note and above go to the right hand, below to the left:',
    'MIDI 输入设备': 'MIDI input device', '选择 MIDI 键盘：': 'Choose a MIDI keyboard:', 'MIDI 状态': 'MIDI status',
    '未检测到 MIDI 输入设备。\n\n请连接 MIDI 键盘后重试。\nmacOS：系统设置 → 隐私与安全性（如有提示请允许）。\nWindows：确认驱动正常，设备管理器中可见。':
        'No MIDI input device found.\n\nConnect a MIDI keyboard and try again.\n'
        'macOS: System Settings → Privacy & Security (allow if prompted).\n'
        'Windows: make sure the driver works and the device shows in Device Manager.',
    '（勾选「MIDI键盘」启用）': '(tick "MIDI keyboard" to enable)',
    'MIDI：无法写入（小节已满或被规则禁止）': 'MIDI: cannot write (measure full or blocked by rules)',
    'MIDI：找不到可写入的谱表': 'MIDI: no writable staff',
    '辅助作曲已开启': 'Assist on', '辅助作曲已关闭（仍可用规则建议）': 'Assist off (rule suggestions still available)',
    '辅助作曲 · 1–5 切换 ghost · Tab 下一音 · Ctrl+Tab 本小节 · Ctrl+Enter 全曲':
        'Assist · 1–5 switch ghost · Tab next note · Ctrl+Tab this measure · Ctrl+Enter whole piece',
    '辅助 ON · 1–5 切换预览 · Tab 下一音 · Ctrl+Tab 本小节 · Ctrl+Enter 全曲':
        'Assist ON · 1–5 switch preview · Tab next note · Ctrl+Tab this measure · Ctrl+Enter whole piece',
    '编辑模式：♪ 插入音符 ｜ 休止/擦除 ｜ ↑↓ 移调、Shift+1-5 时值、Del 删除、Ctrl+Z 撤销':
        'Edit mode: ♪ insert notes | rest/erase | ↑↓ transpose, Shift+1-5 duration, Del delete, Ctrl+Z undo',
    '已退出编辑模式': 'Left edit mode', '没有下一音候选': 'No next-note candidates',
    '已铺满本小节（Ctrl+Tab）': 'Filled this measure (Ctrl+Tab)',
    '没有本小节铺满稿：先写入至少一个音符': 'No fill for this measure: write at least one note first',
    '已全曲铺满（Ctrl+Enter）': 'Filled the whole piece (Ctrl+Enter)',
    '没有全曲铺满稿：先写入至少一个音符': 'No whole-piece fill: write at least one note first',
    '没有可切换的可能性': 'No possibilities to switch',
    '先在谱面点选小节或音符，再应用力度': 'Select measures or notes on the score before applying dynamics',
    '先选中音符，再点延音/连线/渐强弱等符号': 'Select notes before applying ties/slurs/hairpins',
    '先选中小节，再设小节线': 'Select a measure before setting a barline',
    '先选中小节（或选中小节内音符），再应用调号/拍号/谱号等':
        'Select a measure (or notes in it) before applying key/time signature/clef',
    '编辑已写回，正在合成可播放音频…': 'Edits saved; rendering playable audio…',
    '上一请求仍在进行（LLM 最长约 3 分钟），请稍候…': 'Previous request still running (LLM up to ~3 minutes), please wait…',
    '创曲失败': 'Composition failed', '文档未生成': 'Document not generated', '无法打开': 'Cannot open',
    '音频合成失败': 'Audio rendering failed', '音频合成失败（可再点 ▶ 重试）': 'Audio rendering failed (press ▶ to retry)',
    '编辑已写回 MusicXML（尚无发声音符，写入后可播放）':
        'Edits written to MusicXML (no sounding notes yet; playable once written)',
    '已生成修改建议（未改谱）': 'Suggestions generated (score unchanged)',
    '作曲库已关闭（更改已生效）': 'Library closed (changes applied)',
    '查看单项说明': 'Look up', '输入命令或功能名（如 /chords、和弦顾问、风格插值）：':
        'Enter a command or feature name (e.g. /chords, chord advisor, style interpolation):',
    '♪ 正在从 MusicXML 加载…': '♪ Loading from MusicXML…',
    '（下次播放生效）': '(applies on next playback)',
    '（确定性生成，不调 LLM）': '(deterministic, no LLM)',
    # 作曲库管理器
    '作曲库 — Skill / Genre / 脚本 / 规则库': 'Library — Skills / Genres / Scripts / Rules',
    '每个条目都对应磁盘上的真实文件；改动立即写盘并在下次创曲时生效。「诊断」页看是否生效；「规则库」页统一查看对位/教学规则与确定性脚本的文档与源码。':
        'Every entry is a real file on disk; edits are saved immediately and take effect on the next composition. '
        'See "Diagnostics" for what is used; "Rules" shows docs and source of counterpoint/pedagogy rules and deterministic scripts.',
    '诊断': 'Diagnostics', '任务 Skill': 'Task skills', '风格 Genre': 'Style genres',
    '内核脚本': 'Kernel scripts', '规则库': 'Rules', '风格包': 'Style packs', '全部刷新': 'Refresh all',
    '清空缓存后重新扫描 skills/、genre/packs/、rules/': 'Clear caches and rescan skills/, genre/packs/, rules/',
    '启用（关闭后不再参与路由）': 'Enabled (disabled skills are not routed)',
    '维度': 'Dimensions', '实证': 'Evidence', '启用此风格包': 'Enable this style pack',
    '（可选）仅当有「只影响计划、写不进 profile」的特例时填写；一般留空。':
        '(Optional) only for planner-only exceptions that cannot go in the profile; usually empty.',
    '（可选）仅当有「只影响写谱」的特例时填写；一般留空。音乐画像请写在 meta.yaml → profile。':
        '(Optional) only for composer-only exceptions; usually empty. Put the musical profile in meta.yaml → profile.',
    '实证契约 ★': 'Evidence contract ★', 'planner.md 可选': 'planner.md (optional)',
    'composer.md 可选': 'composer.md (optional)',
    '过滤脚本名 / 说明 / 参数…': 'Filter by script name / description / parameter…',
    '完整规则（含对位/教学）见「规则库」页，可同时看文档与源码。':
        'Full rules (incl. counterpoint/pedagogy) are on the "Rules" page with docs and source.',
    '过滤规则 id / 标题 / 标签 / 摘要…': 'Filter by rule id / title / tag / summary…',
    '打开规则文件…': 'Open rule file…',
    '在系统文件管理器中打开当前规则的文档或源码路径': "Reveal the current rule's doc or source in the file manager",
    '规则': 'Rule', '摘要': 'Summary', '文档': 'Docs', '代码': 'Code',
    '任务 Skill（决定 pipeline 与路由；正文注入见右栏）':
        'Task skills (pipeline and routing; injected text on the right)',
    '按语汇家族 (family) 分组；维度见 taxonomy.yaml': 'Grouped by family; dimensions in taxonomy.yaml',
    '确定性脚本（LLM 只能选脚本 + 给参数，音符由内核写）':
        'Deterministic scripts (the LLM only picks scripts and parameters; the kernel writes the notes)',
    '脚本文档（= 翻译器提示词里那份，唯一事实来源）': 'Script docs (the same text the translator prompt uses)',
    '实现源码（compose_kernel.SCRIPT_IMPL）': 'Source (compose_kernel.SCRIPT_IMPL)',
    '统一规则库：对位权威 · 和声/声部/乐句教学规则 · 全部确定性内核脚本。左侧按分类浏览；右侧同时查看文档与实现代码。':
        'Unified rules: counterpoint authorities · harmony/voice-leading/phrase pedagogy · all deterministic kernel scripts. '
        'Browse by category on the left; docs and code on the right.',
    '请先选择一条规则。': 'Select a rule first.',
    '维度权威定义（只读预览）。修改请编辑 genre/taxonomy.yaml 后点「全部刷新」。':
        'Authoritative dimension definitions (read-only). Edit genre/taxonomy.yaml, then click "Refresh all".',
    '本 skill 的 SKILL.md 没有注入任何阶段 —— 在 index.yaml 加 inject: [<stage>] 可开启。':
        "This skill's SKILL.md is not injected into any stage — add inject: [<stage>] in index.yaml to enable.",
    '（已过期）': '(stale)', '（尚无风格包）': '(no style packs yet)',
    # 骨架生成器（studio_daw）
    '基调（style）': 'Style',
    '只列出已学过的风格包（有 grammar.yaml）：决定拍号/速度/和声/织体/音区':
        'Only learned style packs (with grammar.yaml): sets metre/tempo/harmony/texture/register',
    '（未选择）': '(none selected)', '生成': 'Generate', '小节': 'Measures',
    '自动（对位风格=3）': 'Auto (contrapuntal styles = 3)', '2 声部': '2 voices', '3 声部': '3 voices',
    '4 声部': '4 voices', 'new_score 的 voices：2=创意曲，3/4=赋格': 'new_score voices: 2 = invention, 3/4 = fugue',
    'auto：必含赋格段': 'auto: always includes a fugal section', 'full：整首赋格': 'full: whole fugue',
    'off：不要赋格': 'off: no fugue', '赋格': 'Fugue',
    '赋格由内核确定性生成：主题→答题→对题→间插→密接→终止':
        'The kernel writes fugues deterministically: subject → answer → countersubject → episode → stretto → cadence',
    '留空=每次不同；填了可逐音符复现': 'Empty = different each time; set it to reproduce note for note',
    '种子': 'Seed', '提示词变奏：只改你提到的维度（情绪/速度/结构…）':
        'Prompt variation: changes only the dimensions you mention (mood/tempo/form…)',
    '变奏': 'Variation', '生成骨架（确定性）': 'Generate skeleton (deterministic)',
    '不调 LLM：按风格包直接生成乐谱骨架': 'No LLM: build the score skeleton straight from the style pack',
    '生成全曲（LLM）': 'Generate full piece (LLM)',
    '骨架作基调，LLM 在其上做提示词变奏': 'The skeleton is the base; the LLM applies prompt variations on top',
    '再来一版（换种子）': 'Another version (new seed)', '选中': 'Selected',
    '（乐谱区未选中）': '(nothing selected in the score)', '（无 / 用提示词判断）': '(none / infer from prompt)',
    '（空选区）': '(empty selection)', '还没有学过的风格包。先用 /learn-grammar 学习':
        'No learned style packs yet. Learn one with /learn-grammar first',
}

# 动态拼接消息的片段（最长优先替换；替换后不得残留汉字）
PHRASES = {
    '乐谱工作室 - ': 'Score Studio - ', '时值 → ': 'Duration → ',
    '（也可点工具条「全/二/四/八/十六」）': '(or use the toolbar buttons)',
    '（也可点工具条「二」「四」）': '(or use the toolbar buttons)',
    '循环播放：': 'Loop: ', '循环入点：': 'Loop in: ', '循环出点：': 'Loop out: ', '节拍器：': 'Metronome: ',
    '骨架已生成：': 'Skeleton generated: ', ' ｜ 赋格：': ' | fugue: ',
    ' 小节空谱（': '-measure empty score (', '）｜ 辅助作曲可直接点写': ') | Assist can write directly',
    '当前 ': 'Currently ', ' 小节。追加多少空小节？': ' measures. How many empty measures to append?',
    ' ｜ 已增至 ': ' | now ', '已增加小节，共 ': 'Measures added, total ',
    ' 个声部。选择要添加的声部：': ' parts. Choose a part to add:',
    ' 声部 · 已配乐': ' parts · accompanied',
    '根据旋律声部写入伴奏。选择配乐型：': 'Write accompaniment from the melody. Choose a pattern:',
    '另一只手补休止：': 'Fill other hand with rests: ', 'MIDI 八度偏移：': 'MIDI octave offset: ',
    ' 半音）': ' semitones)', ' · 第 ': ' · #',
    '预览可能性 ': 'Preview possibility ', '（Ctrl+Tab 采纳）': '(Ctrl+Tab to accept)',
    '选区：': 'Selection: ', '本次 Skill：': 'Skill: ',
    '已重新加载：': 'Reloaded: ', ' 个任务 Skill（': ' task skills (', ' 个注入提示词），': ' injected), ',
    ' 个风格包已学过（UI 可见），': ' learned style packs (shown), ', ' 个内核脚本': ' kernel scripts',
    '已重新加载\n任务 Skill：': 'Reloaded\nTask skills: ', '（其中 ': ' (', ' 个会注入提示词）\n风格 Genre：': ' injected)\nStyle genres: ',
    '（已学过 ': ' (learned ', ' 个，有 grammar.yaml；UI 只列这些）\n内核脚本：': ', with grammar.yaml; only these are listed)\nKernel scripts: ',
    ' 小节 ｜ 源: ': ' measures | source: ', '五线谱已从 MusicXML 加载: ': 'Staff loaded from MusicXML: ',
    '播放中…（': 'Playing… (', '分析报告已写入并显示:\n': 'Analysis report written and shown:\n',
    '\n程序员报告同时写入:\n': '\nDeveloper report also written:\n',
    ' 项通过，⚠️ ': ' passed, ⚠️ ', ' 项提示，❌ ': ' hints, ❌ ', ' 项问题': ' issues',
    '分析报告已导出: ': 'Analysis report exported: ', '播放头定位到 ': 'Playhead at ',
    's（空格继续）': 's (Space to continue)', '已选中声部 ': 'Selected part ', '（混音台第 ': ' (mixer channel ',
    ' 条通道）': ')', ' 音量': ' volume', '混音失败：': 'Mixing failed: ', '分手点：': 'Split point: ',
    'MIDI 键盘已接入：': 'MIDI keyboard connected: ', '已记住 MIDI 设备：': 'Remembered MIDI device: ',
    '听审更新预览 → 可能性 ': 'Review updated preview → possibility ', '已写入铺满稿 ': 'Wrote fill ',
    ' 个符号': ' symbols', '已生成：': 'Generated: ', '未找到 ': 'Not found: ',
    '。请先运行：python gen_help_pro.py，或改用菜单「帮助文档」直接在应用内查看。':
        '. Run python gen_help_pro.py first, or use Help → Help to read it in the app.',
    '系统未能用默认程序打开该文档：\n': 'The system could not open the document:\n',
    '\n\n可手动用文本编辑器打开，或改用菜单「帮助文档」在应用内查看。':
        '\n\nOpen it in a text editor, or use Help → Help to read it in the app.',
    '报告已刷新: ': 'Report refreshed: ', '已打开音频: ': 'Opened audio: ', ' 秒，': ' s, ',
    'MIDI 播放就绪（': 'MIDI playback ready (',
    's）｜需要时可在下拉框选择 Mel/波形/色度等': 's) | choose Mel/waveform/chroma in the drop-down if needed',
    '已打开乐谱: ': 'Opened score: ', ' — MIDI 合成中...': ' — rendering MIDI...',
    '可编辑五线谱已写入:\n': 'Editable score written:\n', '已导出 MusicXML: ': 'Exported MusicXML: ',
    'MIDI 已写入:\n': 'MIDI written:\n', '已导出 MIDI: ': 'Exported MIDI: ', 'WAV 已写入:\n': 'WAV written:\n',
    '已导出 WAV: ': 'Exported WAV: ', '已导出 LilyPond: ': 'Exported LilyPond: ',
    '归档目录:\n': 'Archive folder:\n', '\n\n产物: ': '\n\nArtifacts: ', '已归档: ': 'Archived: ',
    '单击采纳灰色铺满 · ': 'Click to accept the grey fill · ',
    '无法读取对话记录（文件可能已损坏）：\n': 'Cannot read the chat file (it may be corrupt):\n',
    '编辑写回失败：': 'Failed to save edits: ', '未找到与「': 'No command or feature matches "',
    '」匹配的命令或功能。\n输入 /help 可查看完整目录。': '".\nType /help for the full list.',
    '已暂停于 ': 'Paused at ', '程序员报告: ': 'Developer report: ', '听审更新失败：': 'Review update failed: ',
    '候选：': 'Candidates: ', '无法从乐谱生成音频：': 'Cannot render audio from the score: ',
    ' 键切换 ghost；Ctrl+Tab 采纳本小节\n': ' keys switch ghost; Ctrl+Tab accepts this measure\n',
    'Tab 采纳下一音 ': 'Tab accepts next note ',
    # 作曲库管理器诊断行
    '任务 Skill <b>': 'Task skills <b>', '</b> 个（<span style="color:': '</b> (<span style="color:',
    ' 个真正注入提示词</span>，<span style="color:': ' injected</span>, <span style="color:',
    ' 个不注入</span>）\u3000\u3000风格包 <b>': ' not injected</span>)\u3000\u3000Style packs <b>',
    '</b> 个（有实证契约 ': '</b> (with evidence contract ', ' 个）\u3000\u3000内核脚本 <b>': ')\u3000\u3000Kernel scripts <b>',
    '</b> 个\u3000\u3000规则库 <b>': '</b>\u3000\u3000Rules <b>', '</b> 条（手工 ': '</b> (manual ',
    ' + 脚本 ': ' + scripts ', '共 ': 'Total ', ' 条 · 手工 ': ' · manual ', ' · 内核脚本 ': ' · kernel scripts ',
    ' · 已过期 ': ' · stale ', ' · 目录 ': ' · categories ', '注入 ': 'Injected into ', ' 阶段 ': ' stages ',
    ' 字符': ' chars',
    '打开 genre/packs': 'Open genre/packs', '打开 skills 文件夹': 'Open skills folder',
    '打开规则库文件夹': 'Open rules folder', '结论与待办': 'Findings and to-dos',
    '任务 Skill × 提示词阶段 —— 注入的字符数（「·」= 不注入）':
        'Task skill × prompt stage — injected characters ("·" = not injected)',
    '风格包 —— 实证契约覆盖（有契约才会由数值约束生成）':
        'Style packs — evidence-contract coverage (only packs with a contract are generated under numeric constraints)',
    '分类：': 'Category: ', '注入阶段：': 'Injected stages: ', ' 字<br>': ' chars<br>',
    # 规则库分类与条目
    '脚本 · ': 'Script · ', '学到的语法 · ': 'Learned grammar · ', '学到的作曲语法': 'Learned composition grammars',
    '内核确定性脚本': 'Deterministic kernel scripts', '已过期代码': 'Stale code',
    '乐句与曲式': 'Phrase and form', '和声与终止': 'Harmony and cadence', '声部进行': 'Voice leading',
    '对位法': 'Counterpoint', '旋律写作': 'Melody writing', '节奏与节拍': 'Rhythm and metre',
    '乐句 · 乐段 · 句子': 'Phrase · period · sentence', '乐句和声模型（T → PD → D）': 'Phrase harmony model (T → PD → D)',
    '伴奏织体型': 'Accompaniment textures', '四部和声声部进行基础': 'Four-part voice-leading basics',
    '常用和弦进行': 'Common chord progressions', '平行五度 / 八度': 'Parallel fifths / octaves',
    '旋律轮廓与动机发展': 'Melodic contour and motivic development',
    '第一类二声部对位（对位法权威）': 'First-species two-voice counterpoint (authority)',
    '终止式（PAC / IAC / HC）': 'Cadences (PAC / IAC / HC)', '节拍与节奏基础': 'Metre and rhythm basics',
    '非和声音（经过/邻音/留音等）': 'Non-chord tones (passing/neighbour/suspension...)',
    '一次性迁移': 'One-off migration', '内核辅助': 'Kernel helper', '旧对题生成': 'old countersubject generator',
    '旧版导入': 'Legacy import', '旧版编辑器': 'Legacy editor', '逆向': 'Reverse', '旧名': 'old name',
    '旧 JSON 块解析': 'old JSON block parsing', '旧验收样例': 'old acceptance sample',
    # 骨架生成器
    '已学过的风格包（': 'Learned style packs (',
    ' 个，均有 grammar.yaml）：决定拍号/速度/和声/织体/音区；自然语言可走 /compose-grammar':
        ', all with grammar.yaml): sets metre/tempo/harmony/texture/register; natural language via /compose-grammar',
    '拍号 ': 'Metre ', '\n织体 ': '\nTexture ', ' / 伴奏 ': ' / accompaniment ', '\n和声 ': '\nHarmony ',
    '\n常用调 ': '\nCommon keys ', ' 拍': ' beats', '声部 ': 'Voices ', '（读取失败 ': '(read failed ',
    ' 小节 · 密度 ': ' measures · density ',
    # 乐谱信息条
    ' 小节': ' measures', '源: ': 'source: ',
    # 文件对话框过滤器
    '所有文件': 'All files', '乐谱文件': 'Score files', '音频文件': 'Audio files',
    # 风格语法名（grammar.yaml）
    '英伦/凯尔特民间舞曲': 'British/Celtic folk dance', '爵士即兴独奏': 'Jazz improvised solo',
    '流行歌': 'Pop song', '蒙特威尔第牧歌': 'Monteverdi madrigal', '古典钢琴奏鸣曲': 'Classical piano sonata',
    '古典弦乐四重奏': 'Classical string quartet', '古典交响曲': 'Classical symphony',
    '海顿交响曲': 'Haydn symphony', '肖邦钢琴小品': 'Chopin piano miniature', '巴赫': 'Bach',
    '众赞歌': 'Chorale', '吉格（6/8）': 'Jig (6/8)', '里尔（4/4）': 'Reel (4/4)', '角笛舞曲': 'Hornpipe',
    '华尔兹（3/4）': 'Waltz (3/4)', '摇摆乐': 'Swing', '比波普': 'Bebop', '酷爵士': 'Cool jazz',
    '硬波普': 'Hard bop', '后波普': 'Post-bop', '传统爵士': 'Traditional jazz',
    '牧歌（最高声部 + 和声）': 'Madrigal (top voice + harmony)',
    '快板乐章（4/4）': 'Allegro movement (4/4)', '小步舞曲 / 谐谑曲（3/4）': 'Minuet / scherzo (3/4)',
    '小步舞曲（3/4）': 'Minuet (3/4)', '二拍子乐章（2/4）': 'Duple-metre movement (2/4)',
    '复拍子乐章（6/8）': 'Compound-metre movement (6/8)', '玛祖卡（3/4）': 'Mazurka (3/4)',
    '前奏曲': 'Prelude', '交响曲乐章': 'Symphony movement',
    ' 大调': ' major', ' 小调': ' minor', '大调': ' major', '小调': ' minor',
    '语法）': ' grammar)',
    # 单字（最后才替换）
    '开': 'on', '关': 'off', '有': 'yes', '无': 'none',
}

# (正则, 英文模板)；模板中 {n} 引用分组，分组内容会再翻译一次
PATTERNS = [
    (r'^([A-G][♭♯b#]?) 大调 / ([a-g][♭♯b#]?) 小调$', '{1} major / {2} minor'),
    (r'^([A-G][♭♯b#]?) 大调$', '{1} major'),
    (r'^速度 (\d+) BPM$', 'Tempo {1} BPM'),
    (r'^排练号 (\S+)$', 'Rehearsal mark {1}'),
    (r'^(.+)（(.+?)语法）$', '{1} ({2} grammar)'),
    (r'^(.+)语法$', '{1} grammar'),
]

_CJK = re.compile(r'[\u3400-\u9fff]')
_PUNCT = [('：', ': '), ('，', ', '), ('（', ' ('), ('）', ')'), ('。', '. '), ('、', ', '),
          ('；', '; '), ('「', '"'), ('」', '"'), ('｜', '|'), ('？', '?'), ('！', '!')]


def _humanise(key: str) -> str:
    s = str(key).replace('_', ' ').replace('-', ' ').strip()
    return s[:1].upper() + s[1:]


def _load_data_phrases() -> dict:
    """风格包名 → name_en；taxonomy 维度标签 → 维度值 id。"""
    out = {}
    try:
        import yaml
    except Exception:
        return out
    gdir = os.path.join(_ROOT, 'skills', 'composition', 'genre')
    for p in glob.glob(os.path.join(gdir, 'packs', '*', '*', 'meta.yaml')):
        try:
            with open(p, encoding='utf-8') as f:
                m = yaml.safe_load(f) or {}
        except Exception:
            continue
        zh, en = m.get('name'), m.get('name_en') or (m.get('id') and _humanise(m['id']))
        if zh and en and _CJK.search(str(zh)):
            out[str(zh)] = str(en)
    try:
        with open(os.path.join(gdir, 'taxonomy.yaml'), encoding='utf-8') as f:
            tax = yaml.safe_load(f) or {}
        for dim in (tax.get('dimensions') or {}).values():
            for vid, v in ((dim or {}).get('values') or {}).items():
                lab = (v or {}).get('label') if isinstance(v, dict) else None
                if lab and _CJK.search(str(lab)) and str(lab) not in out:
                    out[str(lab)] = _humanise(vid)
    except Exception:
        pass
    return out


_PHRASE_ITEMS = None


def _phrase_items():
    global _PHRASE_ITEMS
    if _PHRASE_ITEMS is None:
        merged = dict(_load_data_phrases())
        merged.update({k: v for k, v in EXACT.items() if len(k) >= 2})
        merged.update(PHRASES)
        _PHRASE_ITEMS = sorted(merged.items(), key=lambda kv: -len(kv[0]))
    return _PHRASE_ITEMS


def _by_pattern(s: str):
    for pat, tmpl in PATTERNS:
        m = re.match(pat, s, re.S)
        if m:
            groups = [_to_en(g) or g for g in m.groups()]
            res = re.sub(r'\{(\d+)\}', lambda mm: groups[int(mm.group(1)) - 1], tmpl)
            if not _CJK.search(res):
                return res
    return None


def _by_phrases(s: str):
    out = s
    for zh, en in _phrase_items():
        if zh in out:
            out = out.replace(zh, en)
            if not _CJK.search(out):
                break
    if _CJK.search(out):
        return None
    for a, b in _PUNCT:
        out = out.replace(a, b)
    return re.sub(r'(?<=\S) {2,}(?=\S)', '  ', out).replace('( ', '(').replace(' )', ')')


@lru_cache(maxsize=8192)
def _to_en(s: str):
    if not s or not _CJK.search(s):
        return None
    if s in EXACT:
        return EXACT[s]
    core = s.strip()
    if core != s and core in EXACT:
        return s[:len(s) - len(s.lstrip())] + EXACT[core] + s[len(s.rstrip()):]
    res = _by_pattern(core)
    if res is None and '\n' in core:
        lines = [(_to_en(x) if _CJK.search(x) else x) for x in core.split('\n')]
        res = None if any(x is None for x in lines) else '\n'.join(lines)
    if res is None:
        res = _by_phrases(core)
    return res


def tr(s: str) -> str:
    """当前语言下的显示文字（中文模式原样返回）。"""
    if _lang != 'en' or not isinstance(s, str):
        return s
    r = _to_en(s)
    return s if r is None else r


# ---------------------------------------------------------------------------
# 控件树重译
# ---------------------------------------------------------------------------

_SEP = '\x1f'


def _swap(store, key, cur, setter):
    """store(key) / store(key, value) 读写原文记录；cur 为当前显示文字。"""
    rec = store(key)
    src, done = rec.split(_SEP, 1) if rec else (None, None)
    if _lang != 'en':
        if rec and cur == done and src != cur:
            setter(src)
        return
    if rec and cur == done:
        return
    if not cur or not _CJK.search(cur):
        return
    t = _to_en(cur)
    if t is not None and t != cur:
        store(key, cur + _SEP + t)
        setter(t)


def _qstore(obj):
    def store(key, value=None):
        if value is None:
            v = obj.property(key)
            return v if isinstance(v, str) else None
        obj.setProperty(key, value)
    return store


def _item_store(item, col):
    from PyQt5.QtCore import Qt
    role = Qt.UserRole + 731

    def store(key, value=None):
        if value is None:
            v = item.data(col, role)
            return v if isinstance(v, str) else None
        item.setData(col, role, value)
    return store


def _list_item_store(item):
    from PyQt5.QtCore import Qt
    role = Qt.UserRole + 731

    def store(key, value=None):
        if value is None:
            v = item.data(role)
            return v if isinstance(v, str) else None
        item.setData(role, value)
    return store


def retranslate_widget(w) -> None:
    """只处理单个控件自身的外壳文字（不递归）。"""
    from PyQt5 import QtWidgets as W
    st = _qstore(w)
    try:
        if w.isWindow():
            _swap(st, '_i18n_title', w.windowTitle(), w.setWindowTitle)
        _swap(st, '_i18n_tip', w.toolTip(), w.setToolTip)
        _swap(st, '_i18n_status', w.statusTip(), w.setStatusTip)
        if isinstance(w, (W.QLabel, W.QAbstractButton)):
            _swap(st, '_i18n_text', w.text(), w.setText)
        if isinstance(w, (W.QLineEdit, W.QTextEdit, W.QPlainTextEdit)):
            _swap(st, '_i18n_ph', w.placeholderText(), w.setPlaceholderText)
        if isinstance(w, W.QGroupBox):
            _swap(st, '_i18n_text', w.title(), w.setTitle)
        if isinstance(w, W.QDockWidget):
            _swap(st, '_i18n_title', w.windowTitle(), w.setWindowTitle)
        if isinstance(w, W.QTabWidget):
            for i in range(w.count()):
                _swap(st, f'_i18n_tab{i}', w.tabText(i), lambda t, i=i: w.setTabText(i, t))
                _swap(st, f'_i18n_tabtip{i}', w.tabToolTip(i), lambda t, i=i: w.setTabToolTip(i, t))
        if isinstance(w, W.QComboBox):
            for i in range(w.count()):
                _swap(st, f'_i18n_item{i}', w.itemText(i), lambda t, i=i: w.setItemText(i, t))
        if isinstance(w, W.QTreeWidget):
            hi = w.headerItem()
            if hi is not None:
                for c in range(w.columnCount()):
                    _swap(_item_store(hi, c), '', hi.text(c), lambda t, c=c: hi.setText(c, t))
            it = W.QTreeWidgetItemIterator(w)
            while it.value():
                item = it.value()
                for c in range(w.columnCount()):
                    _swap(_item_store(item, c), '', item.text(c), lambda t, c=c, item=item: item.setText(c, t))
                it += 1
        elif isinstance(w, W.QTableWidget):
            for c in range(w.columnCount()):
                hi = w.horizontalHeaderItem(c)
                if hi is not None:
                    _swap(_list_item_store(hi), '', hi.text(), hi.setText)
        elif isinstance(w, W.QListWidget):
            for i in range(w.count()):
                item = w.item(i)
                _swap(_list_item_store(item), '', item.text(), item.setText)
        if isinstance(w, W.QMenu):
            a = w.menuAction()
            _swap(_qstore(a), '_i18n_text', a.text(), a.setText)
            _swap(st, '_i18n_title', w.title(), w.setTitle)
    except RuntimeError:
        pass


def retranslate_action(a) -> None:
    st = _qstore(a)
    try:
        _swap(st, '_i18n_text', a.text(), a.setText)
        _swap(st, '_i18n_tip', a.toolTip(), a.setToolTip)
        _swap(st, '_i18n_status', a.statusTip(), a.setStatusTip)
    except RuntimeError:
        pass


def retranslate_tree(root, visible_only: bool = False) -> None:
    from PyQt5 import QtWidgets as W
    from PyQt5.QtWidgets import QAction
    try:
        widgets = [root] + root.findChildren(W.QWidget)
        actions = root.findChildren(QAction)
    except RuntimeError:
        return
    for w in widgets:
        try:
            if visible_only and not w.isVisible() and not isinstance(w, W.QMenu):
                continue
        except RuntimeError:
            continue
        retranslate_widget(w)
    for a in actions:
        retranslate_action(a)


class Retranslator:
    """安装到 QApplication：新窗口显示时翻译、英文模式下定时扫一遍动态文字。"""

    def __init__(self, app, interval_ms: int = 1000):
        from PyQt5.QtCore import QObject, QEvent, QTimer

        outer = self

        class _Filter(QObject):
            def eventFilter(self, obj, ev):
                if _lang == 'en' and ev.type() == QEvent.Show:
                    try:
                        from PyQt5.QtWidgets import QWidget
                        if isinstance(obj, QWidget):
                            if obj.isWindow():
                                outer._hook_status(obj)
                                retranslate_tree(obj)
                            else:
                                retranslate_widget(obj)
                    except RuntimeError:
                        pass
                return False

        self.app = app
        self._filter = _Filter()
        app.installEventFilter(self._filter)
        self._timer = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._sweep)
        self._timer.start()

    def _hook_status(self, win):
        from PyQt5.QtWidgets import QMainWindow
        if not isinstance(win, QMainWindow) or win.property('_i18n_sb'):
            return
        win.setProperty('_i18n_sb', True)
        sb = win.statusBar()
        busy = [False]

        def on_msg(msg):
            if _lang != 'en' or busy[0] or not msg:
                return
            t = _to_en(msg)
            if t and t != msg:
                busy[0] = True
                try:
                    sb.showMessage(t, 0)
                finally:
                    busy[0] = False
        sb.messageChanged.connect(on_msg)

    def _sweep(self):
        if _lang != 'en':
            return
        for w in self.app.topLevelWidgets():
            try:
                if w.isVisible():
                    self._hook_status(w)
                    retranslate_tree(w, visible_only=True)
            except RuntimeError:
                pass

    def apply(self, lang: str, persist: bool = True) -> None:
        set_language(lang, persist)
        for w in self.app.topLevelWidgets():
            try:
                self._hook_status(w)
                retranslate_tree(w)
            except RuntimeError:
                pass
