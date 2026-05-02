import re
import json
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone

# =========================
# Config
# =========================
API_KEY = st.secrets["YOUTUBE_API_KEY"]

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEO_URL = "https://www.googleapis.com/youtube/v3/videos"

MAX_VIEWS_STRICT = 300
MAX_SHORTS_SECONDS = 180

LANGUAGE_OPTIONS = {
    "Any Language": "",
    "English": "en",
    "Spanish": "es",
    "Hindi": "hi",
    "Arabic": "ar",
    "Portuguese": "pt",
}

DURATION_BUCKETS = {
    "Less than 1 minute": "lt_60",
    "1 to 3 minutes": "61_180",
    "All Shorts (0-180 sec)": "all",
}

CLICK_WORDS = {
    "best", "crazy", "insane", "secret", "secrets", "viral", "why", "how",
    "top", "vs", "before", "after", "never", "fast", "easy", "truth",
    "exposed", "win", "mistake", "hack", "hacks", "trick", "tricks"
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on",
    "with", "is", "are", "was", "were", "be", "this", "that", "these", "those"
}

# =========================
# NEW CONTENT FILTER (ADDED ONLY)
# =========================
def is_content_match(text: str, niche: str) -> bool:
    text = text.lower()
    niche = niche.lower()
    
    keywords_map = {
        "street food": ["street food", "india street food", "dirty food", "unhygienic", "roadside food"],
        "cake": ["cake", "bakery", "icing", "chocolate cake", "asmr baking"],
        "crime": ["police", "arrest", "crime", "bodycam", "interrogation"]
    }
    
    for key, words in keywords_map.items():
        if key in niche:
            return any(w in text for w in words)
    
    return True

# =========================
# Helpers
# =========================
def parse_duration(duration_iso: str) -> int:
    if not duration_iso:
        return 0
    pattern = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
    match = pattern.match(duration_iso)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds

def parse_published_at(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def minutes_ago(dt: datetime) -> int:
    return int((datetime.now(timezone.utc) - dt).total_seconds() // 60)

def format_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def duration_matches_bucket(duration_seconds: int, bucket: str) -> bool:
    if duration_seconds < 1:
        return False
    if duration_seconds > MAX_SHORTS_SECONDS:
        return False
    if bucket == "lt_60":
        return duration_seconds < 60
    if bucket == "61_180":
        return 60 <= duration_seconds <= 180
    return 0 < duration_seconds <= MAX_SHORTS_SECONDS

def generate_query_list(main_query: str, extra_keywords_raw: str):
    queries = [normalize_text(main_query)]
    if extra_keywords_raw.strip():
        extras = [normalize_text(x) for x in extra_keywords_raw.split(",") if normalize_text(x)]
        queries.extend(extras)
    return list(dict.fromkeys([q for q in queries if q]))

def chunked(seq, size=50):
    for i in range(0, len(seq), size):
        yield seq[i:i+size]

def fetch_search_results(query: str, language_code: str, published_after_iso: str, max_results: int):
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "date",
        "publishedAfter": published_after_iso,
        "maxResults": min(max_results, 50),
        "videoDuration": "short",
        "key": API_KEY,
    }
    if language_code:
        params["relevanceLanguage"] = language_code

    resp = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])

def fetch_video_details(video_ids):
    result = {}
    for batch in chunked(video_ids, 50):
        params = {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
            "key": API_KEY
        }
        resp = requests.get(YOUTUBE_VIDEO_URL, params=params, timeout=30)
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            result[item["id"]] = item
    return result

# =========================
# UPDATED STRICT FILTER (ONLY CHANGE INSIDE)
# =========================
def is_strict_match(item, window_minutes: int, duration_bucket: str, niche_name: str) -> bool:
    try:
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})

        views = int(stats.get("viewCount", 0))
        published_at = parse_published_at(snippet.get("publishedAt"))
        duration_seconds = parse_duration(content.get("duration", ""))
        age_min = minutes_ago(published_at)

        if views > MAX_VIEWS_STRICT:
            return False

        if age_min < 0 or age_min > window_minutes:
            return False

        if not duration_matches_bucket(duration_seconds, duration_bucket):
            return False

        # ✅ NEW FILTER
        title = snippet.get("title", "")
        desc = snippet.get("description", "")

        if not is_content_match(title + " " + desc, niche_name):
            return False

        if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
            return False

        return True
    except Exception:
        return False

# =========================
# UI (UNCHANGED)
# =========================
st.set_page_config(page_title="YouTube Shorts Data Finder", layout="wide")
st.title("🎯 YouTube Shorts Data Finder")

niche_name = st.text_input("Main query / niche")
search_btn = st.button("Search")

if search_btn:
    queries = generate_query_list(niche_name, "")
    published_after_iso = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat().replace("+00:00", "Z")

    candidate_ids = []
    for q in queries:
        items = fetch_search_results(q, "", published_after_iso, 25)
        for it in items:
            vid = it.get("id", {}).get("videoId")
            if vid:
                candidate_ids.append(vid)

    details = fetch_video_details(candidate_ids)

    results = [
        item for item in details.values()
        if is_strict_match(item, 60, "lt_60", niche_name)
    ]

    st.write(f"Found: {len(results)} videos")
