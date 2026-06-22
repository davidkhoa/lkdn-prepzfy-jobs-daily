"""
image_card_reference.py — VALIDATED design reference for the daily card.
NOTE for Claude Code: this currently uses HARDCODED demo offers. Adapt it to:
  - take the 5 SELECTED offers as input (company, role, location, type/sector, domain),
  - fetch each company logo via `domain` with a monogram fallback,
  - English labels, dynamic date, dynamic "+ N more" count,
  - and be called by daily_post.py after Claude picks the offers.
Keep the design tokens and layout exactly as-is (validated by the owner).
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1080, 1350
NAVY      = (10, 22, 40)     # #0A1628
NAVY_DEEP = (6, 13, 26)      # #060d1a
BLUE      = (37, 99, 196)    # #2563C4
BLUE_BR   = (59, 130, 246)   # #3b82f6
INK       = (232, 236, 243)  # #E8ECF3
INK_SOFT  = (192, 201, 214)  # #c0c9d6
INK_DIM   = (138, 151, 172)  # #8A97AC
INK_DIMR  = (90, 101, 120)   # #5a6578

SANS  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD  = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"
def f(p, s): return ImageFont.truetype(p, s)

def tracked(d, x, y, text, font, fill, tr=0, anchor="la"):
    widths = [d.textlength(c, font=font) for c in text]
    total = sum(widths) + tr * (len(text) - 1)
    if anchor[0] == "r": x -= total
    elif anchor[0] == "m": x -= total / 2
    ay = "m" if len(anchor) > 1 and anchor[1] == "m" else "a"
    cx = x
    for c, w in zip(text, widths):
        d.text((cx, y), c, font=font, fill=fill, anchor="l" + ay)
        cx += w + tr

def grad_circle(r, c1, c2):
    s = r * 2
    g = Image.new("RGB", (s, s))
    px = g.load()
    for yy in range(s):
        for xx in range(s):
            t = (xx + yy) / (2 * s)
            px[xx, yy] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
    g.putalpha(mask)
    return g

def grad_line(d, x1, x2, y, color, thickness=2):
    mid = (x1 + x2) / 2
    for xx in range(int(x1), int(x2)):
        t = 1 - abs(xx - mid) / (mid - x1)
        a = max(0.0, t)
        col = tuple(int(NAVY[i] + (color[i] - NAVY[i]) * a) for i in range(3))
        d.line([xx, y, xx, y + thickness], fill=col)

img = Image.new("RGB", (W, H), NAVY)

# soft blue glow top-right (site vibe)
glow = Image.new("RGB", (W, H), NAVY)
gd = ImageDraw.Draw(glow)
gd.ellipse([560, -260, 1240, 360], fill=(24, 60, 130))
glow = glow.filter(ImageFilter.GaussianBlur(120))
img = Image.blend(img, glow, 0.55)
# subtle bottom darkening
dark = Image.new("RGB", (W, H), NAVY_DEEP)
m = Image.new("L", (W, H), 0)
md = ImageDraw.Draw(m)
for yy in range(H):
    md.line([0, yy, W, yy], fill=int(120 * max(0, (yy - 950) / 400)))
img = Image.composite(dark, img, m)

d = ImageDraw.Draw(img)
PAD = 72
RIGHT = W - PAD

# top hairline (gradient blue)
grad_line(d, 0, W, 0, BLUE, 5)

# wordmark PREPZ + fy
d.text((PAD, 92), "PREPZ", font=f(BOLD, 46), fill=INK)
wl = d.textlength("PREPZ", font=f(BOLD, 46))
d.text((PAD + wl + 2, 88), "fy", font=f(SERIF, 50), fill=BLUE)
tracked(d, PAD, 158, "OFFRES DU JOUR", f(BOLD, 21), INK_DIM, tr=6)
d.text((RIGHT, 96), "Lundi 22 juin", font=f(BOLD, 28), fill=INK_SOFT, anchor="ra")
d.text((RIGHT, 142), "M&A · Private Equity · Conseil", font=f(SANS, 22), fill=INK_DIM, anchor="ra")

grad_line(d, PAD, RIGHT, 222, BLUE, 2)

rows = [
    ("GS", ((37,99,196),(26,61,122)),  "Associate, Prime Brokerage",  "Goldman Sachs · Singapour",    "MARKETS"),
    ("HL", ((13,148,136),(19,78,74)),  "M&A Internship (2027)",       "Houlihan Lokey · Zurich",      "M&A"),
    ("TC", ((124,58,237),(76,29,149)), "Analyste Financier (Stage)",  "Tikehau Capital · Paris",      "PRIVATE EQUITY"),
    ("LI", ((180,83,9),(120,53,15)),   "M&A Internship (jan. 2027)",  "Lincoln International · Paris", "M&A"),
    ("MQ", ((190,18,60),(131,24,67)),  "Infrastructure M&A Analyst",  "Macquarie · New York",         "M&A"),
]

y = 312
for initials, (c1, c2), role, sub, tag in rows:
    cx, r = 116, 44
    circ = grad_circle(r, c1, c2)
    img.paste(circ, (cx - r, y - r), circ)
    d.text((cx, y), initials, font=f(BOLD, 30), fill=INK, anchor="mm")
    d.text((190, y - 30), role, font=f(BOLD, 35), fill=INK)
    d.text((190, y + 8), sub, font=f(SANS, 26), fill=INK_DIM)
    tf = f(BOLD, 19)
    tw = sum(d.textlength(c, font=tf) for c in tag) + 1.5 * (len(tag) - 1)
    pw = tw + 40
    px1, px2 = RIGHT - pw, RIGHT
    d.rounded_rectangle([px1, y - 20, px2, y + 20], radius=20, outline=(60, 80, 110), width=2)
    tracked(d, (px1 + px2) / 2, y, tag, tf, INK_SOFT, tr=1.5, anchor="mm")
    y += 170

grad_line(d, PAD, RIGHT, 1112, BLUE, 2)

# footer
d.text((PAD, 1162), "+ 95 autres offres", font=f(BOLD, 34), fill=BLUE_BR)
d.text((PAD, 1216), "sur jobs.prepzfy.com", font=f(SANS, 30), fill=INK_SOFT)
tracked(d, RIGHT, 1205, "LINKFIN × PREPZFY", f(BOLD, 20), INK_DIMR, tr=2, anchor="ra")

out = "/mnt/user-data/outputs/prepzfy_offres_du_jour_v2.png"
img.save(out, "PNG")
print("saved", out)
