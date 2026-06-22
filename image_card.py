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
NAVY_CARD = (13, 26, 47)      # #0d1a2f  row card (exact site token)
BLUE_SOFT = (91, 139, 217)    # #5b8bd9
GREEN     = (52, 178, 123)    # #34b27b  the Job Board identity colour
LINE      = (38, 52, 76)      # faint card border (~ rgba(255,255,255,0.08) on navy)

# Sector pill accent colours, EXACT from the site's job board (.jp-sector).
SECTOR_COLORS = {
    "IBD": (91, 139, 217),     # #5b8bd9  blue-soft
    "TS":  (52, 178, 123),     # #34b27b  green
    "PE":  (199, 155, 255),    # #c79bff  light purple
    "CONSULTING": (217, 154, 74),  # warm amber (site leaves this open)
    "M&A": (91, 139, 217),
    "MARKETS": (199, 155, 255),
}
DEFAULT_SECTOR_COLOR = (138, 151, 172)


def blend(a, b, f):
    """Mix colour a toward b by fraction f (0..1)."""
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))

# Monogram gradients (one per row, cycling) - from the brand tokens.
GRADIENTS = [
    ((37, 99, 196), (26, 61, 122)),    # blue   #2563C4 -> #1a3d7a
    ((13, 148, 136), (19, 78, 74)),    # teal   #0d9488 -> #134e4a
    ((124, 58, 237), (76, 29, 149)),   # purple #7c3aed -> #4c1d95
    ((180, 83, 9), (120, 53, 15)),     # amber  #b45309 -> #78350f
    ((190, 18, 60), (131, 24, 67)),    # rose   #be123c -> #831843
]

# ---- Fonts ----
# The prepzfy site uses Arial/Helvetica for text and Times New Roman *italic*
# (in blue) for its signature accents. We mirror that exactly:
#   sans  -> Arial      (Liberation Sans is the metric-identical Linux clone)
#   serif -> Times Italic (Liberation Serif Italic)
# Each role lists the best match first, then graceful fallbacks so a missing
# font never crashes CI. A repo-local fonts/ dir wins if present.
_REPO_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def _repo(*names):
    return [os.path.join(_REPO_FONTS, n) for n in names]


_REGULAR_CANDIDATES = _repo("Arial.ttf") + [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_BOLD_CANDIDATES = _repo("Arial-Bold.ttf") + [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_BLACK_CANDIDATES = _BOLD_CANDIDATES  # Arial's heaviest weight is Bold
_SERIF_ITALIC_CANDIDATES = _repo("Times-Italic.ttf") + [
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]


def _first_existing(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


_REGULAR_PATH = _first_existing(_REGULAR_CANDIDATES)
_BOLD_PATH = _first_existing(_BOLD_CANDIDATES)
_BLACK_PATH = _first_existing(_BLACK_CANDIDATES)
_SERIF_PATH = _first_existing(_SERIF_ITALIC_CANDIDATES)


def f_sans(size):
    return ImageFont.truetype(_REGULAR_PATH, size) if _REGULAR_PATH else ImageFont.load_default()


def f_bold(size):
    return ImageFont.truetype(_BOLD_PATH, size) if _BOLD_PATH else ImageFont.load_default()


def f_black(size):
    return ImageFont.truetype(_BLACK_PATH, size) if _BLACK_PATH else ImageFont.load_default()


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


def _rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def grad_square(size, c1, c2, radius=20):
    """Diagonal-gradient rounded square (monogram fallback tile)."""
    g = Image.new("RGB", (size, size))
    px = g.load()
    for yy in range(size):
        for xx in range(size):
            t = (xx + yy) / (2 * size)
            px[xx, yy] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    g.putalpha(_rounded_mask(size, radius))
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


def _logo_tile(logo_img, size=88, radius=20):
    """Put a real logo on a clean white rounded-square tile (job-board style)."""
    bg = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    inner = int(size * 0.70)
    lg = logo_img.copy()
    lg.thumbnail((inner, inner), Image.LANCZOS)
    ox, oy = (size - lg.width) // 2, (size - lg.height) // 2
    bg.paste(lg, (ox, oy), lg)
    bg.putalpha(_rounded_mask(size, radius))
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


def sector_tag(offer):
    """Short code on the pill, exactly like the job board (PE, IBD, TS...)."""
    raw = (offer.get("sector") or offer.get("type") or "").strip()
    return raw.upper()


def date_badge_label(age_days, today, lang):
    """Green freshness badge text per offer, like the site (.jp-date)."""
    if age_days is None:
        return None
    if age_days <= 0:
        return "Today" if lang == "en" else "Auj."
    if age_days == 1:
        return "Yesterday" if lang == "en" else "Hier"
    d0 = today - datetime.timedelta(days=age_days)
    mon = (MONTHS if lang == "en" else MONTHS_FR)[d0.month - 1][:3]
    return f"{d0.day} {mon}"


def _pill_width(d, text, font, tr, padx):
    tw = sum(d.textlength(c, font=font) for c in text) + tr * (len(text) - 1)
    return tw + padx * 2


def build_card(selected, out_path, recent_count=0, recent_days=3, lang="en",
               today=None, brandfetch_key=None, logodev_token=None):
    """
    Render the daily card.
      selected      : list of offer dicts (company, role, location, type,
                      sector, domain). Up to 5 are drawn; fewer is fine.
      out_path      : where to save the PNG.
      recent_count  : how many offers were added in the last `recent_days` days
                      (shown as "+ X added in the last N days"). 0 -> generic line.
      lang          : "en" (default) or "fr".
      brandfetch_key/logodev_token : optional logo sources.
    Returns out_path.
    """
    today = today or datetime.date.today()
    rows = selected[:5]

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

    # wordmark PREPZ + fy (Arial bold + Times italic blue, the brand signature)
    d.text((PAD, 92), "PREPZ", font=f_black(47), fill=INK)
    wl = d.textlength("PREPZ", font=f_black(47))
    d.text((PAD + wl + 3, 88), "fy", font=f_serif(50), fill=BLUE)

    # green job-board kicker (dot + label), mirroring the site's jobs band
    sublabel = "OFFERS OF THE DAY" if lang == "en" else "OFFRES DU JOUR"
    right_tag = "M&A · Private Equity · Consulting" if lang == "en" else "M&A · Private Equity · Conseil"
    d.ellipse([PAD, 165, PAD + 11, 176], fill=GREEN)
    tracked(d, PAD + 26, 159, sublabel, f_bold(20), GREEN, tr=5)
    d.text((RIGHT, 96), format_date(today, lang), font=f_bold(28), fill=INK_SOFT, anchor="ra")
    d.text((RIGHT, 142), right_tag, font=f_sans(22), fill=INK_DIM, anchor="ra")

    grad_line(d, PAD, RIGHT, 222, BLUE, 2)

    # rows (each offer sits on a subtle rounded card, like the job board)
    # Centre the block vertically so 2-3 offers look balanced, not top-stacked.
    TILE = 88
    ROW_STEP, CARD_H = 156, 124
    area_top, area_bottom = 236, 1100
    block_h = (len(rows) - 1) * ROW_STEP + CARD_H
    y = int(area_top + ((area_bottom - area_top) - block_h) / 2 + CARD_H / 2)
    for i, o in enumerate(rows):
        # row card background
        d.rounded_rectangle([PAD, y - 62, RIGHT, y + 62], radius=22, fill=NAVY_CARD, outline=LINE, width=1)

        # square logo tile (white) or gradient monogram tile
        tx, ty = PAD + 28, y - TILE // 2
        logo = fetch_logo(o.get("domain"), brandfetch_key=brandfetch_key,
                          logodev_token=logodev_token)
        if logo is not None:
            tile = _logo_tile(logo, TILE, 20)
            img.paste(tile, (tx, ty), tile)
        else:
            c1, c2 = GRADIENTS[i % len(GRADIENTS)]
            tile = grad_square(TILE, c1, c2, 20)
            img.paste(tile, (tx, ty), tile)
            d.text((tx + TILE // 2, y), initials(o.get("company")), font=f_bold(30), fill=INK, anchor="mm")

        text_x = tx + TILE + 28  # left edge of the text column

        # right column: green date badge (top) + sector pill (bottom), like .jp-right
        x_right = RIGHT - 28
        date_lbl = date_badge_label(o.get("age_days"), today, lang)
        tag = sector_tag(o)
        date_font, sect_font = f_bold(18), f_bold(18)
        widths = []
        if date_lbl:
            widths.append(_pill_width(d, date_lbl, date_font, 0.5, 15))
        if tag:
            widths.append(_pill_width(d, tag, sect_font, 1.2, 17))
        col_left = x_right - (max(widths) if widths else 0)

        date_cy = (y - 22) if (date_lbl and tag) else y
        sect_cy = (y + 22) if (date_lbl and tag) else y
        if date_lbl:
            w = _pill_width(d, date_lbl, date_font, 0.5, 15)
            d.rounded_rectangle([x_right - w, date_cy - 17, x_right, date_cy + 17], radius=17,
                                fill=blend(NAVY_CARD, GREEN, 0.14))
            tracked(d, x_right - w / 2, date_cy, date_lbl, date_font, GREEN, tr=0.5, anchor="mm")
        if tag:
            acc = SECTOR_COLORS.get(tag, DEFAULT_SECTOR_COLOR)
            w = _pill_width(d, tag, sect_font, 1.2, 17)
            d.rounded_rectangle([x_right - w, sect_cy - 17, x_right, sect_cy + 17], radius=17,
                                fill=blend(NAVY_CARD, acc, 0.13))
            tracked(d, x_right - w / 2, sect_cy, tag, sect_font, acc, tr=1.2, anchor="mm")

        # role + "Company · Location", truncated so it never hits the right column
        text_max = col_left - 26 - text_x
        role = fit_text(d, o.get("role", ""), f_bold(33), text_max)
        company = (o.get("company") or "").strip()
        location = (o.get("location") or "").strip()
        sub = f"{company} · {location}" if location else company
        sub = fit_text(d, sub, f_sans(25), text_max)
        d.text((text_x, y - 28), role, font=f_bold(33), fill=INK)
        d.text((text_x, y + 10), sub, font=f_sans(25), fill=INK_DIM)

        y += ROW_STEP

    grad_line(d, PAD, RIGHT, 1112, BLUE, 2)

    # footer
    if recent_count and recent_count > 0:
        more_label = (f"+ {recent_count} added in the last {recent_days} days" if lang == "en"
                      else f"+ {recent_count} ajoutees ces {recent_days} derniers jours")
    else:
        more_label = "New roles added daily" if lang == "en" else "De nouvelles offres chaque jour"
    on_label = ("jobs.prepzfy.com · updated every day" if lang == "en"
                else "jobs.prepzfy.com · mis a jour chaque jour")
    d.text((PAD, 1162), more_label, font=f_bold(34), fill=GREEN)
    d.text((PAD, 1216), on_label, font=f_sans(28), fill=INK_SOFT)
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
    path = build_card(demo, out_path="card_preview.png", recent_count=12, recent_days=3)
    print("saved", path)
