# 终止式（PAC / IAC / HC）

来源要点综合自 [Open Music Theory — Intro to Harmony](https://viva.pressbooks.pub/openmusictheory/chapter/intro-to-harmony/) 与 musictheory.net「Phrases and Cadences」。

## 定义

| 类型 | 进行 | 听感 / 用途 |
|------|------|-------------|
| **PAC** 完全正格终止 | V → I（或 V7→I），两和弦均原位；高声部落主音 | 最强收束；乐段末句常用 |
| **IAC** 不完全正格终止 | V→I 但非原位，或高声部非主音 | 较弱收束；中途或前句 |
| **HC** 半终止 | 停在 V（常见 ii→V、IV→V、I→V） | 「问句」感；乐段前句常用 |

变格终止（IV/iv→I）与假终止（V→vi）教学中亦常见，本库内核 `_s_cadence` 以正格/半终止为主。

## 写法要点

1. **小调**：属和弦必须含导音（升高 VII），否则听感不属。
2. **外声部**：PAC 时高声部优先落主音；低声部 V→I 根音进行最稳。
3. **与乐段**：典型 period = 前句 HC + 后句 PAC（见「乐句·乐段·句子」）。
4. **内核脚本**：`cadence(kind=authentic|half|…)` 在乐句末强制终止型；`finalize` 也会做基础收束校验。

## 与对位的关系

第一类对位终止用「末拍八度 + 导→主 / Ⅱ↓Ⅰ」，功能上等价于强收束，但不按罗马数字写；二者勿混用校验器。
