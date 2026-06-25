import os, sys, csv, io, json, shutil, urllib.request, datetime

# ---- Settings you can tweak ----
MODEL = "claude-sonnet-4-6"   # the AI model that picks the offers and writes the text
LANGUAGE = "en"               # "en" for English, "fr" for French
MAX_OFFERS = 5                # never feature more than this
MIN_OFFERS = 2                # better to post 2-3 fresh ones than pad with stale
FRESH_WINDOWS = (7, 14, 30)   # days: prefer the freshest pool that has enough offers
RECENT_DAYS = 3               # the "+ X added in the last N days" badge on the image
# Logo quality gate (owner's rule): only offers whose company has a Brandfetch
# logo reach Claude, so the card never features an unknown firm. Bounds the
# number of Brand API calls per day.
LOGO_PREFILTER_TARGET = 10    # stop once this many logo-backed candidates are found
LOGO_PREFILTER_MAX_CHECKS = 18  # never make more than this many Brand API calls/day
VARIANT_COUNT = 4             # how many first-comment variants Claude drafts for the owner
# --------------------------------

CSV_URL = os.environ.get("SHEET_CSV_URL", "")

VOICE = (
    "You are the editor for PREPZfy, a coaching brand for French grandes ecoles students "
    "(HEC, ESSEC, SKEMA, EM Lyon, EDHEC) heading into M&A, Private Equity, markets and MBB consulting. "
    "Voice: a generous senior who already did the internship and now explains it plainly. "
    "Confident, never arrogant. Precise, never jargon-drunk. Teaches, never performs. No clickbait, no hype."
)


def fetch_csv(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8")


def parse_date(s):
    """Best-effort parse of a sheet date. Returns a datetime.date or None."""
    s = (s or "").strip()
    if not s:
        return None
    s = s.split("T")[0].split(" ")[0]  # drop any time part
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def is_linkedin_link(link):
    """LinkedIn (job or permalink) links can go straight in the caption without
    hurting reach; everything else is treated as an external link."""
    return "linkedin.com" in (link or "").lower()


def parse_offers(text, today=None):
    today = today or datetime.date.today()
    rows = list(csv.DictReader(io.StringIO(text)))
    offers = []
    for r in rows:
        link = (r.get("link") or "").strip()
        company = (r.get("company") or "").strip()
        role = (r.get("role") or "").strip()
        if not (link and company and role):
            continue
        published = parse_date(r.get("date_published")) or parse_date(r.get("date_added"))
        added = parse_date(r.get("date_added")) or published
        age_days = (today - published).days if published else None
        offers.append({
            "company": company,
            "role": role,
            "location": (r.get("location") or r.get("city") or "").strip(),
            "city": (r.get("city") or "").strip(),
            "type": (r.get("type") or "").strip(),
            "sector": (r.get("sector") or "").strip(),
            "domain": (r.get("domain") or "").strip(),
            "link": link,
            "is_linkedin": is_linkedin_link(link),
            "age_days": age_days,
            "_added": added,
        })
    return offers


def count_recent(offers, days, today=None):
    """How many offers were ADDED within the last `days` days (for the image badge)."""
    today = today or datetime.date.today()
    n = 0
    for o in offers:
        added = o.get("_added")
        if added and (today - added).days <= days:
            n += 1
    return n


def candidate_pool(offers):
    """Build the freshest pool of offers to hand to the model. Prefer offers
    from the last 7 days; widen to 14 then 30 only if too few are that fresh.
    Offers with no usable date are kept but ranked after dated ones."""
    dated = sorted((o for o in offers if o["age_days"] is not None), key=lambda o: o["age_days"])
    undated = [o for o in offers if o["age_days"] is None]
    for window in FRESH_WINDOWS:
        fresh = [o for o in dated if o["age_days"] <= window]
        if len(fresh) >= max(MIN_OFFERS + 1, 3):
            return fresh + undated
    return dated + undated  # nothing clearly fresh: hand over everything, sorted



def prefilter_known(pool):
    """Keep only candidates whose company has a Brandfetch logo, walking the
    freshest-first pool until we have enough or hit the call cap. This enforces
    the owner's rule (no logo -> firm not notable enough -> don't feature it)
    BEFORE Claude writes anything, so the caption never names a dropped firm.
    Returns (kept_pool, did_filter). With no API key (local previews) we cannot
    check, so we keep the pool untouched."""
    import image_card
    if not image_card.BRANDFETCH_API_KEY:
        return pool, False
    kept, checks = [], 0
    for o in pool:
        if len(kept) >= LOGO_PREFILTER_TARGET or checks >= LOGO_PREFILTER_MAX_CHECKS:
            break
        checks += 1
        if image_card.has_brandfetch_logo(o.get("domain")):
            kept.append(o)
        else:
            print(f"  - skip {o.get('company')} ({o.get('domain')}): no Brandfetch logo")
    return kept, True


def build_prompt(offers, today=None):
    today = today or datetime.date.today()
    # Only send the fields the model needs (drop internal keys like _added).
    keep = ("company", "role", "location", "city", "type", "sector", "domain", "link",
            "is_linkedin", "age_days")
    listing = json.dumps([{k: o.get(k) for k in keep} for o in offers], ensure_ascii=False)
    lang = "English" if LANGUAGE == "en" else "French"
    return (
        f"{VOICE}\n\n"
        f"Today is {today.isoformat()}. Here are candidate job offers as JSON "
        f"(`age_days` = days since the offer was published, smaller is fresher; "
        f"`is_linkedin` = true when the link is a LinkedIn job/permalink):\n{listing}\n\n"
        f"Task:\n"
        f"1. SELECT the best offers for the audience above. Hard rules on selection:\n"
        f"   - ATTRACTIVENESS FIRST: pick the offers students will most want to click. Strongly favor "
        f"well-known, prestigious firms (bulge-bracket and elite-boutique banks, top PE funds, MBB and "
        f"Big-4 deal advisory, blue-chip corporates) and clearly relevant roles (M&A, PE, IBD, "
        f"consulting, markets), internships, graduate and junior positions. Skip obscure, vague or "
        f"confidential listings even if they are very fresh.\n"
        f"   - All candidates above already have a real company logo, so judge purely on prestige and "
        f"relevance.\n"
        f"   - Recency is a tie-breaker, not the goal: prefer recent offers, and it is perfectly fine to "
        f"re-feature an attractive offer from yesterday or the day before. Pick between {MIN_OFFERS} and "
        f"{MAX_OFFERS} offers (fewer is fine; never pad with weak ones). Avoid duplicates.\n"
        f"   - Prefer offers where is_linkedin is true (so we can link them in the post).\n"
        f"2. Write a LinkedIn caption in {lang} in the PREPZfy voice, formatted CLEAN, SOBER and PREMIUM "
        f"(think elite finance newsletter, not a flashy ad):\n"
        f"   - Open with ONE short, understated hook line.\n"
        f"   - Then ONE short context line.\n"
        f"   - Then ONE line PER featured offer, formatted as \"{{Role}} at {{Company}} ({{City}})\", each prefixed "
        f"with a single restrained marker (use the bullet character · or ›, NOT emojis). Use the `city` field "
        f"for the city. If is_linkedin is true you MAY append its link at the end of that line; if is_linkedin is "
        f"false, do NOT include its link.\n"
        f"   - Close with ONE line making clear the board is refreshed every day. Do NOT mention or refer to a "
        f"first comment, the comments, or where the link is.\n"
        f"   - Separate these blocks with blank lines so the post breathes.\n"
        f"   - EMOJIS: at most ONE subtle, professional emoji in the WHOLE caption, and only if it genuinely adds "
        f"polish (a finance-appropriate one). Zero is perfectly fine. NEVER one per line, no rockets/party/celebration "
        f"emojis, nothing childish or salesy.\n"
        f"3. Write {VARIANT_COUNT} DISTINCT variants of the LinkedIn FIRST COMMENT in {lang}. This first "
        f"comment is the main call to action. Each variant must point readers to jobs.prepzfy.com, note it "
        f"is updated every day, and keep the same generous spirit (e.g. \"The full board is live at "
        f"jobs.prepzfy.com\", \"Follow us for more offers...\"). Vary the opening hook and phrasing across "
        f"the {VARIANT_COUNT} variants so the owner can choose; each should stand alone.\n\n"
        f"Caption rules: no em dashes, capitalize firm names, NO link to jobs.prepzfy.com anywhere in the "
        f"caption body, NO external (non-LinkedIn) link, NO hashtags "
        f"at all, no engagement bait. Keep it tight and actionable.\n\n"
        f"For each selected offer, copy its fields verbatim from the input above "
        f"(including the exact link and domain).\n\n"
        f"Return ONLY valid JSON, no markdown fences, with exactly this shape:\n"
        f'{{"selected":[{{"company":"","role":"","location":"","type":"","sector":"","domain":"","link":""}}],'
        f'"caption":"","first_comment_variants":["","",""]}}'
    )


def call_claude(prompt):
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    if not text.startswith("{"):
        s, e = text.find("{"), text.rfind("}")
        text = text[s:e + 1]
    return json.loads(text)


def enrich_selected(selected, offers):
    """Re-fill each selected offer's fields from the original sheet rows,
    matching on the link (unique). Guards against the model dropping a field
    such as `domain`, which the image needs for logos."""
    by_link = {o["link"]: o for o in offers}
    for s in selected:
        src = by_link.get((s.get("link") or "").strip())
        if not src:
            continue
        for key in ("company", "role", "location", "city", "type", "sector", "domain"):
            if not (s.get(key) or "").strip():
                s[key] = src.get(key, "")
        s["age_days"] = src.get("age_days")   # authoritative, for the date badge
    return selected


PAYLOAD_FILE = "post_payload.json"
PUBLIC_CARD_DIR = "docs/cards"   # committed so each card gets a public raw URL
DEFAULT_REPO = "davidkhoa/lkdn-prepzfy-jobs-daily"


def public_image_url(rel_path):
    """Public raw.githubusercontent URL for a file committed to this repo. Uses the
    Actions-provided repo/branch when available, else sensible defaults."""
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    rel = rel_path.replace("\\", "/")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel}"


def write_payload(payload):
    """Hand-off file read by publish.py (caption, variants, public image URL)."""
    with open(PAYLOAD_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def make_image(selected, recent_count, out_path="card.png"):
    """Render the daily card for the selected offers. Returns the PNG path."""
    import image_card
    return image_card.build_card(
        selected, recent_count=recent_count, recent_days=RECENT_DAYS,
        out_path=out_path, lang=LANGUAGE,
    )


def comment_variants(data):
    """The first-comment CTA variants, tolerant of the old single-comment shape."""
    variants = data.get("first_comment_variants")
    if isinstance(variants, list):
        variants = [v.strip() for v in variants if (v or "").strip()]
        if variants:
            return variants
    single = (data.get("first_comment") or "").strip()
    return [single] if single else []


def to_markdown(data):
    today = datetime.date.today().isoformat()
    out = [f"# PREPZfy, offres du jour ({today})\n", "## Selection\n"]
    for o in data.get("selected", []):
        out.append(f"- **{o.get('company','')}** , {o.get('role','')} ({o.get('city') or o.get('location','')})  ")
        out.append(f"  {o.get('link','')}")
    out.append("\n## LinkedIn caption\n")
    out.append("```\n" + data.get("caption", "") + "\n```")
    out.append("\n## First comment - variants (pick one)\n")
    for i, v in enumerate(comment_variants(data), 1):
        out.append(f"\n**Variant {i}**\n")
        out.append("```\n" + v + "\n```")
    return "\n".join(out)


def main():
    if not CSV_URL:
        print("ERROR: the SHEET_CSV_URL secret is empty.")
        sys.exit(1)
    today = datetime.date.today()
    offers = parse_offers(fetch_csv(CSV_URL), today=today)
    print(f"Loaded {len(offers)} offers from the sheet.")
    if not offers:
        print("ERROR: no offers with a link were found. Check the Public tab / the CSV link.")
        sys.exit(1)
    fresh_pool = candidate_pool(offers)
    recent_count = count_recent(offers, RECENT_DAYS, today=today)
    print(f"{len(fresh_pool)} fresh candidates; {recent_count} added in the last {RECENT_DAYS} days.")
    pool, did_filter = prefilter_known(fresh_pool)
    if did_filter:
        print(f"{len(pool)} candidates kept after the Brandfetch logo check.")
    # Fail-safe: the Brandfetch gate must never silently kill the whole post. If it
    # leaves too few candidates (Brandfetch outage / a thin day of lesser-known
    # firms), fall back to the full fresh pool so we still publish something.
    if did_filter and len(pool) < MIN_OFFERS:
        print(f"Only {len(pool)} logo-backed candidate(s); falling back to the full "
              f"fresh pool ({len(fresh_pool)}) so the post still goes out.")
        pool = fresh_pool
    if not pool:
        print("No offers at all today (empty sheet/pool). Skipping gracefully.")
        return
    data = call_claude(build_prompt(pool, today=today))
    selected = enrich_selected(data.get("selected", []), offers)[:MAX_OFFERS]
    if not selected:
        print("Nothing cleared the bar today. Skipping gracefully, no image, no post.")
        write_payload({"skip": True})
        return

    # Render to a dated public path (committed by the workflow so Buffer can fetch
    # it by URL); also keep card.png for the run artifact.
    rel_path = f"{PUBLIC_CARD_DIR}/{today.isoformat()}.png"
    os.makedirs(PUBLIC_CARD_DIR, exist_ok=True)
    make_image(selected, recent_count=recent_count, out_path=rel_path)
    shutil.copyfile(rel_path, "card.png")
    image_url = public_image_url(rel_path)
    print(f"Image generated: {rel_path} ({len(selected)} offers featured)")
    print(f"Public image URL (after commit): {image_url}")

    variants = comment_variants(data)
    write_payload({
        "skip": False,
        "date": today.isoformat(),
        "image_path": rel_path,
        "image_url": image_url,
        "caption": data.get("caption", ""),
        "first_comment_variants": variants,
        # for company tagging in publish.py (domain -> LinkedIn org in li_companies.json)
        "companies": [{"company": o.get("company", ""), "domain": o.get("domain", "")}
                      for o in selected],
    })

    md = to_markdown(data)
    print("\n" + md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md + "\n")
            f.write(f"\n## Image\nGenerated `{rel_path}` "
                    f"(download it from the run's Artifacts).\n")


if __name__ == "__main__":
    main()
