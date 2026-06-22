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
BUFFER_ENDPOINT = "https://api.buffer.com"
BUFFER_API_KEY = os.environ.get("BUFFER_API_KEY") or ""
BUFFER_CHANNEL_ID = os.environ.get("BUFFER_CHANNEL_ID") or ""
# Minutes from now to schedule the post (gives Buffer a moment to fetch the image).
BUFFER_DELAY_MIN = int(os.environ.get("BUFFER_DELAY_MIN", "5") or "5")


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


def create_post(channel_id, text, image_url, delay_min=BUFFER_DELAY_MIN):
    """Schedule the post (image + caption) on Buffer a few minutes from now."""
    due = (datetime.datetime.now(datetime.timezone.utc)
           + datetime.timedelta(minutes=delay_min)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    mutation = (
        "mutation($text: String!, $channelId: ChannelId!, $url: String!, $dueAt: DateTime!) {"
        "  createPost(input: {"
        "    text: $text, channelId: $channelId,"
        "    schedulingType: automatic, mode: customScheduled, dueAt: $dueAt,"
        "    assets: [{ image: { url: $url } }]"
        "  }) {"
        "    __typename"
        "    ... on PostActionSuccess { post { id status dueAt } }"
        "    ... on MutationError { message }"
        "  }"
        "}")
    data = _graphql(mutation, {
        "text": text, "channelId": channel_id, "url": image_url, "dueAt": due})
    return data.get("createPost") or {}


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

    channels = list_channels()
    linkedin = [c for c in channels if c["service"] == "linkedin"]
    print(f"Buffer connected channels: {len(channels)} "
          f"({len(linkedin)} LinkedIn).")
    for c in linkedin:
        print(f"  - LinkedIn channel: {c['name']} (id {c['id']})")

    channel = find_linkedin_channel(channels)
    if not channel:
        print("No LinkedIn channel connected in Buffer. Connect the PREPZfy Page "
              "in Buffer, then re-run.")
        return
    print(f"Target channel: {channel.get('name')} (id {channel.get('id')})")
    print(f"Image URL: {image_url}")

    if DRY_RUN:
        print("\nDRY_RUN=1 -> not posting. The above is what WOULD be published.")
        _print_comment_reminder(variants)
        _summary([
            "## Buffer (dry run)",
            f"- Would post to **{channel.get('name')}** (id `{channel.get('id')}`)",
            f"- Image: {image_url}",
            "- First comment NOT automated; paste a variant by hand.",
        ])
        return

    result = create_post(channel["id"], caption, image_url)
    if result.get("__typename") == "PostActionSuccess":
        post = result.get("post") or {}
        print(f"Posted to Buffer queue. Post id {post.get('id')} "
              f"status {post.get('status')} due {post.get('dueAt')}.")
        _summary([
            "## Buffer publish",
            f"- Scheduled on **{channel.get('name')}** (post `{post.get('id')}`, "
            f"due {post.get('dueAt')}).",
            "- REMEMBER: add the first comment by hand (a variant is in the run log).",
        ])
    else:
        msg = result.get("message") or json.dumps(result)
        print(f"Buffer did NOT accept the post: {msg}")
        sys.exit(1)

    _print_comment_reminder(variants)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "channels":
        # Diagnostic: just list channels (handy to confirm the LinkedIn connection).
        if not BUFFER_API_KEY:
            print("Set BUFFER_API_KEY first.")
            sys.exit(1)
        for c in list_channels():
            print(f"{c['service']:10} | {c['name']} | id {c['id']} "
                  f"| org {c['org_name']}")
    else:
        main()
