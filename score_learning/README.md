# Score-to-Skill：直接更新风格包

**不写 `learned/`。** 按**曲风筛选**语料后写入：

```text
skills/composition/genre/packs/<family>/<pack_id>/
  meta.yaml                 ← 风格 Skill（合并 empirical）
  features/*.json
  empirical_statistics.json
  EMPIRICAL_SKILL.md
```

## 外部语料采集（sources.py）

music21 自带 corpus 撑不起爵士/交响/流行——这三族它是空的。外部库统一由
`sources.py` 登记，一条命令取回：

```bat
REM 列出语料源与许可（哪些需要确认条款）
python -m score_learning list-sources

REM 取一个源（条款不清的源必须显式确认）
python -m score_learning fetch weimar-jazz
python -m score_learning fetch haydn-symphony --accept-license

REM 全部取回
python -m score_learning fetch --all --accept-license
```

落地在 `scores/corpus/<源>/`（**不进版本控制**），每个源带
`manifest.json` + `LICENSE_NOTICE.txt`。随后照常：

```bat
python -m score_learning learn scores\corpus\weimar-jazz ^
  --family jazz --pack-id swing-standard --no-llm
```

| 源 id | 内容 | 许可 |
|---|---|---|
| `nottingham` | ~1200 首英美民谣 ABC | 传统曲调，条款未明示 |
| `haydn-symphony` | 海顿伦敦交响 99–104，24 个乐章全编制总谱 | CCARH MuseData（学术/个人用途） |
| `classical-kern` | 海顿/莫扎特/贝多芬奏鸣曲、巴赫赋格 | 同上 |
| `weimar-jazz` | 456 段爵士独奏（旋律+和弦标注） | ODbL-1.0 |
| `cocopops` | Billboard 流行歌旋律/和声 | 派生自 McGill（CC BY-NC-SA） |

三个源需要做格式转换才能学，原因都记在代码里：

- **多曲 ABC 必须拆单曲**：整文件喂进去会被当成一首曲子，音高串成一条荒谬旋律。
- **kern 要清洗**：music21 遇到 `!!!OCO:` 会抛 KeyError，不处理后海顿交响全军覆没。
- **和弦记法自己拆**：`ChordSymbol('Bb7')` 会静默解析成 B-D#-F#-A，
  jazz 的和声指纹正是靠自己拆才保住的。

## 从 music21 按曲风学习（推荐）

**全 corpus，但按曲风分桶**——每首只进入匹配查询的风格包，不把整库混成一个 Skill。

默认取该曲风在 corpus 中的**全部单曲命中**（自动跳过 airdsAirs / Essen 等多曲合集）；需要抽样时再加 `--limit`。

```bat
REM 查看已配置曲风（加 --count 统计命中）
python -m score_learning list-m21 --count

REM 单曲风：筛选 → 导出 → 更新对应 pack
python -m score_learning learn-m21 --pack-id chorale --no-llm --replace-features

REM 所有已配置曲风（命中过少的会跳过）
python -m score_learning learn-m21 --all --no-llm --replace-features --min-scores 2

REM 抽样上限（可选）
python -m score_learning learn-m21 --pack-id celtic-jig --limit 40 --no-llm
```

曲风 ↔ 检索词见 `score_learning/music21_corpus.py` 中 `GENRE_CORPUS_QUERIES`。

导出缓存：`scores/corpus/m21_by_genre/<pack_id>/`。

Stock music21 对 folk jig/air、Bach chorale 较丰富；waltz/nocturne/prelude 等往往命中不足，会自动 skip。

## 本地文件夹语料

```bat
python -m score_learning learn scores\corpus\bach_chorales_10 ^
  --family sacred --pack-id chorale --genre 众赞歌 --no-llm
```

Studio：`/learn-score chorale` → 当前曲写入对应风格包并更新 `meta.yaml`。
