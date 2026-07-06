#!/usr/bin/env python3
"""Unofficial RSS feed generator for https://ai.meta.com/blog/.

Design principles (see README):
- Parse the SERVER-rendered HTML only (no JS, no GraphQL, no tokens).
- Anchor on stable invariants: the /blog/<slug>/ URL pattern and
  "Month DD, YYYY" date text. NEVER on CSS class names (they are
  obfuscated and rotate on every deploy).
- Keep state (state.json): page order is curated, not chronological,
  so items are sorted by parsed date, never by page position.
- Fail loudly: if parsing collapses, exit non-zero so the CI run is
  marked failed and notifies the owner, instead of publishing an
  empty/stale feed.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- config

BLOG_INDEX = "https://ai.meta.com/blog/"
STATE_FILE = Path(__file__).parent / "state.json"
FEED_FILE = Path(__file__).parent / "docs" / "feed.xml"

FEED_TITLE = "AI at Meta Blog (unofficial feed)"
FEED_DESC = "Unofficial RSS feed scraped from ai.meta.com/blog. Not affiliated with Meta."
# Set to your GitHub Pages URL after enabling Pages (used for atom:self).
FEED_SELF_URL = os.environ.get("FEED_SELF_URL", "https://example.github.io/meta-ai-blog-rss/feed.xml")

MAX_FEED_ITEMS = 50
MIN_EXPECTED_POSTS = 5          # sanity floor; index always shows more than this
REQUEST_TIMEOUT = 30
RETRIES = 3
# Must NOT claim to be Chrome: ai.meta.com returns 400 to Chrome-like UAs
# that don't send matching sec-ch-ua client-hint headers (requests never
# does). A generic Mozilla/compatible UA gets the full static fallback.
USER_AGENT = "Mozilla/5.0 (compatible; unofficial-rss-bot; personal use; low frequency)"

# Optional test hook: when FIXTURE_DIR is set, read local files instead of
# the network. index.html for the index; article.html for every article.
FIXTURE_DIR = os.environ.get("FIXTURE_DIR")

SLUG_RE = re.compile(r"^(?:https?://ai\.meta\.com)?/blog/([a-z0-9][a-z0-9-]*)/?$")
DATE_RE = re.compile(r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
GENERIC_ANCHOR_TEXT = {"", "featured", "learn more", "blog", "next", "prev", "read more"}

# ---------------------------------------------------------------- fetch


def fetch(url: str) -> str:
    if FIXTURE_DIR:
        name = "index.html" if url.rstrip("/").endswith("/blog") or url == BLOG_INDEX else "article.html"
        return (Path(FIXTURE_DIR) / name).read_text(encoding="utf-8")
    last_err = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001 - single retry loop, re-raised below
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


# ---------------------------------------------------------------- parsing


def parse_date(text: str):
    m = DATE_RE.search(text)
    if not m:
        return None
    raw = f"{m.group(1)} {int(m.group(2))} {m.group(3)}"
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def slug_of(href: str):
    m = SLUG_RE.match(href or "")
    return m.group(1) if m else None


def find_card(anchor, own_slug):
    """Smallest ancestor with a date and no OTHER post's link; prefer one
    that also carries a title heading. Live grid cards (July 2026) keep the
    date in a small footer div next to a generic "Learn More" anchor, with
    the h-title one or two levels up — the date-only ancestor is kept as a
    fallback for hero cards, which have no headings at all."""
    node = anchor
    fallback = None
    for _ in range(8):
        node = node.parent
        if node is None or node.name in ("body", "html"):
            break
        other = [
            s for a in node.find_all("a", href=True)
            if (s := slug_of(a["href"])) and s != own_slug
        ]
        if other:
            break  # grew past the card into a shared container
        if DATE_RE.search(node.get_text(" ", strip=True)):
            if node.find(["h3", "h4", "h5"]):
                return node
            fallback = fallback or node
    return fallback


def parse_index(html: str) -> dict:
    """Extract {slug: {title, date, categories, description}} from the index.

    Two independent extraction passes (hero anchors + grid cards) feed the
    same dict; the union is what matters. Titles: longest text wins.
    """
    soup = BeautifulSoup(html, "html.parser")
    posts: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        slug = slug_of(a["href"])
        if not slug:
            continue
        entry = posts.setdefault(
            slug, {"title": "", "date": None, "categories": [], "description": ""}
        )

        text = a.get_text(" ", strip=True)
        if text.lower() not in GENERIC_ANCHOR_TEXT and len(text) > len(entry["title"]):
            entry["title"] = text

        card = find_card(a, slug)
        if card is None:
            continue
        card_text = card.get_text(" ", strip=True)
        d = parse_date(card_text)
        if d and (entry["date"] is None or d > entry["date"]):
            entry["date"] = d

        headings = card.find_all(["h3", "h4", "h5"])
        if headings:
            texts = [h.get_text(" ", strip=True) for h in headings]
            texts = [t for t in texts if t]
            if texts:
                title = max(texts, key=len)
                if len(title) > len(entry["title"]):
                    entry["title"] = title
                for t in texts:
                    if t != title and len(t) <= 40 and t not in entry["categories"]:
                        entry["categories"].append(t)

        for p in card.find_all("p"):
            pt = p.get_text(" ", strip=True)
            if len(pt) > 60 and not DATE_RE.search(pt) and len(pt) > len(entry["description"]):
                entry["description"] = pt

    return {s: e for s, e in posts.items() if e["title"]}


def enrich_from_article(slug: str) -> dict:
    """One extra fetch per NEW post only: og:image + annotation fallback."""
    out = {"image": "", "annotation": ""}
    try:
        html = fetch(f"https://ai.meta.com/blog/{slug}/")
    except RuntimeError as e:
        print(f"  warn: could not enrich {slug}: {e}", file=sys.stderr)
        return out
    soup = BeautifulSoup(html, "html.parser")

    og_img = soup.find("meta", attrs={"property": "og:image"})
    if og_img and og_img.get("content"):
        out["image"] = og_img["content"]

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content", "").strip():
        out["annotation"] = og_desc["content"].strip()
    else:
        h1 = soup.find("h1")
        start = h1 if h1 else soup
        for p in start.find_all_next("p"):
            pt = p.get_text(" ", strip=True)
            if len(pt) > 100:
                out["annotation"] = pt[:400] + ("…" if len(pt) > 400 else "")
                break
    return out


# ---------------------------------------------------------------- state


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


# ---------------------------------------------------------------- feed


def render_feed(state: dict) -> str:
    def sort_key(item):
        slug, e = item
        return (e.get("date") or "0000-00-00", e.get("first_seen") or "", slug)

    items = sorted(state.items(), key=sort_key, reverse=True)[:MAX_FEED_ITEMS]
    now = format_datetime(datetime.now(timezone.utc))

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:media="http://search.yahoo.com/mrss/">',
        "<channel>",
        f"<title>{escape(FEED_TITLE)}</title>",
        f"<link>{escape(BLOG_INDEX)}</link>",
        f"<description>{escape(FEED_DESC)}</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        f'<atom:link href="{escape(FEED_SELF_URL)}" rel="self" type="application/rss+xml"/>',
    ]

    for slug, e in items:
        url = f"https://ai.meta.com/blog/{slug}/"
        if e.get("date"):
            d = datetime.strptime(e["date"], "%Y-%m-%d").replace(
                hour=12, tzinfo=timezone.utc
            )
            pub = format_datetime(d)
        else:
            pub = e.get("first_seen_rfc822", now)

        desc_html = ""
        if e.get("image"):
            desc_html += f'<img src="{escape(e["image"], {chr(34): "&quot;"})}"/><br/>'
        if e.get("annotation") or e.get("description"):
            desc_html += f"<p>{escape(e.get('annotation') or e.get('description'))}</p>"

        parts.append("<item>")
        parts.append(f"<title>{escape(e['title'])}</title>")
        parts.append(f"<link>{escape(url)}</link>")
        parts.append(f'<guid isPermaLink="true">{escape(url)}</guid>')
        parts.append(f"<pubDate>{pub}</pubDate>")
        for c in e.get("categories", []):
            parts.append(f"<category>{escape(c)}</category>")
        if desc_html:
            parts.append(f"<description>{escape(desc_html)}</description>")
        if e.get("image"):
            parts.append(
                f'<media:content url="{escape(e["image"], {chr(34): "&quot;"})}" medium="image"/>'
            )
        parts.append("</item>")

    parts += ["</channel>", "</rss>", ""]
    return "\n".join(parts)


# ---------------------------------------------------------------- main


def main() -> int:
    html = fetch(BLOG_INDEX)
    scraped = parse_index(html)
    print(f"parsed {len(scraped)} posts from index")

    # Loud-failure canary: a healthy index always yields well over this.
    if len(scraped) < MIN_EXPECTED_POSTS:
        print(
            f"FATAL: only {len(scraped)} posts parsed (< {MIN_EXPECTED_POSTS}). "
            "Meta likely changed the markup — refusing to publish.",
            file=sys.stderr,
        )
        return 1

    state = load_state()
    now_utc = datetime.now(timezone.utc)
    new_slugs = []

    for slug, e in scraped.items():
        if slug not in state:
            new_slugs.append(slug)
            extra = enrich_from_article(slug)
            state[slug] = {
                "title": e["title"],
                "date": e["date"].isoformat() if e["date"] else None,
                "categories": e["categories"],
                "description": e["description"],
                "image": extra["image"],
                "annotation": extra["annotation"],
                "first_seen": now_utc.isoformat(timespec="seconds"),
                "first_seen_rfc822": format_datetime(now_utc),
            }
            print(f"  new post: {slug} ({e['date']})")
        else:
            # Refresh mutable fields; never touch first_seen.
            state[slug]["title"] = e["title"] or state[slug]["title"]
            if e["date"]:
                state[slug]["date"] = e["date"].isoformat()
            if e["categories"]:
                state[slug]["categories"] = e["categories"]
            if e["description"] and not state[slug].get("description"):
                state[slug]["description"] = e["description"]

    save_state(state)
    FEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    FEED_FILE.write_text(render_feed(state), encoding="utf-8")
    print(f"feed written: {FEED_FILE} ({len(state)} known posts, {len(new_slugs)} new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
