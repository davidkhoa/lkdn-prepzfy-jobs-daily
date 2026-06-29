"""
publish.py - posts the daily card to the PREPZfy LinkedIn Page via the Buffer API.

How it fits the pipeline:
  daily_post.py  -> generates the card + caption, writes post_payload.json,
                    and saves the PNG under docs/cards/<date>.png
  (the workflow)  -> commits that PNG so it has a PUBLIC raw.githubusercontent URL
  publish.py      -> reads post_payload.json and asks Buffer to publish
                     image + caption to the connected LinkedIn channel.

Important limitation (Buffer API beta): it can publish the post (image + caption)
but it CANNOT post the first comment. So the owner pastes the chosen first-comment
variant by hand right after (the variants are printed below for convenience).

Safety:
  - Needs BUFFER_API_KEY (read from env, never committed).
  - DRY_RUN=1 (or no key) -> it only DISCOVERS the LinkedIn channel and prints what
    it WOULD do, without posting. Use this for the first test run.
  - BUFFER_CHANNEL_ID can pin a specific channel; otherwise the LinkedIn channel is
    auto-detected.
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.error

PAYLOAD_FILE = "post_payload.json"
LI_MAP_FILE = "li_companies.json"   # domain -> LinkedIn org (for company tagging)
BUFFER_ENDPOINT = "https://api.buffer.com"
BUFFER_API_KEY = os.environ.get("BUFFER_API_KEY") or ""
# The PREPZfy "prepz-fy" LinkedIn Page channel id (discovered via list_channels on
# 2026-06-22). Used as the default so we can post WITHOUT listing channels -- some API
# keys grant posts:write but not the channel-read scope, and posting only needs the id.
DEFAULT_LINKEDIN_CHANNEL_ID = "6a39468f5ab6d2f1065c965c"
BUFFER_CHANNEL_ID = os.environ.get("BUFFER_CHANNEL_ID") or DEFAULT_LINKEDIN_CHANNEL_ID
# Minutes from now to schedule the post (gives Buffer a moment to fetch the image).
BUFFER_DELAY_MIN = int(os.environ.get("BUFFER_DELAY_MIN", "5") or "5")
# Fixed wall-clock time the post should go LIVE, in Europe/Paris (HH:MM, 24h).
# GitHub Actions cron is unreliable (it fires hours late), so we no longer rely on
# it for timing: the workflow just PREPARES the card in the morning, and Buffer is
# told to publish at this exact Paris time. Using the Paris zone handles summer/
# winter (DST) automatically -- always 17:15 local, no UTC drift. Set POST_TIME_PARIS
# to "" (empty) to fall back to the old "now + BUFFER_DELAY_MIN" behaviour.
POST_TIME_PARIS = os.environ.get("POST_TIME_PARIS", "17:15")


def _due_at(delay_min=BUFFER_DELAY_MIN):
    """Buffer dueAt string. If POST_TIME_PARIS is set, return the next occurrence of
    that wall-clock time in Europe/Paris (converted to UTC). Otherwise, or if the tz
    database is unavailable (e.g. a local Windows preview), fall back to now+delay."""
    hhmm = (POST_TIME_PARIS or "").strip()
    if hhmm:
        try:
            from zoneinfo import ZoneInfo
            paris = ZoneInfo("Europe/Paris")
            h, m = (int(x) for x in hhmm.split(":"))
            now = datetime.datetime.now(paris)
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            # If today's slot is already past (or within 2 min), aim for tomorrow so
            # Buffer always gets a future time. A morning cron keeps this on "today".
            if due <= now + datetime.timedelta(minutes=2):
                due += datetime.timedelta(days=1)
            return due.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except Exception as exc:  # tzdata missing locally, bad HH:MM, etc.
            print(f"  (POST_TIME_PARIS unusable: {exc}; using now+{delay_min}min)")
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=delay_min)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _is_truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


DRY_RUN = _is_truthy(os.environ.get("DRY_RUN"))


def _graphql(query, variables=None):
    """Send a GraphQL request to Buffer and return the parsed `data`. Raises on
    transport errors or GraphQL `errors`."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        BUFFER_ENDPOINT, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {BUFFER_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "prepzfy-daily-bot",
        })
    try:
        resp = urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as e:
        # Buffer returns 400 with a JSON body describing the real validation error;
        # surface it so the run log is self-diagnosing instead of "Bad Request".
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        detail = body
        try:
            parsed = json.loads(body)
            if parsed.get("errors"):
                detail = "; ".join(er.get("message", str(er)) for er in parsed["errors"])
        except Exception:
            pass
        raise RuntimeError(f"Buffer HTTP {e.code}: {detail or e.reason}")
    payload = json.loads(resp)
    if payload.get("errors"):
        msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise RuntimeError(f"Buffer GraphQL error: {msgs}")
    return payload.get("data") or {}


def list_channels():
    """All connected channels across the account, flattened to dicts with
    org_id, id, name, service."""
    data = _graphql(
        "query { account { organizations { id name "
        "channels { id name service } } } }")
    out = []
    account = data.get("account") or {}
    for org in account.get("organizations") or []:
        for ch in org.get("channels") or []:
            out.append({
                "org_id": org.get("id"),
                "org_name": org.get("name"),
                "id": ch.get("id"),
                "name": ch.get("name"),
                "service": (ch.get("service") or "").lower(),
            })
    return out


def find_linkedin_channel(channels):
    """Pick the LinkedIn channel to post to. An explicit BUFFER_CHANNEL_ID wins.
    Otherwise prefer one whose name mentions PREPZfy/LINKFIN, else the first
    LinkedIn channel. Returns the channel dict or None."""
    if BUFFER_CHANNEL_ID:
        for c in channels:
            if c["id"] == BUFFER_CHANNEL_ID:
                return c
        return {"id": BUFFER_CHANNEL_ID, "name": "(pinned by BUFFER_CHANNEL_ID)",
                "service": "linkedin"}
    linkedin = [c for c in channels if c["service"] == "linkedin"]
    if not linkedin:
        return None
    for c in linkedin:
        nm = (c["name"] or "").lower()
        if "prepz" in nm or "linkfin" in nm:
            return c
    return linkedin[0]


def create_post(channel_id, text, image_url, annotations=None,
                first_comment=None, delay_min=BUFFER_DELAY_MIN):
    """Schedule the post (image + caption) on Buffer for the configured Paris time
    (POST_TIME_PARIS), or a few minutes from now if that is disabled.
      - `annotations` tags LinkedIn organizations in the text
        (metadata.linkedin.annotations, see build_annotations).
      - `first_comment` is auto-posted by Buffer as the FIRST COMMENT once the post
        goes live (metadata.linkedin.firstComment, a String; added to the Buffer API
        on 2026-02-19). Needs a Buffer plan that allows first comments.
    The LinkedIn metadata block is assembled only from the parts we actually have, so
    the mutation stays valid in every combination (commas are optional in GraphQL)."""
    due = _due_at(delay_min)
    variables = {"text": text, "channelId": channel_id, "url": image_url, "dueAt": due}

    var_decls = ["$text: String!", "$channelId: ChannelId!",
                 "$url: String!", "$dueAt: DateTime!"]
    li_fields = []
    if annotations:
        var_decls.append("$annotations: [AnnotationInputLinkedIn!]!")
        li_fields.append("annotations: $annotations")
        variables["annotations"] = annotations
    fc = (first_comment or "").strip()
    if fc:
        var_decls.append("$firstComment: String!")
        li_fields.append("firstComment: $firstComment")
        variables["firstComment"] = fc

    input_fields = [
        "text: $text", "channelId: $channelId",
        "schedulingType: automatic", "mode: customScheduled", "dueAt: $dueAt",
        "assets: [{ image: { url: $url } }]",
    ]
    if li_fields:
        input_fields.append("metadata: { linkedin: { " + " ".join(li_fields) + " } }")

    mutation = (
        "mutation(" + ", ".join(var_decls) + ") {"
        "  createPost(input: { " + " ".join(input_fields) + " }) {"
        "    __typename"
        "    ... on PostActionSuccess { post { id status dueAt } }"
        "    ... on MutationError { message }"
        "  }"
        "}")
    data = _graphql(mutation, variables)
    return data.get("createPost") or {}


def _norm_domain(domain):
    d = (domain or "").strip().lower()
    d = d.split("//")[-1].split("/")[0]
    return d[4:] if d.startswith("www.") else d


def load_li_map():
    """domain -> LinkedIn org entry, from li_companies.json (skips the _README key
    and any malformed rows). Returns {} if the file is missing."""
    if not os.path.exists(LI_MAP_FILE):
        return {}
    try:
        with open(LI_MAP_FILE, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except Exception:
        return {}
    return {_norm_domain(k): v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict)}


def tag_coverage(companies, li_map):
    """Split today's companies into (taggable now, not yet mappable)."""
    taggable, missing = [], []
    for c in companies or []:
        entry = li_map.get(_norm_domain(c.get("domain")))
        if entry and str(entry.get("id") or "").strip():
            taggable.append(c.get("company") or "")
        else:
            missing.append(c.get("company") or "")
    return [t for t in taggable if t], [m for m in missing if m]


def _utf16_len(s):
    """Length in UTF-16 code units (how LinkedIn counts annotation offsets)."""
    return len((s or "").encode("utf-16-le")) // 2


def build_annotations(caption, companies, li_map):
    """Turn each taggable company into a LinkedIn organization mention. LinkedIn
    REQUIRES the tagged text, its length and the declared name to describe the SAME
    span, and counts offsets in UTF-16 code units; one bad mention rejects the WHOLE
    post. So we annotate the canonical name from li_companies.json (or the offer
    company if that is what appears), and keep text == localizedName == length."""
    anns, used = [], []
    for c in companies or []:
        entry = li_map.get(_norm_domain(c.get("domain")))
        oid = str((entry or {}).get("id") or "").strip()
        if not entry or not oid:
            continue
        # Prefer the canonical mapping name; fall back to the offer's company string.
        text = idx = None
        for cand in (entry.get("name"), c.get("company")):
            cand = (cand or "").strip()
            if cand and caption.find(cand) >= 0:
                text, idx = cand, caption.find(cand)
                break
        if text is None:
            continue
        end = idx + len(text)
        if any(not (end <= u0 or idx >= u1) for u0, u1 in used):
            continue   # overlaps an already-tagged span
        used.append((idx, end))
        anns.append({
            "id": oid,
            "entity": f"urn:li:organization:{oid}",
            "link": entry.get("link") or f"https://www.linkedin.com/company/{entry.get('vanity', '')}",
            "vanityName": entry.get("vanity") or "",
            "localizedName": text,                 # MUST equal the tagged text
            "start": _utf16_len(caption[:idx]),    # UTF-16 offset, like LinkedIn
            "length": _utf16_len(text),
        })
    return anns


def publish_with_fallback(channel_id, text, image_url, annotations, first_comment=None):
    """Publish the post, trying the richest form first (company tags + first comment)
    and dropping the riskiest parts on rejection so the daily post ALWAYS goes out.
    The first comment (a paid feature the owner wants) is kept longer than the company
    tags (historically the flakier part). Each rejected attempt creates NO post, so
    there is never a duplicate. Returns (result, tagged_bool, commented_bool)."""
    fc = (first_comment or "").strip() or None
    attempts = []
    if annotations and fc:
        attempts.append(("tags + first comment",
                         dict(annotations=annotations, first_comment=fc), True, True))
    if fc:
        attempts.append(("first comment only", dict(first_comment=fc), False, True))
    if annotations:
        attempts.append(("tags only", dict(annotations=annotations), True, False))
    attempts.append(("plain post", dict(), False, False))

    res = {}
    for label, kwargs, tagged, commented in attempts:
        try:
            res = create_post(channel_id, text, image_url, **kwargs)
            if res.get("__typename") == "PostActionSuccess":
                return res, tagged, commented
            print(f"  Post form '{label}' rejected ({res.get('message')}); "
                  f"trying a simpler form.")
        except RuntimeError as e:
            print(f"  Post form '{label}' failed ({e}); trying a simpler form.")
    return res, False, False


def _print_comment_reminder(variants):
    print("\n" + "=" * 60)
    print("ACTION REQUIRED: add the FIRST COMMENT by hand (Buffer's API cannot).")
    print("Paste ONE of these as the first comment (ideally from your personal profile):")
    for i, v in enumerate(variants or [], 1):
        print(f"\n--- Variant {i} ---\n{v}")
    print("=" * 60)


def _summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    if not os.path.exists(PAYLOAD_FILE):
        print(f"No {PAYLOAD_FILE}; nothing was generated. Skipping publish.")
        return
    with open(PAYLOAD_FILE, encoding="utf-8-sig") as f:
        payload = json.load(f)

    if payload.get("skip"):
        print("Payload marked skip=true (nothing worth posting). No publish.")
        return

    caption = payload.get("caption") or ""
    image_url = payload.get("image_url") or ""
    variants = payload.get("first_comment_variants") or []

    if not (caption and image_url):
        print("Payload missing caption or image_url; skipping publish.")
        return

    if not BUFFER_API_KEY:
        print("No BUFFER_API_KEY set: skipping Buffer publish (manual posting).")
        _print_comment_reminder(variants)
        return

    # Post straight to the known channel id. We deliberately DO NOT call list_channels()
    # here: posting needs only posts:write + the channel id, whereas reading the channel
    # list needs a scope some keys lack (that read failing must never block publishing).
    if BUFFER_CHANNEL_ID:
        channel = {"id": BUFFER_CHANNEL_ID, "name": "(pinned channel)", "service": "linkedin"}
        print(f"Target channel id: {BUFFER_CHANNEL_ID} (skipping channel listing).")
    else:
        channels = list_channels()
        channel = find_linkedin_channel(channels)
        if not channel:
            print("No LinkedIn channel connected in Buffer. Connect the PREPZfy Page "
                  "in Buffer, then re-run.")
            return
        print(f"Target channel: {channel.get('name')} (id {channel.get('id')})")
    print(f"Image URL: {image_url}")

    # Upfront company-tag check: who can we tag today, who still needs an entry.
    companies = payload.get("companies") or []
    li_map = load_li_map()
    taggable, missing = tag_coverage(companies, li_map)
    annotations = build_annotations(caption, companies, li_map)
    print(f"Company tags ready: {len(annotations)} "
          f"({', '.join(taggable) if taggable else 'none'}).")
    if missing:
        print(f"Not taggable yet (add to {LI_MAP_FILE} with a LinkedIn id): "
              f"{', '.join(missing)}")

    # The first comment Buffer will auto-post: Claude's first/primary variant.
    first_comment = variants[0] if variants else ""

    if DRY_RUN:
        print("\nDRY_RUN=1 -> not posting. The above is what WOULD be published.")
        if first_comment:
            print(f"\nFirst comment that WOULD be auto-posted:\n{first_comment}")
        else:
            _print_comment_reminder(variants)
        _summary([
            "## Buffer (dry run)",
            f"- Would post to **{channel.get('name')}** (id `{channel.get('id')}`)",
            f"- Image: {image_url}",
            f"- Company tags ready: {len(annotations)} "
            f"({', '.join(taggable) if taggable else 'none'}).",
            (f"- Not taggable yet: {', '.join(missing)}" if missing else ""),
            ("- First comment: WOULD be auto-posted by Buffer."
             if first_comment else "- First comment: none generated."),
        ])
        return

    result, tagged, commented = publish_with_fallback(
        channel["id"], caption, image_url, annotations, first_comment)
    if result.get("__typename") == "PostActionSuccess":
        post = result.get("post") or {}
        applied = len(annotations) if tagged else 0
        print(f"Posted to Buffer queue. Post id {post.get('id')} "
              f"status {post.get('status')} due {post.get('dueAt')} "
              f"(company tags applied: {applied}; "
              f"first comment automated: {'yes' if commented else 'no'}).")
        _summary([
            "## Buffer publish",
            f"- Scheduled on **{channel.get('name')}** (post `{post.get('id')}`, "
            f"due {post.get('dueAt')}).",
            f"- Company tags applied: {applied}.",
            ("- First comment: posted automatically by Buffer."
             if commented else
             "- First comment: NOT automated this run (paste a variant by hand)."),
        ])
    else:
        msg = result.get("message") or json.dumps(result)
        print(f"Buffer did NOT accept the post: {msg}")
        sys.exit(1)

    if not commented:
        _print_comment_reminder(variants)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "channels":
        # Diagnostic: confirm the secret reaches the runner, then list channels.
        # We print the KEY LENGTH (not the key) to tell "secret empty/missing" apart
        # from "Buffer rejects a present key". GitHub masks the value itself in logs.
        k = BUFFER_API_KEY or ""
        print(f"BUFFER_API_KEY present: {bool(k)} | length: {len(k)} | "
              f"starts with 'buf_': {k.startswith('buf_')} | "
              f"surrounding spaces: {k != k.strip()}")
        if not k:
            print("=> The BUFFER_API_KEY secret is EMPTY in this run.")
            sys.exit(1)
        # Probe, simplest first, to pinpoint EXACTLY what the key may/can't access.
        probes = [
            ("1. account.id          ", "query { account { id email } }"),
            ("2. organizations.id     ", "query { account { organizations { id name } } }"),
            ("3. organizations.channels", "query { account { organizations { id channels { id name service } } } }"),
        ]
        any_fail = False
        for label, q in probes:
            try:
                data = _graphql(q)
                print(f"OK   {label} -> {json.dumps(data)[:160]}")
            except Exception as e:
                any_fail = True
                print(f"FAIL {label} -> {e}")
        sys.exit(1 if any_fail else 0)
    else:
        main()
