import os, sys, csv, io, json, urllib.request, datetime

# ---- Settings you can tweak ----
MODEL = "claude-sonnet-4-6"   # the AI model that picks the offers and writes the text
LANGUAGE = "en"               # "en" for English, "fr" for French
MAX_OFFERS = 5                # never feature more than this
MIN_OFFERS = 2                # better to post 2-3 fresh ones than pad with stale
FRESH_WINDOWS = (7, 14, 30)   # days: prefer the freshest pool that has enough offers
RECENT_DAYS = 3               # the "+ X added in the last N days" badge on the image
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



def build_prompt(offers, today=None):
    today = today or datetime.date.today()
    # Only send the fields the model needs (drop internal keys like _added).
    keep = ("company", "role", "location", "type", "sector", "domain", "link",
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
        f"   - FRESHNESS FIRST: strongly prefer offers with a small age_days (last few days). "
        f"It is BETTER to feature only {MIN_OFFERS} or 3 genuinely fresh, relevant offers than to "
        f"pad up to 5 with stale ones. Do NOT feature anything older than ~14 days unless it is "
        f"exceptionally strong. Pick between {MIN_OFFERS} and {MAX_OFFERS} offers (fewer is fine).\n"
        f"   - Favor prestigious firms and relevant roles (M&A, PE, IBD, consulting, markets), "
        f"internships, graduate and junior roles. Avoid duplicates, vague or confidential listings.\n"
        f"   - Prefer offers where is_linkedin is true (so we can link them in the post).\n"
        f"2. Write a LinkedIn caption in {lang} in the PREPZfy voice. The caption MUST make clear the "
        f"job board is refreshed daily (e.g. \"We update it every day.\"). For each featured offer:\n"
        f"   - if is_linkedin is true, include its link inline in the caption (LinkedIn links do not hurt reach);\n"
        f"   - if is_linkedin is false (external site), do NOT put its link in the caption.\n"
        f"3. Write a first comment in {lang}. It points readers to jobs.prepzfy.com, notes it is updated "
        f"every day, and lists any featured offers whose link was external (with a soft CTA to the board). "
        f"VARY the wording of this comment from day to day so it is never identical (rotate the opening hook).\n\n"
        f"Caption rules: no em dashes, capitalize firm names, NO external (non-LinkedIn) link in the body, "
        f"no engagement bait, 3 to 5 niche hashtags at the very bottom. Keep it tight and actionable.\n\n"
        f"For each selected offer, copy its fields verbatim from the input above "
        f"(including the exact link and domain).\n\n"
        f"Return ONLY valid JSON, no markdown fences, with exactly this shape:\n"
        f'{{"selected":[{{"company":"","role":"","location":"","type":"","sector":"","domain":"","link":""}}],'
        f'"caption":"","first_comment":""}}'
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
        for key in ("company", "role", "location", "type", "sector", "domain"):
            if not (s.get(key) or "").strip():
                s[key] = src.get(key, "")
        s["age_days"] = src.get("age_days")   # authoritative, for the date badge
    return selected


def make_image(selected, recent_count, out_path="card.png"):
    """Render the daily card for the selected offers. Returns the PNG path."""
    import image_card
    return image_card.build_card(
        selected, recent_count=recent_count, recent_days=RECENT_DAYS,
        out_path=out_path, lang=LANGUAGE,
        brandfetch_key=os.environ.get("BRANDFETCH_API_KEY") or None,   # square icons (best)
        logodev_token=os.environ.get("LOGODEV_TOKEN") or None,         # optional fallback
    )


def to_markdown(data):
    today = datetime.date.today().isoformat()
    out = [f"# PREPZfy, offres du jour ({today})\n", "## Selection\n"]
    for o in data.get("selected", []):
        out.append(f"- **{o.get('company','')}** , {o.get('role','')} ({o.get('location','')})  ")
        out.append(f"  {o.get('link','')}")
    out.append("\n## LinkedIn caption\n")
    out.append("```\n" + data.get("caption", "") + "\n```")
    out.append("\n## First comment\n")
    out.append("```\n" + data.get("first_comment", "") + "\n```")
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
    pool = candidate_pool(offers)
    recent_count = count_recent(offers, RECENT_DAYS, today=today)
    print(f"{len(pool)} fresh candidates; {recent_count} added in the last {RECENT_DAYS} days.")
    data = call_claude(build_prompt(pool, today=today))
    selected = enrich_selected(data.get("selected", []), offers)
    if not selected:
        print("Nothing cleared the bar today. Skipping gracefully, no image, no post.")
        return
    image_path = make_image(selected, recent_count=recent_count)
    print(f"Image generated: {image_path} ({len(selected)} offers featured)")

    md = to_markdown(data)
    print("\n" + md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md + "\n")
            f.write(f"\n## Image\nGenerated `{image_path}` "
                    f"(download it from the run's Artifacts).\n")


if __name__ == "__main__":
    main()
