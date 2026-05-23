# -*- coding: utf-8 -*-
"""Generate subsetra_icon.ico (+ a .png preview): a stylized colorful abacus
app icon.

Each icon size is rendered NATIVELY (drawn at that size, with supersampling for
smooth edges) and the small sizes use a simplified layout. This avoids the
classic mistake of down-scaling one detailed 256px image to 16/32px, which
turns the abacus into unreadable colorful noise. The per-size frames are then
embedded in a single .ico via Pillow's ``append_images``.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

PALETTE = [(255, 201, 74), (255, 107, 107), (78, 205, 196), (124, 196, 120)]
TOP, BOT = (60, 124, 246), (26, 62, 144)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _rrect(draw, box, radius, **kw):
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(box, radius=radius, **kw)
    else:
        draw.rectangle(box, **kw)


def _bead(d, x, y, r, col, gloss):
    ow = max(1, int(r * 0.12))
    d.ellipse([x - r, y - r, x + r, y + r], fill=col,
              outline=(20, 20, 40, 90), width=ow)
    if gloss:
        d.ellipse([x - r * 0.55, y - r * 0.55, x - r * 0.05, y - r * 0.05],
                  fill=(255, 255, 255, 130))


def render(size):
    """Return a crisp RGBA icon image of (size x size)."""
    ss = 8 if size <= 32 else 4 if size <= 64 else 2     # supersample factor
    D = size * ss

    # Tier-specific detail: fewer, fatter beads on tiny icons so they read.
    if size <= 36:
        layout = [(2, 1), (1, 2), (2, 1)]                # (left, right) clusters
        frame_w, bead_r, rod_w, gloss = 0.075, 0.090, 0.024, False
    elif size <= 80:
        layout = [(2, 2), (3, 1), (1, 3), (2, 2)]
        frame_w, bead_r, rod_w, gloss = 0.052, 0.062, 0.018, False
    else:
        layout = [(2, 3), (3, 2), (2, 3), (3, 2)]
        frame_w, bead_r, rod_w, gloss = 0.043, 0.047, 0.016, True
    rows = len(layout)

    img = Image.new("RGBA", (D, D), (0, 0, 0, 0))

    # --- background: vertical blue gradient clipped to a rounded square ---
    grad = Image.new("RGBA", (D, D))
    gd = ImageDraw.Draw(grad)
    for y in range(D):
        t = y / (D - 1)
        gd.line([(0, y), (D, y)],
                fill=(int(TOP[0] * (1 - t) + BOT[0] * t),
                      int(TOP[1] * (1 - t) + BOT[1] * t),
                      int(TOP[2] * (1 - t) + BOT[2] * t), 255))
    mask = Image.new("L", (D, D), 0)
    inset = D * 0.02
    _rrect(ImageDraw.Draw(mask),
           [inset, inset, D - inset, D - inset], D * 0.21, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)

    # --- abacus frame (white rounded outline) ---
    fx0, fy0, fx1, fy1 = D * 0.16, D * 0.19, D * 0.84, D * 0.81
    fw = max(2, int(D * frame_w))
    _rrect(d, [fx0, fy0, fx1, fy1], D * 0.085,
           outline=(255, 255, 255, 255), width=fw)

    # --- rods + colorful beads ---
    inner_l = fx0 + fw + D * 0.02
    inner_r = fx1 - fw - D * 0.02
    br = D * bead_r
    gap = D * 0.012
    rw = max(1, int(D * rod_w))
    pad = fy0 + (fy1 - fy0) * 0.16
    span = (fy1 - fy0) * 0.68
    rod_ys = [pad + span * (i / (rows - 1)) for i in range(rows)]

    for i, ry in enumerate(rod_ys):
        d.line([(inner_l, ry), (inner_r, ry)],
               fill=(255, 255, 255, 150), width=rw)
        col = PALETTE[i % len(PALETTE)]
        left, right = layout[i]
        x = inner_l + br
        for _ in range(left):
            _bead(d, x, ry, br, col, gloss)
            x += 2 * br + gap
        x = inner_r - br
        for _ in range(right):
            _bead(d, x, ry, br, col, gloss)
            x -= 2 * br + gap

    return img.resize((size, size), Image.LANCZOS)


frames = [render(s) for s in SIZES]
big = frames[-1]                                          # 256x256 master
big.save(os.path.join(HERE, "subsetra_icon.png"))
big.save(os.path.join(HERE, "subsetra_icon.ico"),
         sizes=[(s, s) for s in SIZES],
         append_images=frames[:-1])
print("saved subsetra_icon.png and subsetra_icon.ico to", HERE)
