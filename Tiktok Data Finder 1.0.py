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
MAX_SHORTS_SECONDS = 180  # Official Shorts can be up to 3 minutes in many cases

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
        "order": "date",               # latest results first
        "publishedAfter": published_after_iso,
        "maxResults": min(max_results, 50),
        "videoDuration": "short",      # API-level prefilter (< 4 min), then we strictly re-filter to <= 180 sec
        "key": API_KEY,
    }
    if language_code:
        params["relevanceLanguage"] = language_code

    resp = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])

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
        data = resp.json()

        for item in data.get("items", []):
            vid = item["id"]
            result[vid] = item
    return result

def is_strict_match(item, window_minutes: int, duration_bucket: str) -> bool:
    try:
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})

        views = int(stats.get("viewCount", 0))
        published_at = parse_published_at(snippet.get("publishedAt"))
        duration_seconds = parse_duration(content.get("duration", ""))
        age_min = minutes_ago(published_at)

        # strict view threshold
        if views > MAX_VIEWS_STRICT:
            return False

        # strict latest timeframe
        if age_min < 0 or age_min > window_minutes:
            return False

        # strict short duration bucket
        if not duration_matches_bucket(duration_seconds, duration_bucket):
            return False

        # exclude live/upcoming
        if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
            return False

        return True
    except Exception:
        return False

def title_has_click_power(title: str) -> bool:
    t = title.lower()
    wc = len(t.split())
    has_digit = any(ch.isdigit() for ch in t)
    has_power = any(word in t for word in CLICK_WORDS)
    return (3 <= wc <= 7 and (has_digit or has_power))

def compact_words(text: str):
    words = re.findall(r"[A-Za-z0-9']+", text)
    cleaned = [w for w in words if w.lower() not in STOPWORDS]
    return cleaned

def suggest_title(original_title: str, niche: str) -> str:
    original_title = normalize_text(original_title)

    if title_has_click_power(original_title) and len(original_title.split()) <= 7:
        return original_title

    words = compact_words(original_title)
    if len(words) < 5:
        words += compact_words(niche)

    words = words[:7]
    if len(words) < 5:
        fallback = compact_words(f"{niche} viral short clip now")
        words = (words + fallback)[:5]

    title = " ".join(w.capitalize() for w in words[:7])
    return title[:80].strip()

def suggest_description(title: str, niche: str) -> str:
    line1 = f"{title} — quick viral-style short for fast attention."
    line2 = f"Best for repost/edit workflow in {niche} niche."
    return f"{line1}\n{line2}"

def suggest_hashtags(title: str, niche: str):
    pool = []
    for part in compact_words(f"{niche} {title}"):
        tag = "#" + re.sub(r"[^A-Za-z0-9]", "", part)
        if len(tag) > 1:
            pool.append(tag)

    # generic TikTok-style additions
    pool += ["#fyp", "#viral", "#tiktok", "#shortvideo", "#trend"]

    unique = []
    for tag in pool:
        low = tag.lower()
        if low not in [x.lower() for x in unique]:
            unique.append(tag)

    return unique[:5]

def build_result_row(item, niche):
    snippet = item["snippet"]
    stats = item.get("statistics", {})
    content = item.get("contentDetails", {})

    vid = item["id"]
    title = snippet.get("title", "").strip()
    channel = snippet.get("channelTitle", "Unknown Channel")
    published_at = parse_published_at(snippet["publishedAt"])
    duration_seconds = parse_duration(content.get("duration", ""))
    views = int(stats.get("viewCount", 0))

    suggested_title = suggest_title(title, niche)
    suggested_desc = suggest_description(suggested_title, niche)
    suggested_hashtags = suggest_hashtags(suggested_title, niche)

    return {
        "video_id": vid,
        "title": title,
        "channel": channel,
        "published_at": published_at.isoformat(),
        "minutes_ago": minutes_ago(published_at),
        "duration_seconds": duration_seconds,
        "duration_text": format_duration(duration_seconds),
        "views": views,
        "shorts_url": f"https://www.youtube.com/shorts/{vid}",
        "watch_url": f"https://www.youtube.com/watch?v={vid}",
        "suggested_title": suggested_title,
        "suggested_description": suggested_desc,
        "suggested_hashtags": suggested_hashtags,
    }

# =========================
# UI
# =========================
st.set_page_config(page_title="YouTube Shorts Data Finder", layout="wide")
st.title("🎯 YouTube Shorts Data Finder")
st.caption("Strict low-view, latest-upload Shorts finder for repurposing workflows")

st.warning(
    "Important: This app strictly verifies views, publish time and duration using the public YouTube Data API. "
    "Exact public 9:16/orientation verification is NOT exposed for arbitrary videos via official API."
)

col1, col2 = st.columns(2)

with col1:
    niche_name = st.text_input(
        "Main query / niche",
        value="",
        placeholder="e.g. crime bodycam, AI tools, cooking hacks, gym motivation"
    )

    user_keywords = st.text_area(
        "Extra keywords (optional, comma separated)",
        value="",
        placeholder="e.g. police chase, bodycam footage, interrogation"
    )

    selected_language = st.selectbox(
        "Language",
        options=list(LANGUAGE_OPTIONS.keys()),
        index=0
    )

with col2:
    latest_minutes = st.slider(
        "Latest upload window (minutes)",
        min_value=0,
        max_value=180,
        value=60,
        help="Only videos published within the last selected minutes will be shown."
    )

    duration_option = st.selectbox(
        "Shorts duration filter",
        options=list(DURATION_BUCKETS.keys()),
        index=0
    )

    num_results = st.number_input(
        "Number of final results",
        min_value=1,
        max_value=50,
        value=10
    )

with st.expander("Advanced"):
    search_depth = st.slider(
        "Search depth per query",
        min_value=10,
        max_value=50,
        value=25
    )

    st.text_input(
        "Strict max views",
        value=str(MAX_VIEWS_STRICT),
        disabled=True
    )

search_btn = st.button("🚀 Find Strict Shorts", type="primary")

# =========================
# Search flow
# =========================
if search_btn:
    if not niche_name.strip():
        st.error("Please enter a main query / niche.")
    elif latest_minutes == 0:
        st.error("0 min practically no results dega. 1 ya us se zyada select karein.")
    else:
        try:
            with st.spinner("Searching latest low-view Shorts..."):
                language_code = LANGUAGE_OPTIONS[selected_language]
                duration_bucket = DURATION_BUCKETS[duration_option]

                queries = generate_query_list(niche_name, user_keywords)
                published_after_dt = datetime.now(timezone.utc) - timedelta(minutes=int(latest_minutes))
                published_after_iso = published_after_dt.isoformat().replace("+00:00", "Z")

                # 1) Search latest videos
                candidate_video_ids = []
                for q in queries:
                    items = fetch_search_results(
                        query=q,
                        language_code=language_code,
                        published_after_iso=published_after_iso,
                        max_results=int(search_depth)
                    )
                    for it in items:
                        vid = it.get("id", {}).get("videoId")
                        if vid:
                            candidate_video_ids.append(vid)

                candidate_video_ids = list(dict.fromkeys(candidate_video_ids))

                if not candidate_video_ids:
                    st.warning("No candidate videos found.")
                    st.stop()

                # 2) First strict verification
                details_map = fetch_video_details(candidate_video_ids)
                filtered_ids = [
                    vid for vid, item in details_map.items()
                    if is_strict_match(item, latest_minutes, duration_bucket)
                ]

                filtered_ids = list(dict.fromkeys(filtered_ids))

                if not filtered_ids:
                    st.warning("No videos matched strict filters.")
                    st.stop()

                # 3) Final re-verification right before output
                final_map = fetch_video_details(filtered_ids)
                final_items = [
                    item for _, item in final_map.items()
                    if is_strict_match(item, latest_minutes, duration_bucket)
                ]

                # 4) Build output rows
                rows = [build_result_row(item, niche_name) for item in final_items]

                # newest first, then lowest views
                rows.sort(key=lambda x: (x["minutes_ago"], x["views"]))

                rows = rows[:int(num_results)]

                if not rows:
                    st.warning("No videos remained after final strict re-check.")
                    st.stop()

                st.success(f"✅ Found {len(rows)} strict matches")

                # CSV export
                csv_lines = [
                    "video_id,title,channel,views,duration_seconds,duration_text,minutes_ago,shorts_url,watch_url,suggested_title,suggested_description,suggested_hashtags"
                ]
                for r in rows:
                    line = [
                        r["video_id"],
                        json.dumps(r["title"]),
                        json.dumps(r["channel"]),
                        str(r["views"]),
                        str(r["duration_seconds"]),
                        r["duration_text"],
                        str(r["minutes_ago"]),
                        r["shorts_url"],
                        r["watch_url"],
                        json.dumps(r["suggested_title"]),
                        json.dumps(r["suggested_description"]),
                        json.dumps(" ".join(r["suggested_hashtags"]))
                    ]
                    csv_lines.append(",".join(line))

                st.download_button(
                    "⬇️ Download Results CSV",
                    data="\n".join(csv_lines),
                    file_name="strict_youtube_shorts_results.csv",
                    mime="text/csv"
                )

                # Results
                for idx, r in enumerate(rows, start=1):
                    with st.container(border=True):
                        c1, c2 = st.columns([3, 2])

                        with c1:
                            st.markdown(f"### {idx}. {r['title']}")
                            st.write(f"**Channel:** {r['channel']}")
                            st.write(f"**Views:** {r['views']} ✅")
                            st.write(f"**Duration:** {r['duration_text']}")
                            st.write(f"**Published:** {r['minutes_ago']} min ago")
                            st.link_button("▶ Open Short", r["shorts_url"])
                            st.caption("Official strict checks applied: views, publish time, duration.")

                        with c2:
                            st.markdown("#### TikTok Metadata Suggestion")
                            st.write(f"**Suggested Title:** {r['suggested_title']}")
                            st.write("**Suggested Description:**")
                            st.code(r["suggested_description"], language="text")
                            st.write("**Suggested Hashtags:**")
                            st.code(" ".join(r["suggested_hashtags"]), language="text")

                st.info(
                    "Legal note: direct YouTube video download button intentionally not added. "
                    "Use metadata export + open link workflow instead."
                )

        except requests.HTTPError as e:
            st.error(f"HTTP error: {e}")
        except Exception as e:
            st.error(f"Error: {e}")
