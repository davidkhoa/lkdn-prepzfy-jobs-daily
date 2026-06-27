"""
publish_make.py - sends the prepared daily post to a Make.com webhook, which then
publishes the image + caption to the PREPZfy LinkedIn Page. Replaces the flaky
Buffer beta API.

Flow:
  daily_post.py  -> writes post_payload.json (caption, image_url, variants) and
                    commits the card so image_url is a public raw.githubusercontent URL.
  (workflow)     -> commits/pushes the card image.
  publish_make.py-> POSTs the payload as JSON to MAKE_WEBHOOK_URL. The Make scenario
                    maps `caption` + `image_url` into its LinkedIn "Create an
                    Organization Post" module.

The first comment stays MANUAL (Buffer/Make/LinkedIn all make auto-commenting hard).
The variants are printed here so the owner can paste one by hand.

Safety: DRY_RUN=1 (or no MAKE_WEBHOOK_URL) -> prepare only, do NOT send.
"""

import os
import sys
import json
import urllib.request
import urllib.error

PAYLOAD_FILE = "post_payload.json"
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL") or ""
DRY_RUN = str(os.environ.get("DRY_RUN", "")).strip().lower() in ("1", "true", "yes", "on")


def _print_variants(variants):
    print("\n" + "=" * 60)
    print("ACTION: add the FIRST COMMENT by hand (paste ONE, ideally from your personal profile):")
    for i, v in enumerate(variants or [], 1):
        print(f"\n--- Variant {i} ---\n{v}")
    print("=" * 60)


def main():
    if not os.path.exists(PAYLOAD_FILE):
        print(f"No {PAYLOAD_FILE}; nothing to publish.")
        return
    with open(PAYLOAD_FILE, encoding="utf-8-sig") as f:
        payload = json.load(f)

    if payload.get("skip"):
        print("Payload skip=true (nothing worth posting). No publish.")
        return

    caption = payload.get("caption") or ""
    image_url = payload.get("image_url") or ""
    variants = payload.get("first_comment_variants") or []
    if not (caption and image_url):
        print("Payload missing caption or image_url; skipping.")
        return

    print(f"Image URL: {image_url}")
    if DRY_RUN:
        print("DRY_RUN=1 -> not sending to Make. Caption + image are ready above.")
        _print_variants(variants)
        return
    if not MAKE_WEBHOOK_URL:
        print("No MAKE_WEBHOOK_URL secret set yet -> prepare-only (not sent). "
              "Add the Make webhook URL as the MAKE_WEBHOOK_URL secret to enable auto-publish.")
        _print_variants(variants)
        return

    body = json.dumps({
        "caption": caption,
        "image_url": image_url,
        "date": payload.get("date", ""),
        "first_comment_variants": variants,
    }).encode("utf-8")
    req = urllib.request.Request(
        MAKE_WEBHOOK_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "prepzfy-bot"})
    try:
        resp = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        print(f"Make webhook HTTP {e.code}: {detail or e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"Make webhook failed: {e}")
        sys.exit(1)

    print(f"Sent to Make. Response: {resp[:200]}")
    _print_variants(variants)


if __name__ == "__main__":
    main()
