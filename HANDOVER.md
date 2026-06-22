# HANDOVER — PREPZfy daily LinkedIn job-offers bot

> Brief for Claude Code. The repo owner (David, GitHub `davidkhoa`) is **non-technical**.
> Read this whole file + the repo, then propose a short plan before changing anything.
> Work in French, explain plainly, one step at a time, STOP for owner-only actions
> (accounts, secrets, app authorizations), never print/commit secrets.

## STATUS — updated 2026-06-22 (read this first)
**Done:** Steps 1-5 + a full image overhaul. Latest commit on `main`: see git log
(the image work is `3f76157`).
- **Step 5 (schedule) DONE:** `daily.yml` runs **every day** at `cron: "0 5 * * *"`
  (~07:00 Paris) AND keeps the manual `workflow_dispatch` button. Search + image run
  daily; the owner handles posting timing himself. Graceful skip already coded.
- **Image (`image_card.py`) OVERHAULED & on main:**
  - Logos cascade: **Brandfetch Brand API** (secret `BRANDFETCH_API_KEY`, server-side,
    picks a square symbol / light-theme transparent mark) -> **logo.dev** (PUBLIC token
    `pk_...` hardcoded, safe) -> Google favicon -> deterministic initials tile.
    Brandfetch's **CDN "Logo Link" was dropped** (browser-only; returns HTML to a bot).
  - Smart rendering: reject broken/near-empty images; trim transparent margins; darken
    light marks; full-bleed brand icons fill the tile edge-to-edge (no stray black box).
  - Typography: tight letter-spacing ("One access" signature) on wordmark + titles;
    role titles shrink then **wrap to 2 lines** instead of cutting with "...".
  - Windows font paths added for LOCAL previews (Linux/CI paths untouched).
- **Liquid-glass:** the RAINBOW version was rejected. A **subtle navy glass** on the
  row tiles was then added on owner request (`glass_panel()` in `image_card.py`):
  monochrome top-to-bottom lightening + a thin white top sheen. Keep it subtle; NO rainbow.

**Owner adjustments round 2 (2026-06-22), all DONE:**
- **City only** on the card: sub-line shows `Company · City` (e.g. "Lyon"), never the
  region/country. Uses the sheet `city` column, falling back to the first segment of
  `location`. See `display_city()` in `image_card.py`.
- **Attractiveness gate:** offers are pre-filtered by `prefilter_known()` so only
  companies that HAVE a Brandfetch logo reach Claude (owner's rule: no logo ~= not
  notable enough). Bounded API calls (`LOGO_PREFILTER_*`). Claude then picks the most
  prestigious/relevant ones and may re-feature recent (yesterday/day-before) offers.
  NOTE: the filter only runs in CI (needs `BRANDFETCH_API_KEY`); locally it keeps all.
- **Premium footer:** "jobs.prepzfy.com" (bright ink) + blue dot + tracked
  "UPDATED EVERY DAY" small-caps tag.
- **Caption:** NO `jobs.prepzfy.com` link in the body, NO hashtags at all (the board
  link lives only in the first comment). LinkedIn job links MAY still appear inline.
- **First comment = main CTA, multiple variants:** Claude returns
  `first_comment_variants` (`VARIANT_COUNT`=4); all are printed in the run summary for
  the owner to choose. Backward compatible with the old single `first_comment`.

**Pending validation:** the owner kept clicking GitHub **"Re-run"** (which replays the OLD
commit `aaf8358` = old layout) instead of **"Run workflow"** on `main`. So the NEW card
(Brandfetch logos on white tiles for JPMorgan/Lazard) has **not been seen on a real run yet**.
Next: trigger a fresh **Run workflow** on `main`, confirm the run shows commit `3f76157`,
and check the `daily-card` artifact.

**Step 6 (publishing) BUILT (2026-06-22):**
- 6(a) public image URL DONE: the card is rendered to `docs/cards/<date>.png`, the
  workflow commits it, and `publish.py` uses its `raw.githubusercontent.com` URL.
- 6(b) Buffer DONE in code (`publish.py`): GraphQL on `https://api.buffer.com`,
  Bearer `BUFFER_API_KEY`. Auto-detects the LinkedIn channel (`BUFFER_CHANNEL_ID`
  optional pin), schedules image + caption via `createPost` (customScheduled, now+5min).
- **Buffer API CANNOT post comments** -> the first comment is NOT automated. `publish.py`
  prints the variants; the OWNER pastes one by hand (ideally from his personal profile).
- **Safety:** manual "Run workflow" defaults to `dry_run=true` (prepares + commits the
  image, prints the target channel, but does NOT post). Scheduled runs publish.
- daily_post.py writes `post_payload.json` (caption + variants + image_url); publish.py reads it.
- **VALIDATED 2026-06-22:** `BUFFER_API_KEY` secret is set; the PREPZfy Page `prepz-fy`
  (id `6a39468f5ab6d2f1065c965c`) is connected and auto-detected; a real test post was
  accepted by Buffer and scheduled. Fixes that got it working: assets use the `@oneOf`
  shape `assets:[{image:{url}}]`, and the GraphQL vars are typed `ChannelId!`/`DateTime`.
- A throwaway `buffer_test.yml` (Buffer-only, no Anthropic cost) was used to debug and
  has since been removed.

**Company tagging on LinkedIn (built 2026-06-22):** Buffer DOES support LinkedIn org
mentions via `metadata.linkedin.annotations` (`AnnotationInputLinkedIn`).
- `li_companies.json` maps a sheet `domain` -> `{id, vanity, name, link}`. Only entries
  with a non-empty NUMERIC `id` are used; others leave the name as plain text.
- `daily_post.py` passes the selected `companies` (company+domain) in `post_payload.json`;
  `publish.py` (`build_annotations`) finds each taggable company's name in the caption and
  builds the annotation (start/length = char offset of the name). `publish_with_fallback`
  posts WITH tags, and if Buffer/LinkedIn rejects them, retries WITHOUT so the post still
  goes out. Every run logs tag coverage (taggable now vs missing an entry).
- **To enable a tag, fill the `id`** = the firm's numeric LinkedIn org id. How to find it:
  open the company's LinkedIn page, View Page Source (Ctrl+U), Ctrl+F `urn:li:organization:`
  -> the number after it is the id. Verify `vanity`/`link` while you're there.
- Caveat: char offsets assume few/no emojis before a tag (caption is kept emoji-sparse).

## 0. One-line goal
Every morning, with the owner's PC off, automatically: read a Google Sheet of finance job
offers → pick the 5 most attractive → generate a branded image → post to the PREPZfy / LINKFIN
LinkedIn **page** with a first comment linking to `jobs.prepzfy.com`.

## 1. Context
- **PREPZfy**: coaching brand for French *grandes écoles* students (HEC, ESSEC, SKEMA, EM Lyon,
  EDHEC) targeting **M&A, Private Equity, markets, MBB consulting** careers. Sister brand **LINKFIN**
  (finance newsletter + job board). `jobs.prepzfy.com` is the job board.
- The whole point is **cloud automation**: it must run on GitHub Actions, never depending on a PC.

## 2. Current state (DONE — steps 1–3)
- **Repo**: `github.com/davidkhoa/lkdn-prepzfy-jobs-daily` (PUBLIC, chosen so Actions are free/unlimited
  and the daily image can be hosted via a raw URL; secrets live in GitHub's encrypted vault, never in code).
- **Secrets already set**: `ANTHROPIC_API_KEY`, `SHEET_CSV_URL`.
- **Files** (see the attached files; add to repo if not yet committed):
  - `daily_post.py` — the **brain**: downloads the CSV, asks Claude to pick 5 offers + write a caption
    + a first comment, prints the result to the Actions run summary. Works for steps 1–3.
  - `.github/workflows/daily.yml` — runs the brain on a **manual button** (`workflow_dispatch`).
- **Validated image design**: `image_card_reference.py` (1080×1350 branded card in the exact PREPZfy
  identity). It currently uses **hardcoded demo offers**; it must be adapted (step 4).

## 3. How it runs
- GitHub Actions (cloud). Model `claude-sonnet-4-6` (changeable). Output language **English**
  (`LANGUAGE="en"`, switchable to `"fr"`).
- Pipeline order: **read sheet → select (Claude) → make image → publish**. Build the rest by ADDING
  steps to the same `daily_post.py` (image and publish can be separate modules it imports).

## 4. Data — the Google Sheet
- A Google Sheet **"published to web" as CSV**; URL is in secret `SHEET_CSV_URL`. It points to the
  cleaned **`Public`** tab (personal columns like `shared_by` / `raw_message` are intentionally excluded).
- CSV header columns:
  `date_added, company, domain, role, location, city, country, type, duration, sector, link, status, date_published`
- `domain` = company domain (e.g. `goldmansachs.com`) → use for logos. `sector` ∈ {IBD, PE, TS, Consulting}.
  `type` ∈ {Internship/Stage, Full-time, Apprenticeship/Alternance, VIE, …}. Some rows have an empty `link` → skip them.
- ~100 offers typical. **Fetch with a normal `User-Agent` header** (works from Actions; the robots block only affects preview crawlers).

## 5. TODO — build these, in order

### Step 4 — Image generation  ← "la problématique image"
Adapt `image_card_reference.py` so that, **after Claude picks the 5 offers**, the bot draws the card for
THOSE offers and returns a PNG.
- Input: the 5 selected offers (`company, role, location, type, sector, domain, link`).
- Output: a **1080×1350 PNG**. Labels in **English** ("OFFERS OF THE DAY", the date, "+ N more offers on jobs.prepzfy.com").
- Dynamic: today's date; the "+ N more" count = total offers minus the 5 shown.
- **LOGOS** (the key open point): fetch each company's real logo via `domain`, with a clean **fallback to the
  gradient monogram (initials)** when the logo is missing or fails. Logo source options (explain trade-offs, let the owner choose):
  - Google favicon `https://www.google.com/s2/favicons?domain={domain}&sz=128` — no key, lower resolution.
  - **logo.dev** — better quality, needs a free API token → new secret `LOGODEV_TOKEN`.
  - the sheet's `logo_url` column, if ever populated (usually empty today).
  Default recommendation: logo.dev (or favicon) **with monogram fallback**.
- Keep the validated DESIGN (tokens + layout in §6). Use **Pillow** (already proven; no headless browser needed — lighter for CI than Playwright).

### Step 5 — Schedule
- Replace `on: workflow_dispatch` with a daily cron, target **~07:00 Europe/Paris**. GitHub cron is **UTC**:
  use `cron: "0 5 * * *"` (≈07:00 Paris in summer / 06:00 in winter — 1 h DST drift, acceptable, or handle DST in code).
  Keep `workflow_dispatch` as well for manual runs.
- Add a **graceful skip**: if nothing clears the quality bar, do NOT post; log and exit cleanly.

### Step 6 — Publish to LinkedIn  (NEEDS an owner decision + a new account)
Post the image + caption to the LinkedIn **Page**, then add the first comment (the `jobs.prepzfy.com` link).
Two requirements:
1. **Public image URL**: most publishers fetch the image from a URL. Options: commit the PNG to a public path
   in this repo and use its `raw.githubusercontent.com` URL, **or** Cloudinary (free).
2. **A publishing route** (the owner must create the account + you add a secret):
   - **Buffer** (beta personal-key GraphQL API): can post to a LinkedIn Page + schedule a first comment; image
     via public URL only; ~$5–6/mo if first-comment needs a paid plan; Buffer holds the LinkedIn auth (reconnect ~every 60 days).
   - **LinkedIn native API** (Posts API + comment): free, full control, but requires a LinkedIn developer app + approval + OAuth token management.
   - **Ayrshare / unified API**: clean REST, paid.
   **Present these to the owner; do NOT sign up or pick on their behalf.** Once chosen, add the secret (e.g. `BUFFER_API_KEY`) and wire it.

## 6. Brand / design tokens (from prepzfy.com — already validated by the owner)
- **Wordmark**: `PREPZ` (Arial bold, ink) + `fy` (Times New Roman *italic*, blue). This signature is mandatory.
- **Colors**: navy `#0A1628` (bg), navy-deep `#060d1a`, navy-card `#0d1a2f`; blue `#2563C4` (primary),
  blue-bright `#3b82f6`; ink `#E8ECF3` (text), ink-soft `#c0c9d6`, ink-dim `#8A97AC`, ink-dimmer `#5a6578`;
  line `rgba(255,255,255,0.08)`.
- **Monogram gradients** (for the fallback initials, one per row, cycling): blue `#2563C4→#1a3d7a`;
  teal `#0d9488→#134e4a`; purple `#7c3aed→#4c1d95`; amber `#b45309→#78350f`; rose `#be123c→#831843`.
- **Layout (1080×1350)**: thin blue top line; header (wordmark left, "OFFERS OF THE DAY" sublabel under it,
  date + "M&A · Private Equity · Consulting" right-aligned); 5 rows (88px circular logo/monogram, role in bold,
  "Company · Location" muted, sector tag pill on the right); footer ("+ N more offers" in blue, "on jobs.prepzfy.com",
  "LINKFIN × PREPZFY" right). Exact coordinates: see `image_card_reference.py`.

## 7. Copy voice (caption + first comment)
- Voice: a generous senior who did the internship and now explains it plainly. Confident, never arrogant. No clickbait, no hype.
- **Hard rules**: NO em dashes (—). Capitalize firm names. No engagement bait.
  **NO `jobs.prepzfy.com` link in the post body. NO hashtags at all.** Short and punchy.
  Default language English. (LinkedIn job links MAY appear inline; they stay on-platform.)
- The `jobs.prepzfy.com` link goes ONLY in the **first comment** (the main CTA).
- **First comment = several variants** (`first_comment_variants`), same generous spirit
  ("The full board is live at...", "Follow us for more offers..."); owner picks one.

## 8. Secrets / config
| Name | Status | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | set | Claude API (selection + copy) |
| `SHEET_CSV_URL` | set | the published Google Sheet CSV |
| `LOGODEV_TOKEN` | TBD (step 4, optional) | logo fetching, if logo.dev is chosen |
| `BUFFER_API_KEY` | TBD (owner adds) | Buffer GraphQL publishing (image + caption) |
| `BUFFER_CHANNEL_ID` (repo *variable*, optional) | optional | pin the LinkedIn channel; else auto-detected |

## 9. Gotchas
- Public repo: **never print or commit secret values**; read them from env only.
  (Exception already in code: the logo.dev **publishable** `pk_` token is public/safe.)
- **GitHub "Re-run" replays the OLD commit** of that run. To test new code, always use
  **"Run workflow"** on `main` (Actions -> workflow name -> "Run workflow" button).
- **Logos are server-side:** use Brandfetch **Brand API** (Bearer secret), NOT the CDN
  "Logo Link" (`cdn.brandfetch.io/.../symbol?c=...`) which is browser-only and returns HTML.
- logo.dev ignores `theme`/`greyscale` on the free plan (returns the brand's own icon).
- CSV fetch needs a `User-Agent` header.
- Image must be at a **public URL** for publishing (step 6).
- Cron is **UTC**; mind the CET/CEST 1 h drift.
- **Skip posting gracefully** when nothing is worth posting.
- Keep `daily_post.py` as the single orchestrator.
- Local image previews on Windows: `python image_card.py` writes `card_preview.png`
  (gitignored). Brandfetch needs the secret -> locally it falls back to logo.dev.

## 10. Working agreement (owner is non-technical)
1. Read the repo + this file, then propose a **short plan** before editing.
2. **Explain each step in plain language** (what and why), not just code.
3. **STOP and ask** whenever a step needs something only the owner can do: create an account, authorize an app
   (LinkedIn / Buffer), add a secret, or click a manual toggle (e.g. Google "publish to web"). You cannot do these.
4. **Never expose, print, or commit secret keys.**
5. **Test before handing back**; if a run fails, explain the error in plain words and propose the fix.
6. **One step at a time**: confirm step 4 (image generated) works before step 5, etc.
