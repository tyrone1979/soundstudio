# -*- coding: utf-8 -*-
"""帮助文档模块（单一事实来源）。

把「命令」与「功能」两套目录集中成结构化数据，统一渲染成 markdown：
- 每个命令都带：语法、说明、示例、效果。
- 每个功能都带：一句话、相关命令、示例、效果。

`/help`（无参数）输出完整目录；`/help <命令或功能名>` 输出单项详情。
`build_help()` 同时被 `runs/help.md` 复用，保证聊天内帮助与文档文件始终一致。

设计契合护城河：本模块纯数据 + 纯文本渲染，零外部依赖、零 LLM、确定性。
"""

from typing import Dict, List, Optional

# —— 命令目录（每个命令都给一个示例）——
COMMANDS: List[Dict] = [
    # 会话与帮助
    {
        "cmd": "/help", "cat": "会话与帮助", "args": "[命令或功能名]",
        "desc": "显示帮助文档。可带一个命令或功能名查看单项说明与示例；"
                "不带参数则输出完整命令+功能目录。/?, /help 等价。",
        "example": "/help /chords",
        "result": "输出 /chords 命令的语法、说明、示例与效果。",
        "aliases": ["/?", "help", "帮助"],
    },
    {
        "cmd": "/assist", "cat": "编辑与辅助", "args": "[on|off|style|inspire|harmony|status]",
        "desc": "音符级辅助作曲（程序化补全，不调 LLM）。"
                "开启后：Tab 下一音、Ctrl+Tab 本小节、Ctrl+Enter 全曲；1–5 选候选。",
        "example": "/assist inspire jazzy",
        "result": "切换辅助开关、绑定风格、灵感重排权重，或写入建议和声轨。",
        "aliases": ["assist", "辅助作曲"],
    },
    {
        "cmd": "/new", "cat": "会话与帮助", "args": "",
        "desc": "开始新曲会话：重置当前乐谱与创作状态，但保留调性/速度等偏好。",
        "example": "/new /style 圆舞曲 快板轻盈",
        "result": "清空当前曲目，生成新的 composition_id，并按 /style 设定基调。",
        "aliases": ["new", "新曲"],
    },
    {
        "cmd": "/clear", "cat": "会话与帮助", "args": "",
        "desc": "清空对话历史与当前曲目状态，保留已设偏好（调性/速度/风格等）。",
        "example": "/clear",
        "result": "会话被清空，但 /key、/tempo、/style 等偏好仍在。",
        "aliases": ["clear", "清空"],
    },
    {
        "cmd": "/config", "cat": "会话与帮助", "args": "",
        "desc": "显示当前配置与已设偏好（API 接入情况、调性、速度、风格、Groove 等）。",
        "example": "/config",
        "result": "返回一份配置摘要，便于确认创作环境。",
        "aliases": ["config", "配置"],
    },
    # 演示与导出
    {
        "cmd": "/demo", "cat": "演示与导出", "args": "",
        "desc": "已停用。新曲请用 /compose-grammar（例：/compose-grammar 奏鸣曲 小步舞曲）。",
        "example": "/compose-grammar 奏鸣曲",
        "result": "提示改用 /compose-grammar。",
        "aliases": ["demo", "演示"],
    },
    {
        "cmd": "/export", "cat": "演示与导出", "args": "",
        "desc": "导出当前乐谱为 MusicXML + MIDI（若开启 Groove，则 MIDI 含人性化处理）。",
        "example": "/export",
        "result": "在 scores/ 下写出当前曲目的 .xml 与 .mid。",
        "aliases": ["export", "导出"],
    },
    # 分析与建议闭环
    {
        "cmd": "/analyze", "cat": "分析与建议", "args": "",
        "desc": "分析当前曲目：风格重叠率、16 维特征、声部/调性/曲式等，输出可读报告。",
        "example": "/analyze",
        "result": "返回风格向量比对与特征摘要，不含任何 LLM 主观评价。",
        "aliases": ["analyze", "分析"],
    },
    {
        "cmd": "/advice", "cat": "分析与建议", "args": "",
        "desc": "给出修改建议（只分析、不改谱），待确认后用 /apply 执行。",
        "example": "/advice",
        "result": "输出针对当前曲的修改建议，状态记为「待执行」。",
        "aliases": ["advice", "建议"],
    },
    {
        "cmd": "/apply", "cat": "分析与建议", "args": "",
        "desc": "执行上一次 /advice 给出的建议，真正改写乐谱。",
        "example": "/apply",
        "result": "按建议重写当前乐谱；无建议或无色谱时给出提示。",
        "aliases": ["apply", "执行"],
    },
    # 偏好设置（创作控件）
    {
        "cmd": "/key", "cat": "偏好设置", "args": "<调性>",
        "desc": "设置调性，例如 C / G / Am / Dm / bB。若已有乐谱则标记为移调待执行。",
        "example": "/key Am",
        "result": "调性 → Am（sharps=-3, minor）；有谱时提示可移调。",
        "aliases": ["key", "调性"],
    },
    {
        "cmd": "/tempo", "cat": "偏好设置", "args": "<bpm>",
        "desc": "设置速度，范围 40–240。",
        "example": "/tempo 72",
        "result": "速度 → ♩=72。",
        "aliases": ["tempo", "速度"],
    },
    {
        "cmd": "/meter", "cat": "偏好设置", "args": "<a/b>",
        "desc": "设置拍号，例如 4/4、3/4、6/8。",
        "example": "/meter 3/4",
        "result": "拍号 → 3/4。",
        "aliases": ["meter", "拍号"],
    },
    {
        "cmd": "/bars", "cat": "偏好设置", "args": "<n>",
        "desc": "设置小节数，范围 4–32（影响新建/下一首生成；不改当前已打开的谱）。",
        "example": "/bars 16",
        "result": "小节数 → 16。",
        "aliases": ["bars", "小节"],
    },
    {
        "cmd": "/addbars", "cat": "编辑类", "args": "<n>",
        "desc": "在当前乐谱末尾追加 n 个空小节（1–32）。也可用菜单「文件 → 增加小节…」"
                "或编辑工具条「+小节」。",
        "example": "/addbars 4",
        "result": "谱末增加 4 个空小节，可直接点写。",
        "aliases": ["addbars", "加小节", "增加小节"],
    },
    {
        "cmd": "/addpart", "cat": "编辑类", "args": "[piano|bass|violin|cello|…]",
        "desc": "为当前乐谱追加空声部。单声部用 piano/左手 可升为钢琴大谱表。"
                "也可用菜单「文件 → 添加声部…」或编辑工具条「+声部」。",
        "example": "/addpart piano",
        "result": "在旋律旁加上 LH 低音谱表（花括号大谱表）。",
        "aliases": ["addpart", "addvoice", "/addvoice", "添加声部", "加声部"],
    },
    {
        "cmd": "/accomp", "cat": "编辑类", "args": "[bass|block|broken|alberti]",
        "desc": "根据旋律声部写入配乐（确定性）。无低音声部时自动添加。"
                "也可用菜单「文件 → 设置配乐…」或编辑工具条「配乐」。",
        "example": "/accomp alberti",
        "result": "低音声部写入阿尔贝蒂低音伴奏。",
        "aliases": ["accomp", "accompaniment", "/accompaniment", "配乐", "设置配乐", "伴奏"],
    },
    {
        "cmd": "/voices", "cat": "偏好设置", "args": "0|2|3|4",
        "desc": "设置声部数。0=由风格自动推导，2/3/4 为固定声部数。",
        "example": "/voices 3",
        "result": "声部数 → 3（0=自动）。",
        "aliases": ["voices", "声部数"],
    },
    {
        "cmd": "/style", "cat": "偏好设置", "args": "<风格名>",
        "desc": "设定/绑定风格包。风格名写命令后，创作描述写后面或下一句。"
                "输入 /style 会弹出可选列表——只列「学过」的包（有 grammar.yaml，"
                "见右侧检查器「风格包」下拉）；未学过的包不在候选里。",
        "example": "/style 众赞歌 庄重匀净",
        "result": "风格 → 众赞歌（并绑定 genre 包）；「庄重匀净」记入创作描述。",
        "aliases": ["style", "风格"],
    },
    {
        "cmd": "/fugue", "cat": "偏好设置", "args": "auto|full|off",
        "desc": "赋格设置。auto=仅当 brief 明确提到赋格/卡农/创意曲等才加（「对位」本身不加，默认）；full=整首写成赋格；off=不加赋格。",
        "example": "/fugue full",
        "result": "赋格 → full：整首按赋格写法生成。",
        "aliases": ["fugue", "赋格"],
    },
    # 生成类
    {
        "cmd": "/skeleton", "cat": "生成类", "args": "[风格名]",
        "desc": "纯确定性骨架生成（不调 LLM）：按风格包特征直接铺骨架，可复现可审计。",
        "example": "/skeleton 圆舞曲",
        "result": "按圆舞曲指纹确定性生成一段骨架谱，全程无随机。",
        "aliases": ["skeleton", "骨架"],
    },
    {
        "cmd": "/compose-grammar", "cat": "生成类", "args": "<自由描述>",
        "desc": "唯一的作曲入口：把「圆舞曲，带一点雨夜钢琴的忧伤」这类"
                "自然语言描述编译成风格规则并生成，含风格权重混合"
                "（描述里写「40% 偏 A」即等价于旧 /interpolate 的 t）。",
        "example": "/compose-grammar 圆舞曲，40% 偏雨夜钢琴",
        "result": "按描述混合风格包并确定性生成 SATB / 简谱草稿。",
        "aliases": ["compose-grammar", "作曲", "生成"],
    },
    {
        "cmd": "/system", "cat": "生成类", "args": "<体系>",
        "desc": "体系层生成：板腔 / 曲牌 / 拉格 / 十二音序列等非功能和声体系。",
        "example": "/system 十二音序列",
        "result": "按十二音序列技法生成，不走常规功能和声。",
        "aliases": ["system", "体系", "十二音"],
    },
    # 编辑类（需先有乐谱）
    {
        "cmd": "/continue", "cat": "编辑类", "args": "",
        "desc": "从当前乐谱结尾继续续写（需先有曲目）。",
        "example": "/continue",
        "result": "在现有结尾之后接续发展，小节数增加。",
        "aliases": ["continue", "续写"],
    },
    {
        "cmd": "/extend", "cat": "编辑类", "args": "[n]",
        "desc": "扩写 n 小节（n 为数字时同步设为小节数，需先有曲目）。",
        "example": "/extend 8",
        "result": "将乐曲再延展 8 小节并续写。",
        "aliases": ["extend", "扩展"],
    },
    {
        "cmd": "/develop", "cat": "编辑类", "args": "",
        "desc": "动机发展段（需先有曲目）。",
        "example": "/develop",
        "result": "生成以已有动机展开的发展段落。",
        "aliases": ["develop", "发展"],
    },
    {
        "cmd": "/variation", "cat": "编辑类", "args": "",
        "desc": "变奏段（需先有曲目）。",
        "example": "/variation",
        "result": "对主题做变奏处理，生成变奏段落。",
        "aliases": ["variation", "变奏"],
    },
    {
        "cmd": "/harmonize", "cat": "编辑类", "args": "",
        "desc": "为当前旋律重新配置和声（需先有曲目）。",
        "example": "/harmonize",
        "result": "用规则引擎重配和声，改写和弦层。",
        "aliases": ["harmonize", "配和声"],
    },
    {
        "cmd": "/orchestrate", "cat": "编辑类", "args": "[配器]",
        "desc": "重新配器，可指定乐器组合（需先有曲目）。",
        "example": "/orchestrate 弦乐四重奏",
        "result": "把声部分配到弦乐四重奏的音色上。",
        "aliases": ["orchestrate", "配器"],
    },
    {
        "cmd": "/transpose", "cat": "编辑类", "args": "<调性>",
        "desc": "移调（需先有曲目）。",
        "example": "/transpose D",
        "result": "把当前乐谱整体移调到 D。",
        "aliases": ["transpose", "移调"],
    },
    {
        "cmd": "/shorter", "cat": "编辑类", "args": "",
        "desc": "精简/裁短当前曲目（需先有曲目）。",
        "example": "/shorter",
        "result": "删除冗余段落，缩短乐曲。",
        "aliases": ["shorter", "精简"],
    },
    {
        "cmd": "/longer", "cat": "编辑类", "args": "",
        "desc": "扩充/加长当前曲目（需先有曲目）。",
        "example": "/longer",
        "result": "在中段或结尾补充材料，加长乐曲。",
        "aliases": ["longer", "加长"],
    },
    {
        "cmd": "/simplify", "cat": "编辑类", "args": "",
        "desc": "简化当前谱面（需先有曲目）。",
        "example": "/simplify",
        "result": "降低密度/难度，输出更简洁的版本。",
        "aliases": ["simplify", "简化"],
    },
    {
        "cmd": "/complexify", "cat": "编辑类", "args": "",
        "desc": "复杂化/加花当前谱面（需先有曲目）。",
        "example": "/complexify",
        "result": "增加装饰音与对位细节，更华丽。",
        "aliases": ["complexify", "复杂化"],
    },
    {
        "cmd": "/rewrite", "cat": "编辑类", "args": "",
        "desc": "整体重写当前曲目（也用于 /apply 内部执行建议）。",
        "example": "/rewrite",
        "result": "在保留意图的前提下重新生成一版。",
        "aliases": ["rewrite", "重写"],
    },
    {
        "cmd": "/learn-score", "cat": "风格学习", "args": "<风格包>",
        "desc": "把当前乐谱「学进」对应风格包（直接更新 genre/packs 的统计与指纹）。",
        "example": "/learn-score chorale",
        "result": "用当前曲更新 chorale 风格包的特征统计，使后续生成更贴近。",
        "aliases": ["learn-score", "学习", "学进风格"],
    },
    # 增强功能命令
    {
        "cmd": "/groove", "cat": "增强功能", "args": "[on|off] [swing] [humanize]",
        "desc": "人性化后处理（确定性，仅导出/回放 MIDI 时生效，不改符号谱）。"
                "on/off 开关；swing 0–0.6；humanize 0–1。",
        "example": "/groove on 0.2 0.5",
        "result": "Groove → on（swing=0.20, humanize=0.50），下次 /export 生效。",
        "aliases": ["groove", "人性化", "摇摆"],
    },
    {
        "cmd": "/refine", "cat": "增强功能", "args": "[风格名]",
        "desc": "风格保真度闭环精炼（确定性 DPO 等价物：seed 分支 + 旋钮坐标上升"
                "最大化 style_overlap；可审计、不调 LLM）。",
        "example": "/refine 赋格",
        "result": "在锁定 seed 下搜索最优风格旋钮，输出高保真谱 + 审计摘要。",
        "aliases": ["refine", "精炼", "风格保真"],
    },
    {
        "cmd": "/import", "cat": "增强功能", "args": "<MusicXML路径>",
        "desc": "MusicXML 往返导入 + 再校验（对标 AIVA 的 DAW 编辑闭环）："
                "用确定性规则引擎复检，并重新导出规范化文件。",
        "example": "/import /path/to/song.musicxml",
        "result": "输出完整性/声部/音域/风格重叠校验报告，并重导出规范化 XML+MIDI。",
        "aliases": ["import", "导入", "往返"],
    },
    {
        "cmd": "/reverse-prompt", "cat": "增强功能", "args": "[MusicXML|JSON路径]",
        "desc": "MusicXML→纯中文逐音文字描述（无 JSON，确定性，不调 LLM）。"
                "再用 /replay 可将文字还原为 MusicXML。",
        "example": "/reverse-prompt scores/chats/demo.xml",
        "result": "写出 runs/reverse_*/score_description.md（纯文字）。",
        "aliases": ["reverse-prompt", "逆向提示词", "逆向复现", "文字描述"],
    },
    {
        "cmd": "/replay", "cat": "增强功能", "args": "[score_description.md路径]",
        "desc": "把 /reverse-prompt 生成的纯文字描述还原为乐谱（MusicXML←文字，"
                "确定性，不调 LLM）。",
        "example": "/replay runs/reverse_abc/score_description.md",
        "result": "载入与源谱音符级一致的 score_spec。",
        "aliases": ["replay", "复现"],
    },
    {
        "cmd": "/brief-expand", "cat": "增强功能", "args": "<简单提示词>",
        "desc": "把短句（如「写一首明朗的交响曲快板乐章」）确定性扩写为专业分段 brief，"
                "不生成乐谱；创曲时也会自动扩写。",
        "example": "/brief-expand 写一首明朗的交响曲快板乐章，像舒伯特早期",
        "result": "输出含编制/段落/速度阶梯的专业提示词。",
        "aliases": ["brief-expand", "扩写提示词"],
    },
    {
        "cmd": "/chords", "cat": "增强功能", "args": "[调性] [进行] [write]",
        "desc": "和弦进行顾问（确定性规则引擎，不调 LLM，对标 Hooktheory/Captain Chords）："
                "调内合法性 + 根音运动 + 功能圈 + 导音解决 + 四声部实现 + 平行五八度复检。"
                "末尾加 write 则写入钢琴大谱表（等同 /satb）。",
        "example": "/chords I V vi IV",
        "result": "输出进行校验、功能圈分析、SATB 实现与平行检测报告。",
        "aliases": ["chords", "和弦", "和弦顾问"],
    },
    {
        "cmd": "/satb", "cat": "增强功能", "args": "[调性] <进行>",
        "desc": "在钢琴大谱表上写出四部和声（RH=Soprano+Alto，LH=Tenor+Bass）；"
                "确定性声部引导，含平行五八度检测；写完可进编辑模式继续改。",
        "example": "/satb I V vi IV",
        "result": "生成带花括号的钢琴大谱表，每小节一个和弦的四声部叠写。",
        "aliases": ["satb", "四部和声", "大谱表四部"],
    },
    {
        "cmd": "/learn-grammar", "cat": "风格学习",
        "args": "[chorale|folk|jazz|pop|madrigal|sonata|strings|symphony|chopin|all]",
        "desc": "从乐谱语料学习可解释作曲语法，蒸馏成带编号的规则脚本 grammar.yaml 与中文说明 grammar.md，"
                "并报告留出集困惑度。chorale 与 leadsheet 两套流水线共用 score_learning.learn_core："
                "Witten-Bell 回退、只删低频上下文的剪枝（留出困惑度变差则自动回退）、k-means++ 多次重启。"
                "不带参数 = music21 自带的 350 首巴赫众赞歌（逐拍和弦识别 → 乐句/终止切分 → 句终规划、"
                "终止公式、按“距终止拍数”条件化的和声转移、旋律轮廓、声部配置）。"
                "folk = Nottingham 民谣（吉格/里尔/角笛舞/华尔兹）、jazz = Weimar 爵士独奏（摇摆/比波普/酷派/"
                "硬波普/后波普/传统）、pop = Billboard 流行歌、madrigal = 蒙特威尔第牧歌：按子风格学曲式"
                "（AABB/AABA/ABAB…）、段落终止公式、按段内位置条件化的和弦转移、小节节奏模板或拍内节奏"
                "单元链、旋律音程/和弦音比例/音域与硬规则 R01–R10（复杂度预算随声部/配器自动伸缩）。"
                "sonata = KernScores 贝多芬/莫扎特/海顿钢琴奏鸣曲、strings = 贝多芬/莫扎特/海顿弦乐四重奏、"
                "symphony = 海顿《伦敦交响曲》第 99–104 号 + CCARH 贝多芬九部交响曲（MuseData）"
                " + S3 莫扎特第 41 / 贝多芬第 9（只读弦乐/全谱分析和声）、"
                "chopin = 肖邦玛祖卡：乐谱没有和弦标注，分析器自己识别和声——逐拍（4/4 每半小节）按音高"
                "证据给候选和弦打分 → 维特比平滑（强拍换和弦代价低）→ 24 个调上的维特比找局部调，"
                "≥8 小节的调区各自成段、和弦按局部调记级数；词表只留占比 ≥1% 且有古典和声功能的和弦"
                "（调内、副属、借用、那不勒斯），转移概率与整体频率按 1:3 插值以抵消识别噪声；"
                "子风格按拍号分（快板 4/4、小步舞曲 3/4、二拍子 2/4、复拍子 6/8）。all = 全部。"
                "说明：/learn-score 是把当前曲学进风格包指纹；/learn-grammar 才是可解释规则学习。",
        "example": "/learn-grammar jazz",
        "result": "写出 skills/composition/genre/packs/<风格>/grammar.yaml / grammar.md 并列出各子风格的"
                  "曲子数、曲式与困惑度（含 prune_kept / bigram_coverage）。",
        "aliases": ["learn-grammar", "学习语法", "可解释语法", "巴赫众赞歌学习", "民谣语法", "爵士语法",
                    "流行语法", "牧歌语法", "奏鸣曲语法", "四重奏语法", "交响曲语法", "肖邦语法"],
    },
    {
        "cmd": "/learn-all", "cat": "风格学习",
        "args": "[--skip chorale|leadsheet|relation] [--style jazz pop] [--dry-run]",
        "desc": "统一学习入口：一次跑完三条流水线并全部编译入库 —— chorale（巴赫众赞歌）、"
                "leadsheet（8 个体裁）、relation（Weimar 人工段落标注 → 段落结构与关系）。"
                "前两条产出和声/旋律/节奏规则，第三条产出 form_plan（段落角色、真实段长、"
                "段间引用/承接/对比/递进关系），注入风格包后 /compose-grammar 的 plan_form "
                "优先采用它规划曲式。参数与命令行 "
                "`python -m score_learning.relation.learn_all` 一致；"
                "不带参数 = 全量三条都跑。--dry-run 只报告会发生什么、不写盘。",
        "example": "/learn-all --skip chorale --skip leadsheet",
        "result": "重写各风格包 grammar.yaml（含 form_plan），并在 runs/relation/ 下产出"
                  "learn_all_report.md 与 learn_all_stages.json（含各阶段耗时与留出集困惑度）。",
        "aliases": ["learn-all", "统一学习", "全部学习", "全量学习", "一键学习"],
    },
    {
        "cmd": "/compose-grammar", "cat": "风格学习",
        "args": "[风格 子风格] [major|minor] [小节数] [弱起|强拍起] [乐器] [seed=…] [t=温度]",
        "desc": "只读学到的规则脚本、确定性地作曲（不调 LLM），同一 seed 逐音符复现。"
                "不带风格词 = SATB 巴赫众赞歌：句终规划 → 终止公式 → 和声+女高音联合搜索 → 低音 → 内声部 → "
                "经过音；平行五八度/交叉/导音/七音等硬规则 0 违例，每拍可追溯到规则编号（H 和声 / C 终止 / "
                "F 句终规划 / M 旋律轮廓）。弱起（pickup）= 一拍弱起小节 + 指定小节数，句终落第 3 拍并加延长；"
                "不指定时按语料比例（约 72% 弱起）抽签。"
                "带风格词（民谣/jig/reel/hornpipe/waltz、爵士/swing/bebop/cool/hardbop/postbop/traditional、"
                "流行、牧歌）= 旋律 + 钢琴伴奏：按学到的曲式分段（重复段逐音符再现）→ 每段和弦进行（终止公式 "
                "+ 位置条件转移）→ 节奏（小节模板或节奏单元链）→ 旋律动态规划 → 按风格写伴奏（民谣“嘭-嚓”、"
                "爵士走动低音 + 三七和弦、流行分解律动、牧歌持续和弦）；R01–R10 硬规则（强拍非和弦音解决、"
                "跳进上限、音域、主和弦收尾、语料未见的和弦转移、三声部之间无平行五八度、节奏只用学到的"
                "模板、同一和弦连续小节上限）0 违例。小节数取 8 的倍数（默认 32，牧歌 24）；乐器可写 "
                "violin/flute/trumpet/clarinet/voice/soprano 或中文名；调号/速度没指定时用风格默认。"
                "奏鸣曲（快板/小步舞曲/二拍子/复拍子）与肖邦玛祖卡 = 钢琴独奏：右手旋律，左手按拍号写"
                "阿尔贝蒂低音（4/4、2/4）、分解三和弦（6/8）或低音-和弦-和弦（3/4），每个音型候选都"
                "逐起音检查与旋律无平行五八度；四重奏 = 第一小提琴旋律、第二小提琴 + 中提琴双音配位"
                "同音反复（3/4 第一拍休止）、大提琴低音，四个声部两两无平行五八度。"
                "交响曲（海顿 / 莫扎特 / 贝多芬 / symphony：快板/小步舞曲/二拍子/复拍子）= 古典双管缩编总谱：长笛、双簧管、"
                "圆号、大管、定音鼓 + 弦乐四部；弦乐组按四重奏织体全程演奏，偶数段与末段全奏——长笛高八度"
                "/双簧管同度加倍旋律、大管加倍大提琴、圆号持续主/属音（避开与各声部的平行）、定音鼓在主/属"
                "和弦小节第一拍击奏。"
                "不用斜杠也行：直接说「作一曲小步舞曲」「写一首海顿交响曲小步舞曲」「来一首忧伤的玛祖卡」"
                "「写一首巴赫众赞歌」「做一首交响」——关键词或 LLM 路由到 /compose-grammar 后做确定性作曲"
                "（不再走 Planner / translator 写新曲）。识别不出已学风格时会提示用法。"
                "只说「小步舞曲」= 钢琴奏鸣曲小步舞曲，带「弦乐/四重奏」= "
                "四重奏，带「交响/管弦」= 交响曲。",
        "example": "/compose-grammar 爵士 bebop 32 seed=1",
        "result": "众赞歌：四部谱面 + 每句的终止目标与和声进行；其他风格：旋律 + 钢琴大谱表 / 四重奏 / "
                  "管弦乐总谱，回复里列出曲式与每段的终止公式和和弦。",
        "aliases": ["compose-grammar", "语法作曲", "众赞歌", "chorale_grammar", "leadsheet_grammar",
                    "民谣作曲", "爵士作曲", "流行作曲", "牧歌作曲", "奏鸣曲作曲", "四重奏作曲",
                    "交响曲作曲", "肖邦作曲", "小步舞曲", "learned_grammar"],
    },
    {
        "cmd": "/render", "cat": "增强功能",
        "args": "[piano|gm] [reverb 0-0.9] [velocity <预设>] [<音色.sf2>]",
        "desc": "渲染增强（确定性、表演层、不调 LLM，对标 AIVA 的渲染管线）："
                "选 SoundFont 音色 + 卷积混响 + 力度曲线。仅动音色/空间/力度，不改符号谱。",
        "example": "/render piano reverb 0.3 velocity soft",
        "result": "用 Salamander 钢琴采样 + 30% 混响 + 柔和力度渲染当前乐谱 → scores/render/*.wav。",
        "aliases": ["render", "渲染", "混响", "力度", "soundfont"],
    },
    {
        "cmd": "/batch", "cat": "增强功能", "args": "[风格包/家族...]",
        "desc": "风格包批量生成流水线（确定性、可审计，复用确定性内核）："
                "枚举风格包→每包生成 N 首确定性骨架乐谱→算风格保真度→出报告。",
        "example": "/batch fugue chorale",
        "result": "为 fugue、chorale 各生成 1 首确定性乐谱与保真度报告 runs/batch_report.md。",
        "aliases": ["batch", "批量", "流水线", "批量生成"],
    },
]

# —— 功能目录（每个功能都给一个示例）——
FEATURES: List[Dict] = [
    {
        "id": "自然语言作曲", "cat": "核心能力",
        "summary": "用自然语言描述想要的曲子，自动匹配风格包并生成符号谱（MusicXML/MIDI）。",
        "commands": ["（自然语言输入）", "/style", "/new"],
        "example": "做一首雨夜钢琴风格的圆舞曲，带一点忧伤",
        "result": "匹配「雨夜钢琴」风格包，生成 3/4 拍、柔和哀婉的圆舞曲。",
        "aliases": ["作曲", "创作", "自然语言"],
    },
    {
        "id": "风格包与绑定", "cat": "核心能力",
        "summary": "风格包是护城河基石：零训练数据的确定性指纹。"
                "/style 设定基调并可绑定 genre 包，创作描述单独记录。",
        "commands": ["/style"],
        "example": "/style 圆舞曲 快板轻盈",
        "result": "绑定圆舞曲风格包，并把「快板轻盈」作为创作描述传给生成器。",
        "aliases": ["风格", "style"],
    },
    {
        "id": "风格插值 / 参数化风格合成", "cat": "增强功能",
        "summary": "在两个风格之间确定性插值，得到「二者之间」的新风格——"
                "现由 /compose-grammar 的自然语言权重表达承接"
                "（旧 /interpolate 入口已退役，底层插值原语仍可用）。",
        "commands": ["/compose-grammar"],
        "example": "/compose-grammar 圆舞曲，40% 偏雨夜钢琴",
        "result": "在雨夜钢琴与圆舞曲间按 0.4 权重生成过渡风格乐曲。",
        "aliases": ["插值", "风格插值", "interpolate", "参数化风格"],
    },
    {
        "id": "赋格与对位", "cat": "核心能力",
        "summary": "内置赋格引擎：仅当 brief 明确提到赋格/卡农/创意曲时写入赋格段，"
                   "或整首写成赋格（主题-答题-展开）。普通对位提示不会自动加赋格。",
        "commands": ["/fugue"],
        "example": "/fugue full",
        "result": "整首按赋格写法生成（二声部时为二声部创意曲）。",
        "aliases": ["赋格", "fugue"],
    },
    {
        "id": "确定性骨架", "cat": "核心能力",
        "summary": "不调 LLM 的纯确定性骨架：按风格包指纹直接铺节奏/和声骨架，可复现可审计。"
                "旧的 `/skeleton` 入口已退役，该能力现由 /compose-grammar 内部调用。",
        "commands": ["/compose-grammar"],
        "example": "/compose-grammar 圆舞曲",
        "result": "确定性生成圆舞曲骨架，seed 固定时结果完全可复现。",
        "aliases": ["骨架", "skeleton", "确定性生成"],
    },
    {
        "id": "Groove 人性化后处理", "cat": "增强功能",
        "summary": "导出/回放 MIDI 时做确定性人性化：力度微扰 + 微量 timing + swing，"
                "落在表演层，不改符号谱正确性。",
        "commands": ["/groove"],
        "example": "/groove on 0.2 0.5（然后 /export）",
        "result": "回放更有「人味」，且因 seed 固定而可复现。",
        "aliases": ["groove", "人性化", "摇摆"],
    },
    {
        "id": "风格保真度闭环精炼", "cat": "增强功能",
        "summary": "确定性 DPO 等价物：锁 seed、用 seed 分支与旋钮坐标上升最大化风格重叠率，"
                "输出可审计的精炼谱。",
        "commands": ["/refine"],
        "example": "/refine 赋格",
        "result": "在风格指纹上收敛到高保真版本，并给出每一轮 overlap 的审计轨迹。",
        "aliases": ["refine", "精炼", "风格保真"],
    },
    {
        "id": "MusicXML 往返导入", "cat": "增强功能",
        "summary": "对标 AIVA 的 DAW 编辑闭环：导入外部（含 DAW 改过的）MusicXML，"
                "用规则引擎复检并重新规范化导出。",
        "commands": ["/import"],
        "example": "/import /path/to/song.musicxml",
        "result": "校验时值完整性/声部引导/音域/风格重叠，并重导出规范文件。",
        "aliases": ["import", "导入", "往返"],
    },
    {
        "id": "五线谱记谱符号", "cat": "核心能力",
        "summary": "谱面显示与 MusicXML 导入/导出共用同一套记谱模型，以下符号均可显示且往返不丢："
                "谱号（高音/低音/中音/次中音/高音8vb/低音8vb，可曲中换谱号）、调号（含换调还原号）、"
                "拍号（数字或 C/¢，可曲中换拍）、符杠（含次级符杠）、延音线（可跨小节/跨行）、"
                "圆滑线（可嵌套）、三连音等连音（括号+数字）、倚音、"
                "演奏法（跳音/短跳音/重音/强重音/保持音/延长记号）、"
                "装饰音（颤音/波音/逆波音/回音）、琶音、换气记号、"
                "力度（小节首与小节中途）与渐强/渐弱楔形、速度（文字 + ♩=数值）、文字指示、"
                "排练号、歌词、和弦记号、双纵线/终止线/虚线小节线、反复记号与房子号、弱起小节；"
                "调内变音按调号与小节内已出现的升降自动省略/补还原号，重升/重降用矢量绘制。",
        "commands": ["/import", "/export"],
        "example": "/import scores/chopin_mazurka.xml",
        "result": "三连音、换调、反复等按原谱显示；编辑后导出再导入与原谱逐音一致。",
        "aliases": ["记谱", "符号", "三连音", "延音线", "反复", "倚音", "装饰音", "谱号"],
    },
    {
        "id": "乐谱逆向复现", "cat": "增强功能",
        "summary": "MusicXML→纯中文逐音文字描述→MusicXML（无 JSON）；"
                "/replay 按文字还原，音符级一致，不调 LLM。",
        "commands": ["/reverse-prompt", "/replay"],
        "example": "/reverse-prompt\n/replay",
        "result": "产出 score_description.md；复现后与源谱音符级一致。",
        "aliases": ["逆向", "复现", "reverse", "replay", "文字描述"],
    },
    {
        "id": "简单提示词扩写", "cat": "增强功能",
        "summary": "把「写一首明朗的交响曲快板乐章」等短句确定性扩成专业分段 brief；"
                "创曲路径自动触发，也可用 /brief-expand 只看扩写结果。",
        "commands": ["/brief-expand"],
        "example": "/brief-expand 写一首明朗的交响曲快板乐章，像舒伯特早期",
        "result": "输出含编制、速度阶梯、段落对比的可执行专业提示词。",
        "aliases": ["扩写", "brief", "简单提示词"],
    },
    {
        "id": "和弦进行顾问", "cat": "增强功能",
        "summary": "对标 Hooktheory / Captain Chords 的交互式和弦顾问：纯和声逻辑、零依赖、"
                "不调 LLM；调内合法性 + 功能圈 + 导音解决 + 四声部 + 平行复检。"
                "加 write 可写入钢琴大谱表。",
        "commands": ["/chords", "/satb"],
        "example": "/chords I V vi IV\n/satb I V vi IV",
        "result": "对进行做校验、功能分析、SATB 实现并检测平行五八度；"
                  "/satb 直接生成钢琴大谱表四部和声。",
        "aliases": ["chords", "和弦", "和弦顾问", "四部和声", "satb"],
    },
    {
        "id": "规则库（作曲库管理器）", "cat": "核心能力",
        "summary": "作曲库「规则库」页统一管理对位权威、和声/声部/乐句教学规则与全部"
                "确定性内核脚本；每条可同时查看文档与实现源码。菜单：作曲库 → 规则库。"
                "规则摘要会自动注入 Planner/Translator 提示词，供 LLM 按意图选字段与脚本。",
        "commands": ["作曲库管理器（Ctrl+L）→ 规则库"],
        "example": "打开作曲库管理器 → 规则库 → 第一类二声部对位 / 终止式 / 脚本 melody",
        "result": "左侧按分类浏览规则；右侧文档 + 代码双栏；创曲时摘要进入 Planner/Translator。",
        "aliases": ["规则库", "规则", "rule library", "作曲库规则"],
    },
    {
        "id": "钢琴大谱表四部和声", "cat": "增强功能",
        "summary": "在钢琴大谱表（RH/LH 花括号）上写四部和声：S+A 叠写高音谱表，"
                "T+B 叠写低音谱表；菜单「新建钢琴四部和声谱」或 /satb。",
        "commands": ["/satb", "/chords"],
        "example": "/satb Bb I IV V I",
        "result": "打开可编辑的钢琴大谱表，每小节一个和弦的四声部。",
        "aliases": ["大谱表", "钢琴四部", "SATB", "四声部"],
    },
    {
        "id": "二声部对位（钢琴大谱表）", "cat": "增强功能",
        "summary": "第一类二声部对位（对位法权威规则）：Planner 识别后确定性写谱并校验。"
                "凡涉及对位/一比一/species 均套用同一规则集（禁纯4、禁同时跳进、"
                "限平行3/6、反向为主、终止导→主/Ⅱ↓Ⅰ等）。对位≠赋格。"
                "也可「根据当前打开的乐谱写一个1比1对位」：抽定旋律再写另一声。",
        "commands": ["/help"],
        "example": "根据当前打开的乐谱写一个1比1对位",
        "result": "从当前乐谱每小节抽一音作定旋律，生成钢琴大谱表二声部 1:1 对位；"
                  "无当前谱时也可从零写「2声部一比一对位」。",
        "aliases": [
            "二声部对位", "两声部对位", "第一类对位", "species", "bicinium",
            "对位法", "对位规则", "一比一对位", "1比1对位", "1:1对位",
            "first species", "counterpoint", "对位",
            "根据当前乐谱对位", "基于当前乐谱对位",
        ],
        # tutorial 在模块加载后由 COUNTERPOINT_RULES_DOC 注入（见文件末尾）
        "tutorial": "",
    },
    {
        "id": "MIDI 键盘输入", "cat": "增强功能",
        "summary": "接入外部 MIDI 键盘步进输入（macOS CoreMIDI / Windows MM，基于 mido+python-rtmidi）。"
                "按下的键实时以橙色显示在五线谱上；默认左右手分谱：分手点（默认 C4）及以上进右手"
                "高音谱表、以下进左手低音谱表，单谱表时自动升为钢琴大谱表；两手同时按的和弦"
                "各自写成和弦并上下对齐，只弹一只手时另一只手补休止。可选和弦模式、八度偏移、分手点。",
        "commands": [],
        "example": "工具 → MIDI 键盘 → 启用 MIDI 输入；左手按 C3+G3、右手按 C5+E5+G5",
        "result": "按住时谱面出现橙色预览；写入后右手得到 C5-E5-G5 和弦、左手得到 C3-G3 和弦，同一拍对齐。",
        "aliases": ["MIDI键盘", "midi键盘", "MIDI输入", "midi input", "外接键盘",
                    "左右手", "大谱表输入", "实时显示"],
    },
    {
        "id": "渲染增强（soundfont/混响/力度）", "cat": "增强功能",
        "summary": "对标 AIVA 渲染管线的确定性渲染层：SoundFont 音色选择 + 卷积混响 + 力度曲线。"
                "全部落在表演层，不改符号谱音高/时值/和声；同输入逐样本可复现、可审计。",
        "commands": ["/render"],
        "example": "/render piano reverb 0.3 velocity soft",
        "result": "用 Salamander 钢琴采样 + 30% 混响 + 柔和力度，渲染当前乐谱为 WAV。",
        "aliases": ["render", "渲染", "混响", "力度", "soundfont"],
    },
    {
        "id": "风格包批量生成流水线", "cat": "增强功能",
        "summary": "复用确定性内核的批量产出：枚举风格包→每包生成 N 首确定性骨架乐谱"
                "（MusicXML+MIDI）→ 算风格保真度 → 出审计报告。不调 LLM、可复现。",
        "commands": ["/batch"],
        "example": "/batch fugue chorale",
        "result": "为 fugue、chorale 各生成 1 首确定性乐谱，并产出 runs/batch_report.md/.csv。",
        "aliases": ["batch", "批量", "流水线", "批量生成"],
    },
    {
        "id": "五线谱辅助编辑教程", "cat": "核心能力",
        "summary": "空谱点写 + 程序化补全 + LLM 听审：从零手写出莫扎特《小夜曲》开篇完整 16 小节。",
        "commands": ["/assist", "/key", "/tempo", "/meter", "/bars", "/new", "/export"],
        "example": "/help 编辑教程",
        "result": "打开完整教程：准备空谱 → 手写动机/应答/收束 → 辅助预览与听审 → 播放导出。",
        "aliases": [
            "编辑教程", "辅助编辑", "辅助作曲教程", "五线谱编辑",
            "小夜曲", "莫扎特", "莫扎特小夜曲", "Nachtmusik", "serenade",
            "创造小夜曲", "编小夜曲",
        ],
        "tutorial": """## 完整教程：亲手创造莫扎特《小夜曲》开篇

作品：**Eine kleine Nachtmusik**（K.525）第一乐章快板开篇主题。  
本教程用手写 + 辅助预览，在 **G 大调 / 4/4 / ♩=120** 下写出可播放的 **16 小节单声部缩谱**（小提琴 I 旋律）。不是完整弦乐五重奏总谱。

整曲分三段写完：

| 段落 | 小节 | 角色 |
| --- | --- | --- |
| A 号角动机 | 1–4 | 主→属号角分解 |
| B 应答句 | 5–8 | 下属色彩，落回属 |
| A′ 收束句 | 9–16 | 再现动机并全音符收在主音 |

---

### 一、快捷键一览（先记住这几条）

| 操作 | 键 / 按钮 |
| --- | --- |
| 二分 / 四分时值 | 工具条「二」「四」，或先**点一下谱面**再 `Shift+2` / `Shift+3` |
| 切换 ghost 预览 | `1`–`5`（只改灰色预览，不写谱） |
| 采纳下一音 | `Tab` |
| 采纳当前本小节预览 | `Ctrl+Tab` |
| 全曲铺满（慎用） | `Ctrl+Enter` |
| 升/降八度 | 选中音符后 `Shift+↑` / `Shift+↓` |
| 撤销 | `Ctrl+Z` |
| 播放 | 工具栏 `▶` |

**Shift+2 没反应时**：先用鼠标点五线谱抢焦点；或直接点工具条「二」。macOS 上 Shift+2 常变成 `@`，软件已兼容。

勾选 **辅助作曲**。写完一个音后，谱上出现**一种**灰色 ghost；工具栏列出多种可能性；`1`–`5` 切换预览；后台 LLM 听审后可能延迟改默认预览（你手动切过则不覆盖）。

---

### 二、音高在谱上哪里？（高音谱号）

五线从下往上：底线 **E4**，二线 **G4**，三线 **B4**，**四线 D5**，顶线 **F5**。

```text
        ·  ← D6  第三条加线
        ·  ← C6
        ·  ← B5  ★ 第一条加线（不是五线中间的 B4）
        ·  ← A5
        ·  ← G5  ★ 顶线上面第一间
      ——F5——  顶线（G 大调调号已带升号时即 F# 线）
        E5
      ——D5——  第 4 线  ★
        C5
      ——B4——  第 3 线（中间线，易和 B5 搞混）
        A4
      ——G4——
        F4
      ——E4——  底线
```

写高音窍门：先点容易点的低八度，再 **✎ 选择** → `Shift+↑` 升八度（B4→B5，D5→D6，C5→C6，A4→A5）。

---

### 三、准备空谱

在对话框一次输入（或分行）：

```text
/key G /tempo 120 /meter 4/4 /bars 16
/assist on
/assist inspire arpeggio
/assist harmony 1=I 2=I 3=V 4=I 5=IV 6=IV 7=V 8=I 9=I 10=I 11=V 12=I 13=IV 14=IV 15=V 16=I
/new
```

应看到：一个升号的空小节网格、编辑模式已开、辅助作曲已勾选。若仍是 C 大调，再 `/key G` 后 `/new` 一次。

---

### 四、手写 A 段 · 号角动机（第 1–4 小节）

| 小节 | 时值 | 依次写入 |
| --- | --- | --- |
| 1 | **二** | G5，D5 |
| 2 | **二** | G5，D5 |
| 3 | **四** | G5，D5，G5，B5 |
| 4 | **二** | D6，D5 |

步骤建议：

1. 点工具 **♪ 音符**，点「二」。  
2. 悬停到 G5（顶线上一间），看状态栏 `悬停 G5`，单击。  
3. 同小节写 D5（第 4 线）。第 2 小节同样 G5、D5。  
4. 点「四」，第 3 小节写四个四分：G5 D5 G5，最后一个若点成 B4 → 选中 → `Shift+↑` → B5。  
5. 点「二」，第 4 小节：可先点 D5 再升八度得 D6，再写低八度 D5。

**辅助用法（可选）**：写完第 1 音 G5 后，工具栏出现多种本小节可能性。按 `2`/`3` 看不同 ghost；满意则 `Ctrl+Tab` 写入本小节剩余，或继续手写。`Tab` 只补**下一个单音**。

写完 1–4 后点 `▶`，应听到熟悉的号角开场。

---

### 五、手写 B 段 · 应答句（第 5–8 小节）

| 小节 | 时值 | 依次写入 |
| --- | --- | --- |
| 5 | **二** | C6，A5 |
| 6 | **二** | C6，A5 |
| 7 | **四** | C6，A5，F#5，A5 |
| 8 | **二** | G5，D5 |

- C6 / A5：可点 C5 / A4 再 `Shift+↑`。  
- F#5：点顶线 F；G 大调调号已是升号，即为 F#。  
- 第 8 小节回到 G5–D5，与开头呼应。

再点 `▶` 听完整前 8 小节（呈示 + 应答）。

---

### 六、手写 A′ 段 · 收束句（第 9–16 小节）

再现号角，最后用全音符落在主音。

| 小节 | 时值 | 依次写入 |
| --- | --- | --- |
| 9 | **二** | G5，D5 |
| 10 | **二** | G5，D5 |
| 11 | **四** | G5，D5，G5，B5 |
| 12 | **二** | D6，D5 |
| 13 | **二** | C6，A5 |
| 14 | **二** | C6，A5 |
| 15 | **四** | B5，G5，A5，F#5 |
| 16 | **全**（工具条「全」或 `Shift+1`） | G5 |

第 15 小节是下行倾向属（F#），第 16 小节全音符 G5 收束。整段 16 小节即一条完整的教学版开篇。

---

### 七、两条可选加速路径

**路径 α · 种子 + 本小节铺满**

1. 每小节只手写第一个音（如第 1 小节只写 G5）。  
2. 等 ghost 与工具栏可能性出现；`1`–`5` 选最像小夜曲的一条（听审说明出现在建议条右侧）。  
3. `Ctrl+Tab` 写入本小节剩余。  
4. 不对就 `Ctrl+Z`，改用手写。

**路径 β · 对照速查表逐音点写（最稳）**

直接按文末「16 小节音符速查」从左到右点，不依赖辅助。

不要一上来就 `Ctrl+Enter` 全曲铺满——程序续写不是莫扎特原谱，适合草稿，不适合本教程的目标成品。

---

### 八、写完验收

1. `▶` 通听：1–4 号角 → 5–8 应答 → 9–14 再现 → 15–16 属→主收束。  
2. `/export` 导出 MusicXML / MIDI。  
3. 写错音：`Ctrl+Z`；音高不对：选中后 `↑`/`↓` 或 `Shift+↑`。  
4. 完整多声部复现另见 `runs/reverse_live_demo/`（自动生成路径，与本手写教程分开）。

---

### 九、16 小节音符速查

```text
# A 号角
m1   G5 half · D5 half
m2   G5 half · D5 half
m3   G5 q · D5 q · G5 q · B5 q
m4   D6 half · D5 half
# B 应答
m5   C6 half · A5 half
m6   C6 half · A5 half
m7   C6 q · A5 q · F#5 q · A5 q
m8   G5 half · D5 half
# A′ 收束
m9   G5 half · D5 half
m10  G5 half · D5 half
m11  G5 q · D5 q · G5 q · B5 q
m12  D6 half · D5 half
m13  C6 half · A5 half
m14  C6 half · A5 half
m15  B5 q · G5 q · A5 q · F#5 q
m16  G5 whole
```

对照口诀：**两下号角 → 四分爬升 → 属音落下；应答同型偏低；再现后再全音符收主音。**
""",
    },
    {
        "id": "编辑指令族（已退役）", "cat": "核心能力",
        "summary": "续写/扩展/发展/变奏/配器/移调/裁剪/简化/复杂化/重写等改谱指令"
                "**已全部退役**：作曲只保留 LLM → /compose-grammar 一条路径，"
                "避免两套语义并存。",
        "commands": ["/compose-grammar"],
        "example": "/compose-grammar 圆舞曲 8 小节 转 D 大调",
        "result": "改谱需求请写进 /compose-grammar 的描述里重新生成。",
        "aliases": ["编辑", "改谱", "edit"],
    },
    {
        "id": "分析与建议闭环", "cat": "核心能力",
        "summary": "用 /analyze 看特征与风格重叠，据此调整下一次生成的说法——"
                "规则驱动、无幻觉。改谱入口已退役，要改谱请重新描述需求生成。",
        "commands": ["/analyze"],
        "example": "/analyze → 按建议重写描述 → /compose-grammar",
        "result": "输出可审计的诊断；把结论写进下一次 /compose-grammar 的描述即可生效。",
        "aliases": ["分析", "建议", "analyze"],
    },
    {
        "id": "学习风格", "cat": "核心能力",
        "summary": "把当前乐谱「学进」风格包：更新 genre/packs 的特征统计与指纹，"
                "让后续生成更贴近该样本。",
        "commands": ["/learn-score"],
        "example": "/learn-score chorale",
        "result": "用当前曲更新 chorale 包，后续 /style chorale 更贴合。",
        "aliases": ["学习", "learn-score", "学进风格"],
    },
    {
        "id": "体系层（非功能和声）", "cat": "核心能力",
        "summary": "支持板腔 / 曲牌 / 拉格 / 十二音序列等体系，跳出功能和声范式。",
        "commands": ["/system"],
        "example": "/system 十二音序列",
        "result": "按十二音序列技法生成，约束来自体系规则而非调性。",
        "aliases": ["体系", "system", "十二音", "板腔", "曲牌", "拉格"],
    },
    {
        "id": "偏好与调性设置", "cat": "核心能力",
        "summary": "用一组控件精确设定创作约束：调性/速度/拍号/小节/声部/赋格/风格。",
        "commands": ["/key", "/tempo", "/meter", "/bars", "/voices", "/fugue", "/style"],
        "example": "/key Am /tempo 90 /meter 3/4 /bars 24",
        "result": "组合设定一首 A 小调、♩=90、3/4 拍、24 小节的创作约束。",
        "aliases": ["偏好", "设置", "控件"],
    },
    {
        "id": "导出 / 演示 / 会话", "cat": "核心能力",
        "summary": "无 API 也能 /demo 看效果；/export 出谱；/new /clear /config 管理会话。",
        "commands": ["/demo", "/export", "/new", "/clear", "/config"],
        "example": "/demo /export",
        "result": "先用演示曲看效果，再一键导出 MusicXML+MIDI。",
        "aliases": ["导出", "演示", "会话", "demo"],
    },
    {
        "id": "确定性内核与护城河", "cat": "设计理念",
        "summary": "零训练数据、seed 固定可复现、规则校验、可审计——所有功能都建立在"
                "确定性内核 + 规则校验之上，不靠 LLM「凭空生成」。",
        "commands": ["/compose-grammar", "/groove", "/refine", "/import",
                     "/reverse-prompt", "/replay", "/brief-expand", "/chords"],
        "example": "任意 /chords 或 /compose-grammar 输入在固定 seed 下结果完全一致",
        "result": "同一输入恒得同一输出，过程可被规则引擎逐步复算与审计。",
        "aliases": ["护城河", "确定性", "可审计", "零训练数据"],
    },
]


def _bind_counterpoint_help() -> None:
    """把对位法权威规则注入「二声部对位」功能的 tutorial（单一来源）。"""
    try:
        from music_score.two_part_species import get_counterpoint_rules_doc
        doc = get_counterpoint_rules_doc()
    except Exception:
        doc = ""
    for f in FEATURES:
        if f.get("id") == "二声部对位（钢琴大谱表）":
            f["tutorial"] = doc
            break


_bind_counterpoint_help()


#: 已退役命令。作曲主路径只保留 **LLM → /compose-grammar**，这些命令在
#: llm_compose 里已retire（回一条指向 /compose-grammar 的提示），因此**不应
#: 继续出现在 /help 目录里**——否则用户会照着文档去敲一条必然失败的命令。
#: 单一事实来源：这里与 ``llm_compose._RETIRED_{COMPOSITION,EDIT}_CMDS`` 对应。
RETIRED_CMDS = {
    '/skeleton', '/interpolate',
    '/rewrite', '/continue', '/apply', '/advice',
    '/extend', '/develop', '/variation', '/harmonize', '/orchestrate',
    '/transpose', '/shorter', '/longer', '/simplify', '/complexify',
}


def _prune_retired() -> None:
    """从命令目录与功能目录里剔除已退役条目（原地修改，保持单一数据源）。"""
    for coll in (COMMANDS, FEATURES):
        coll[:] = [c for c in coll
                   if c.get('cmd') not in RETIRED_CMDS]
        for c in coll:
            cmds = c.get('commands')
            if isinstance(cmds, list):
                c['commands'] = [x for x in cmds if x not in RETIRED_CMDS]


_prune_retired()


def write_help_file(path: str = "runs/help.md") -> str:
    """把 build_help() 完整目录写到 runs/help.md，与聊天内 /help 同源。"""
    import os
    text = build_help()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
    return path


def _norm(s: str) -> str:
    return (s or '').strip().lower()


def find_topic(name: str) -> Optional[Dict]:
    """在命令与功能目录中按名称/别名查找；找不到返回 None。

    解析优先级：
    1) 精确命令名（如 `/chords`）→ 命令详情
    2) 精确功能名（如 `和弦进行顾问`）→ 功能详情
    3) 功能别名 → 功能详情（中文名/别名优先映射到功能，便于「看功能说明」）
    4) 命令别名 → 命令详情
    5) 取首 token 兜底（如 `/chords I V vi IV` → `/chords`）
    """
    q = _norm(name)
    if not q:
        return None
    # 1) 精确命令名
    for c in COMMANDS:
        if _norm(c["cmd"]) == q:
            return {"kind": "command", "entry": c}
    # 2) 精确功能名
    for f in FEATURES:
        if _norm(f["id"]) == q:
            return {"kind": "feature", "entry": f}
    # 3) 功能别名
    for f in FEATURES:
        if q in [_norm(a) for a in f.get("aliases", [])]:
            return {"kind": "feature", "entry": f}
    # 4) 命令别名
    for c in COMMANDS:
        if q in [_norm(a) for a in c.get("aliases", [])]:
            return {"kind": "command", "entry": c}
    # 5) 首 token 兜底
    first = q.split()[0] if q.split() else q
    for c in COMMANDS:
        if _norm(c["cmd"]) == first:
            return {"kind": "command", "entry": c}
    return None


def _render_command(c: Dict) -> str:
    sig = f"{c['cmd']} {c['args']}".rstrip()
    lines = [
        f"### {c['cmd']}　·　{c['cat']}",
        "",
        f"- **语法**：`{sig}`",
        f"- **说明**：{c['desc']}",
        f"- **示例**：`{c['example']}`",
        f"- **效果**：{c['result']}",
        "",
    ]
    return "\n".join(lines)


def _render_feature(f: Dict) -> str:
    cmds = "、".join(f"`{x}`" for x in f["commands"])
    lines = [
        f"### {f['id']}　·　{f['cat']}",
        "",
        f"- **说明**：{f['summary']}",
        f"- **相关命令**：{cmds}",
        f"- **示例**：`{f['example']}`",
        f"- **效果**：{f['result']}",
        "",
    ]
    tutorial = (f.get("tutorial") or "").strip()
    if tutorial:
        lines.append(tutorial)
        lines.append("")
    return "\n".join(lines)


def build_help(topic: Optional[str] = None) -> str:
    """生成帮助文档 markdown。topic 为空输出完整目录；否则输出单项详情。"""
    t = find_topic(topic) if topic else None
    if t is not None:
        if t["kind"] == "command":
            head = "# 命令详情\n"
            return head + _render_command(t["entry"])
        head = "# 功能详情\n"
        return head + _render_feature(t["entry"])

    # 完整目录
    out: List[str] = []
    out.append("# ComposerChat 帮助文档（确定性内核 · 规则校验 · 可复现 · 可审计）")
    out.append("")
    out.append("> 所有命令与功能都建立在确定性内核之上：**零训练数据、seed 固定可复现、"
               "规则校验、可审计**。带示例，复制即可用。")
    out.append("")
    out.append("## 一、功能清单（每个功能一个示例）")
    out.append("")
    cur = None
    for f in FEATURES:
        if f["cat"] != cur:
            cur = f["cat"]
            out.append(f"### ▸ {cur}")
            out.append("")
        cmds = "、".join(f"`{x}`" for x in f["commands"])
        out.append(f"- **{f['id']}** — {f['summary']}")
        out.append(f"  - 相关命令：{cmds}")
        out.append(f"  - 示例：`{f['example']}`")
        out.append(f"  - 效果：{f['result']}")
        out.append("")
    out.append("## 二、命令速查（每个命令一个示例）")
    out.append("")
    cur = None
    for c in COMMANDS:
        if c["cat"] != cur:
            cur = c["cat"]
            out.append(f"### ▸ {cur}")
            out.append("")
        sig = f"{c['cmd']} {c['args']}".rstrip()
        out.append(f"- **`{sig}`** — {c['desc']}")
        out.append(f"  - 示例：`{c['example']}`")
        out.append(f"  - 效果：{c['result']}")
        out.append("")
    out.append("## 三、使用提示")
    out.append("")
    out.append("- 命令可与自然语言混用，一行可写多个命令，例如 "
               "`/new /style 圆舞曲 快板轻盈 做一首轻快的圆舞曲`。")
    out.append("- 创作需配置 API；未配置时先用 `/demo` 看效果。")
    out.append("- 查看单项说明与示例：`/help <命令或功能名>`，例如 `/help /chords`、"
               "`/help 和弦顾问`、`/help 风格插值`、`/help 对位法`、`/help 规则库`。")
    out.append("- 规则库入口：菜单「作曲库 → 规则库（对位 / 教学 / 脚本 · 文档+代码）」"
               "或 Ctrl+L 后切到「规则库」页。")
    out.append("- 作曲只有一条路径：`/compose-grammar <自由描述>`（或直接用自然语言说）。"
               "旧的 `/rewrite`、`/continue`、`/apply`、`/transpose` 等改谱命令已退役；"
               "要改谱请把需求写进描述里重新生成。")
    out.append("- 五线谱点写教程（莫扎特《小夜曲》完整 16 小节）：`/help 编辑教程` 或菜单"
               "「帮助 → 编辑教程 · 莫扎特小夜曲」。")
    out.append("- LLM 生成可能需 1–3 分钟，请等待「创曲中」结束。")
    out.append("")
    out.append("## 四、对位法规则（权威）")
    out.append("")
    out.append("> 凡涉及对位 / 一比一 / species，均遵守下列规则。"
               "单项详情：`/help 对位法`。")
    out.append("")
    try:
        from music_score.two_part_species import get_counterpoint_rules_doc
        out.append(get_counterpoint_rules_doc())
    except Exception:
        out.append("（规则模块不可用时请查看 `music_score/two_part_species.py`）")
    out.append("")
    return "\n".join(out)


def _selftest():
    import sys
    fails = 0
    n = 0

    def check(desc, cond):
        nonlocal fails, n
        n += 1
        if not cond:
            fails += 1
            print(f"  ✗ {desc}")
        else:
            print(f"  ✓ {desc}")

    # 每条命令都有示例与非空字段
    for c in COMMANDS:
        check(f"命令 {c['cmd']} 有示例", bool(c.get("example")))
        check(f"命令 {c['cmd']} 有说明", bool(c.get("desc")))
        check(f"命令 {c['cmd']} 有效果", bool(c.get("result")))

    # 每个功能都有示例与非空字段
    for f in FEATURES:
        check(f"功能 {f['id']} 有示例", bool(f.get("example")))
        check(f"功能 {f['id']} 有说明", bool(f.get("summary")))
        check(f"功能 {f['id']} 有效果", bool(f.get("result")))

    # 主题查找：命令与功能各取一个
    check("find_topic('/chords') 命中命令",
          find_topic("/chords") is not None and find_topic("/chords")["kind"] == "command")
    check("find_topic('和弦顾问') 命中功能",
          find_topic("和弦顾问") is not None and find_topic("和弦顾问")["kind"] == "feature")
    check("find_topic('风格插值') 命中功能",
          find_topic("风格插值") is not None and find_topic("风格插值")["kind"] == "feature")
    check("find_topic('编辑教程') 命中功能",
          find_topic("编辑教程") is not None
          and find_topic("编辑教程")["kind"] == "feature")
    check("find_topic('对位法') 命中二声部对位",
          find_topic("对位法") is not None
          and find_topic("对位法")["entry"]["id"] == "二声部对位（钢琴大谱表）")
    cp = build_help("对位法")
    check("对位法帮助含权威规则",
          "禁止纯 4 度" in cp and "导音" in cp and "对位 ≠ 赋格" in cp)
    check("find_topic('小夜曲') 命中功能",
          find_topic("小夜曲") is not None
          and find_topic("小夜曲")["entry"]["id"] == "五线谱辅助编辑教程")
    tut = build_help("编辑教程")
    check("编辑教程含小夜曲音符速查", "G5 half" in tut and "F#5" in tut)
    check("编辑教程含完整16小节", "m16  G5 whole" in tut or "m16  G5 whole" in tut.replace('  ', ' '))
    check("编辑教程含收束段", "A′ 收束" in tut or "收束句" in tut)
    check("find_topic('') 返回 None", find_topic("") is None)
    check("find_topic('不存在XYZ') 返回 None", find_topic("不存在XYZ") is None)

    # 完整帮助覆盖所有命令/功能示例
    full = build_help()
    for c in COMMANDS:
        check(f"完整帮助含命令示例 {c['cmd']}", c["example"] in full)
    for f in FEATURES:
        check(f"完整帮助含功能示例 {f['id']}", f["example"] in full)

    # 单项帮助
    single = build_help("/chords")
    check("单项帮助含 /chords 语法", "/chords" in single and "示例" in single)

    print(f"\n帮助文档自测：{n - fails}/{n} 通过")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    _selftest()
