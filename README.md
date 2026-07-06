# meta-ai-blog-rss

Unofficial RSS feed for [ai.meta.com/blog](https://ai.meta.com/blog/), which offers no native feed.

A single Python script runs twice a day in GitHub Actions, scrapes the server-rendered blog index, keeps a memory of seen posts in `state.json`, and publishes `docs/feed.xml` via GitHub Pages.

## Setup (one time, ~5 minutes)

1. Create a new GitHub repository (public, or private with a paid plan for Pages) and push these files to `main`.
2. In the repo: **Settings → Pages → Source: Deploy from a branch → Branch: `main`, folder: `/docs`**. Save.
3. In the repo: **Settings → Secrets and variables → Actions → Variables** → add `FEED_SELF_URL` = `https://<username>.github.io/<repo>/feed.xml`. (Optional; only used for the feed's `atom:link rel="self"`.)
4. Go to the **Actions** tab, select "Update feed", press **Run workflow** to do the first run manually.
5. Subscribe your reader to `https://<username>.github.io/<repo>/feed.xml`.

The first run seeds `state.json` with every post currently visible on the index page and fetches each article once for its thumbnail and annotation. Subsequent runs are one HTTP request, plus one per newly published post (~1–3/month).

## Design notes — read before "fixing" anything

- **Parses server HTML, not the rendered page.** The live site is a React/BigPipe app; the raw HTML contains a static fallback (hero + curated grid) which is simpler and sufficient. No headless browser needed.
- **Anchored on URL patterns (`/blog/<slug>/`) and date text, never CSS classes.** Meta's class names (`_8xiz` etc.) are obfuscated and rotate; the URL scheme and visible dates are product-level invariants.
- **`state.json` is the source of truth, page order is not.** The index grid is *curated, not chronological* (verified: it interleaves 2019 posts among 2026 ones). The feed sorts by parsed date; the seen-set prevents old curated posts from reappearing as new.
- **Loud failure.** If fewer than 5 posts parse, the script exits non-zero, the Action fails, and GitHub emails you. This is deliberate: the alternative is a feed that silently goes stale. When it fires, Meta changed the markup — fix `parse_index()`.

## Known limitations

- **Thumbnails rot.** Post images are served from Meta's CDN with signed, expiring URLs (`oe=` parameter, ~60 days). Fresh items show images; old items eventually won't. Fixing this would require proxying/rehosting images — out of scope for a barebones setup.
- **Dates have day precision only.** The blog shows no timestamps; `pubDate` is set to 12:00 UTC of the visible date. Same-day posts are ordered by first-seen time.
- **Scheduled Actions pause** on repos with no commits for 60 days. The feed's own commits normally keep it alive, but during long quiet periods GitHub may email you to re-enable the workflow — one click.
- **ToS.** Scraping ai.meta.com is technically against Meta's terms of use. One page fetch twice a day for personal consumption is low-risk, but it is your risk.
- If Meta ever blocks GitHub's IP ranges, move the cron anywhere else — the script has no GitHub dependency (`python scraper.py` + commit/host the XML however you like).

## Local test

```
pip install requests beautifulsoup4
FIXTURE_DIR=fixtures python scraper.py   # offline, against saved HTML
python scraper.py                        # live run
```
