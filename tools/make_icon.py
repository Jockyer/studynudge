# -*- coding: utf-8 -*-
"""生成 StudyNudge 图标：原创 Q 版角色头像（银白短发 + 深色外套），无版权风险。

用法：python tools/make_icon.py
输出：assets/icon.png（512）、assets/icon.ico（多尺寸）
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

S = 4                      # 超采样倍数
W = 512
C = W * S

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def hexc(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def ellipse(d, box, fill, outline=None, width=1):
    d.ellipse([int(v * S) for v in box], fill=fill, outline=outline, width=int(width * S))


def poly(d, pts, fill):
    d.polygon([(int(x * S), int(y * S)) for x, y in pts], fill=fill)


def main():
    os.makedirs(OUT, exist_ok=True)
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆底 + 渐变（上蓝下深）
    bg = Image.new("RGB", (C, C), hexc("#1B2430")[:3])
    bd = ImageDraw.Draw(bg)
    for y in range(C):
        t = y / (C - 1)
        r = int(0x2A + (0x12 - 0x2A) * t)
        g = int(0x4C + (0x16 - 0x4C) * t)
        b = int(0x7A + (0x1C - 0x7A) * t)
        bd.line([(0, y), (C, y)], fill=(r, g, b))
    mask = Image.new("L", (C, C), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, C - 1, C - 1], fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # 头发（后层）
    ellipse(d, (96, 96, 416, 424), hexc("#D9E2EE"))
    # 脸
    ellipse(d, (140, 168, 372, 392), hexc("#F8E4D8"))
    # 耳朵
    ellipse(d, (126, 262, 168, 306), hexc("#F3D8C9"))
    ellipse(d, (344, 262, 386, 306), hexc("#F3D8C9"))

    # 刘海：三片
    poly(d, [(140, 250), (176, 150), (250, 214), (214, 262)], hexc("#E7EEF7"))
    poly(d, [(228, 206), (300, 146), (330, 232), (262, 262)], hexc("#DDE7F3"))
    poly(d, [(300, 232), (368, 176), (380, 292), (330, 300)], hexc("#E7EEF7"))
    # 侧发
    poly(d, [(126, 240), (150, 250), (156, 392), (118, 386)], hexc("#CFDAE8"))
    poly(d, [(386, 240), (362, 250), (356, 392), (394, 386)], hexc("#CFDAE8"))
    # 呆毛
    d.line([(268 * S, 110 * S), (252 * S, 74 * S), (286 * S, 58 * S)],
           fill=hexc("#E7EEF7"), width=int(9 * S), joint="curve")

    # 眼睛
    for cx in (204, 308):
        ellipse(d, (cx - 34, 288, cx + 34, 356), hexc("#FFFFFF"))
        ellipse(d, (cx - 27, 296, cx + 27, 352), hexc("#3E6FA8"))
        ellipse(d, (cx - 20, 306, cx + 20, 344), hexc("#26405F"))
        ellipse(d, (cx - 12, 306, cx + 4, 324), hexc("#FFFFFF"))
        ellipse(d, (cx + 6, 330, cx + 18, 342), hexc("#FFFFFF", 200))
    # 眉毛
    d.line([(176 * S, 272 * S), (232 * S, 266 * S)], fill=hexc("#9FB0C4"), width=int(6 * S))
    d.line([(280 * S, 266 * S), (336 * S, 272 * S)], fill=hexc("#9FB0C4"), width=int(6 * S))
    # 腮红
    blush = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    bdr = ImageDraw.Draw(blush)
    bdr.ellipse([166 * S, 330 * S, 216 * S, 358 * S], fill=hexc("#F19AA0", 110))
    bdr.ellipse([296 * S, 330 * S, 346 * S, 358 * S], fill=hexc("#F19AA0", 110))
    blush = blush.filter(ImageFilter.GaussianBlur(6 * S))
    img = Image.alpha_composite(img, blush)
    d = ImageDraw.Draw(img)
    # 嘴
    d.arc([244 * S, 330 * S, 288 * S, 366 * S], 20, 160, fill=hexc("#C9706E"), width=int(6 * S))

    # 外套 + 领子
    poly(d, [(96, 512), (150, 396), (362, 396), (416, 512)], hexc("#242B38"))
    poly(d, [(200, 396), (256, 470), (312, 396)], hexc("#2E3646"))
    poly(d, [(226, 396), (256, 432), (286, 396)], hexc("#4A5568"))
    # 领口红条
    d.line([(206 * S, 400 * S), (256 * S, 468 * S)], fill=hexc("#4A90D9"), width=int(7 * S))
    d.line([(306 * S, 400 * S), (256 * S, 468 * S)], fill=hexc("#4A90D9"), width=int(7 * S))

    out = img.resize((W, W), Image.LANCZOS)
    png = os.path.join(OUT, "icon.png")
    out.save(png)
    ico = os.path.join(OUT, "icon.ico")
    out.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("saved:", png, ico)


if __name__ == "__main__":
    sys.exit(main())
