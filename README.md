# Dispatch — a personal daily news digest

Free, self-hosted daily briefing: pulls yesterday's news per topic across four
geographic tiers (city → state → country → world), summarizes each with
Gemini's free tier, and serves it as a static site you can bookmark on any
device. No server to run or pay for — a GitHub Actions cron job does the
daily work and GitHub Pages serves the result.

## How it works

- `config.json` — your location and the topics you're tracking.
- `scripts/fetch_news.py` — for every topic × tier, builds a Google News RSS
  query, pulls the last day's headlines, and asks Gemini to write a short
  summary. Writes everything to `digest.json`.
- `.github/workflows/daily-digest.yml` — runs that script once a day (and
  on-demand) and commits the updated `digest.json`.
- `index.html` — a static page that reads `digest.json`. Each topic card has
  a zoom control (city/state/country/world) — your choice per topic is saved
  in your browser's local storage, so different devices can show different
  granularity if you want.

Nothing costs money: GitHub Pages, GitHub Actions (on a public repo), Google
News RSS, and Gemini's free API tier are all free at this kind of volume
(4 topics × 4 tiers ≈ 16 short summaries a day, well under Gemini's free
rate/quota limits as of mid-2026 — worth a quick check on
[ai.google.dev/pricing](https://ai.google.dev/pricing) if you add a lot more
topics).

## Setup

1. **Create a new GitHub repo** (public is easiest, since Actions minutes are
   unmetered there) and push these files to it.

2. **Get a free Gemini API key** at
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

3. **Add it as a repo secret**: repo → Settings → Secrets and variables →
   Actions → New repository secret → name it `GEMINI_API_KEY`, paste the key.

4. **Allow Actions to push commits**: repo → Settings → Actions → General →
   under "Workflow permissions," select "Read and write permissions." (The
   workflow commits the updated `digest.json` back to the repo each day.)

5. **Edit `config.json`** with your actual city, state, country, and the
   topics you want tracked. Topic names become the query terms, so plain
   words work best (e.g. `"Healthcare"`, not `"Healthcare news please"`).

6. **Enable GitHub Pages**: repo → Settings → Pages → Source: "Deploy from a
   branch" → Branch: `main`, folder: `/ (root)`.

7. **Run it once manually**: repo → Actions → "Daily News Digest" →
   "Run workflow." This generates the first `digest.json` so the site isn't
   empty. After that it runs automatically on the schedule in the workflow
   file (default ~7am IST daily — edit the `cron` line to suit you).

8. Visit `https://<your-username>.github.io/<repo-name>/` and bookmark it.

## Customizing

- **Change topics or your location** any time in `config.json` — takes
  effect on the next scheduled or manual run.
- **Change how far each topic zooms by default**: edit `DEFAULT_TIER` in
  `index.html`'s script, or just click the tier you want in each card —
  it's remembered per browser.
- **Change the schedule**: edit the `cron` line in
  `.github/workflows/daily-digest.yml`. Cron times are in UTC.
- **Add more geographic tiers or split "state" into "region"** etc. would
  need a small edit to `TIERS` in both `fetch_news.py` and `index.html` —
  ask if you want a hand with that.

## Known limitations

- Per-topic granularity narrower than "country" (e.g. per-neighborhood) isn't
  supported — Google News RSS doesn't reliably have that much local
  granularity for most cities.
- The zoom setting lives in browser local storage, so it's per-device, not
  synced. If you want one shared setting across all your devices, that would
  need a small backend (e.g. a free tier on Supabase or a GitHub Gist as a
  tiny settings store) — doable later if it turns out to matter.
- Free Gemini tiers can change quota limits over time; if summaries start
  failing, check your usage at [aistudio.google.com](https://aistudio.google.com).
