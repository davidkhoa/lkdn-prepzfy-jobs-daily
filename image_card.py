"""
image_card.py - daily branded card generator for the PREPZfy job-offers bot.

Turns the 5 offers selected by Claude into a 1080x1350 PNG that matches the
validated PREPZfy identity (see image_card_reference.py for the original design).

What this adds on top of the reference:
  - takes the 5 SELECTED offers as input (no more hardcoded demo data),
  - fetches each company's real logo from its `domain`, with a clean fallback
    to the gradient monogram (initials) when the logo is missing or fails,
  - English labels, today's date, and a dynamic "+ N more offers" count,
  - robust font loading (works even if a font is missing on the CI runner).

Called by daily_post.py. No headless browser needed (Pillow only).
"""

import os
import io
import re
import json
import datetime
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---- Canvas + design tokens (validated by the owner, do not change) ----
W, H = 1080, 1350
NAVY      = (10, 22, 40)      # #0A1628  background
NAVY_DEEP = (6, 13, 26)       # #060d1a
BLUE      = (37, 99, 196)     # #2563C4  primary
BLUE_BR   = (59, 130, 246)    # #3b82f6
INK       = (232, 236, 243)   # #E8ECF3  text
INK_SOFT  = (192, 201, 214)   # #c0c9d6
INK_DIM   = (138, 151, 172)   # #8A97AC
INK_DIMR  = (90, 101, 120)    # #5a6578

# Monogram gradients (one per row, cycling) - from the brand tokens.
GRADIENTS = [
    ((37, 99, 196), (26, 61, 122)),    # blue   #2563C4 -> #1a3d7a
    ((13, 148, 136), (19, 78, 74)),    # teal   #0d9488 -> #134e4a
    ((124, 58, 237), (76, 29, 149)),   # purple #7c3aed -> #4c1d95
    ((180, 83, 9), (120, 53, 15)),     # amber  #b45309 -> #78350f
    ((190, 18, 60), (131, 24, 67)),    # rose   #be123c -> #831843
]

# ---- Fonts: try the validated DejaVu first, fall back so CI never crashes ----
# The "fy" of the wordmark must be an italic serif (Times New Roman feel).
# Liberation Serif is a Times New Roman clone, so it is a faithful fallback.
_SANS_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_SERIF_ITALIC_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]


def _first_existing(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


_SANS_PATH = _first_existing(_SANS_CANDIDATES)
_BOLD_PATH = _first_existing(_BOLD_CANDIDATES)
_SERIF_PATH = _first_existing(_SERIF_ITALIC_CANDIDATES)


def f_sans(size):
    return ImageFont.truetype(_SANS_PATH, size) if _SANS_PATH else ImageFont.load_default()


def f_bold(size):
    return ImageFont.truetype(_BOLD_PATH, size) if _BOLD_PATH else ImageFont.load_default()


def f_serif(size):
    return ImageFont.truetype(_SERIF_PATH, size) if _SERIF_PATH else ImageFont.load_default()


# ---- Drawing helpers (kept from the validated reference) ----
def tracked(d, x, y, text, font, fill, tr=0, anchor="la"):
    """Draw text with letter-spacing `tr`."""
    widths = [d.textlength(c, font=font) for c in text]
    total = sum(widths) + tr * (len(text) - 1)
    if anchor[0] == "r":
        x -= total
    elif anchor[0] == "m":
        x -= total / 2
    ay = "m" if len(anchor) > 1 and anchor[1] == "m" else "a"
    cx = x
    for c, w in zip(text, widths):
        d.text((cx, y), c, font=font, fill=fill, anchor="l" + ay)
        cx += w + tr


def grad_circle(r, c1, c2):
    """Diagonal-gradient filled circle (used for monogram fallback)."""
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


def fit_text(d, text, font, max_w):
    """Truncate `text` with an ellipsis so it fits within max_w pixels."""
    if d.textlength(text, font=font) <= max_w:
        return text
    ell = "..."
    while text and d.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


# ---- Logo handling ----
def _clean_domain(domain):
    domain = (domain or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _download_image(url, headers=None, timeout=15, min_px=24):
    """Download a URL and return a PIL RGBA image, or None on any failure."""
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=timeout).read()
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        if im.width >= min_px and im.height >= min_px:
            return im
    except Exception:
        pass
    return None


# We draw each mark on a WHITE disc, so we want the square SYMBOL/ICON of a
# brand (e.g. the Societe Generale red-and-black square), never the full
# wordmark which would be illegible at 88px. Brandfetch exposes a logo `type`,
# so we can ask for the icon explicitly. `theme: "light"` means a logo meant
# for LIGHT backgrounds (a dark-coloured mark) -> what we want on a white disc.
_TYPE_PRIORITY = {"icon": 0, "symbol": 1, "logo": 3, "other": 4}
_THEME_PRIORITY = {"light": 0, None: 1, "dark": 2}


def _brandfetch_icon_url(domain, api_key, timeout=15):
    """Query the Brandfetch Brand API and return the best square-icon image URL,
    preferring icon/symbol over wordmark and PNG over other raster formats."""
    try:
        req = urllib.request.Request(
            f"https://api.brandfetch.io/v2/brands/{domain}",
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "Mozilla/5.0"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return None

    def best_png(formats):
        raster = [f for f in formats if f.get("src") and f.get("format") in ("png", "webp", "jpeg")]
        png = [f for f in raster if f.get("format") == "png"] or raster
        return max(png, key=lambda f: (f.get("width") or 0)) if png else None

    logos = data.get("logos") or []
    logos.sort(key=lambda l: (_TYPE_PRIORITY.get(l.get("type"), 5),
                              _THEME_PRIORITY.get(l.get("theme"), 1)))
    for lg in logos:
        fmt = best_png(lg.get("formats") or [])
        if fmt:
            return fmt["src"]
    return None


def fetch_logo(domain, brandfetch_key=None, logodev_token=None, size=256, timeout=15):
    """
    Return a PIL RGBA logo for `domain`, or None if nothing usable is found.
    Fallback chain, square-icon first:
      1. Brandfetch icon/symbol (best: the brand's square mark)   [BRANDFETCH_API_KEY]
      2. logo.dev mark (sharp)                                    [LOGODEV_TOKEN]
      3. Google favicon (almost always the square icon, low-res)  [no key]
      4. -> caller draws the gradient monogram (initials)
    Any network or decode error just moves to the next source.
    """
    domain = _clean_domain(domain)
    if not domain:
        return None
    # 1. Brandfetch (square icon/symbol)
    if brandfetch_key:
        src = _brandfetch_icon_url(domain, brandfetch_key, timeout)
        if src:
            im = _download_image(src, timeout=timeout)
            if im is not None:
                return im
    # 2. logo.dev
    if logodev_token:
        im = _download_image(
            f"https://img.logo.dev/{domain}?token={logodev_token}&size={size}&format=png",
            timeout=timeout)
        if im is not None:
            return im
    # 3. Google favicon (the square site icon)
    im = _download_image(f"https://www.google.com/s2/favicons?domain={domain}&sz=128", timeout=timeout)
    if im is not None:
        return im
    return None


def initials(company):
    words = [w for w in re.split(r"[\s/&,.\-]+", company or "") if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def _circle_from_logo(logo_img, r):
    """Put a real logo on a clean white disc, masked to a circle."""
    s = r * 2
    bg = Image.new("RGBA", (s, s), (255, 255, 255, 255))
    inner = int(s * 0.66)
    lg = logo_img.copy()
    lg.thumbnail((inner, inner), Image.LANCZOS)
    ox, oy = (s - lg.width) // 2, (s - lg.height) // 2
    bg.paste(lg, (ox, oy), lg)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
    bg.putalpha(mask)
    return bg


# ---- Labels ----
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
WEEKDAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MONTHS_FR = ["janvier", "fevrier", "mars", "avril", "mai", "juin",
             "juillet", "aout", "septembre", "octobre", "novembre", "decembre"]


def format_date(today, lang):
    if lang == "fr":
        return f"{WEEKDAYS_FR[today.weekday()]} {today.day} {MONTHS_FR[today.month - 1]}"
    return f"{WEEKDAYS[today.weekday()]} {today.day} {MONTHS[today.month - 1]}"


# Make sector codes from the sheet read nicely on the pill.
SECTOR_LABELS = {
    "PE": "PRIVATE EQUITY",
    "IBD": "IBD",
    "TS": "TRANSACTION SERVICES",
    "CONSULTING": "CONSULTING",
    "M&A": "M&A",
    "MARKETS": "MARKETS",
}


def sector_tag(offer):
    raw = (offer.get("sector") or offer.get("type") or "").strip()
    if not raw:
        return ""
    return SECTOR_LABELS.get(raw.upper(), raw.upper())


def build_card(selected, total_count, out_path, lang="en", today=None,
               brandfetch_key=None, logodev_token=None):
    """
    Render the daily card.
      selected      : list of offer dicts (company, role, location, type,
                      sector, domain). Up to 5 are drawn.
      total_count   : total number of valid offers in the sheet (for "+ N more").
      out_path      : where to save the PNG.
      lang          : "en" (default) or "fr".
      logodev_token : optional logo.dev token (sharper logos).
    Returns out_path.
    """
    today = today or datetime.date.today()
    rows = selected[:5]
    more = max(0, total_count - len(rows))

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
    d.text((PAD, 92), "PREPZ", font=f_bold(46), fill=INK)
    wl = d.textlength("PREPZ", font=f_bold(46))
    d.text((PAD + wl + 2, 88), "fy", font=f_serif(50), fill=BLUE)

    sublabel = "OFFERS OF THE DAY" if lang == "en" else "OFFRES DU JOUR"
    right_tag = "M&A · Private Equity · Consulting" if lang == "en" else "M&A · Private Equity · Conseil"
    tracked(d, PAD, 158, sublabel, f_bold(21), INK_DIM, tr=6)
    d.text((RIGHT, 96), format_date(today, lang), font=f_bold(28), fill=INK_SOFT, anchor="ra")
    d.text((RIGHT, 142), right_tag, font=f_sans(22), fill=INK_DIM, anchor="ra")

    grad_line(d, PAD, RIGHT, 222, BLUE, 2)

    # rows
    y = 312
    for i, o in enumerate(rows):
        cx, r = 116, 44
        logo = fetch_logo(o.get("domain"), brandfetch_key=brandfetch_key,
                          logodev_token=logodev_token)
        if logo is not None:
            circ = _circle_from_logo(logo, r)
            img.paste(circ, (cx - r, y - r), circ)
        else:
            c1, c2 = GRADIENTS[i % len(GRADIENTS)]
            circ = grad_circle(r, c1, c2)
            img.paste(circ, (cx - r, y - r), circ)
            d.text((cx, y), initials(o.get("company")), font=f_bold(30), fill=INK, anchor="mm")

        # sector pill on the right
        tag = sector_tag(o)
        if tag:
            tf = f_bold(19)
            tw = sum(d.textlength(c, font=tf) for c in tag) + 1.5 * (len(tag) - 1)
            pw = tw + 40
            px1, px2 = RIGHT - pw, RIGHT
            d.rounded_rectangle([px1, y - 20, px2, y + 20], radius=20, outline=(60, 80, 110), width=2)
            tracked(d, (px1 + px2) / 2, y, tag, tf, INK_SOFT, tr=1.5, anchor="mm")
        else:
            px1 = RIGHT

        # role + "Company . Location", truncated to avoid colliding with the pill
        text_max = px1 - 24 - 190
        role = fit_text(d, o.get("role", ""), f_bold(35), text_max)
        company = (o.get("company") or "").strip()
        location = (o.get("location") or "").strip()
        sub = f"{company} · {location}" if location else company
        sub = fit_text(d, sub, f_sans(26), text_max)
        d.text((190, y - 30), role, font=f_bold(35), fill=INK)
        d.text((190, y + 8), sub, font=f_sans(26), fill=INK_DIM)

        y += 170

    grad_line(d, PAD, RIGHT, 1112, BLUE, 2)

    # footer
    more_label = f"+ {more} more offers" if lang == "en" else f"+ {more} autres offres"
    on_label = "on jobs.prepzfy.com" if lang == "en" else "sur jobs.prepzfy.com"
    d.text((PAD, 1162), more_label, font=f_bold(34), fill=BLUE_BR)
    d.text((PAD, 1216), on_label, font=f_sans(30), fill=INK_SOFT)
    tracked(d, RIGHT, 1205, "LINKFIN × PREPZFY", f_bold(20), INK_DIMR, tr=2, anchor="ra")

    img.save(out_path, "PNG")
    return out_path


if __name__ == "__main__":
    # Quick local smoke test with demo offers (logos will fall back to monograms
    # if there is no outbound network access).
    demo = [
        {"company": "Goldman Sachs", "role": "Associate, Prime Brokerage",
         "location": "Singapore", "sector": "MARKETS", "domain": "goldmansachs.com"},
        {"company": "Houlihan Lokey", "role": "M&A Internship (2027)",
         "location": "Zurich", "sector": "M&A", "domain": "hl.com"},
        {"company": "Tikehau Capital", "role": "Financial Analyst (Internship)",
         "location": "Paris", "sector": "PE", "domain": "tikehaucapital.com"},
        {"company": "Lincoln International", "role": "M&A Internship (Jan 2027)",
         "location": "Paris", "sector": "M&A", "domain": "lincolninternational.com"},
        {"company": "Macquarie", "role": "Infrastructure M&A Analyst",
         "location": "New York", "sector": "M&A", "domain": "macquarie.com"},
    ]
    path = build_card(demo, total_count=100, out_path="card_preview.png")
    print("saved", path)
