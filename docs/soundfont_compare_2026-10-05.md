# SoundFont 四库综合对比（2026-10-05）

渲染：fluidsynth `-r 44100 -g 0.85`，渲染后统一峰值归一化到 0.99（仅比音色）。
峰值/RMS 反映响度与动态；crest 越大动态越大；centroid 越大越"亮"；flatness 越大越"噪/有气声"；bass/mid/treb 为频段能量占比。

## 跨样本平均

| 声库 | 峰值 | RMS(dB) | crest(dB) | 频谱质心(Hz) | 平坦度 | 低频% | 中频% | 高频% | 削波样本 |
|---|---|---|---|---|---|---|---|---|---|
| sf3 (MuseScore General) | 0.891 | -15.9 | 14.8 | 863 | 0.039 | 9 | 90 | 1 | 0 |
| GeneralUser GS | 0.931 | -15.8 | 15.2 | 731 | 0.047 | 13 | 87 | 0 | 0 |
| FluidR3 GM | 0.812 | -17.3 | 15.4 | 805 | 0.048 | 27 | 72 | 1 | 0 |
| Timbres of Heaven 4.0 | 0.937 | -12.9 | 12.3 | 739 | 0.071 | 22 | 76 | 2 | 0 |

## 逐样本明细

### orchestral (scores/pirate_storm_allegro.mid)
| 声库 | 峰值 | RMS(dB) | crest(dB) | 质心(Hz) | 平坦度 | 削波 |
|---|---|---|---|---|---|---|
| sf3 (MuseScore General) | 0.956 | -14.6 | 14.2 | 931 | 0.063 | 0 |
| GeneralUser GS | 0.983 | -14.7 | 14.6 | 896 | 0.071 | 0 |
| FluidR3 GM | 0.926 | -15.3 | 14.6 | 962 | 0.072 | 0 |
| Timbres of Heaven 4.0 | 0.990 | -8.3 | 8.2 | 1006 | 0.149 | 0 |

### piano (scores/grammar/sonata_allegro.mid)
| 声库 | 峰值 | RMS(dB) | crest(dB) | 质心(Hz) | 平坦度 | 削波 |
|---|---|---|---|---|---|---|
| sf3 (MuseScore General) | 0.864 | -14.5 | 13.3 | 549 | 0.014 | 0 |
| GeneralUser GS | 0.883 | -14.6 | 13.5 | 501 | 0.037 | 0 |
| FluidR3 GM | 0.687 | -18.0 | 14.7 | 646 | 0.044 | 0 |
| Timbres of Heaven 4.0 | 0.832 | -15.8 | 14.2 | 417 | 0.034 | 0 |

### jazz (scores/grammar/jazz_swing.mid)
| 声库 | 峰值 | RMS(dB) | crest(dB) | 质心(Hz) | 平坦度 | 削波 |
|---|---|---|---|---|---|---|
| sf3 (MuseScore General) | 0.854 | -18.4 | 17.0 | 1110 | 0.038 | 0 |
| GeneralUser GS | 0.928 | -18.0 | 17.4 | 798 | 0.034 | 0 |
| FluidR3 GM | 0.824 | -18.6 | 16.9 | 806 | 0.028 | 0 |
| Timbres of Heaven 4.0 | 0.990 | -14.6 | 14.6 | 794 | 0.029 | 0 |
