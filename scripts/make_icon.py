"""Draw the integration icon (orange map pin with a lightning bolt) into brand/.

Usage: python scripts/make_icon.py   (needs Pillow)
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent.parent / "custom_components/public_charge_watch/brand"
ORANGE = (249, 115, 22, 255)
WHITE = (255, 255, 255, 255)
SIZE = 2048  # drawn large, then downscaled for smooth edges
S = SIZE / 1024  # all coordinates below are on a 1024 canvas

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# pin: a circle plus a tail to the tip, joined at the two tangent points
cx, cy, r = 512, 420, 330
tip = (512, 965)
dist = tip[1] - cy
spread = math.acos(r / dist)  # angle at the centre between the tip direction and a tangent point
tangents = [
    (cx + r * math.sin(sign * spread), cy + r * math.cos(spread))
    for sign in (-1, 1)
]
d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=ORANGE)
d.polygon([(x * S, y * S) for x, y in (*tangents, tip)], fill=ORANGE)

# bolt (24-unit grid, centred in the circle)
BOLT = [(13, 2), (4, 14), (10, 14), (9, 22), (20, 10), (14, 10)]
unit = 19
d.polygon(
    [((512 + (x - 12) * unit) * S, (cy + (y - 12) * unit) * S) for x, y in BOLT],
    fill=WHITE,
)

OUT.mkdir(parents=True, exist_ok=True)
for name, px in (("icon.png", 256), ("icon@2x.png", 512)):
    img.resize((px, px), Image.LANCZOS).save(OUT / name, optimize=True)
print("wrote", sorted(p.name for p in OUT.iterdir()))
