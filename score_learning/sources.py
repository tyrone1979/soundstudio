# -*- coding: utf-8 -*-
"""外部乐谱语料采集器 —— 把「去哪个库找谱子」变成可复现的一步命令。

## 为什么要有这一层

此前仓库能学的只有两样：用户手边恰好有的乐谱文件，以及 music21 自带的
corpus（仅限安装时打包的那批民谣 ABC + 巴赫众赞歌）。结果是：26 个风格包
里一个爵士都没有，交响/流行也没有 —— 不是算法不会学，是压根没东西可学。

本模块把外部语料登记成一等公民：每个源声明**去哪下载、什么格式、什么许可、
落到哪个风格包**，然后由一条命令确定性地取回，交給既有的 analyze → mine
链路。

## 三个设计约束

1. **确定性**：同样的源 + 同样的 --limit ⇒ 同样的文件集合。抽样不带随机
   成分，遍历一律按排序后的顺序。
2. **许可显式**：每个源都带 SourceLicense。需要确认（ack_required）的源，
   不接受 --accept-license 就不下载 —— 宁可少一份语料，也不能把条款不清的
   数据默默写进别人的仓库。
3. **语料不入库**：所有采集结果落在 scores/corpus/（已 gitignore）。进仓库
   的是「怎么再取一遍」，不是数据本身。
"""
from __future__ import annotations

import bisect
import json
import os
import re
import sqlite3
import tarfile
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_ROOT = os.path.join(ROOT, 'scores', 'corpus')

MANIFEST_NAME = 'manifest.json'
LICENSE_NAME = 'LICENSE_NOTICE.txt'

_USER_AGENT = (
    'composerchat-score-learning/1.0 '
    '(+https://github.com/composerchat; contact: local user)'
)
_TIMEOUT = 120


# --------------------------------------------------------------------------
# 许可
# --------------------------------------------------------------------------

@dataclass
class SourceLicense:
    """一个语料源的许可事实。字段只写查证过的，不猜。"""
    spdx: str                      # 无法归类时写 'LicenseRef-<源名>'
    url: str                       # 条款原文在哪看
    redistributable: bool          # 原始数据能否再分发
    ack_required: bool = False     # 是否需要使用者显式确认后才能下载
    note: str = ''                 # 一句人话总结，会打印给用户看

    def as_text(self) -> str:
        lines = [
            f'SPDX / 标识 : {self.spdx}',
            f'条款原文     : {self.url}',
            f'可再分发     : {"是" if self.redistributable else "否"}',
            f'需显式确认   : {"是" if self.ack_required else "否"}',
        ]
        if self.note:
            lines.append(f'备注         : {self.note}')
        return '\n'.join(lines)


# --------------------------------------------------------------------------
# 结果容器
# --------------------------------------------------------------------------

@dataclass
class FetchResult:
    source_id: str
    out_dir: str
    files: List[str] = field(default_factory=list)
    skipped: int = 0
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f'{self.source_id}: {len(self.files)} 份乐谱 → {self.out_dir}'
            + (f'（跳过 {self.skipped}）' if self.skipped else '')
            + (('\n    ' + '\n    '.join(self.notes)) if self.notes else '')
        )


# --------------------------------------------------------------------------
# 通用下载 / 解包
# --------------------------------------------------------------------------

def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read()


def _github_tarball(owner_repo: str, branch: str = 'master') -> bytes:
    """下载 GitHub 仓库 tarball（codeload，免 API 配额）。"""
    return _http_get(
        f'https://codeload.github.com/{owner_repo}/tar.gz/refs/heads/{branch}'
    )


def _safe_join(out_dir: str, name: str) -> str:
    """防止压缩包里的 ../ 穿越到目标目录之外。"""
    dest = os.path.abspath(os.path.join(out_dir, name))
    root = os.path.abspath(out_dir)
    if not (dest == root or dest.startswith(root + os.sep)):
        raise ValueError(f'非法归档成员：{name}')
    return dest


def _write_bytes(out_dir: str, relname: str, data: bytes) -> str:
    dest = _safe_join(out_dir, relname)
    os.makedirs(os.path.dirname(dest) or out_dir, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    return dest


# --------------------------------------------------------------------------
# 文本预处理：各格式的既有坑
# --------------------------------------------------------------------------

# music21 的 humdrum 前端把 OCO（commission）映射到一个它自己 Metadata
# 不接受的名字，遇到就抛 KeyError 让整份谱解析失败。害upo「查看音乐」
# 的事实：所谓 ARG 的符号们不参与特征提取，丢掉不影响任何统计量。
_KERN_BREAKING_KEY = re.compile(r'^(!!!+)OCO:', re.M)


def sanitize_kern_text(text: str) -> str:
    """剔除 music21 无法吸收的 humdrum 全局引用键。

    定位过程写在 commit message 里：kern.humdrum.org 的 CCARH 版本内含
    `!!!OCO:`（题献给谁），而 music21 10.5 的 spineParser 把它映射到
    'commission' 后直接调 `Metadata.add()`，不存在的名字会抛 KeyError。
    海顿交响曲 kern 因此全军覆没 —— 不处理的话，「学交响乐」这一步就空了。
    """
    def _drop(_m: 're.Match[str]') -> str:
        return '!!OCO: (dropped by score_learning)'
    return _KERN_BREAKING_KEY.sub(_drop, text)


_ABC_INDEX_RE = re.compile(r'^X:\s*(\d+)\s*$', re.M)
_ABC_TITLE_RE = re.compile(r'^T:\s*(.+?)\s*$', re.M)


def split_abc_collection(text: str, collection: str) -> List[Tuple[str, str]]:
    """把一个多曲 ABC 集合按 `X:` 头拆成单曲。

    Nottingham 全库只有 13 个 .abc 文件 —— 每个是几百首曲子拼起来的合集。
    若整文件丢给 analyzer，music21 会把它当**一首曲子**解析（各首的音高被
    串联成一条荒谬的旋律），统计量毫无意义。拆成单曲是必须的一步。

    返回 [(文件名 stem, 单曲 abc 文本), ...]，顺序按文件中出现顺序。
    """
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    tunes: List[List[str]] = []
    cur: Optional[List[str]] = None
    for ln in lines:
        if re.match(r'^X:\s*\d+\s*$', ln):
            if cur is not None:
                tunes.append(cur)
            cur = [ln]
            continue
        if cur is not None:
            cur.append(ln)
        # 第一个 X: 之前的内容是版本声明之类，与单曲无关，丢弃
    if cur is not None:
        tunes.append(cur)

    out: List[Tuple[str, str]] = []
    for i, t in enumerate(tunes, 1):
        if len([x for x in t if x.strip()]) < 4:
            continue                                   # 空壳，跳过
        body = '\n'.join(t).strip() + '\n'
        m = _ABC_TITLE_RE.search(body)
        stem = f'{collection}_{i:04d}'
        if m:
            stem = f'{stem}_{sluggify(m.group(1))[:48]}'
        out.append((stem, body))
    return out


def sluggify(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '_', str(s or '')).strip('_')


# --------------------------------------------------------------------------
# 源定义
# --------------------------------------------------------------------------

@dataclass
class CorpusSource:
    id: str
    name: str
    family: str                 # 目标 genre family
    pack_id: str                # 目标 genre pack id
    homepage: str
    est_scores: str
    license: SourceLicense
    summary: str = ''
    kind: str = ''              # zip | github | sqlite


SOURCES: Dict[str, CorpusSource] = {}


def register(src: CorpusSource) -> CorpusSource:
    SOURCES[src.id] = src
    return src


register(CorpusSource(
    id='nottingham',
    name='Nottingham Music Database（ABC 民谣曲调）',
    family='folk',
    pack_id='british-folk-tune',
    homepage='http://www.ifdo.ca/~seymour/nottingham/nottingham.html',
    est_scores='~1200 首英美传统曲调',
    kind='zip',
    summary='英伦/爱尔兰 jig、reel、hornpipe、waltz、morris、playford；'
            '格式为多曲合一的大 ABC，需拆分成单曲才能统计。',
    license=SourceLicense(
        spdx='LicenseRef-Nottingham-NMD',
        url='http://abc.sourceforge.net/NMD',
        redistributable=False,
        ack_required=True,
        note='传统民谣（乐曲本身多已进入公有领域），但 NMD 原始格式的 '
             'IPR 归 Mick Peat，ABC 转写版由 Jay Glanville / Seymour Shlien '
             '维护，站点未给出明确的再分发条款。此处仅作本地学习用，'
             '不随仓库分发。',
    ),
))

register(CorpusSource(
    id='haydn-symphony',
    name='KernScores / CCARH：海顿伦敦交响曲 99–104（全乐章）',
    family='classical',
    pack_id='symphony',
    homepage='https://github.com/musedata/humdrum-haydn-symphonies',
    est_scores='24 个乐章（6 部交响 × 4 乐章）',
    kind='github',
    summary='完整管弦乐总谱 **kern 编码：18 个声部含木管/铜管/定音鼓/弦乐。'
            '此前本项目从未有过真正的交响乐曲料。',
    license=SourceLicense(
        spdx='LicenseRef-CCARH-MuseData',
        url='https://raw.githubusercontent.com/musedata/humdrum-haydn-symphonies/'
            'master/LICENSE.txt',
        redistributable=False,
        ack_required=True,
        note='CCARH MuseData 条款：仅供学术研究与个人使用，不得用于任何商业'
             '分发/演奏/录音，须注明来源。此处只从中导出聚合统计量，'
             '不入库、不外发。',
    ),
))

register(CorpusSource(
    id='mctl-symphony',
    name='S3 Symbolic Symphony Set：莫扎特第 41 / 贝多芬第 9（全乐章 MusicXML）',
    family='classical',
    pack_id='symphony',
    homepage='https://github.com/iis-mctl/mctl-symphony-dataset',
    est_scores='8 个乐章（莫扎特 4 + 贝多芬 4；同库另有德沃夏克/柴可夫斯基可选）',
    kind='github',
    summary='MIT 许可的管弦乐 MusicXML + 乐句/和声/配器层标注。'
            '补齐海顿之外的莫扎特、贝多芬交响语料。',
    license=SourceLicense(
        spdx='MIT',
        url='https://raw.githubusercontent.com/iis-mctl/mctl-symphony-dataset/main/LICENSE',
        redistributable=True,
        ack_required=False,
        note='S3 数据集 MIT License；原曲公有领域。本地学习与再分发标注均可。',
    ),
))

register(CorpusSource(
    id='beethoven-symphony',
    name='CCARH：贝多芬九部交响曲（MuseData stage2 总谱）',
    family='classical',
    pack_id='symphony',
    homepage='https://www.ccarh.org/publications/beethoven-symphonies/',
    est_scores='约 40 个乐章/段落（九部交响；第九终曲拆段）',
    kind='bitbucket',
    summary='CCARH 2009 数字版：Bitbucket musedata/beethoven 上的 study/score '
            'MuseData（.md2）总谱。许可同 CCARH MuseData：学术研究与个人使用，'
            '只导出聚合统计量，不入库、不外发。',
    license=SourceLicense(
        spdx='LicenseRef-CCARH-MuseData',
        url='https://www.ccarh.org/publications/beethoven-symphonies/',
        redistributable=False,
        ack_required=True,
        note='CCARH MuseData 条款：仅供学术研究与个人使用，不得用于任何商业'
             '分发/演奏/录音，须注明来源。此处只从中导出聚合统计量，'
             '不入库、不外发。',
    ),
))

register(CorpusSource(
    id='classical-kern',
    name='KernScores 系：海顿/莫扎特钢琴奏鸣曲与弦乐四重奏',
    family='classical',
    pack_id='classical-sonata',
    homepage='https://github.com/craigsapp/beethoven-piano-sonatas',
    est_scores='数百个乐章',
    kind='github',
    summary='奏鸣曲/四重奏乐章层面的古典语料，与交响曲互补，用于撑起'
            '「交响/古典」这一族的统计量。',
    license=SourceLicense(
        spdx='LicenseRef-CCARH-MuseData',
        url='https://raw.githubusercontent.com/musedata/humdrum-haydn-symphonies/'
            'master/LICENSE.txt',
        redistributable=False,
        ack_required=True,
        note='同一批 CCARH/KernScores 编码，条款相同：学术研究与个人用途，'
             '不入库、不外发。',
    ),
))

register(CorpusSource(
    id='weimar-jazz',
    name='Weimar Jazz Database（456 首爵士即兴独奏 + 和弦标注）',
    family='jazz',
    pack_id='swing-standard',
    homepage='https://jazzomat.hfm-weimar.de/download/download.html',
    est_scores='456 段独奏，20 万+ 音符',
    kind='sqlite',
    summary='含 Parker/Coltrane/Rollins/Davis 等从传统爵士到 post-bop 的转写，'
            '并按拍给出和弦标记 —— 直接可算出「七和弦占比」这个体裁指纹。',
    license=SourceLicense(
        spdx='ODbL-1.0',
        url='https://opendatacommons.org/licenses/odbl/1-0/',
        redistributable=True,
        ack_required=False,
        note='开放数据库许可：可自由使用，衍生数据库需同样开放并署名。',
    ),
))

register(CorpusSource(
    id='cocopops',
    name='CoCoPops：Billboard Hot 100 与 RollingStone 流行歌旋律/和声',
    family='pop',
    pack_id='pop-standard',
    homepage='https://github.com/Computational-Cognitive-Musicology-Lab/CoCoPops',
    est_scores='~740 首',
    kind='github',
    summary='补齐「流行乐」谱爱吃的一环：214 首带旋律+和声的完整转写，'
            '其余为 McGill Billboard 的和声转写。',
    license=SourceLicense(
        spdx='LicenseRef-McGill-Billboard',
        url='https://ddmal.music.mcgill.ca/research/'
            'The_McGill_Billboard_Project_(Chord_Analysis_Dataset)/',
        redistributable=False,
        ack_required=True,
        note='派生自 McGill Billboard 项目（CC BY-NC-SA），上层 CoCoPops 仓库'
             '本身未声明许可。原曲仍有版权；此处仅提取统计量，不入库。',
    ),
))



register(CorpusSource(
    id='chopin-mazurkas',
    name='KernScores：肖邦玛祖卡全集（Humdrum）',
    family='dance',
    pack_id='mazurka',
    homepage='https://github.com/craigsapp/chopin-mazurkas',
    est_scores='52 首',
    kind='github',
    summary='Fryderyk Chopin 玛祖卡数字版——波兰舞曲体裁的大师级语料。',
    license=SourceLicense(
        spdx='LicenseRef-CCARH-MuseData',
        url='https://github.com/craigsapp/chopin-mazurkas',
        redistributable=False,
        ack_required=True,
        note='KernScores/CCARH 系编码：学术研究与个人用途；只导出聚合统计，不入库。',
    ),
))

register(CorpusSource(
    id='chopin-preludes',
    name='KernScores：肖邦前奏曲 Op.28（Humdrum）',
    family='academic',
    pack_id='piano-prelude',
    homepage='https://github.com/craigsapp/chopin-preludes',
    est_scores='24 首',
    kind='github',
    summary='Chopin 二十四首前奏曲完整套——钢琴前奏曲体裁的大师级语料。',
    license=SourceLicense(
        spdx='LicenseRef-CCARH-MuseData',
        url='https://github.com/craigsapp/chopin-preludes',
        redistributable=False,
        ack_required=True,
        note='KernScores/CCARH 系编码：学术研究与个人用途；只导出聚合统计，不入库。',
    ),
))


# --------------------------------------------------------------------------
# 各个源的取数实现
# --------------------------------------------------------------------------

def fetch_nottingham(src: CorpusSource, out_dir: str, limit: Optional[int],
                     refresh: bool) -> FetchResult:
    """下载 NMD 全集 zip，把每个多曲 ABC 拆成单曲文件。"""
    if not refresh and any(f.endswith('.abc') for _, _, fs in os.walk(out_dir)
                           for f in fs):
        return FetchResult(src.id, out_dir, notes=['已存在，跳过（--refresh 强制重采）'])

    raw = _http_get('https://ifdo.ca/~seymour/nottingham/nottingham_database.zip')
    res = FetchResult(src.id, out_dir)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        for name in sorted(zf.namelist()):
            if not name.lower().endswith('.abc'):
                continue
            collection = os.path.splitext(os.path.basename(name))[0]
            try:
                payload = zf.read(name).decode('utf-8', errors='replace')
            except Exception as e:
                res.notes.append(f'{name}: 解码失败 {e}')
                continue
            tunes = split_abc_collection(payload, collection)
            for stem, body in tunes:
                dest = _write_bytes(
                    out_dir, f'{collection}/{stem}.abc', body.encode('utf-8'))
                res.files.append(dest)
                if limit and len(res.files) >= limit:
                    res.notes.append(f'达到 --limit={limit}，提前停止')
                    return res
    return res


def _fetch_github_scores(src: CorpusSource, out_dir: str,
                         repos: Sequence[Tuple[str, str]],
                         limit: Optional[int], refresh: bool,
                         sub_in_exts: Sequence[str],
                         filename_prefix: str = '') -> FetchResult:
    """从一批 GitHub humdrum 仓库里抽出指定扩展名的乐谱文件。

    每个仓库是一个 releases repo（可能有的版本/重试），这里统一取默认分支
    master/main 中第一个成功的那个 —— 确定性优先，不猜分支名以外的东西。
    """
    res = FetchResult(src.id, out_dir)
    exts = tuple(e.lower() for e in sub_in_exts)
    for owner_repo, sub in repos:
        blob = None
        for br in ('master', 'main'):
            try:
                blob = _github_tarball(owner_repo, br)
                break
            except Exception as e:
                res.notes.append(f'{owner_repo}@{br}: {e}')
        if blob is None:
            continue
        repo_tag = sluggify(owner_repo.rsplit('/', 1)[-1])
        try:
            tf = tarfile.open(fileobj=BytesIO(blob), mode='r:gz')
        except Exception as e:
            res.notes.append(f'{owner_repo}: tarball 损坏 {e}')
            continue
        members = [m for m in tf.getmembers()
                   if m.isfile() and m.name.lower().endswith(exts)]
        # tar 内的顶层目录名带分支名（repo-master/…），这里只保留子路径
        for m in sorted(members, key=lambda m: m.name):
            parts = m.name.split('/')
            rel = '/'.join(parts[1:])
            if sub and not rel.startswith(sub.rstrip('/') + '/'):
                continue
            base = os.path.basename(rel)
            stem = f'{repo_tag}_{sluggify(os.path.splitext(base)[0])}'
            relname = os.path.join(repo_tag, f'{filename_prefix}{stem}.krn')
            dest = os.path.join(out_dir, relname)
            if os.path.exists(dest) and not refresh:
                res.files.append(dest)
                continue
            try:
                data = tf.extractfile(m).read()
            except Exception as e:
                res.notes.append(f'{owner_repo}/{rel}: {e}')
                continue
            try:
                text = data.decode('utf-8', errors='replace')
            except Exception:
                text = ''
            if text:
                _write_bytes(out_dir, relname, sanitize_kern_text(text).encode('utf-8'))
                res.files.append(os.path.join(out_dir, relname))
            if limit and len(res.files) >= limit:
                res.notes.append(f'达到 --limit={limit}，提前停止')
                return res
        tf.close()
    return res


def fetch_haydn_symphony(src: CorpusSource, out_dir: str,
                         limit: Optional[int], refresh: bool) -> FetchResult:
    return _fetch_github_scores(
        src, out_dir,
        [('musedata/humdrum-haydn-symphonies', 'kern')],
        limit, refresh, ('.krn',),
    )


#: CCARH 贝多芬交响曲总谱（MuseData .md2）在 Bitbucket 上的路径模式
_BEETHOVEN_MD2_RE = re.compile(
    r'https://bitbucket\.org/musedata/beethoven/raw/master/'
    r'(bhl/orch/sym\d+/editions/public/score/mvt[^/\s]+\.md2)',
    re.I,
)
_BEETHOVEN_INDEX_URLS = (
    'https://musedata.org/list/musedata-index.txt',
    'https://raw.githubusercontent.com/musedata/musedata-website/master/'
    'list/musedata-index.txt',
)


def _beethoven_symphony_md2_urls() -> List[Tuple[str, str]]:
    """返回 [(相对路径, 绝对 URL), ...]；优先扫 musedata 菜单索引。"""
    text = ''
    notes = []
    for url in _BEETHOVEN_INDEX_URLS:
        try:
            text = _http_get(url).decode('utf-8', errors='replace')
            if 'bhl/orch/sym' in text:
                break
        except Exception as e:
            notes.append(str(e))
            text = ''
    found: Dict[str, str] = {}
    if text:
        for m in _BEETHOVEN_MD2_RE.finditer(text):
            rel = m.group(1)
            found[rel] = m.group(0)
    # 索引缺失时按九部交响的惯例补全（第 6 有 5 乐章；第 9 终曲多段）
    if not found:
        for n in range(1, 10):
            mvt_ids = (
                ['1', '2', '3', '4', '5'] if n == 6 else
                ['1', '2', '3', '4a', '4b', '4c', '4d', '4e', '4f', '4g'] if n == 9 else
                ['1', '2', '3', '4']
            )
            for mvt in mvt_ids:
                rel = f'bhl/orch/sym{n}/editions/public/score/mvt{mvt}.md2'
                found[rel] = (
                    'https://bitbucket.org/musedata/beethoven/raw/master/' + rel)
    return sorted(found.items())


def fetch_beethoven_symphony(src: CorpusSource, out_dir: str,
                             limit: Optional[int], refresh: bool) -> FetchResult:
    """CCARH 贝多芬九部交响曲：从 Bitbucket MuseData 拉 study/score .md2 总谱。"""
    res = FetchResult(src.id, out_dir)
    try:
        entries = _beethoven_symphony_md2_urls()
    except Exception as e:
        res.notes.append(f'无法列出乐章：{e}')
        return res
    if not entries:
        res.notes.append('未找到任何 sym*/score/mvt*.md2')
        return res
    seen = set()
    for rel, url in entries:
        # 去重（索引里第 9 终曲个别 shortcut 指到同一文件）
        if rel in seen:
            continue
        seen.add(rel)
        stem = sluggify(rel.replace('/', '_').replace('.md2', ''))
        relname = f'{stem}.md2'
        dest = os.path.join(out_dir, relname)
        if os.path.exists(dest) and not refresh:
            res.files.append(dest)
        else:
            try:
                data = _http_get(url)
            except Exception as e:
                res.notes.append(f'{rel}: {e}')
                continue
            if len(data) < 200 or b'WK#:' not in data and b'@filename' not in data:
                res.notes.append(f'{rel}: 内容不像 MuseData（{len(data)} bytes）')
                continue
            _write_bytes(out_dir, relname, data)
            res.files.append(dest)
        if limit and len(res.files) >= limit:
            res.notes.append(f'达到 --limit={limit}，提前停止')
            break
    res.notes.append(f'已收录 {len(res.files)} 个乐章 MuseData（.md2）')
    return res


def fetch_mctl_symphony(src: CorpusSource, out_dir: str,
                        limit: Optional[int], refresh: bool) -> FetchResult:
    """S3：默认只取莫扎特（mo*）与贝多芬（be*）MusicXML；可用环境变量扩到全库。

    MCTL_SYMPHONY_ALL=1 → 同时收德沃夏克 / 柴可夫斯基。
    """
    import os as _os
    want_all = str(_os.environ.get('MCTL_SYMPHONY_ALL') or '').strip() in (
        '1', 'true', 'yes', 'YES')
    prefixes = ('mo', 'be') if not want_all else ('mo', 'be', 'dv', 'tc')
    res = FetchResult(src.id, out_dir)
    blob = None
    for br in ('main', 'master'):
        try:
            blob = _github_tarball('iis-mctl/mctl-symphony-dataset', br)
            break
        except Exception as e:
            res.notes.append(f'mctl-symphony@{br}: {e}')
    if blob is None:
        return res
    try:
        tf = tarfile.open(fileobj=BytesIO(blob), mode='r:gz')
    except Exception as e:
        res.notes.append(f'mctl tarball 损坏: {e}')
        return res
    members = [m for m in tf.getmembers()
               if m.isfile() and m.name.lower().endswith('sheet.xml')]
    for m in sorted(members, key=lambda x: x.name):
        parts = m.name.split('/')
        # …/symbolic_symphony_set/mo1/sheet.xml
        piece = ''
        for i, p in enumerate(parts):
            if p == 'symbolic_symphony_set' and i + 1 < len(parts):
                piece = parts[i + 1]
                break
        if not piece or not piece.startswith(prefixes):
            continue
        relname = f'{piece}.musicxml'
        dest = os.path.join(out_dir, relname)
        if os.path.exists(dest) and not refresh:
            res.files.append(dest)
            continue
        try:
            data = tf.extractfile(m).read()
        except Exception as e:
            res.notes.append(f'{piece}: {e}')
            continue
        _write_bytes(out_dir, relname, data)
        res.files.append(os.path.join(out_dir, relname))
        if limit and len(res.files) >= limit:
            res.notes.append(f'达到 --limit={limit}，提前停止')
            tf.close()
            return res
    tf.close()
    res.notes.append(f'已收录 {len(res.files)} 个乐章（前缀 {prefixes}）')
    return res


def fetch_classical_kern(src: CorpusSource, out_dir: str,
                         limit: Optional[int], refresh: bool) -> FetchResult:
    per_repo = limit // 4 if limit else None
    return _fetch_github_scores(
        src, out_dir,
        [
            ('craigsapp/beethoven-piano-sonatas', 'kern'),
            ('craigsapp/mozart-piano-sonatas', 'kern'),
            ('craigsapp/haydn-piano-sonatas', 'kern'),
            ('humdrum-tools/bach-wtc-fugues', 'kern'),
        ],
        per_repo, refresh, ('.krn',),
    )


def fetch_cocopops(src: CorpusSource, out_dir: str,
                   limit: Optional[int], refresh: bool) -> FetchResult:
    """CoCoPops：只收既有旋律又有和声的 .hum（214 首），质量最高。"""
    return _fetch_github_scores(
        src, out_dir,
        [('Computational-Cognitive-Musicology-Lab/CoCoPops', 'Billboard/Data')],
        limit, refresh, ('.hum',),
    )


# --------------------------------------------------------------------------
# Weimar Jazz：SQLite → lead sheet MusicXML
# --------------------------------------------------------------------------

# 数据库里的乐器缩写 → 人类可读
_INSTRUMENT_NAMES = {
    'ts': 'Tenor Sax', 'tp': 'Trumpet', 'as': 'Alto Sax', 'tb': 'Trombone',
    'ss': 'Soprano Sax', 'cor': 'Cornet', 'cl': 'Clarinet', 'vib': 'Vibraphone',
    'bs': 'Baritone Sax', 'p': 'Piano', 'g': 'Guitar', 'b': 'Bass',
    'fl': 'Flute', 'org': 'Organ', 'tu': 'Tuba', 'vc': 'Cello',
}
_SUBDIV = 4      # 量化到 1/4 拍
_MAX_EXPORT_BARS = 32


_WEIMAR_ROOT_RE = re.compile(r'^([A-Ga-g])([#b]*)(.*)$')

# Weimar 的和弦记法不是 music21 的记法，且更危险的是**朴素喂给
# ChordSymbol() 会静默解析错**：实测 ChordSymbol('Bb7') 得到 root=B、
# 和音 B-D#-F#-A（把 'b7' 当成了后缀而不是降记号），'Eb7' 同理。
# 自己拆「根音 + 品质后缀」绕开这条路，再用 ChordSymbol(root=..., kind=...)
# 显式构造，后者经 MusicXML 往返是完全保真的。
_WEIMAR_KIND_RULES: Sequence[Tuple[str, str]] = (
    # 顺序要紧：更具体的先匹配
    ('7b5', 'half-diminished-seventh'), ('7#5', 'dominant-seventh'),
    ('m7b5', 'half-diminished-seventh'), ('-7b5', 'half-diminished-seventh'),
    ('maj7', 'major-seventh'), ('ma7', 'major-seventh'),
    ('j7', 'major-seventh'),            # Weimar 用 j 表示大七
    ('mi7', 'minor-seventh'), ('-7', 'minor-seventh'), ('m7', 'minor-seventh'),
    ('dim7', 'diminished-seventh'), ('o7', 'diminished-seventh'),
    ('b9', 'dominant-seventh'), ('#9', 'dominant-seventh'),
    ('7alt', 'dominant-seventh'), ('alt', 'dominant-seventh'),
    ('7sus', 'dominant-seventh'), ('sus', 'major'),
    ('13', 'dominant-seventh'), ('9', 'dominant-seventh'),
    ('6/9', 'major-sixth'), ('69', 'major-sixth'), ('6', 'major-sixth'),
    ('aug', 'augmented'), ('+', 'augmented'),
    ('dim', 'diminished'), ('o', 'diminished'),
    ('min', 'minor'), ('mi', 'minor'), ('-', 'minor'), ('m', 'minor'),
    ('maj', 'major'), ('j', 'major'), ('M', 'major'),
)


_NO_CHORD_TOKENS = frozenset({'NC', 'NC.', 'N.C.', 'X', 'REST'})


def parse_weimar_chord(symbol: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """把 Weimar 的和弦字符串拆成 (music21 根音名, chordKind, 低音名或 None)。

    返回 None 表示解析不了 —— 宁可少一个和弦，也不要猜一个错的混进统计量。
    'NC'（no chord，无和弦）是**合法的空**，按 None 处理但不应计入失败。
    """
    sym = str(symbol or '').strip()
    if not sym:
        return None
    if sym.upper() in _NO_CHORD_TOKENS:
        return None

    # 'F/A' 这类指定了低音的转位：前半段按普通和弦解析，后半段是低音
    bass: Optional[str] = None
    if '/' in sym:
        head, tail = sym.split('/', 1)
        bm = _WEIMAR_ROOT_RE.match(tail.replace(' ', ''))
        if bm:
            bass = bm.group(1).upper() + bm.group(2).replace('b', '-')
            sym = head

    m = _WEIMAR_ROOT_RE.match(sym.replace(' ', ''))
    if not m:
        return None
    letter, acc, rest = m.group(1), m.group(2), m.group(3)
    root = letter.upper() + acc.replace('b', '-')
    rest_l = rest
    if not rest_l:
        return root, 'major', bass
    # '7' 单独出现要在其它键之前处理完；到这里再按规则表逐条匹配
    for token, kind in _WEIMAR_KIND_RULES:
        if token in rest_l:
            return root, kind, bass
    if rest_l.startswith('7'):
        return root, 'dominant-seventh', bass
    return None


# --------------------------------------------------------------------------
# CoCoPops：humdrum 多轨格式 → lead sheet MusicXML
# --------------------------------------------------------------------------

# music21 读不了 CoCoPops 的 `**harm`（罗马数字）轨 —— spineParser 会把
# 里面的 token 交给 roman.RomanNumeral()，遇到 N attribute 就崩。
# 但我们要的那列不是它：`**harte` 才是 sudo FITA 的和弦标记，形如 `E:min`、
# `A:maj6`、`B:sus4(b7)`。于是同样的策略 —— 自己拆，不喂给 music21 猜。
_HARTE_KIND_RULES: Sequence[Tuple[str, str]] = (
    ('maj7', 'major-seventh'), ('min7', 'minor-seventh'),
    ('maj6', 'major-sixth'), ('min6', 'minor-sixth'),
    ('maj9', 'major-ninth'), ('min9', 'minor-ninth'),
    ('hdim7', 'half-diminished-seventh'),
    ('min7b5', 'half-diminished-seventh'),
    ('dim7', 'diminished-seventh'), ('dim', 'diminished'),
    ('aug', 'augmented'), ('+', 'augmented'),
    ('sus4(b7)', 'dominant-seventh'), ('sus4', 'major'), ('sus2', 'major'),
    ('7', 'dominant-seventh'), ('9', 'dominant-seventh'),
    ('maj', 'major'), ('min', 'minor'), ('mi', 'minor'), ('m', 'minor'),
)

_HARTE_ROOT_RE = re.compile(r'^([A-Ga-g])([#-]*)')

_KERN_TOK_RE = re.compile(r'^(\d+)(\.*)(?![.\d])(.*)$')
_KERN_PITCH_RE = re.compile(r'^([a-gA-G]+)([#-]*)')


def parse_harte_chord(token: str) -> Optional[Tuple[str, str]]:
    """把 `E:min` / `A:maj6` 这类标记拆成 (music21 根音名, chordKind)。"""
    tok = str(token or '').strip()
    if not tok or tok == '.' or tok.upper() in _NO_CHORD_TOKENS:
        return None
    if ':' not in tok:
        return None
    root_s, quality_s = tok.split(':', 1)
    rm = _HARTE_ROOT_RE.match(root_s.strip())
    if not rm:
        return None
    root = rm.group(1).upper() + rm.group(2).replace('b', '-')
    q = quality_s.strip().lower()
    if not q:
        return root, 'major'
    for token_k, kind in _HARTE_KIND_RULES:
        if token_k in q:
            return root, kind
    return None


def _kern_token_to_note(token: str):
    """把一个 **kern token 解成 (音名或 None(休止), 四分音符数)。

    None 的音名表示休止；返回 None 表示这个 token 不占位（fill / 非数据行）。
    **kern 的时值用倒数表示（4 = 四分音符 ⇒ 4/4 = 1 个四分），附点加半倍。
    """
    t = str(token or '').strip()
    if not t or t == '.':
        return None
    t = t.strip('[]_;`').replace(';', '')
    if not t or t == '.':
        return None
    m = _KERN_TOK_RE.match(t)
    if not m:
        return None
    n = int(m.group(1))
    dots = len(m.group(2))
    body = m.group(3).strip('[]_')
    if n <= 0:
        return None
    quarters = (4.0 / float(n)) * (2.0 - (0.5 ** dots))
    if not body or body.startswith('r'):
        return None, quarters
    pm = _KERN_PITCH_RE.match(body)
    if not pm:
        return None, quarters
    letters = pm.group(1)
    acc = pm.group(2)
    letter = letters[0]
    count = len(letters) if len(set(letters.lower())) == 1 else 1
    octave = (3 + count) if letter.islower() else (3 - count)
    name = letter.upper() + acc.replace('-', '-') + str(octave)
    return name, quarters


def hum_to_leadsheet(text: str, title: str, max_bars: int = 64) -> Optional[Any]:
    """把一份 CoCoPops .hum 转成「旋律 + 和弦符号」的 music21 Score。

    为什么必须自己走一遍 token：music21 的 humdrum 前端会被 `**harm` 轨
    （罗马数字）直接打死，而 CoCoPops 每份都有这一轨。迷信 accessibility 的
    做法是把整份文件交给 converter.parse()，结果 214 首**一首都读不出来**。
    """
    from music21 import harmony as m21harmony
    from music21 import key as m21key
    from music21 import meter as m21meter
    from music21 import note as m21note
    from music21 import stream as m21stream

    lines = text.replace('\r\n', '\n').split('\n')
    header_i = -1
    for i, ln in enumerate(lines[:20]):
        if ln.startswith('**'):
            header_i = i
            break
    if header_i < 0:
        return None
    names = lines[header_i].rstrip('\n').split('\t')
    try:
        i_harte = names.index('**harte')
    except ValueError:
        return None
    i_kern = None
    for cand in ('**kern', '**melody'):
        if cand in names:
            i_kern = names.index(cand)
            break
    if i_kern is None:
        return None

    meter_str, key_root, key_mode = '4/4', None, 'major'
    for ln in lines[header_i + 1: header_i + 14]:
        cols = ln.rstrip('\n').split('\t')
        if len(cols) <= max(i_harte, i_kern):
            continue
        m = re.match(r'^\*M(\d+/\d+)$', cols[i_harte].strip())
        if m:
            meter_str = m.group(1)
        km = re.match(r'^\*([A-G][#-]?):(\w*)$', cols[i_harte].strip())
        if km:
            key_root = km.group(1).replace('b', '-')
            key_mode = 'minor' if 'min' in km.group(2).lower() else 'major'
        mk = re.match(r'^\*M(\d+/\d+)$', cols[i_kern].strip())
        if mk and meter_str == '4/4':
            meter_str = mk.group(1)

    part = m21stream.Part()
    try:
        part.insert(0, m21meter.TimeSignature(meter_str))
    except Exception:
        pass
    if key_root:
        try:
            part.insert(0, m21key.Key(key_root, key_mode))
        except Exception:
            pass

    offset = 0.0
    bars = 0
    notes_written = 0
    chords_written = 0
    last_root_kind: Optional[Tuple[str, str]] = None
    for ln in lines[header_i + 1:]:
        s = ln.strip()
        if not s or s.startswith('*') or s.startswith('!'):
            continue
        if s.startswith('='):
            bars += 1
            if max_bars and bars > max_bars:
                break
            continue
        cols = ln.rstrip('\n').split('\t')
        if len(cols) <= max(i_harte, i_kern):
            continue
        parsed_n = _kern_token_to_note(cols[i_kern])
        if parsed_n is not None:
            name, quarters = parsed_n
            if quarters > 0:
                if name:
                    try:
                        n = m21note.Note(name)
                    except Exception:
                        n = None
                    if n is not None:
                        n.quarterLength = quarters
                        part.insert(offset, n)
                        notes_written += 1
                try:
                    from music21 import note as _mnote
                    if not name:
                        r = _mnote.Rest()
                        r.quarterLength = quarters
                        part.insert(offset, r)
                except Exception:
                    pass
            offset += quarters
        # 和弦：只记录和弦换了的那些拍
        hk = parse_harte_chord(cols[i_harte])
        if hk is not None and hk != last_root_kind:
            try:
                cs = m21harmony.ChordSymbol(root=hk[0], kind=hk[1])
                part.insert(offset, cs)
                chords_written += 1
            except Exception:
                pass
            last_root_kind = hk

    if notes_written < 8 or chords_written < 2:
        return None
    score = m21stream.Score()
    score.insert(0, part)
    try:
        from music21 import metadata as m21metadata
        md = m21metadata.Metadata()
        md.title = str(title or 'untitled')
        score.insert(0, md)
    except Exception:
        pass
    return score


def fetch_cocopops(src: CorpusSource, out_dir: str,
                   limit: Optional[int], refresh: bool) -> FetchResult:
    """CoCoPops：把 humdrum 多轨文件逐份转成 lead sheet MusicXML。

    原文件保留在 _source/hum/ 下（.hum 不在 SCORE_EXTS 里，不会被入库分析），
    真正参与学习的是转出来的 lead sheet。
    """
    res = FetchResult(src.id, out_dir)
    blob = None
    for br in ('master', 'main'):
        try:
            blob = _github_tarball(
                'Computational-Cognitive-Musicology-Lab/CoCoPops', br)
            break
        except Exception as e:
            res.notes.append(f'CoCoPops@{br}: {e}')
    if blob is None:
        return res
    raw_dir = os.path.join(out_dir, '_source', 'hum')
    failed = 0
    try:
        tf = tarfile.open(fileobj=BytesIO(blob), mode='r:gz')
    except Exception as e:
        res.notes.append(f'tarball 损坏：{e}')
        return res
    members = [m for m in tf.getmembers()
               if m.isfile() and m.name.lower().endswith('.hum')]
    for m in sorted(members, key=lambda m: m.name):
        if limit and len(res.files) >= limit:
            res.notes.append(f'达到 --limit={limit}，提前停止')
            break
        base = os.path.basename(m.name)
        if 'Billboard/Data' not in m.name:
            continue
        try:
            data = tf.extractfile(m).read()
        except Exception:
            continue
        text = data.decode('utf-8', errors='replace')
        _write_bytes(raw_dir, base, data)
        score = hum_to_leadsheet(text, os.path.splitext(base)[0])
        if score is None:
            failed += 1
            continue
        rel = os.path.join('Billboard', sluggify(os.path.splitext(base)[0])
                           [:64] + '.musicxml')
        dest = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            score.write('musicxml', fp=dest)
        except Exception as e:
            res.notes.append(f'{base}: 写盘失败 {e}')
            failed += 1
            continue
        res.files.append(dest)
    tf.close()
    res.skipped = failed
    return res


def _key_and_mode(key_text: str) -> Tuple[Optional[str], str]:
    """'Bb-maj' → ('Bb', 'major')；'F#-min' → ('F#', 'minor')。"""
    if not key_text:
        return None, 'major'
    tok = key_text.strip()
    mode = 'minor' if tok.lower().endswith('-min') else 'major'
    root = tok.rsplit('-', 1)[0].replace('b', '-')
    return root, mode


def _beat_quarter_length(signature: str) -> Tuple[float, int, int]:
    """(每拍几个四分音符, 分子, 分母)。解析不了就按 4/4 处理。"""
    m = re.match(r'^\s*(\d+)\s*/\s*(\d+)\s*$', signature or '')
    if not m:
        return 1.0, 4, 4
    num, den = int(m.group(1)), int(m.group(2))
    return 4.0 / float(den or 4), num, den


def _quantize(pos_beats: float, subdiv: int = _SUBDIV) -> float:
    return round(pos_beats * subdiv) / subdiv


def build_weimar_leadsheets(db_path: str, out_dir: str,
                            limit: Optional[int] = None,
                            max_bars: int = _MAX_EXPORT_BARS,
                            styles: Optional[Sequence[str]] = None
                            ) -> FetchResult:
    """把 Weimar Jazz DB 的一个合唱段 export 成「旋律 + 和弦符号」lead sheet。

    为什么必须自己合成谱，而不用库里现成的 MIDI：变换出来的 Core Emotion
    MIDI 是**单声部**的。只交给 analyzer 的话，chordify() 解不出和声，
    七和弦占比会恒为 0 —— 「爵士有大量七和弦」这个唯一的体裁指纹就丢了，
    学到的东西跟「旋律很多连音」没区别。

    库里 beats 表按拍给出了和弦标注，正好用来补回这一层。
    """
    from music21 import harmony as m21harmony
    from music21 import key as m21key
    from music21 import metadata as m21metadata
    from music21 import meter as m21meter
    from music21 import note as m21note
    from music21 import stream as m21stream
    from music21 import tempo as m21tempo

    res = FetchResult('weimar-jazz', out_dir)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    style_filter = {s.upper() for s in styles} if styles else None
    rows = cur.execute(
        'SELECT melid, performer, title, instrument, style, avgtempo, '
        'rhythmfeel, key, signature FROM solo_info ORDER BY melid'
    ).fetchall()

    for sus in rows:
        if limit and len(res.files) >= limit:
            res.notes.append(f'达到 --limit={limit}，提前停止')
            break
        style = (sus['style'] or 'UNKNOWN').upper()
        if style_filter and style not in style_filter:
            continue
        melid = int(sus['melid'])
        beats = cur.execute(
            'SELECT beatid, onset, bar, beat, chord, chorus_id FROM beats '
            'WHERE melid=? ORDER BY beatid', (melid,)
        ).fetchall()
        if len(beats) < 8:
            res.skipped += 1
            continue
        # chorus_id 0 是前奏/计数小节，取正式的第一段合唱
        chorus_ids = sorted({int(b['chorus_id']) for b in beats})
        usable = [cid for cid in chorus_ids if cid >= 1] or chorus_ids
        first_chorus = usable[0]
        win = [b for b in beats if int(b['chorus_id']) == first_chorus]
        if len(win) < 8:
            res.skipped += 1
            continue

        beat_ql, num, den = _beat_quarter_length(sus['signature'])
        beats_per_bar = max(1, num if den in (2, 4, 8) and den <= 8 else 4)
        max_beats = int(max_bars * beats_per_bar)
        if len(win) > max_beats:
            win = win[:max_beats]

        grid = [float(b['onset']) for b in win]
        t0 = grid[0]

        def to_beats(t: float) -> Optional[float]:
            """秒 → 拍（含小数）。用相邻两个拍点的实际间隔插值，
            吸收一点即时 Flex 起伏。窗口之外返回 None。"""
            if len(grid) < 2 or grid[-1] <= grid[0]:
                return None
            idx = bisect.bisect_right(grid, t) - 1
            if idx < 0 or idx >= len(grid) - 1:
                return None
            span = grid[idx + 1] - grid[idx]
            frac = (t - grid[idx]) / span if span > 0 else 0.0
            return float(idx) + max(0.0, min(1.0, frac))

        part = m21stream.Part()
        notes_written = 0
        notes = cur.execute(
            'SELECT onset, pitch, duration FROM melody WHERE melid=? '
            'ORDER BY eventid', (melid,)).fetchall()
        for nt in notes:
            t = float(nt['onset'])
            dur_s = float(nt['duration'])
            b = to_beats(t)
            if b is None:
                continue
            b_end = to_beats(t + dur_s)
            if b_end is None:
                b_end = b + 0.25
            qb = _quantize(b)
            qd = max(1.0 / _SUBDIV, _quantize(max(b_end - b, 0.1)))
            try:
                n = m21note.Note(int(round(float(nt['pitch']))))
            except Exception:
                continue
            n.quarterLength = float(qd) * beat_ql
            part.insert(qb * beat_ql, n)
            notes_written += 1
        if notes_written < 8:
            res.skipped += 1
            continue

        # 和弦标记 —— 体裁指纹的来源。逐个换算，而不是丢给 ChordSymbol 去猜。
        chords_written = 0
        chords_unparsed = 0
        for idx, b in enumerate(win):
            raw = b['chord']
            if raw is None or not str(raw).strip():
                continue                       # 这一拍没换和弦，不是失败
            if str(raw).strip().upper() in _NO_CHORD_TOKENS:
                continue                       # NC = 无和弦，合法的空
            parsed = parse_weimar_chord(raw)
            if parsed is None:
                chords_unparsed += 1
                continue
            root, kind, bass = parsed
            try:
                cs = m21harmony.ChordSymbol(root=root, kind=kind, bass=bass)
            except Exception:
                chords_unparsed += 1
                continue
            part.insert(float(idx) * beat_ql, cs)
            chords_written += 1
        if chords_written < 2:
            # 没有和声标注的话这份语料就失去了最关键的维度，宁缺毋滥
            res.skipped += 1
            continue
        if chords_unparsed:
            res.notes.append(
                f'melid={melid}: {chords_unparsed} 个和弦记法未能解析')

        _root, mode = _key_and_mode(sus['key'])
        score = m21stream.Score()
        score.insert(0, part)
        try:
            md = m21metadata.Metadata()
            md.title = str(sus['title'] or f'untitled_{melid}')
            md.composer = (sus['performer']
                           or _INSTRUMENT_NAMES.get(sus['instrument'], '')
                           or 'unknown')
            score.insert(0, md)
        except Exception:
            pass
        try:
            part.insert(0, m21meter.TimeSignature(f'{num}/{den}'))
        except Exception:
            try:
                part.insert(0, m21meter.TimeSignature('4/4'))
            except Exception:
                pass
        if _root:
            try:
                part.insert(0, m21key.Key(_root, mode))
            except Exception:
                pass
        try:
            bpm = float(sus['avgtempo'])
            if 20 <= bpm <= 320:
                part.insert(0, m21tempo.MetronomeMark(number=round(bpm)))
        except Exception:
            pass

        title_stub = sluggify(f"{sus['performer']}_{sus['title']}")[:48]
        rel = f"{style.lower()}/{melid:04d}_{title_stub or 'solo'}.musicxml"
        dest = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            score.write('musicxml', fp=dest)
        except Exception as e:
            res.notes.append(f'melid={melid}: 写盘失败 {e}')
            res.skipped += 1
            continue
        res.files.append(dest)

    con.close()
    return res


def fetch_weimar_jazz(src: CorpusSource, out_dir: str,
                      limit: Optional[int], refresh: bool) -> FetchResult:
    db_url = 'https://jazzomat.hfm-weimar.de/download/downloads/wjazzd.db'
    db_path = os.path.join(out_dir, '_source', 'wjazzd.db')
    if not os.path.isfile(db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        try:
            with open(db_path, 'wb') as f:
                f.write(_http_get(db_url))
        except Exception as e:
            return FetchResult(src.id, out_dir, notes=[f'数据库下载失败：{e}'])
    return build_weimar_leadsheets(db_path, out_dir, limit=limit)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def fetch_chopin_mazurkas(src: CorpusSource, out_dir: str,
                          limit: Optional[int], refresh: bool) -> FetchResult:
    return _fetch_github_scores(
        src, out_dir,
        [('craigsapp/chopin-mazurkas', 'kern')],
        limit, refresh, ('.krn',),
    )


def fetch_chopin_preludes(src: CorpusSource, out_dir: str,
                          limit: Optional[int], refresh: bool) -> FetchResult:
    return _fetch_github_scores(
        src, out_dir,
        [('craigsapp/chopin-preludes', 'kern')],
        limit, refresh, ('.krn',),
    )


_FETCHERS = {
    'nottingham': fetch_nottingham,
    'haydn-symphony': fetch_haydn_symphony,
    'beethoven-symphony': fetch_beethoven_symphony,
    'mctl-symphony': fetch_mctl_symphony,
    'classical-kern': fetch_classical_kern,
    'weimar-jazz': fetch_weimar_jazz,
    'cocopops': fetch_cocopops,
    'chopin-mazurkas': fetch_chopin_mazurkas,
    'chopin-preludes': fetch_chopin_preludes,
}


def list_sources() -> List[Dict[str, Any]]:
    out = []
    for sid in sorted(SOURCES):
        s = SOURCES[sid]
        out.append({
            'id': sid,
            'family': s.family,
            'pack_id': s.pack_id,
            'name': s.name,
            'scores': s.est_scores,
            'license': s.license.spdx,
            'ack_required': s.license.ack_required,
            'homepage': s.homepage,
        })
    return out


def source_dir(src: CorpusSource, root: Optional[str] = None) -> str:
    return os.path.join(root or CORPUS_ROOT, src.id)


def write_manifest(src: CorpusSource, out_dir: str, res: FetchResult) -> str:
    """写 manifest.json + LICENSE_NOTICE.txt —— 语料的可复现凭证。"""
    os.makedirs(out_dir, exist_ok=True)
    lic_path = os.path.join(out_dir, LICENSE_NAME)
    with open(lic_path, 'w', encoding='utf-8') as f:
        f.write(f'{src.name}\n{"=" * len(src.name)}\n\n')
        f.write(src.license.as_text() + '\n\n')
        f.write(f'来源：{src.homepage}\n')
        f.write(f'获取时间：{datetime.now().isoformat(timespec="seconds")}\n')
        f.write('本目录不进入版本控制（见 .gitignore 的 scores/corpus/）。\n')
    man_path = os.path.join(out_dir, MANIFEST_NAME)
    payload = {
        'source_id': src.id,
        'name': src.name,
        'family': src.family,
        'pack_id': src.pack_id,
        'homepage': src.homepage,
        'summary': src.summary,
        'expected_scale': src.est_scores,
        'license': asdict(src.license),
        'fetched_at': datetime.now().isoformat(timespec='seconds'),
        'files_written': len(res.files),
        'files_skipped': res.skipped,
        'notes': res.notes,
    }
    with open(man_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return man_path


def fetch_source(source_id: str, root: Optional[str] = None,
                 limit: Optional[int] = None, refresh: bool = False,
                 accept_license: bool = False) -> FetchResult:
    """采集一个源。返回 FetchResult（含写出的文件清单与诊断信息）。"""
    src = SOURCES.get(source_id)
    if not src:
        known = ', '.join(sorted(SOURCES))
        raise KeyError(f'未知语料源：{source_id}。可选：{known}')
    if src.license.ack_required and not accept_license:
        raise PermissionError(
            f'语料源 {source_id} 需要先确认许可条款：\n\n'
            f'  {src.license.as_text()}\n\n'
            f'  条款原文：{src.license.url}\n'
            f'  确认后加 --accept-license 重跑。'
        )
    out_dir = source_dir(src, root)
    os.makedirs(out_dir, exist_ok=True)
    fn = _FETCHERS.get(source_id)
    if fn is None:
        raise NotImplementedError(f'源 {source_id} 尚未实现取数逻辑')
    res = fn(src, out_dir, limit, refresh)
    write_manifest(src, out_dir, res)
    return res
