"""
Fetches yesterday's news for each (topic x geographic tier) combination and
summarizes each cell with Gemini. Writes the result to digest.json at the
repo root, which the static frontend (index.html) reads directly.

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
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

TIERS = ["city", "state", "country", "world"]
MAX_ARTICLES_PER_CELL = 8
REQUEST_TIMEOUT = 30


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


def summarize(topic, tier, location, articles):
    if not articles:
        return "No notable stories turned up for this topic and region in the last day."

    if not GEMINI_API_KEY:
        # Fallback so the pipeline still produces something useful without a key.
        return "Gemini API key not set — showing headlines only. See README to add GEMINI_API_KEY."

    label = location_label(tier, location)
    bullet_list = "\n".join(f"- {a['title']} ({a['source']})" for a in articles)

    prompt = (
        "You are writing one section of a personal daily news briefing.\n"
        f"Topic: {topic}\n"
        f"Region: {label}\n\n"
        "Below are headlines from the last 24 hours on this topic and region. "
        "Write a tight 3-5 sentence summary of what actually happened, grouping "
        "related stories together rather than restating each headline in turn. "
        "Be neutral and factual. If the headlines are thin or largely unrelated "
        "to each other, say so briefly instead of padding.\n\n"
        f"Headlines:\n{bullet_list}"
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 300},
    }

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as exc:  # keep the pipeline alive even if one cell fails
        return f"(Summary unavailable right now: {exc})"


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
        digest["topics"][topic] = {}
        for tier in TIERS:
            query = build_query(topic, tier, location)
            articles = fetch_articles(query)
            summary = summarize(topic, tier, location, articles)
            digest["topics"][topic][tier] = {
                "summary": summary,
                "articles": articles,
            }
            time.sleep(1)  # stay well within free-tier rate limits

    with open(OUTPUT_PATH, "w") as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)

    print(f"Wrote {OUTPUT_PATH} with {len(topics)} topics x {len(TIERS)} tiers.")


if __name__ == "__main__":
    main()
