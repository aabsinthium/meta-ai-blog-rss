# CLAUDE.md — project context handoff

Unofficial RSS feed generator for https://ai.meta.com/blog/ (which has no native
feed). Built July 2026 in a Cowork session after live investigation of the site;
this file transfers that context. Read README.md for setup/ops; this file holds
the findings that justify the design — do not "simplify" the code without them.

## Verified facts about ai.meta.com/blog (July 2026)

- No native RSS. `/blog/rss`, `/blog/feed` — nothing. No `<link rel=alternate>`.
  Meta pushes a newsletter (`/subscribe`) instead.
- The site is a React/BigPipe app. **The server-rendered HTML is RICHER than the
  hydrated DOM** (unusual): plain HTTP GET returns a static fallback with a hero
  ("Latest News", the 4 newest posts + FEATURED pins) and a "Blog Posts" grid
  with working `?page=N` pagination. No headless browser needed.
- **The fallback grid is curated, NOT chronological.** Verified: page 2 mixed
  Dec 2025 → Oct 2025 → May 2019 → Aug 2024. Never trust page order; sort by
  parsed dates and keep a seen-set (state.json). New posts always enter via the
  hero on page 1, so scraping only page 1 is sufficient.
- The client-side "More from AI at Meta" infinite feed IS chronological and is
  fed by `POST https://ai.meta.com/api/graphql/` (batches of 4, ~3.4KB JSON,
  requires session tokens scraped from the page). Deliberately NOT used: tokens
  rotate, more fragile, deeper ToS exposure. It's the fallback data source if
  the HTML approach ever dies.
- CSS class names (`_8xiz`, `_amcw`, ...) are obfuscated and rotate per deploy.
  The parser anchors on the `/blog/<slug>/` URL pattern and `Month DD, YYYY`
  date text only. Keep it that way.
- Grid cards in server HTML contain NO images. Thumbnails/annotations come from
  each article page's `og:image` / `og:description` (fetched once per new post,
  cached in state.json). `og:description` is often EMPTY → fallback is the first
  substantial <p> after the H1.
- Meta CDN image URLs are signed and expire (`oe=` param, ~60 days). Old feed
  items lose their thumbnails. Accepted limitation; fix would require rehosting.
- Date formats seen in the wild: "June 29, 2026", "Jun 29, 2026", "April 08,
  2026" (zero-padded). Parser handles %B and %b, and int-normalizes the day.
- Third-party generators evaluated and rejected: OpenRSS returns an HTML article
  preview, not a feed (verified: zero <item> elements). RSS.app et al. work but
  cost money for what one cron job does, and JS-rendering services see LESS
  content than plain HTTP here.

## Architecture (deliberately barebones)

scraper.py (single file, deps: requests + beautifulsoup4)
  fetch index → parse_index() → merge into state.json → render docs/feed.xml
GitHub Actions cron (2×/day, .github/workflows/feed.yml) commits changes;
GitHub Pages serves docs/feed.xml. Blog cadence is ~1–3 posts/month — do not
add faster polling, caching layers, databases, or a web framework.

Loud-failure canary: < 5 posts parsed → exit 1 → Action fails → owner gets
email. This is the markup-drift alarm; never remove it or swallow the error.

## Status at handoff

- Tested offline against fixtures/ (synthetic but structure-faithful — browser
  security filters blocked exporting real HTML byte-for-byte). All green:
  parse, date sort (2025 curated outlier sinks), idempotency, canary exit 1.
- NOT yet run end-to-end against the live site. The first manual workflow run
  (Actions → Update feed → Run workflow) is the real e2e test.
- state.json is intentionally empty ({}); docs/feed.xml is a placeholder.
- Setup steps for the human: README.md "Setup" section (repo, Pages on
  main:/docs, optional FEED_SELF_URL Actions variable, first manual run).

## If the parser breaks someday

1. Check whether `curl -A "Mozilla/5.0" https://ai.meta.com/blog/` still returns
   the static fallback with `/blog/<slug>/` anchors and date strings.
2. If yes → adjust parse_index() heuristics (card = smallest ancestor with a
   date and no other post's link; title = longest h3/h4/h5; category = short
   sibling headings; description = longest non-date <p>).
3. If the fallback is gone entirely → the GraphQL endpoint above, or a headless
   fetch of the chronological "More from AI at Meta" section, are the fallbacks.
