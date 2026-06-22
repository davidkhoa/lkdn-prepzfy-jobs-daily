import os, sys, csv, io, json, urllib.request, datetime

# ---- Settings you can tweak ----
MODEL = "claude-sonnet-4-6"   # the AI model that picks the offers and writes the text
LANGUAGE = "en"               # "en" for English, "fr" for French
N = 5                         # how many offers to feature
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


def parse_offers(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    offers = []
    for r in rows:
        link = (r.get("link") or "").strip()
        company = (r.get("company") or "").strip()
        role = (r.get("role") or "").strip()
        if not (link and company and role):
            continue
        offers.append({
            "company": company,
            "role": role,
            "location": (r.get("location") or r.get("city") or "").strip(),
            "type": (r.get("type") or "").strip(),
            "sector": (r.get("sector") or "").strip(),
            "domain": (r.get("domain") or "").strip(),
            "link": link,
            "date": (r.get("date_published") or r.get("date_added") or "").strip(),
        })
    return offers


def build_prompt(offers):
    listing = json.dumps(offers, ensure_ascii=False)
    lang = "English" if LANGUAGE == "en" else "French"
    return (
        f"{VOICE}\n\n"
        f"Here are today's job offers as JSON:\n{listing}\n\n"
        f"Task:\n"
        f"1. Pick the {N} MOST ATTRACTIVE offers for the audience above. Favor prestigious firms, "
        f"relevant roles (M&A, PE, IBD, consulting, markets), internships, graduate and junior roles, "
        f"and recent postings. Avoid duplicates and vague or confidential listings.\n"
        f"2. Write a LinkedIn caption in {lang} in the PREPZfy voice.\n"
        f"3. Write a first comment in {lang} that points readers to jobs.prepzfy.com.\n\n"
        f"Caption rules: no em dashes, capitalize firm names, NO link in the body, no engagement bait, "
        f"3 to 5 niche hashtags at the very bottom. Keep it short and punchy.\n\n"
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
    return selected


def make_image(selected, total_count, out_path="card.png"):
    """Render the daily card for the selected offers. Returns the PNG path."""
    import image_card
    return image_card.build_card(
        selected, total_count=total_count, out_path=out_path, lang=LANGUAGE,
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
    offers = parse_offers(fetch_csv(CSV_URL))
    print(f"Loaded {len(offers)} offers from the sheet.")
    if not offers:
        print("ERROR: no offers with a link were found. Check the Public tab / the CSV link.")
        sys.exit(1)
    data = call_claude(build_prompt(offers))
    selected = enrich_selected(data.get("selected", []), offers)
    if not selected:
        print("Nothing cleared the bar today. Skipping gracefully, no image, no post.")
        return
    image_path = make_image(selected, total_count=len(offers))
    print(f"Image generated: {image_path}")

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
