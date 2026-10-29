# -*- coding: utf-8 -*-
"""主题：预设色板 + 由主题色派生的深色界面 + 背景照片合成。"""
from __future__ import annotations

PRESETS = [
    ("深海蓝", "#4A90D9"),
    ("抹茶绿", "#4CAF7D"),
    ("樱花粉", "#E06C9F"),
    ("落日橙", "#E8823D"),
    ("薄荷青", "#3FB8AF"),
    ("薰衣草", "#9B8CE0"),
    ("极简灰", "#8A9099"),
]
DEFAULT_ACCENT = PRESETS[0][1]

BASE_BG = "#17181C"
BASE_BG2 = "#1F2127"
BASE_BG3 = "#2A2E36"


def _rgb(h: str):
    h = (h or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def palette(accent: str, photo_tint: str | None = None, alpha: float = 0.0) -> dict:
    """主题色 → 整套界面色；有背景照片时按 alpha 把界面往照片色调拉一点。"""
    p = {
        "ACCENT": accent,
        "ACCENT_DARK": mix(accent, "#000000", 0.45),
        "BG": mix(BASE_BG, accent, 0.07),
        "BG2": mix(BASE_BG2, accent, 0.10),
        "BG3": mix(BASE_BG3, accent, 0.16),
        "FG": "#E8EAED",
        "DIM": mix("#8A9099", accent, 0.12),
        "OK": "#4CAF7D",
        "DANGER": "#E05252",
    }
    if photo_tint and alpha > 0:
        k = min(0.30, 0.30 * float(alpha))
        for key in ("BG", "BG2", "BG3"):
            p[key] = mix(p[key], photo_tint, k)
        p["DIM"] = mix(p["DIM"], photo_tint, k * 0.6)
    return p


def band_color(img, top_frac: float, bot_frac: float) -> str:
    """背景图某一纵向条带的平均色（用于让卡片“透”出背景）。"""
    try:
        w, h = img.size
        y0 = max(0, min(h - 1, int(h * top_frac)))
        y1 = max(y0 + 1, min(h, int(h * bot_frac)))
        crop = img.crop((0, y0, w, y1)).resize((6, 6))
        px = list(crop.getdata())
        n = len(px) or 1
        return _hex((sum(p[0] for p in px) / n,
                     sum(p[1] for p in px) / n,
                     sum(p[2] for p in px) / n))
    except Exception:
        return BASE_BG2


def blend_over(bg, base_hex: str, alpha: float):
    """卡片底：以背景为基准压暗（不改色相/饱和度），只掺一点点卡片底色。"""
    from PIL import Image, ImageEnhance
    k = max(0.0, min(1.0, float(alpha)))
    img = ImageEnhance.Brightness(bg.convert("RGB")).enhance(1.0 - 0.30 * k)
    base = Image.new("RGB", bg.size, _rgb(base_hex))
    return Image.blend(img, base, 0.08 * k)


def card_image(bg, box, base_hex: str, alpha: float):
    """从背景图裁一块，与卡片底色混合 → 半透明卡片底图（能看见照片花纹）。"""
    from PIL import Image
    w = max(1, int(box[2] - box[0]))
    h = max(1, int(box[3] - box[1]))
    crop = bg.crop((int(box[0]), int(box[1]), int(box[0]) + w, int(box[1]) + h)).convert("RGB")
    if crop.size != (w, h):
        crop = crop.resize((w, h), Image.BILINEAR)
    base = Image.new("RGB", (w, h), _rgb(base_hex))
    k = max(0.0, min(0.88, 0.80 * float(alpha)))
    out = Image.blend(base, crop, k)
    dark = Image.new("RGB", (w, h), _rgb(BASE_BG))
    return Image.blend(out, dark, 0.06)      # 轻微压暗，保证文字可读


def average_color(path: str):
    """照片主色调（用于把界面染成同一调子）。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((48, 48))
        px = list(im.getdata())
        n = len(px) or 1
        return _hex((sum(p[0] for p in px) / n,
                     sum(p[1] for p in px) / n,
                     sum(p[2] for p in px) / n))
    except Exception:
        return None


def compose_bg(path: str, size, accent: str, alpha: float):
    """背景照片 + 主题色纵向渐变叠加，返回 PIL.Image（RGB）。alpha=0 纯主题色，1 几乎原图。"""
    from PIL import Image, ImageDraw
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    img = Image.open(path).convert("RGB")
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    img = img.resize((max(1, int(sw * scale)), max(1, int(sh * scale))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))

    r, g, b = _rgb(accent)
    br, bg_, bb = _rgb(BASE_BG)
    grad = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=(int(r + (br - r) * t),
                                       int(g + (bg_ - g) * t),
                                       int(b + (bb - b) * t)))
    out = Image.blend(img, grad, 1.0 - float(alpha))
    dark = Image.new("RGB", (w, h), _rgb(BASE_BG))
    return Image.blend(out, dark, 0.22)
