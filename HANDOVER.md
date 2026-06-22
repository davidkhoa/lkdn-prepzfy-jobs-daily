# HANDOVER — PREPZfy daily LinkedIn job-offers bot

> Brief for Claude Code. The repo owner (David, GitHub `davidkhoa`) is **non-technical**.
> Read this whole file + the repo, then propose a short plan before changing anything.

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
- **Hard rules**: NO em dashes (—). Capitalize firm names. No engagement bait. **NO link in the post body.**
  3–5 niche hashtags at the very bottom. Short and punchy. Default language English.
- The `jobs.prepzfy.com` link goes in the **first comment**, mentioned plainly.
- **Vary the hook** day to day (rotate 3–4 patterns) to avoid near-duplicate posts (LinkedIn penalises those).

## 8. Secrets / config
| Name | Status | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | set | Claude API (selection + copy) |
| `SHEET_CSV_URL` | set | the published Google Sheet CSV |
| `LOGODEV_TOKEN` | TBD (step 4, optional) | logo fetching, if logo.dev is chosen |
| `BUFFER_API_KEY` (or LinkedIn creds) | TBD (step 6) | publishing |

## 9. Gotchas
- Public repo: **never print or commit secret values**; read them from env only.
- CSV fetch needs a `User-Agent` header.
- Image must be at a **public URL** for publishing (step 6).
- Cron is **UTC**; mind the CET/CEST 1 h drift.
- **Skip posting gracefully** when nothing is worth posting.
- Keep `daily_post.py` as the single orchestrator.

## 10. Working agreement (owner is non-technical)
1. Read the repo + this file, then propose a **short plan** before editing.
2. **Explain each step in plain language** (what and why), not just code.
3. **STOP and ask** whenever a step needs something only the owner can do: create an account, authorize an app
   (LinkedIn / Buffer), add a secret, or click a manual toggle (e.g. Google "publish to web"). You cannot do these.
4. **Never expose, print, or commit secret keys.**
5. **Test before handing back**; if a run fails, explain the error in plain words and propose the fix.
6. **One step at a time**: confirm step 4 (image generated) works before step 5, etc.
