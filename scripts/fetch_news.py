"""
Fetches yesterday's news for each (topic x geographic tier) combination and
summarizes each topic with a SINGLE Gemini call covering all four tiers at
once (not one call per tier) to stay well within free-tier RPD/RPM limits.
Writes the result to digest.json at the repo root, which the static
frontend (index.html) reads directly.

Runs once a day via .github/workflows/daily-digest.yml. Requires a
GEMINI_API_KEY secret (free tier: https://aistudio.google.com/apikey).
"""

import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone

import feedparser
import requests

CONFIG_PATH = "config.json"
OUTPUT_PATH = "digest.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Flash-Lite is Google's recommended model for high-volume, low-complexity
# tasks like this one, and typically carries a more generous free quota than
# the flagship Flash model. Override with the GEMINI_MODEL env var if needed.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

TIERS = ["city", "state", "country", "world"]
MAX_ARTICLES_PER_CELL = 8
REQUEST_TIMEOUT = 30

# Pacing between Gemini calls. Check the RPM row for your chosen model at
# https://aistudio.google.com/usage and adjust if needed (default assumes a
# conservative 5 RPM -> ~13s between calls with margin).
MIN_CALL_INTERVAL_SECONDS = float(os.environ.get("GEMINI_MIN_INTERVAL_SECONDS", "13"))
MAX_RETRIES = 4

_last_call_monotonic = 0.0


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_query(topic, tier, location):
    if tier == "city":
        return f'{topic} "{location["city"]}" when:1d'
    if tier == "state":
        return f'{topic} "{location["state"]}" when:1d'
    if tier == "country":
        return f'{topic} {location["country"]} when:1d'
    return f"{topic} when:1d"  # world


def fetch_articles(query, max_items=MAX_ARTICLES_PER_CELL):
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    feed = feedparser.parse(url)
    articles = []
    seen_titles = set()
    for entry in feed.entries:
        title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())

        source = ""
        src = entry.get("source")
        if src is not None:
            source = getattr(src, "title", "") or (
                src.get("title", "") if isinstance(src, dict) else ""
            )

        articles.append(
            {
                "title": title,
                "source": source,
                "link": entry.get("link", ""),
            }
        )
        if len(articles) >= max_items:
            break
    return articles


def location_label(tier, location):
    return {
        "city": location["city"],
        "state": location["state"],
        "country": location["country"],
        "world": "the world",
    }[tier]


def _wait_for_rate_limit():
    global _last_call_monotonic
    elapsed = time.monotonic() - _last_call_monotonic
    if elapsed < MIN_CALL_INTERVAL_SECONDS:
        time.sleep(MIN_CALL_INTERVAL_SECONDS - elapsed)
    _last_call_monotonic = time.monotonic()


def _call_gemini(body, headers, context_label):
    backoff = 5
    last_status = "unknown"
    for attempt in range(1, MAX_RETRIES + 1):
        _wait_for_rate_limit()
        try:
            resp = requests.post(GEMINI_URL, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            print(f"[warn] {context_label}: network error on attempt {attempt}: {type(exc).__name__}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue

        if resp.status_code in (429, 503):
            last_status = resp.status_code
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else backoff
            print(f"[warn] {context_label}: status={resp.status_code}, retrying in {wait:.0f}s "
                  f"(attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff = min(backoff * 2, 60)
            continue

        if not resp.ok:
            print(f"[warn] {context_label}: status={resp.status_code}, giving up")
            return None

        return resp.json()

    print(f"[warn] {context_label}: exhausted retries, last status={last_status}")
    return None


def summarize_topic(topic, tier_articles, location):
    """One Gemini call covering all four tiers for this topic."""
    any_articles = any(tier_articles[t] for t in TIERS)
    if not any_articles:
        return {t: "No notable stories turned up for this topic and region in the last day." for t in TIERS}

    if not GEMINI_API_KEY:
        return {t: "Gemini API key not set — see README to add GEMINI_API_KEY." for t in TIERS}

    sections = []
    for tier in TIERS:
        label = location_label(tier, location)
        arts = tier_articles[tier]
        bullets = (
            "\n".join(f"  - {a['title']} ({a['source']})" for a in arts)
            if arts else "  (no headlines found)"
        )
        sections.append(f"### {tier} — {label}\n{bullets}")
    joined = "\n\n".join(sections)

    prompt = (
        "You are writing four short sections of a personal daily news briefing, "
        "one per geographic zoom level, all on the same topic.\n"
        f"Topic: {topic}\n\n"
        "For each zoom level below, write a tight 3-5 sentence summary of what "
        "actually happened in the last 24 hours, grouping related stories rather "
        "than restating each headline in turn. Be neutral and factual. If a level "
        "has no headlines, or they're thin or unrelated to each other, say so "
        "briefly instead of padding.\n\n"
        f"{joined}\n\n"
        'Respond with ONLY a JSON object shaped exactly like this, no markdown '
        'fences, no extra commentary: '
        '{"city": "...", "state": "...", "country": "...", "world": "..."}'
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 800,
            "thinkingConfig": {"thinkingLevel": "low"},
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,  # header, not URL — never gets logged/leaked
    }

    data = _call_gemini(body, headers, context_label=f"topic={topic!r}")
    if data is None:
        return {t: "(Summary unavailable right now — check the Action logs for details.)" for t in TIERS}

    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip accidental markdown fences just in case.
        raw_text = re.sub(r"^```(?:json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw_text)
        return {t: parsed.get(t, "(No summary returned for this tier.)") for t in TIERS}
    except Exception as exc:
        print(f"[warn] topic={topic!r}: failed to parse Gemini response: {type(exc).__name__}")
        return {t: "(Summary unavailable — response could not be parsed.)" for t in TIERS}


def main():
    config = load_config()
    location = config["location"]
    topics = config["topics"]

    digest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "location": location,
        "topics": {},
    }

    for topic in topics:
        tier_articles = {}
        for tier in TIERS:
            query = build_query(topic, tier, location)
            tier_articles[tier] = fetch_articles(query)

        summaries = summarize_topic(topic, tier_articles, location)

        digest["topics"][topic] = {
            tier: {"summary": summaries[tier], "articles": tier_articles[tier]}
            for tier in TIERS
        }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)

    print(f"Wrote {OUTPUT_PATH}: {len(topics)} topics, {len(topics)} Gemini call(s) total.")


if __name__ == "__main__":
    main()