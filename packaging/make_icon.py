#!/usr/bin/env python3
"""Draws the app icon, so no binary image lives in the repo.
Usage: python packaging/make_icon.py OUT.ico"""
import sys

from PIL import Image, ImageDraw


def draw(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    s = size / 64
    d.ellipse((2 * s, 2 * s, 62 * s, 62 * s), fill=(200, 164, 92, 255))
    d.polygon([(16 * s, 16 * s), (26 * s, 16 * s), (32 * s, 38 * s), (38 * s, 16 * s),
               (48 * s, 16 * s), (36 * s, 50 * s), (28 * s, 50 * s)], fill=(20, 17, 14, 255))
    return image


if __name__ == "__main__":
    draw(256).save(sys.argv[1], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
