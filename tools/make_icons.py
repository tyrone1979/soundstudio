#!/usr/bin/env python3
"""ComposerChat 图标资产生成器。

依赖：PyQt5（含 QtSvg，用于把矢量 SVG 渲染成 PNG）+ Pillow（用于合成 .ico）。
用法：
    python tools/make_icons.py            # 生成全部 favicon / ico / 预览
    python tools/make_icons.py --ico-only # 仅重新合成 favicon.ico
"""
import os
import sys
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(_ROOT, "assets")
FAV = os.path.join(ASSETS, "favicons")
PREV = os.path.join(ASSETS, "previews")

from PyQt5.QtGui import QGuiApplication, QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtCore import QRectF

from PIL import Image
from PIL import IcoImagePlugin  # 注册 ICO 的 SAVE_ALL 支持（多尺寸合成）


def render_svg(src, out, w, h):
    r = QSvgRenderer(src)
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, w, h))
    p.end()
    img.save(out)
    return out


def build_favicons():
    os.makedirs(FAV, exist_ok=True)
    src = os.path.join(ASSETS, "icon.svg")
    squares = [16, 32, 48, 64, 128, 256, 512]
    imgs = {}
    for s in squares:
        out = os.path.join(FAV, f"favicon-{s}.png")
        render_svg(src, out, s, s)
        imgs[s] = out
    # apple touch icon (180) + android chrome
    render_svg(src, os.path.join(FAV, "apple-touch-icon.png"), 180, 180)
    render_svg(src, os.path.join(FAV, "android-chrome-192.png"), 192, 192)
    render_svg(src, os.path.join(FAV, "android-chrome-512.png"), 512, 512)

    # 合成多尺寸 favicon.ico（Pillow ICO 插件按 sizes 从大图自动缩放生成各档）
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big = Image.open(imgs[256]).convert("RGBA")
    ico_path = os.path.join(FAV, "favicon.ico")
    big.save(ico_path, format="ICO", sizes=ico_sizes)
    print("favicon.ico ->", ico_path)
    return ico_path


def build_previews():
    os.makedirs(PREV, exist_ok=True)
    items = [
        ("icon.svg", 512, 512),
        ("icon-dark.svg", 512, 512),
        ("icon-mono.svg", 512, 512),
        ("icon-teal.svg", 512, 512),
        ("concept-kernel.svg", 512, 512),
        ("concept-monogram.svg", 512, 512),
        ("concept-wave.svg", 512, 512),
        ("logo-horizontal.svg", 760, 200),
        ("logo-horizontal-dark.svg", 760, 200),
    ]
    for name, w, h in items:
        src = os.path.join(ASSETS, name)
        out = os.path.join(PREV, name.replace(".svg", ".png"))
        render_svg(src, out, w, h)
        print("preview ->", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ico-only", action="store_true")
    args = ap.parse_args()

    _ = QGuiApplication(sys.argv)
    if args.ico_only:
        build_favicons()
    else:
        build_favicons()
        build_previews()
    print("DONE")


if __name__ == "__main__":
    main()
