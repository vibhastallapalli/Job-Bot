"""Generate web/static/favicon.ico — a simple 32x32 robot head."""
from PIL import Image, ImageDraw

SIZE = 32
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Palette (matches the app's dark-purple accent colour scheme)
BG     = (17,  17,  24,  255)   # --surface  #111118
HEAD   = (30,  30,  40,  255)   # --surface-3 #1e1e28
BORDER = (124, 104, 245, 255)   # --accent   #7c68f5
EYE    = (124, 104, 245, 255)   # accent
MOUTH  = (52,  211, 153, 255)   # --c-green  #34d399
PANEL  = (37,  37,  50,  255)   # --surface-4 #252532

# Background disc
d.ellipse([1, 1, 30, 30], fill=BG, outline=BORDER, width=1)

# Head rectangle
d.rounded_rectangle([5, 7, 26, 26], radius=3, fill=HEAD, outline=BORDER, width=1)

# Antenna
d.line([(15, 3), (15, 7)], fill=BORDER, width=2)
d.ellipse([13, 1, 17, 5], fill=BORDER)

# Eyes
d.rounded_rectangle([7, 11, 13, 17], radius=1, fill=PANEL, outline=EYE, width=1)
d.rounded_rectangle([18, 11, 24, 17], radius=1, fill=PANEL, outline=EYE, width=1)
# Eye glow dots
d.ellipse([9, 13, 11, 15], fill=EYE)
d.ellipse([20, 13, 22, 15], fill=EYE)

# Mouth — a small green segmented bar
for x in [8, 11, 14, 17, 20]:
    d.rectangle([x, 20, x + 2, 22], fill=MOUTH)

# Save as multi-size ICO (16, 32)
img16 = img.resize((16, 16), Image.LANCZOS)
img.save(
    "web/static/favicon.ico",
    format="ICO",
    sizes=[(16, 16), (32, 32)],
    append_images=[img16],
)
print("Saved web/static/favicon.ico")
