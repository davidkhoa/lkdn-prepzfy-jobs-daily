"""
daily_thought.py - editorial LinkedIn post pipeline for PREPZfy.

Separate from the job-card pipeline (daily_post.py / publish.py). Reuses the Buffer
GraphQL transport from publish.py and the brand palette from image_card.py.

Flow per post:
  1) prepare <n>  -> pick a Pexels photo from the collection, crop 4:5, apply a light
                     navy filter, save to docs/posts/post-NN.png. Prints the local path.
  2) (caller commits + pushes the image so it gets a public raw.githubusercontent URL)
  3) schedule <n> <image_url> <due_iso> -> schedule the post on Buffer with the standard
                     first comment, at an absolute time.

Env: BUFFER_API_KEY, BUFFER_CHANNEL_ID, PEXELS_API_KEY, PEXELS_COLLECTION_ID.
"""
import io
import os
import re
import sys
import json
import datetime
import urllib.request
import urllib.error

from PIL import Image, ImageDraw

import image_card as ic
import publish  # reuse _graphql transport

POSTS_FILE = "knowledge/posts_bank.md"
PUBLIC_DIR = "docs/posts"
NAVY, NAVY_DEEP = ic.NAVY, ic.NAVY_DEEP

FIRST_COMMENT = ("\U0001F4C8 Fresh IBD and Strategy opportunities, "
                 "updated every single day → jobs.prepzfy.com")

PEXELS_COLLECTION_ID = os.environ.get("PEXELS_COLLECTION_ID") or "mvqwc9r"
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY") or ""


def parse_posts(path=POSTS_FILE):
    """Return list of dicts: {n, archetype, title, body}."""
    txt = open(path, encoding="utf-8").read()
    blocks = re.split(r"\n## (\d+)\. ", txt)
    posts = []
    # blocks[0] is the header; then pairs of (number, rest)
    for i in range(1, len(blocks), 2):
        n = int(blocks[i])
        rest = blocks[i + 1]
        head, _, body = rest.partition("\n")
        m = re.match(r"\[(\w+)\]\s*(.*)", head.strip())
        archetype = m.group(1) if m else ""
        title = m.group(2) if m else head.strip()
        body = body.split("\n---", 1)[0].strip()
        posts.append({"n": n, "archetype": archetype, "title": title, "body": body})
    return posts


def get_post(n):
    for p in parse_posts():
        if p["n"] == n:
            return p
    raise SystemExit(f"Post #{n} not found in {POSTS_FILE}")


def pexels_photos():
    url = (f"https://api.pexels.com/v1/collections/{PEXELS_COLLECTION_ID}"
           "?per_page=80&type=photos")
    req = urllib.request.Request(url, headers={"Authorization": PEXELS_API_KEY,
                                               "User-Agent": "prepzfy-bot"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return [m for m in data.get("media", []) if m.get("type") == "Photo"]


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "prepzfy-bot"})
    return Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=30).read())).convert("RGB")


def _crop_resize(img, w, h):
    tw = w / h
    if img.width / img.height > tw:        # too wide -> crop sides
        nw = int(img.height * tw)
        x = (img.width - nw) // 2
        img = img.crop((x, 0, x + nw, img.height))
    else:                                  # too tall -> crop top/bottom
        nh = int(img.width / tw)
        y = (img.height - nh) // 2
        img = img.crop((0, y, img.width, y + nh))
    return img.resize((w, h), Image.LANCZOS)


def _navy_filter(img, strength=0.28):
    tint = Image.new("RGB", img.size, NAVY)
    out = Image.blend(img, tint, strength)
    dark = Image.new("RGB", img.size, NAVY_DEEP)
    m = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(m)
    for yy in range(img.height):
        md.line([0, yy, img.width, yy],
                fill=int(110 * max(0, (yy - img.height * 0.6) / (img.height * 0.4))))
    return Image.composite(dark, out, m)


def prepare(n):
    photos = pexels_photos()
    if not photos:
        raise SystemExit("No photos in the Pexels collection.")
    photo = photos[(n - 1) % len(photos)]               # deterministic per post
    src = photo["src"].get("large2x") or photo["src"].get("large")
    img = _navy_filter(_crop_resize(_fetch(src), 1080, 1350))
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    out = f"{PUBLIC_DIR}/post-{n:02d}.png"
    img.save(out, "PNG")
    print(out)
    return out


def _create(post, image_url, due_iso):
    channel_id = os.environ["BUFFER_CHANNEL_ID"]
    mutation = (
        "mutation($text:String!, $channelId:ChannelId!, $url:String!, "
        "$dueAt:DateTime!, $fc:String!) {"
        "  createPost(input: {"
        "    text:$text, channelId:$channelId,"
        "    schedulingType: automatic, mode: customScheduled, dueAt:$dueAt,"
        "    assets: [{ image: { url:$url } }],"
        "    metadata: { linkedin: { firstComment: $fc } }"
        "  }) {"
        "    __typename"
        "    ... on PostActionSuccess { post { id status dueAt } }"
        "    ... on MutationError { message }"
        "  }"
        "}")
    variables = {"text": post["body"], "channelId": channel_id,
                 "url": image_url, "dueAt": due_iso, "fc": FIRST_COMMENT}
    data = publish._graphql(mutation, variables)
    return data.get("createPost") or {}


def schedule(n, image_url, due_iso):
    post = get_post(n)
    res = _create(post, image_url, due_iso)
    if res.get("__typename") == "PostActionSuccess":
        p = res.get("post") or {}
        print(f"OK scheduled post #{n} [{post['archetype']}] '{post['title']}' "
              f"-> Buffer id {p.get('id')} status {p.get('status')} due {p.get('dueAt')}")
    else:
        print(f"FAILED post #{n}: {res.get('message') or json.dumps(res)}")
        sys.exit(1)


def delete_post(post_id):
    mutation = (
        "mutation($id:PostId!) { deletePost(input:{id:$id}) {"
        "  __typename ... on MutationError { message } } }")
    data = publish._graphql(mutation, {"id": post_id})
    print("deletePost:", json.dumps(data.get("deletePost")))


def prepare_all():
    out = []
    for p in parse_posts():
        out.append(prepare(p["n"]))
    return out


def _weekday_slots(count):
    """The next `count` weekday slots at 06:15 UTC (08:15 Paris CEST), future only."""
    now = datetime.datetime.now(datetime.timezone.utc)
    d = now.replace(hour=6, minute=15, second=0, microsecond=0)
    if d <= now:
        d += datetime.timedelta(days=1)
    slots = []
    while len(slots) < count:
        if d.weekday() < 5:
            slots.append(d)
        d += datetime.timedelta(days=1)
    return slots


def schedule_all(sha):
    posts = parse_posts()
    slots = _weekday_slots(len(posts))
    base = ("https://raw.githubusercontent.com/davidkhoa/"
            "lkdn-prepzfy-jobs-daily/%s/docs/posts/post-%02d.png")
    rows = []
    for post, slot in zip(posts, slots):
        url = base % (sha, post["n"])
        due = slot.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            res = _create(post, url, due)
            if res.get("__typename") == "PostActionSuccess":
                pid = (res.get("post") or {}).get("id")
                print(f"OK #{post['n']:>2} [{post['archetype']:>9}] {slot:%a %d %b} "
                      f"-> {pid}")
                rows.append((post, slot, pid))
            else:
                print(f"FAIL #{post['n']}: {res.get('message') or json.dumps(res)}")
        except Exception as e:
            print(f"ERROR #{post['n']}: {e}")
    print(f"\nScheduled {len(rows)}/{len(posts)} posts.")
    return rows


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "prepare":
        prepare(int(sys.argv[2]))
    elif cmd == "prepare_all":
        prepare_all()
    elif cmd == "schedule":
        schedule(int(sys.argv[2]), sys.argv[3], sys.argv[4])
    elif cmd == "schedule_all":
        schedule_all(sys.argv[2])
    elif cmd == "delete":
        delete_post(sys.argv[2])
    elif cmd == "parse":
        for p in parse_posts():
            print(p["n"], p["archetype"], "|", p["title"], "|", len(p["body"]), "chars")
    else:
        print("usage: daily_thought.py [prepare N | schedule N URL DUE_ISO | parse]")
