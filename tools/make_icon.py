"""Generate the WhisperSub app icon (original design): sound waves turning into subtitle lines.
Output: assets/icon.ico (16..256) and assets/icon.png (512) — run: python tools/make_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
S = 1024            # render size (supersampled, downscaled later)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def rounded_gradient(size, radius, c_top, c_bottom):
    """Vertical gradient inside a rounded square."""
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        c = lerp(c_top, c_bottom, y / (size - 1))
        for x in range(size):
            px[x, y] = (*c, 255)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def draw_icon(size=S):
    img = rounded_gradient(size, int(size * 0.225), (36, 28, 94), (101, 46, 180))

    # soft radial highlight (upper-left) for depth
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-size * 0.25, -size * 0.35, size * 0.75, size * 0.55), fill=(255, 255, 255, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))
    img = Image.alpha_composite(img, glow)

    d = ImageDraw.Draw(img)
    cx = size / 2

    # --- waveform: 11 rounded bars, symmetric, cyan→white accent, floating in the upper area
    heights = [0.10, 0.20, 0.34, 0.50, 0.40, 0.62, 0.40, 0.50, 0.34, 0.20, 0.10]
    n = len(heights)
    bar_w = size * 0.052
    gap = size * 0.028
    total = n * bar_w + (n - 1) * gap
    x0 = cx - total / 2
    wave_cy = size * 0.40
    for i, h in enumerate(heights):
        hh = h * size * 0.58
        x = x0 + i * (bar_w + gap)
        t = abs(i - (n - 1) / 2) / ((n - 1) / 2)          # 0 center → 1 edge
        col = lerp((56, 228, 255), (235, 240, 255), t)      # cyan centre, white edges
        d.rounded_rectangle((x, wave_cy - hh / 2, x + bar_w, wave_cy + hh / 2), radius=bar_w / 2, fill=(*col, 255))

    # --- subtitle plate at the bottom with two "text" lines
    plate_h = size * 0.215
    plate_y0 = size * 0.715
    margin = size * 0.085
    d.rounded_rectangle((margin, plate_y0, size - margin, plate_y0 + plate_h), radius=size * 0.055,
                        fill=(14, 12, 44, 205))
    line_h = size * 0.052
    y1 = plate_y0 + plate_h * 0.24
    y2 = plate_y0 + plate_h * 0.58
    d.rounded_rectangle((size * 0.15, y1, size * 0.85, y1 + line_h), radius=line_h / 2, fill=(255, 255, 255, 240))
    d.rounded_rectangle((size * 0.27, y2, size * 0.73, y2 + line_h), radius=line_h / 2, fill=(56, 228, 255, 235))
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw_icon(S)
    big.resize((512, 512), Image.LANCZOS).save(OUT / "icon.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [big.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(OUT / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    print("written", OUT / "icon.ico", OUT / "icon.png")


if __name__ == "__main__":
    main()
