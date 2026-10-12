"""batch_generate 测试：风格包批量生成流水线（确定性、可审计）。"""
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import batch_generate as B  # noqa: E402
import re as _re


def check(name, cond, extra=''):
    if cond:
        print(f'  ✓ {name}')
        return True
    print(f'  ✗ {name} {extra}')
    return False


def _selftest():
    fail = 0

    pk = B.enumerate_packs()
    fail += 0 if check('枚举到风格包', len(pk) > 0, str(len(pk))) else 1

    # 限定 2 个包，每包 1 首，不渲染（快）
    subset = [p['pack_id'] for p in pk[:2]]
    rep = B.run_batch(subset, per_pack=1, render=False)
    fail += 0 if check('报告含标题', '# 风格包批量生成流水线' in rep) else 1
    for pid in subset:
        fail += 0 if check(f'报告含 {pid}', pid in rep) else 1

    # CSV 落盘
    csvp = os.path.join(B._ROOT, 'runs', 'batch_report.csv')
    fail += 0 if check('CSV 落盘', os.path.exists(csvp)) else 1

    # 输出文件存在
    mids = glob.glob(os.path.join(B._ROOT, 'scores', 'batch', '*.mid'))
    xmls = glob.glob(os.path.join(B._ROOT, 'scores', 'batch', '*.musicxml'))
    fail += 0 if check('生成 ≥2 个 MIDI', len(mids) >= 2, str(len(mids))) else 1
    fail += 0 if check('生成 ≥2 个 MusicXML', len(xmls) >= 2, str(len(xmls))) else 1

    # 保真度数值有效（若生成成功）
    ovs = [float(x) for x in _re.findall(r'保真度 (\d+\.\d+)', rep)]
    fail += 0 if check('保真度在 [0,1]', all(0.0 <= v <= 1.0 for v in ovs)
                       if ovs else True, str(ovs)) else 1

    # _resolve_targets 过滤
    t = B._resolve_targets(B.BatchConfig(packs=subset))
    fail += 0 if check('_resolve_targets 命中子集', set(subset).issubset(
        {r['pack_id'] for r in t})) else 1

    # 全量枚举至少有若干包
    fail += 0 if check('全量枚举 ≥10 包', len(B.enumerate_packs()) >= 10) else 1

    return fail


if __name__ == '__main__':
    sys.exit(1 if _selftest() else 0)
