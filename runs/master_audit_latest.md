# 大师级质量审计报告

- 时间：20260929_055024
- 渲染 WAV：关
- 听审量表：`docs/听审量表.md` / `runs/listening_rubric.yaml`
- 填分文件：`scores/master_audit/listening_scores.yaml`

## 门禁说明

| 检查 | 通过条件 |
|---|---|
| 风格命中 | `style_rank` 目标包 ∈ top3 |
| 非整曲抄袭 | 语料单曲 NN sim < 0.98 |
| 非片段抄袭 | 语料片段 NN sim < 0.99 |
| 听审 | 通用维平均 ≥ 4.0 且硬指标无 ≤ 2 |

## 汇总：符号门 8/9 通过

### `mazurka`

- 符号门：**3/3**

| key | seed | 符号门 | top3 | 单曲NN | 片段NN | overlap | WAV | 听审 |
|---|---|---|---|---|---|---|---|---|
| `mazurka__0` | `master-mazurka-0` | ✅ | ✅ | ✅ 0.9018 | ✅ 0.9715 | 0.6373 | — | 待填 |
| `mazurka__1` | `master-mazurka-1` | ✅ | ✅ | ✅ 0.8963 | ✅ 0.9743 | 0.7531 | — | 待填 |
| `mazurka__2` | `master-mazurka-2` | ✅ | ✅ | ✅ 0.8848 | ✅ 0.975 | 0.7849 | — | 待填 |

#### `mazurka__0`

- MusicXML: `scores/master_audit/mazurka__0.musicxml`
- MIDI: `scores/master_audit/mazurka__0.mid`
- 语料单曲：`mazurka07-4` sim=0.9018
- 语料片段：`mazurka-50@53-56` gen_bar=9 sim=0.9715

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `mazurka_accent`（重音在二/三拍可感）：_ / 5
  - `mazurka_dance`（舞步感而非匀速练习曲）：_ / 5
  - `mazurka_piano`（钢琴语汇可信）：_ / 5

#### `mazurka__1`

- MusicXML: `scores/master_audit/mazurka__1.musicxml`
- MIDI: `scores/master_audit/mazurka__1.mid`
- 语料单曲：`mazurka07-4` sim=0.8963
- 语料片段：`mazurka-50@53-56` gen_bar=13 sim=0.9743

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `mazurka_accent`（重音在二/三拍可感）：_ / 5
  - `mazurka_dance`（舞步感而非匀速练习曲）：_ / 5
  - `mazurka_piano`（钢琴语汇可信）：_ / 5

#### `mazurka__2`

- MusicXML: `scores/master_audit/mazurka__2.musicxml`
- MIDI: `scores/master_audit/mazurka__2.mid`
- 语料单曲：`mazurka07-4` sim=0.8848
- 语料片段：`mazurka-50@53-56` gen_bar=9 sim=0.975

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `mazurka_accent`（重音在二/三拍可感）：_ / 5
  - `mazurka_dance`（舞步感而非匀速练习曲）：_ / 5
  - `mazurka_piano`（钢琴语汇可信）：_ / 5

### `fugue`

- 符号门：**3/3**

| key | seed | 符号门 | top3 | 单曲NN | 片段NN | overlap | WAV | 听审 |
|---|---|---|---|---|---|---|---|---|
| `fugue__0` | `master-fugue-0` | ✅ | ✅ | ✅ 0.8695 | ✅ 0.9869 | 0.8701 | — | 待填 |
| `fugue__1` | `master-fugue-1` | ✅ | ✅ | ✅ 0.8665 | ✅ 0.9869 | 0.8364 | — | 待填 |
| `fugue__2` | `master-fugue-2` | ✅ | ✅ | ✅ 0.8845 | ✅ 0.9865 | 0.9548 | — | 待填 |

#### `fugue__0`

- MusicXML: `scores/master_audit/fugue__0.musicxml`
- MIDI: `scores/master_audit/fugue__0.mid`
- 语料单曲：`bach_wtc_fugues_wtc1f02` sim=0.8695
- 语料片段：`bach_wtc_fugues_wtc2f13@5-8` gen_bar=5 sim=0.9869

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `fugue_subject`（主题可辨）：_ / 5
  - `fugue_answer`（答题/模仿清楚）：_ / 5
  - `fugue_independence`（声部独立）：_ / 5

#### `fugue__1`

- MusicXML: `scores/master_audit/fugue__1.musicxml`
- MIDI: `scores/master_audit/fugue__1.mid`
- 语料单曲：`bach_wtc_fugues_wtc1f02` sim=0.8665
- 语料片段：`bach_wtc_fugues_wtc2f13@5-8` gen_bar=5 sim=0.9869

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `fugue_subject`（主题可辨）：_ / 5
  - `fugue_answer`（答题/模仿清楚）：_ / 5
  - `fugue_independence`（声部独立）：_ / 5

#### `fugue__2`

- MusicXML: `scores/master_audit/fugue__2.musicxml`
- MIDI: `scores/master_audit/fugue__2.mid`
- 语料单曲：`bach_wtc_fugues_wtc1f02` sim=0.8845
- 语料片段：`bach_wtc_fugues_wtc2f13@5-8` gen_bar=9 sim=0.9865

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `fugue_subject`（主题可辨）：_ / 5
  - `fugue_answer`（答题/模仿清楚）：_ / 5
  - `fugue_independence`（声部独立）：_ / 5

### `swing-standard`

- 符号门：**2/3**

| key | seed | 符号门 | top3 | 单曲NN | 片段NN | overlap | WAV | 听审 |
|---|---|---|---|---|---|---|---|---|
| `swing-standard__0` | `master-swing-standard-0` | ❌ | ❌ | ✅ 0.9296 | ✅ 0.8682 | -0.2571 | — | 待填 |
| `swing-standard__1` | `master-swing-standard-1` | ✅ | ✅ | ✅ 0.9235 | ✅ 0.8789 | 0.3403 | — | 待填 |
| `swing-standard__2` | `master-swing-standard-2` | ✅ | ✅ | ✅ 0.936 | ✅ 0.8593 | 0.3252 | — | 待填 |

#### `swing-standard__0`

- MusicXML: `scores/master_audit/swing-standard__0.musicxml`
- MIDI: `scores/master_audit/swing-standard__0.mid`
- 语料单曲：`0389_Sonny_Rollins_The_Everywhere_Calypso` sim=0.9296
- 语料片段：`0044_Buck_Clayton_After_Theatre_Jump@9-12` gen_bar=13 sim=0.8682

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `jazz_walking`（走动低音）：_ / 5
  - `jazz_comping`（comping/2&4 空间）：_ / 5
  - `jazz_harmony`（七和弦/ii–V 色彩）：_ / 5

#### `swing-standard__1`

- MusicXML: `scores/master_audit/swing-standard__1.musicxml`
- MIDI: `scores/master_audit/swing-standard__1.mid`
- 语料单曲：`0242_Johnny_Hodges_Early_Morning_Rock` sim=0.9235
- 语料片段：`0001_Art_Pepper_Anthropology@1-4` gen_bar=13 sim=0.8789

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `jazz_walking`（走动低音）：_ / 5
  - `jazz_comping`（comping/2&4 空间）：_ / 5
  - `jazz_harmony`（七和弦/ii–V 色彩）：_ / 5

#### `swing-standard__2`

- MusicXML: `scores/master_audit/swing-standard__2.musicxml`
- MIDI: `scores/master_audit/swing-standard__2.mid`
- 语料单曲：`0389_Sonny_Rollins_The_Everywhere_Calypso` sim=0.936
- 语料片段：`0044_Buck_Clayton_After_Theatre_Jump@29-32` gen_bar=1 sim=0.8593

听审填分（写入 listening_scores.yaml）：

  - `melody_memorable`（旋律可记性）：_ / 5
  - `phrase_breath`（乐句呼吸）：_ / 5
  - `harmony_clear`（和声功能清晰）：_ / 5
  - `texture_balance`（织体主次）：_ / 5
  - `genre_identity`（体裁辨识度）：_ / 5
  - `jazz_walking`（走动低音）：_ / 5
  - `jazz_comping`（comping/2&4 空间）：_ / 5
  - `jazz_harmony`（七和弦/ii–V 色彩）：_ / 5

---
听审完成后执行：
`python master_audit.py --apply-scores scores/master_audit/listening_scores.yaml`