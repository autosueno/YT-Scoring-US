"""
collector.py

YouTube Data Collector — v4

"Dumb" collector: it collects, saves, loads, and archives. It never
decides WHETHER to rebuild or reuse a niche database — that decision
belongs to main.py.

Public API:
    build_niche_database(api_key, niche_name, query=None, ...)
        ALWAYS pulls fresh data and overwrites the niche's folder.
        If a previous build exists, it's archived to history/ first
        (by its own build date, not today's date) — automatic, no
        separate archive_database() call needed.

    load_database(niche_name)
        ALWAYS reads from disk. No API calls.

    collect_topic(api_key, topic, ...)
        ALWAYS pulls fresh data — no caching, a topic grade should
        reflect right now. Unlike v3, the result IS persisted to
        topics/<topic>/ afterward (storage is free, quota isn't) —
        this is archival for later reference/comparison, not a cache
        that would skip a future API call.

    database_exists(niche_name) -> bool
    load_metadata(niche_name) -> dict or None
    topic_report_path(topic) -> str (so main.py can save a topic's
        grade report into the same folder as its raw data)

Storage layout:
    datasets/<niche>/
        videos.csv        (every raw field + every metrics.py column)
        statistics.csv      (one row per metric: mean/median/std/MAD/IQR/CV/skew/...)
        metadata.json        (niche, query, counts, versions, quota, medians)
        insights.json         (top words/bigrams/trigrams, top channels,
                               upload-time distribution, top tags, title
                               patterns — all derived locally, zero extra
                               API calls, since the data's already downloaded)

    topics/<topic>/
        videos.csv, metadata.json  (written by collect_topic)
        report.json                 (written by main.py after grading —
                                     collector doesn't know about grades)

    history/<niche>/<date-of-that-build>/
        (a full copy of what datasets/<niche>/ looked like before the
        most recent rebuild — written automatically by build_niche_database)

    quota_history.csv — one row per collection run (niche or topic),
    appended forever: date, kind, query, videos, units_used, elapsed_seconds

collector.py NEVER calculates metrics itself — all feature engineering
is delegated to metrics.calculate_all(df).

Requires:
    pip install google-api-python-client pandas isodate
"""

import csv
import json
import os
import shutil
import time
from datetime import datetime, timezone, timedelta

import pandas as pd

import metrics
from api_keys import ApiKeyManager, AllKeysExhausted
from statistics import save_statistics
from insights import generate_insights, save_insights
from config import (
    CACHE_DIR, SEARCH_RESULTS_PER_CALL,
    KEY_PER_SEED, RESERVED_KEYS_FOR_TOPICS,
    NICHE_DATABASE_SIZE, TOPIC_SAMPLE_SIZE,
    SEARCH_MAX_PER_QUERY, SEARCH_WINDOW_DAYS, COLLECTION_LOOKBACK_DAYS,
    RELEVANCE_LANGUAGE, REGION_CODES, REGION_STRATEGY,
    MAX_SEEDS_PER_NICHE, PER_SEED_MIN_RESULTS,
    DATASETS_DIR, TOPICS_DIR, HISTORY_DIR, QUOTA_HISTORY_PATH,
    DATABASE_SCHEMA_VERSION, COLLECTOR_VERSION, METRICS_VERSION, YOUTUBE_API_VERSION,
    COLLECT_VIDEO_DURATION, EXCLUDE_SHORTS_UNDER_SECONDS, REQUIRE_ENGLISH,
    SEARCH_ORDER,
)
from quota_tracker import QuotaTracker


# ============================================================
# PATH / FOLDER HELPERS
# ============================================================

# All path construction lives in paths.py (single source of truth for the
# on-disk layout). Imported under the underscore names collector uses
# internally so call sites read the same as before. topic_report_path stays
# public because main.py calls collector.topic_report_path().
from paths import (
    safe_name as _safe_name,
    niche_dir as _niche_dir,
    videos_path as _videos_path,
    statistics_path as _statistics_path,
    metadata_path as _metadata_path,
    insights_path as _insights_path,
    history_dir as _history_dir,
    topic_dir as _topic_dir,
    topic_videos_path as _topic_videos_path,
    topic_metadata_path as _topic_metadata_path,
    topic_report_path,
)


def _ensure_folders():
    for d in (DATASETS_DIR, TOPICS_DIR, CACHE_DIR, HISTORY_DIR):
        os.makedirs(d, exist_ok=True)


# ============================================================
# PRIVATE PIPELINE — shared by build_niche_database and collect_topic
# ============================================================

def _search_video_ids(keys, query, max_results, order, tracker,
                      published_after=None, published_before=None,
                      region_code=None):
    """
    Paginate a single search.list query through the key manager. YouTube
    caps a single query at ~500 results regardless of pagination — see
    _search_windowed for the workaround. Raises AllKeysExhausted if quota
    runs out mid-pagination (caller handles gracefully).
    """
    video_ids = []
    next_page = None

    while len(video_ids) < max_results:
        page_size = min(SEARCH_RESULTS_PER_CALL, max_results - len(video_ids))
        params = dict(
            q=query,
            part="id",
            type="video",
            order=order,
            maxResults=page_size,
            pageToken=next_page,
        )
        if RELEVANCE_LANGUAGE:
            params["relevanceLanguage"] = RELEVANCE_LANGUAGE
        if COLLECT_VIDEO_DURATION and COLLECT_VIDEO_DURATION != "any":
            # API-level duration bucket. Cheaper than fetching Shorts and
            # discarding them, but coarse ("medium" = 4-20min only), so
            # the default stays "any" and EXCLUDE_SHORTS_UNDER_SECONDS
            # does the precise filtering after contentDetails arrives.
            params["videoDuration"] = COLLECT_VIDEO_DURATION
        if region_code:
            # Biases toward videos viewable in this country (see config).
            params["regionCode"] = region_code
        if published_after:
            params["publishedAfter"] = published_after
        if published_before:
            params["publishedBefore"] = published_before

        tracker.charge("search.list")
        response = keys.execute(lambda yt: yt.search().list(**params))
        for item in response.get("items", []):
            video_ids.append(item["id"]["videoId"])

        next_page = response.get("nextPageToken")
        if not next_page:
            break

    return video_ids


def _iso(dt):
    """RFC3339 timestamp YouTube expects (e.g. 2025-01-01T00:00:00Z)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _search_windowed(keys, query, max_results, order, tracker,
                     window_days=None, lookback_days=None, region_code=None):
    """
    Reach a large target by slicing the search into consecutive DATE WINDOWS.

    A single search.list query tops out near 500 results, so to approach
    `max_results` we run the query separately over back-to-back time windows
    (each with its own ~500 ceiling) and de-duplicate. This also BOUNDS the
    recency of the sample, which the momentum/trend logic requires.

    Iterate-and-append contract: results accumulate window by window. If
    quota runs out mid-way (AllKeysExhausted), we return whatever has been
    gathered so far rather than raising — collection degrades, never dies.
    """
    window_days = window_days or SEARCH_WINDOW_DAYS
    lookback_days = lookback_days if lookback_days is not None else COLLECTION_LOOKBACK_DAYS

    # No recency bound requested → original single-query behavior.
    if not lookback_days:
        try:
            return _search_video_ids(keys, query, max_results, order, tracker,
                                     region_code=region_code)
        except AllKeysExhausted:
            return []

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)

    # Build the window list up front so the budget can be spread EVENLY
    # across time.
    #
    # Why not just fill recent-first until the target is met: that biases
    # the sample by exactly the quantity we're trying to measure. A topic
    # with lots of content fills its budget in the newest few windows and
    # never reaches older ones, so its sample looks artificially "fresh";
    # a sparse topic walks the full lookback and looks stale. Since
    # content_freshness and upload_acceleration are SCORED supply metrics,
    # that turns a sampling artifact into a score. Even allocation makes
    # the sample's age distribution reflect the market's.
    windows = []
    window_end = now
    while window_end > start:
        window_start = max(start, window_end - timedelta(days=window_days))
        windows.append((window_start, window_end))
        window_end = window_start - timedelta(seconds=1)

    seen = set()
    ordered_ids = []
    # Divide the budget evenly. No SEARCH_RESULTS_PER_CALL floor here:
    # maxResults accepts 1-50, and flooring at 50 over-allocates the newest
    # windows and re-truncates the oldest ones — the exact bias being fixed.
    per_window = max(1, -(-max_results // max(1, len(windows))))

    def _pull(w_start, w_end, want):
        """Search one window; returns False if quota died."""
        if want <= 0:
            return True
        try:
            ids = _search_video_ids(
                keys, query, min(want, SEARCH_MAX_PER_QUERY), order, tracker,
                published_after=_iso(w_start),
                published_before=_iso(w_end),
                region_code=region_code,
            )
        except AllKeysExhausted:
            print(f"    Quota exhausted mid-search for {query!r} — "
                  f"keeping the {len(ordered_ids)} ids gathered so far.")
            return False
        for vid in ids:
            if vid not in seen:
                seen.add(vid)
                ordered_ids.append(vid)
        return True

    # Pass 1 — even allocation, so every window is represented.
    for w_start, w_end in windows:
        if len(ordered_ids) >= max_results:
            break
        if not _pull(w_start, w_end, per_window):
            return ordered_ids[:max_results]

    # Pass 2 — if sparse windows left us short of the target, top up from
    # the newest windows (which have the most available). Temporal coverage
    # is already established by pass 1, so this can't re-introduce the bias.
    if len(ordered_ids) < max_results:
        for w_start, w_end in windows:
            if len(ordered_ids) >= max_results:
                break
            if not _pull(w_start, w_end, max_results - len(ordered_ids)):
                break

    return ordered_ids[:max_results]


def _download_video_details(keys, video_ids, tracker):
    """
    Download details in batches of 50, appending batch by batch. On quota
    exhaustion mid-way, returns the rows gathered so far (complete rows
    only — a batch either fully lands or is skipped).
    """
    video_rows = []
    channel_ids = set()

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            tracker.charge("videos.list")
            response = keys.execute(
                lambda yt: yt.videos().list(
                    part="snippet,statistics,contentDetails,topicDetails",
                    id=",".join(batch)
                )
            )
        except AllKeysExhausted:
            print(f"    Quota exhausted during video downloads — keeping "
                  f"{len(video_rows)} of {len(video_ids)} videos gathered so far.")
            break
        for item in response.get("items", []):
            snippet = item["snippet"]
            statistics = item.get("statistics", {})
            content = item.get("contentDetails", {})
            video_rows.append({
                "video_id": item["id"],
                "channel_id": snippet.get("channelId"),
                "channel_title": snippet.get("channelTitle"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "tags": "|".join(snippet.get("tags", [])),
                # YouTube's OWN semantic categorization (Wikipedia topic
                # URLs). Costs nothing extra — videos.list quota is charged
                # per call, not per part — and gives an independent check
                # on our phrase-based clusters.
                # Thumbnail URL is already in the snippet at no extra cost.
                # We CANNOT analyse the image, but exporting the URL lets a
                # human (or a multimodal model) inspect what the winners look
                # like — thumbnails are the biggest CTR lever we can't measure.
                "thumbnail_url": (snippet.get("thumbnails", {})
                                  .get("high", snippet.get("thumbnails", {}).get("default", {}))
                                  .get("url", "")),
                "topic_categories": "|".join(
                    u.rsplit("/", 1)[-1].replace("_", " ")
                    for u in item.get("topicDetails", {}).get("topicCategories", [])
                ),
                "category_id": snippet.get("categoryId"),
                "published_at": snippet.get("publishedAt"),
                "default_language": snippet.get("defaultLanguage"),
                "audio_language": snippet.get("defaultAudioLanguage"),
                "duration": content.get("duration"),
                "definition": content.get("definition"),
                "caption": content.get("caption"),
                "licensed_content": content.get("licensedContent"),
                "view_count": int(statistics.get("viewCount", 0)),
                "like_count": int(statistics.get("likeCount", 0)),
                "comment_count": int(statistics.get("commentCount", 0)),
                "favorite_count": int(statistics.get("favoriteCount", 0)),
            })
            channel_ids.add(snippet.get("channelId"))

    return video_rows, channel_ids


def _download_channel_details(keys, channel_ids, tracker):
    channel_rows = []
    ids = list(channel_ids)

    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        try:
            tracker.charge("channels.list")
            response = keys.execute(
                lambda yt: yt.channels().list(part="statistics", id=",".join(batch))
            )
        except AllKeysExhausted:
            print(f"    Quota exhausted during channel downloads — keeping "
                  f"{len(channel_rows)} of {len(ids)} channels gathered so far.")
            break
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            channel_rows.append({
                "channel_id": item["id"],
                "channel_subs": int(stats.get("subscriberCount", 0)),
                "channel_view_count": int(stats.get("viewCount", 0)),
                "channel_video_count": int(stats.get("videoCount", 0)),
                "channel_hidden_subs": stats.get("hiddenSubscriberCount", False),
            })

    return pd.DataFrame(channel_rows)


def _merge_data(video_rows, channels_df):
    df = pd.DataFrame(video_rows)
    if df.empty:
        return df
    # channels_df can be EMPTY if quota died during the channel-download
    # phase (videos landed, channels didn't). Merging on a column that
    # doesn't exist raises KeyError, which would crash the very
    # graceful-degradation path that kept the videos. Fill the channel
    # columns with NA instead so the partial dataset still flows through
    # metrics; channel-relative features degrade to NaN rather than dying.
    if channels_df is None or channels_df.empty or "channel_id" not in channels_df.columns:
        print("    (no channel metadata available — channel-relative "
              "metrics will be blank for this build)")
        for col in ("channel_subs", "channel_view_count",
                    "channel_video_count", "channel_hidden_subs"):
            if col not in df.columns:
                df[col] = pd.NA
        return df
    return df.merge(channels_df, on="channel_id", how="left")


def _resolve_keys(api_key):
    """
    Accept whatever callers pass and return an ApiKeyManager:
      - an ApiKeyManager (used as-is)
      - a plain key string (single-key manager)
      - None (load all keys from the environment)
    Keeps build_niche_database / collect_topic signatures backwards-compatible.
    """
    if isinstance(api_key, ApiKeyManager):
        return api_key
    if api_key:
        return ApiKeyManager([api_key])
    return ApiKeyManager.from_env()


def _regions_for_seed(seed_index):
    """
    Which regionCode(s) to search for this seed, per REGION_STRATEGY.

    "rotate" spreads target markets across seeds at no extra quota cost;
    "all" multiplies search calls by len(REGION_CODES); "first" pins to one.
    """
    if not REGION_CODES:
        return [None]
    if REGION_STRATEGY == "all":
        return list(REGION_CODES)
    if REGION_STRATEGY == "first":
        return [REGION_CODES[0]]
    return [REGION_CODES[seed_index % len(REGION_CODES)]]   # rotate


def _normalize_seeds(query):
    """Accept a single query string OR a list of seed queries."""
    if query is None:
        return []
    if isinstance(query, str):
        # allow comma-separated seeds in one string: "finance, investing"
        parts = [p.strip() for p in query.split(",") if p.strip()]
        return parts[:MAX_SEEDS_PER_NICHE]
    return [str(q).strip() for q in query if str(q).strip()][:MAX_SEEDS_PER_NICHE]


def estimate_build_units(query, max_results=None):
    """
    Estimate search-quota cost BEFORE spending anything, so a build that
    cannot fit is caught up front instead of dying half-way.
    """
    import math
    max_results = max_results or NICHE_DATABASE_SIZE
    seeds = _normalize_seeds(query) or ["?"]
    per_seed = max(PER_SEED_MIN_RESULTS, math.ceil(max_results / len(seeds)))
    windows = max(1, math.ceil((COLLECTION_LOOKBACK_DAYS or SEARCH_WINDOW_DAYS)
                               / SEARCH_WINDOW_DAYS))
    calls_per_window = max(1, math.ceil(min(per_seed, SEARCH_MAX_PER_QUERY)
                                        / windows / SEARCH_RESULTS_PER_CALL))
    regions = len(REGION_CODES) if REGION_STRATEGY == "all" else 1
    search_calls = len(seeds) * windows * calls_per_window * regions
    video_calls = math.ceil(max_results / 50) * 2   # videos.list + channels.list
    return {
        "seeds": len(seeds),
        "search_calls": search_calls,
        "units": search_calls * 100 + video_calls,
    }


def _print_quota_forecast(api_key, query, max_results):
    est = estimate_build_units(query, max_results)
    line = (f"  Estimated cost: ~{est['units']:,} units "
            f"({est['search_calls']} searches across {est['seeds']} seed(s))")
    if isinstance(api_key, ApiKeyManager):
        cap = api_key.total_capacity
        pct = 100.0 * est["units"] / cap if cap else 0
        line += (f" — {pct:.0f}% of your {api_key.total_keys}-key "
                 f"capacity ({cap:,} units)")
        print(line)
        if est["units"] > cap:
            print("  ⚠️  This EXCEEDS your total quota. The build will stop "
                  "part-way and save as '<niche>_incomplete'.")
            print("      Reduce seeds, lower NICHE_DATABASE_SIZE, or shorten "
                  "COLLECTION_LOOKBACK_DAYS to fit.")
    else:
        print(line)


def apply_quality_filters(df, verbose=True):
    """
    Drop Shorts and non-English videos AFTER enrichment, then report what
    went. Runs post-fetch because both signals need data the search
    endpoint does not return: duration comes from contentDetails, and the
    language decision needs the title (for script detection) plus the
    declared language fields.

    Reports counts loudly rather than silently shrinking the dataset — a
    build that quietly drops 40% of its sample would make every downstream
    percentile subtly wrong with no visible cause.
    """
    if df is None or df.empty:
        return df
    before = len(df)
    notes = []

    if EXCLUDE_SHORTS_UNDER_SECONDS and "duration_seconds" in df.columns:
        secs = pd.to_numeric(df["duration_seconds"], errors="coerce")
        # Keep rows with unknown duration: dropping them would silently
        # bias against videos whose contentDetails failed to fetch.
        mask = (secs >= EXCLUDE_SHORTS_UNDER_SECONDS) | secs.isna()
        removed = int((~mask).sum())
        if removed:
            notes.append(f"{removed:,} Shorts (<{EXCLUDE_SHORTS_UNDER_SECONDS}s)")
        df = df[mask]

    if REQUIRE_ENGLISH and "likely_english" in df.columns:
        mask = df["likely_english"].fillna(True)
        removed = int((~mask).sum())
        if removed:
            notes.append(f"{removed:,} non-English")
        df = df[mask]

    if verbose and notes:
        print(f"  Quality filters removed {before - len(df):,} of {before:,} "
              f"({', '.join(notes)}) → {len(df):,} usable")
    return df.reset_index(drop=True)


def _collect_raw(api_key, query, max_results, order, tracker):
    """
    Multi-seed, quota-resilient collection.

    `query` may be one seed ("finance") or several ("finance, investing,
    economics" or a list). The target is split across seeds; each seed is
    searched with date windows, and ids are merged + de-duplicated in seed
    order. Video/channel downloads then run over the merged id list.

    Iterate-and-append throughout: if quota dies at ANY point, we keep
    everything gathered so far and continue the pipeline with a partial
    dataset — the program never crashes on quota.
    """
    keys = _resolve_keys(api_key)
    seeds = _normalize_seeds(query)
    if not seeds:
        return pd.DataFrame(), pd.DataFrame()

    print(f"Using {keys.total_keys} API key(s); seeds: {seeds}")
    if KEY_PER_SEED and keys.total_keys > 1:
        print(f"  (one dedicated key per seed; "
              f"{RESERVED_KEYS_FOR_TOPICS} reserved for topic grading)")

    # Per-seed allowance is a FLOOR, not target//len(seeds). Dividing the
    # target starves every seed (a single seed routinely returns 600+, so a
    # 2000 target across 5 seeds capped each at 400 and discarded data that
    # was free to collect). Each seed pulls what it can; the loop stops when
    # the overall target is met, the seeds run dry, or quota is exhausted.
    per_seed = max(PER_SEED_MIN_RESULTS, -(-max_results // len(seeds)))

    seen = set()
    video_rows = []
    channel_ids = set()
    collected = 0

    for seed_index, seed in enumerate(seeds):
        if keys.exhausted:
            print(f"  Skipping remaining seeds (quota exhausted): "
                  f"{seeds[seed_index:]}")
            break
        if collected >= max_results:
            print(f"  Target of {max_results} reached — skipping remaining "
                  f"seeds: {seeds[seed_index:]}")
            break

        # Give this seed its own key so a greedy earlier seed cannot have
        # spent its budget. Falls back to any live key if that one is dead.
        #
        # NAMED key_order, not order: `order` is this function's own
        # parameter (the YouTube search sort — "date", "relevance", etc).
        # An earlier version reused `order` for this and silently shadowed
        # it, so build_key_order()'s list of key INDICES was passed to the
        # YouTube API as the sort order — a TypeError that only appears
        # once KEY_PER_SEED actually engages with 2+ keys, which unit tests
        # with a single stub key never exercise.
        if KEY_PER_SEED and keys.total_keys > 1:
            key_order = keys.build_key_order()
            keys.use_key_for_seed(key_order[seed_index % len(key_order)])
            print(f"  → seed {seed!r} assigned API key #{keys.current_key_number}")

        want = min(per_seed, max_results - collected)
        regions = _regions_for_seed(seed_index)
        per_region = want if REGION_STRATEGY == "all" else max(1, -(-want // len(regions)))

        seed_ids = []
        for region in regions:
            if keys.exhausted or collected + len(seed_ids) >= max_results:
                break
            tag = f" [{region}]" if region else ""
            print(f"  Searching seed {seed!r}{tag} (up to {per_region})...")
            ids = _search_windowed(keys, seed, per_region, order, tracker,
                                   region_code=region)
            new = [v for v in ids if v not in seen]
            seen.update(new)
            seed_ids.extend(new)
            print(f"    +{len(new)} new ({len(ids) - len(new)} dupes)")

        # Download THIS SEED's video details before searching the next seed.
        #
        # Searching every seed first and downloading afterwards means quota
        # death during the (expensive, 100-units-per-call) search phase
        # throws away every id already gathered — you can burn thousands of
        # units and save nothing. Interleaving costs nothing extra (a
        # videos.list call is 1 unit) and guarantees that every completed seed
        # is banked as usable rows.
        if seed_ids:
            print(f"  Downloading metadata for {len(seed_ids)} videos "
                  f"from seed {seed!r}...")
            rows, chans = _download_video_details(keys, seed_ids, tracker)
            video_rows.extend(rows)
            channel_ids.update(chans)
            collected = len(video_rows)
            print(f"    banked {len(rows)} rows — total {collected} unique")

    if not video_rows:
        print("⚠️  No videos collected (quota exhausted before any video "
              "metadata was downloaded). Nothing to save.")
        return pd.DataFrame(), pd.DataFrame()

    print(f"Downloading channel metadata for {len(channel_ids)} channels...")
    channels_df = _download_channel_details(keys, channel_ids, tracker)

    df = _merge_data(video_rows, channels_df)

    # If quota died early enough that NOTHING landed, stop here. Running
    # metrics on an empty frame raises KeyError on the columns it expects;
    # returning empty lets the caller report "0 videos collected" cleanly.
    if df.empty or "published_at" not in df.columns:
        print("⚠️  No usable videos collected (quota exhausted before any "
              "video metadata was downloaded). Nothing to save.")
        return pd.DataFrame(), pd.DataFrame()

    if keys.exhausted:
        print(f"⚠️  Build was quota-truncated: {len(df)} of {max_results} "
              f"target videos collected. Continuing with what we have.")

    print("Calculating metrics...")
    df = metrics.calculate_all(df)

    return df, channels_df


# ============================================================
# DIAGNOSTICS
# ============================================================

def _diagnostics(df, requested_n):
    """
    Post-collection sanity checks.

    The span check compares the collected date range against the
    CONFIGURED lookback, not against a fixed constant. An earlier
    version warned whenever the span exceeded 90 days — but
    COLLECTION_LOOKBACK_DAYS is 365 and the collector deliberately
    slices that year into monthly windows to get even temporal
    coverage, so that warning fired on every single successful build.
    A warning that is always on is worse than no warning: it trains
    you to ignore the one time it means something.

    What actually deserves a warning is the span DISAGREEING with the
    configuration:

      - Much shorter than configured usually means the windowed search
        failed or ran out of quota early, leaving a recency-biased
        sample. That is a real problem — momentum compares a recent
        window against a prior one, and a sample skewed to the last
        few weeks has no meaningful prior window to compare against.
      - Longer than configured means stale rows survived from an
        older build, so the dataset mixes collection runs.

    Separately worth knowing, though not a defect: views-per-hour is
    age-sensitive, because view accumulation is front-loaded rather
    than linear. A 300-day-old video's VPH is diluted by its long
    low-traffic tail while a 3-day-old video's reflects its launch
    spike. That is inherent to any wide date range and is why the
    engine leans on outlier_ratio — each video against its own
    channel's average — wherever the comparison needs to be robust to
    age.
    """
    n_videos = len(df)
    diag = {
        "videos_returned": n_videos,
        "underflowed": n_videos < requested_n,
        "span_hours": None,
        "span_warning": None,
    }
    if "hours_since_upload" in df.columns and n_videos > 0:
        span_hours = float(df["hours_since_upload"].max() - df["hours_since_upload"].min())
        diag["span_hours"] = span_hours
        span_days = span_hours / 24.0
        expected = COLLECTION_LOOKBACK_DAYS
        if expected:
            if span_days < expected * 0.5:
                diag["span_warning"] = (
                    f"Collected videos span only {span_days:.0f} days, but "
                    f"the configured lookback is {expected}. The sample is "
                    f"skewed toward recent uploads — momentum needs a prior "
                    f"window to compare against, so treat it as unreliable. "
                    f"Usually means quota ran out mid-build."
                )
            elif span_days > expected * 1.15:
                diag["span_warning"] = (
                    f"Collected videos span {span_days:.0f} days, beyond the "
                    f"configured {expected}-day lookback. The dataset may be "
                    f"mixing rows from an older build — consider a forced "
                    f"rebuild."
                )
    return diag


# ============================================================
# ARCHIVING — automatic, on rebuild
# ============================================================

def _archive_existing_niche(niche_name):
    """
    Moves the current datasets/<niche>/ into history/<niche>/<date>/
    before it gets overwritten — dated by when that snapshot was
    actually BUILT (from its own metadata), not today's date, so the
    history trail reflects real collection dates.
    """
    old_meta = load_metadata(niche_name)
    src = _niche_dir(niche_name)
    if not old_meta or not os.path.exists(src):
        return

    created = old_meta.get("created")
    date_str = created[:10] if created else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = _history_dir(niche_name, date_str)

    if os.path.exists(dest):
        shutil.rmtree(dest)  # same-day rebuild — replace rather than crash
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(src, dest)
    print(f"Archived previous {niche_name!r} database ({date_str}) to {dest}")


# ============================================================
# OUTPUT — videos / metadata
# ============================================================

def _save_videos(df, path):
    """
    Write the dataset to CSV, safely.

    Two failure modes worth handling:

    1. Windows/OneDrive holds the file open (a spreadsheet viewer, an
       antivirus scanner) → PermissionError on the direct write. We write
       to a `.tmp` file first and rename over the destination, so a locked
       file only aborts THIS save attempt — the previous good version
       stays intact.
    2. Disk full or a permission surprise mid-write → we clean up the
       temp file and raise a clear error naming both what failed and where.

    We do NOT swallow the exception: silently "succeeding" while data
    went nowhere would be worse than failing loudly. A caller upstream
    can decide whether to retry, save under a different name, etc.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        df.to_csv(tmp, index=False)
        # os.replace is atomic on POSIX and best-effort on Windows —
        # much safer than write-in-place if something interrupts mid-write.
        os.replace(tmp, path)
    except Exception as e:
        # Best-effort cleanup so we don't leave orphan .tmp files behind.
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise RuntimeError(
            f"Failed to save {len(df):,} rows to {path}: "
            f"{type(e).__name__}: {e}. If the file is open in another "
            f"program (Excel, a viewer, OneDrive sync), close it and retry."
        ) from e


def _save_metadata(display_name, query, df, tracker, diag, max_results, path, kind,
                    order=None, elapsed_seconds=None, quota_cost_this_build=None):
    oldest_video_days = None
    newest_video_days = None
    if "hours_since_upload" in df.columns and not df.empty:
        oldest_video_days = float(df["hours_since_upload"].max() / 24)
        newest_video_days = float(df["hours_since_upload"].min() / 24)

    metadata = {
        "kind": kind,  # "niche" or "topic"
        "niche": display_name,
        "query": query,
        "order_used": order,
        "videos": len(df),
        "channels": int(df["channel_id"].nunique()) if not df.empty else 0,
        "sample_size": max_results,
        "created": datetime.now(timezone.utc).isoformat(),
        "build_duration_seconds": round(elapsed_seconds, 2) if elapsed_seconds is not None else None,
        "database_schema_version": DATABASE_SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "metrics_version": METRICS_VERSION,
        "youtube_api_version": YOUTUBE_API_VERSION,
        "quota_cost_this_build": quota_cost_this_build,  # delta for THIS call, not cumulative
        "quota_summary": tracker.summary(),  # cumulative — may reflect more than this one call
        "median_vph": float(df["views_per_hour"].median()) if "views_per_hour" in df.columns and not df.empty else None,
        "median_views": float(df["view_count"].median()) if not df.empty else None,
        "median_engagement": float(df["engagement_rate"].median()) if "engagement_rate" in df.columns and not df.empty else None,
        "oldest_video_days": oldest_video_days,
        "newest_video_days": newest_video_days,
        "underflowed": diag["underflowed"],
        "span_hours": diag["span_hours"],
        "span_warning": diag["span_warning"],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        json.dump(metadata, f, indent=2, default=str)
    return metadata



# ============================================================
# QUOTA HISTORY — append-only log across all runs
# ============================================================

def _log_quota_history(kind, query, videos, units_used, elapsed_seconds):
    dirname = os.path.dirname(QUOTA_HISTORY_PATH)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    file_exists = os.path.exists(QUOTA_HISTORY_PATH)
    with open(QUOTA_HISTORY_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "kind", "query", "videos", "units_used", "elapsed_seconds"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(), kind, query, videos,
            units_used, round(elapsed_seconds, 2),
        ])


def _print_summary(name, df, tracker, diag):
    print("=" * 60)
    print("Collection Complete")
    print("=" * 60)
    print(f"Name: {name}")
    print(f"Videos: {len(df)}" + (" (underflowed — fewer exist than requested)" if diag["underflowed"] else ""))
    print(f"Channels: {df['channel_id'].nunique() if not df.empty else 0}")
    if diag["span_warning"]:
        print(f"⚠️  {diag['span_warning']}")
    print(tracker.summary())
    print("=" * 60)


# ============================================================
# PUBLIC API
# ============================================================

def database_exists(niche_name):
    return os.path.exists(_videos_path(niche_name))


def load_metadata(niche_name):
    """
    Return parsed metadata, or None if it's missing/unreadable.

    A corrupted metadata JSON should NOT crash the whole run — this file
    is used to show summaries and check freshness, both of which can
    degrade gracefully. The actual dataset is still on disk and usable.
    """
    path = _metadata_path(niche_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️  Metadata file for {niche_name!r} is unreadable "
              f"({type(e).__name__}). Treating as missing; the dataset "
              f"itself is unaffected.")
        return None


def load_database(niche_name):
    """
    Load the niche dataset from CSV.

    Missing file → FileNotFoundError with a clear next-step message.
    Corrupted file → EOFError-style ValueError so the caller can distinguish
    "never built" from "build got corrupted". Silently returning an empty
    DataFrame here would let the whole pipeline compute nonsense on nothing.
    """
    path = _videos_path(niche_name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No niche database found for {niche_name!r} at {path}. "
            f"Call build_niche_database() first."
        )
    try:
        return pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError,
            UnicodeDecodeError, OSError) as e:
        raise ValueError(
            f"Niche database at {path} is present but unreadable "
            f"({type(e).__name__}: {e}). It may have been corrupted by a "
            f"crash during an earlier save. Rebuild with option 1, or "
            f"restore from the history/ folder if a previous copy exists."
        ) from e


def build_niche_database(api_key, niche_name, query=None, max_results=None, order=None, tracker=None):
    """
    ALWAYS pulls fresh data and overwrites datasets/<niche>/. If a
    previous build exists, it's archived to history/<niche>/<date>/
    first automatically.

    `query` lets the search term differ from the display name (e.g.
    niche_name="Economics", query="financial systems"). Defaults to
    niche_name if not given — fully backward compatible.

    Quota cost at the default NICHE_DATABASE_SIZE=2000: ~4,000-4,100
    units (~40% of the 10,000/day free quota) per build.
    """
    _ensure_folders()
    max_results = max_results or NICHE_DATABASE_SIZE
    query = query or niche_name

    # NOTE: the previous dataset is NOT archived here. Archiving before
    # collecting destroyed a perfectly good dataset whenever the new build
    # failed (quota death), leaving datasets/<niche>/ empty and options 2-5
    # broken — recoverable only by digging through history/ by hand.
    # Archiving now happens AFTER the new data is safely in hand.
    had_previous = database_exists(niche_name)

    tracker = tracker or QuotaTracker()
    if hasattr(tracker, "attach_keys") and isinstance(api_key, ApiKeyManager):
        tracker.attach_keys(api_key)
    units_before = tracker.summary()["used"]
    start_time = time.time()

    print(f"Building niche database: {niche_name!r} (query: {query!r}, {max_results} videos)")
    _print_quota_forecast(api_key, query, max_results)
    order = order or SEARCH_ORDER
    df, _ = _collect_raw(api_key, query, max_results, order, tracker)
    df = apply_quality_filters(df)
    elapsed = time.time() - start_time

    if df.empty:
        print(f"No videos found for query {query!r}. Nothing saved.")
        if had_previous:
            print(f"  Your existing {niche_name!r} database is untouched.")
        return df

    # A build that ran out of quota part-way is saved under a SEPARATE
    # "<niche>_incomplete" name. Two reasons:
    #   1. it must never overwrite a good, complete dataset;
    #   2. it is not merely smaller but UNEVEN — collection walks seeds in
    #      order, so early seeds are fully represented and later ones may
    #      be absent. That skew makes it a questionable BASELINE even though
    #      it is perfectly usable for discovery.
    # Metadata records complete=False so grading can warn about it.
    keys_exhausted = isinstance(api_key, ApiKeyManager) and api_key.exhausted
    is_complete = (not keys_exhausted) and len(df) >= max_results * 0.5
    save_name = niche_name if is_complete else f"{niche_name}_incomplete"

    if not is_complete:
        print()
        print("=" * 60)
        print(f"⚠️  INCOMPLETE BUILD — {len(df):,} of {max_results:,} target videos")
        print(f"   Saving as {save_name!r} so your existing data is not lost.")
        print("   Usable for discovery; treat grades from it with caution")
        print("   (later seeds may be under-represented).")
        print("=" * 60)
        print()

    diag = _diagnostics(df, max_results)

    # Archive the old dataset ONLY now that new data is safely in hand,
    # and only when the new build is complete enough to replace it.
    if had_previous and is_complete:
        _archive_existing_niche(niche_name)

    print("Saving dataset...")
    _save_videos(df, _videos_path(save_name))

    print("Saving metadata...")
    units_used_so_far = tracker.summary()["used"] - units_before
    _save_metadata(save_name, query, df, tracker, diag, max_results, _metadata_path(save_name),
                    kind="niche", order=order, elapsed_seconds=elapsed, quota_cost_this_build=units_used_so_far)
    # Record completeness so grading can warn when a baseline is skewed.
    try:
        _mp = _metadata_path(save_name)
        with open(_mp, "r", encoding="utf-8") as fh:
            _meta = json.load(fh)
        _meta["complete"] = bool(is_complete)
        _meta["target_videos"] = max_results
        _meta["collected_videos"] = int(len(df))
        with open(_mp, "w", encoding="utf-8") as fh:
            json.dump(_meta, fh, indent=2, default=str)
    except Exception:
        pass

    print("Generating statistics...")
    save_statistics(df, _statistics_path(save_name))

    print("Generating insights...")
    save_insights(generate_insights(df), _insights_path(save_name))

    units_used = tracker.summary()["used"] - units_before
    _log_quota_history("niche_build", query, len(df), units_used, elapsed)

    _print_summary(niche_name, df, tracker, diag)
    print("Done.")

    return df


def collect_topic(api_key, topic, max_results=None, order=None, tracker=None):
    """
    ALWAYS pulls fresh data — no caching. The result IS saved to
    topics/<topic>/ afterward for later reference/comparison — that's
    archival, not a cache; it doesn't skip the next API call, it just
    stops discarding the result of THIS one. Storage is free, quota
    isn't — comparing Topic A vs Topic B later shouldn't cost more API
    calls just because the data already exists on disk.

    Quota cost at the default TOPIC_SAMPLE_SIZE=500: ~1,010-1,020
    units EVERY call, including repeats of the same topic.
    """
    _ensure_folders()
    max_results = max_results or TOPIC_SAMPLE_SIZE

    tracker = tracker or QuotaTracker()
    units_before = tracker.summary()["used"]
    start_time = time.time()

    print(f"Collecting topic: {topic!r} ({max_results} videos)")
    order = order or SEARCH_ORDER
    df, _ = _collect_raw(api_key, topic, max_results, order, tracker)
    df = apply_quality_filters(df)
    elapsed = time.time() - start_time

    if df.empty:
        print(f"No videos found for topic {topic!r}.")
        return df

    diag = _diagnostics(df, max_results)

    print("Saving topic dataset...")
    _save_videos(df, _topic_videos_path(topic))
    units_used_so_far = tracker.summary()["used"] - units_before
    _save_metadata(topic, topic, df, tracker, diag, max_results, _topic_metadata_path(topic),
                    kind="topic", order=order, elapsed_seconds=elapsed, quota_cost_this_build=units_used_so_far)

    units_used = tracker.summary()["used"] - units_before
    _log_quota_history("topic_collect", topic, len(df), units_used, elapsed)

    _print_summary(topic, df, tracker, diag)
    print("Done.")

    return df


if __name__ == "__main__":
    API_KEY = os.getenv("YOUTUBE_API_KEY")
    if not API_KEY:
        raise ValueError("Set YOUTUBE_API_KEY first.")

    mode = input("Build niche database, load existing, or collect topic? [build/load/topic]: ").strip().lower()
    if mode == "build":
        niche = input("Niche display name: ")
        query = input("Search query (Enter to reuse the niche name): ").strip() or None
        df = build_niche_database(API_KEY, niche, query=query)
    elif mode == "load":
        niche = input("Niche name: ")
        df = load_database(niche)
    else:
        topic = input("Topic: ")
        df = collect_topic(API_KEY, topic)
    print(df.head())
