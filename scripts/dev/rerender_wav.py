# -*- coding: utf-8 -*-
"""用当前默认音色（现为 Timbres of Heaven 4.0）重新渲染盲评包的 WAV。

MIDI 不重生成——生成器未变，seed 固定，MIDI 内容与上一版完全一致。
用法：venv/bin/python rerender_wav.py [--dir runs/exploratory/blind_review/audio]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from render_enhance import _pick_soundfont, preset, render_to_wav   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description='按当前默认音色重渲染 WAV')
    ap.add_argument('--dir', default=os.path.join(ROOT, 'runs', 'exploratory', 'blind_review', 'audio'))
    ap.add_argument('--sf', default=None, help='覆盖音色（别名或路径）')
    a = ap.parse_args(argv)

    p = preset(soundfont=a.sf)
    sf2 = _pick_soundfont(p)
    if not sf2:
        print('找不到可用 SoundFont')
        return 1
    print('音色：%s（%.0f MB）' % (os.path.basename(sf2), os.path.getsize(sf2) / 1024 / 1024))
    mids = sorted(glob.glob(os.path.join(a.dir, '*.mid')))
    if not mids:
        print('目录里没有 MIDI：%s' % a.dir)
        return 1
    ok = 0
    t0 = time.time()
    for m in mids:
        w = m[:-4] + '.wav'
        info = render_to_wav(m, w, p)
        if info.get('ok'):
            ok += 1
            print('  %s  %.1f MB  %s' % (os.path.basename(w),
                                          os.path.getsize(w) / 1024 / 1024,
                                          info.get('duration', '')))
        else:
            print('  ! %s 渲染失败：%s' % (os.path.basename(m), info.get('error')))
    print('\n%d/%d 个 WAV 已重渲染，用时 %.0f 秒' % (ok, len(mids), time.time() - t0))
    return 0 if ok == len(mids) else 1


if __name__ == '__main__':
    sys.exit(main())
