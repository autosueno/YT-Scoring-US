"""
signals.py — demand signals from OUTSIDE the video data.

Everything else in this engine measures YouTube videos that already
exist. That has a structural blind spot: it can only find topics
somebody has already made videos about. A topic nobody has covered
looks identical to a topic nobody wants.

This module reaches outside that boundary in two ways.

1. SEARCH INTENT (YouTube autocomplete)
   What people TYPE into the search box, whether or not good videos
   come back. Free, no API key, no quota. The gap between what people
   search and what the existing videos deliver is the angle nobody is
   covering.

2. EARLY SIGNAL (Reddit)
   Discussion moves before video production does — someone has to
   notice a topic, script it, record it and edit it. Terms that are
   hot in a subreddit but rare in the YouTube dataset are candidates
   for covering before the wave arrives.

HONESTY ABOUT THE ENDPOINTS
---------------------------
Neither source is an officially documented API, and they are a
different risk class from the YouTube Data API the rest of the engine
uses:

  - suggestqueries.google.com is the endpoint the search box itself
    calls. It is stable in practice and requires no credentials, but
    it is undocumented and Google throttles aggressive use.
  - reddit.com/r/<sub>/<listing>.json is Reddit's public JSON view.
    It works without authentication for public reads, but Reddit has
    tightened rate limits substantially since 2023 and can return 429
    or block a User-Agent it dislikes.

Both are therefore built to FAIL SOFTLY: a network problem produces a
warning and a partial or empty result, never a crash and never a
half-written file. Nothing else in the engine depends on them.

The "trending on X becomes a YouTube video in 2-3 weeks" claim that
motivates tools like this is, as far as I know, unvalidated folklore.
Treat the Reddit output as a lead to investigate, not a forecast.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import Counter

import pandas as pd

from config import EXPORTS_DIR, SIGNALS_RECURSE_TOP
from paths import export_dir_for
from insights import _STOPWORDS, _tokenize


# --- Autocomplete -------------------------------------------------
SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
# Polite delay between suggest calls. Google throttles bursts; at ~0.35s
# a 40-query expansion takes about 15 seconds, which is fine for a
# once-per-niche operation.
SUGGEST_DELAY = 0.35
# Modifiers that reveal INTENT rather than just more nouns. "how to X"
# and "is X worth it" surface different populations of searcher than
# the bare term does.
INTENT_PREFIXES = ["how to", "why", "what is", "when to", "is", "best",
                   "should i"]
INTENT_SUFFIXES = ["vs", "for beginners", "explained", "worth it",
                   "2026", "review", "reddit"]

# --- Reddit -------------------------------------------------------
# Reddit publishes a required User-Agent format and rejects generic or
# obviously-scripted ones with a 403. Theirs is:
#     <platform>:<app id>:<version> (by /u/<username>)
# The username is cosmetic for public JSON reads but the SHAPE matters —
# a bare "python-urllib/3.11" or "Mozilla/5.0" gets blocked far more
# aggressively than a UA that identifies itself honestly.
REDDIT_UA = ("python:youtube-market-intelligence:v1.0 "
             "(personal research script)")
REDDIT_DELAY = 1.0
# A post needs at least this many upvotes before its terms count. Very
# new posts with 2 upvotes are noise, not signal.
REDDIT_MIN_SCORE = 15


# ============================================================
# HTTP (isolated so the parsing below can be tested without network)
# ============================================================

def _get(url, headers=None, timeout=12, verbose=True):
    """
    Single GET returning text, or None on failure.

    Returns None rather than raising so a dead signal source degrades to
    an empty result instead of taking out the run — but it REPORTS what
    went wrong first. An earlier version had a bare `except Exception:
    return None`, which meant a 429, a 403, a DNS failure and a timeout
    all surfaced to the user as the same useless "could not reach"
    message with nothing to act on. The distinction matters a lot here:

      429  rate limited      -> wait, or use fewer subreddits
      403  blocked           -> User-Agent rejected; Reddit is strict
      404  wrong name        -> subreddit does not exist
      timeout / DNS          -> network, not the service
    """
    req = urllib.request.Request(
        url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if verbose:
            hint = {429: "rate limited — wait a few minutes or use fewer sources",
                    403: "blocked — the User-Agent was rejected",
                    404: "not found — check the name is spelled correctly",
                    503: "service unavailable — try again shortly"}.get(
                        e.code, "unexpected HTTP status")
            print(f"    HTTP {e.code}: {hint}")
        return None
    except urllib.error.URLError as e:
        if verbose:
            print(f"    Network error: {e.reason} "
                  f"(no route to the host — check your connection or proxy)")
        return None
    except Exception as e:
        if verbose:
            print(f"    {type(e).__name__}: {e}")
        return None


# ============================================================
# AUTOCOMPLETE
# ============================================================

def parse_suggest_response(text):
    """
    Extract suggestion strings from the autocomplete response.

    The endpoint returns JSONP when client=youtube — the JSON array is
    wrapped in a callback like `window.google.ac.h(...)`. Sometimes it
    returns bare JSON instead depending on the client parameter, so
    this strips a wrapper if one is present and parses either shape.

    Response shape is [query, [[suggestion, ...], ...], {...}] where
    each suggestion entry is itself a list whose first element is the
    text. Older/other clients return [query, [str, str, ...]] with
    plain strings, so both are handled.

    Split out from the fetch so it can be tested against captured
    fixtures without touching the network.
    """
    if not text:
        return []
    t = text.strip()
    # Strip a JSONP callback wrapper if present.
    m = re.match(r"^[^(]*\((.*)\)\s*;?\s*$", t, flags=re.S)
    if m:
        t = m.group(1)
    try:
        data = json.loads(t)
    except Exception:
        return []
    if not isinstance(data, list) or len(data) < 2:
        return []
    out = []
    for item in data[1]:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
            out.append(item[0])
    return out


def suggest(query, delay=SUGGEST_DELAY):
    """One autocomplete lookup. Returns [] on failure."""
    params = urllib.parse.urlencode(
        {"client": "youtube", "ds": "yt", "q": query})
    text = _get(f"{SUGGEST_URL}?{params}")
    if delay:
        time.sleep(delay)
    return parse_suggest_response(text)


def build_query_variants(seed, alphabet=True, prefixes=True, suffixes=True):
    """
    Expand one seed into the set of queries to look up.

    Alphabet expansion ("memory stocks a", "...b") is what harvests
    breadth — autocomplete returns different completions for each
    letter, so 26 cheap calls surface far more than one. Prefixes and
    suffixes target intent shape rather than breadth.
    """
    seed = seed.strip()
    variants = [seed]
    if alphabet:
        variants += [f"{seed} {c}" for c in "abcdefghijklmnopqrstuvwxyz"]
    if prefixes:
        variants += [f"{p} {seed}" for p in INTENT_PREFIXES]
    if suffixes:
        variants += [f"{seed} {s}" for s in INTENT_SUFFIXES]
    return variants


def harvest_autocomplete(seeds, alphabet=True, verbose=True, depth=1,
                         recurse_top=None, budget=None):
    """
    Harvest real search queries for one or more seeds.

    depth=1 expands each seed once: the bare term, the term plus each
    letter a-z, plus intent prefixes and suffixes (~41 lookups).

    depth=2 additionally takes the strongest suggestions FOUND at depth 1
    and expands those the same way. This is what surfaces the natural
    phrasing a level down — "mortgage" yields "mortgage rates", and
    expanding THAT yields "mortgage rates today", "mortgage rates
    forecast", "mortgage rates 2026". The vocabulary people actually use
    lives at this level.

    Cost grows fast, which is why recurse_top caps it: 8 second-level
    seeds x ~41 lookups is ~330 extra calls, about two minutes at the
    polite delay. Without a cap this would expand combinatorially.

    Returns {"queries": {query: count}, "attempted": n, "failed": n}
    where count is how many distinct lookups surfaced that query — a
    query returned by several different expansions is more central to
    the topic than one that appeared once.
    """
    if isinstance(seeds, str):
        seeds = [seeds]
    counter = Counter()
    attempted = failed = 0

    # Spend the budget on level one first, then divide whatever remains
    # among the seeds for recursion. This keeps total runtime roughly
    # constant as the seed list grows, instead of multiplying it.
    # No silent throttling. Autocomplete costs nothing but time, and
    # spending twenty minutes is the user's call to make, not the
    # harvester's. An earlier version quietly reduced recursion depth to
    # stay inside a time budget, which meant asking for a deep harvest
    # and getting a shallow one with no indication why. The caller shows
    # the estimate and confirms; this just reports what it is doing.
    if recurse_top is None:
        recurse_top = SIGNALS_RECURSE_TOP
    per_seed_variants = len(build_query_variants(seeds[0], alphabet=alphabet))
    if verbose and depth >= 2:
        total = len(seeds) * per_seed_variants * (1 + recurse_top)
        print(f"  ~{total:,} lookups, roughly {total*0.35/60:.1f} minutes")

    def _expand(term):
        nonlocal attempted, failed
        found = Counter()
        for v in build_query_variants(term, alphabet=alphabet):
            attempted += 1
            results = suggest(v)
            if not results:
                failed += 1
                continue
            for r in results:
                found[r.strip().lower()] += 1
        return found

    for seed in seeds:
        if verbose:
            print(f"  harvesting {seed!r}...")
        first = _expand(seed)
        counter.update(first)

        if depth >= 2 and first:
            # Recurse into the strongest suggestions, skipping any that
            # merely repeat the seed — expanding "mortgage" again as
            # "mortgage" wastes 41 calls for nothing.
            nxt = [q for q, _ in first.most_common(recurse_top * 3)
                   if q != seed.lower() and len(q.split()) > len(seed.split())]
            nxt = nxt[:recurse_top]
            if verbose and nxt:
                print(f"    recursing into {len(nxt)}: {', '.join(nxt[:4])}...")
            for sub in nxt:
                counter.update(_expand(sub))

    return {"queries": dict(counter), "attempted": attempted, "failed": failed}


# Question, modal and filler words. These carry the SHAPE of a search
# ("should i buy X now") but almost never appear in a video title, so
# requiring them to match guarantees a false gap. Found by testing:
# "should i buy micron now" scored zero against a dataset full of
# Micron buy/sell videos, purely because "should" and "now" are not in
# the general stopword list. Kept separate from insights._STOPWORDS
# because that set is tuned for title tokenisation, where these words
# are meaningful, and is used by other modules.
QUERY_FILLER = {
    "should", "would", "could", "will", "can", "does", "did", "do",
    "how", "why", "what", "when", "where", "who", "which", "whats",
    "now", "still", "really", "actually", "any", "some", "good", "bad",
    "worth", "buy", "get", "make", "know", "need", "want", "like",
}


def _stem(word):
    """
    Reduce a query word to a rough root so word-form differences stop
    creating false gaps.

    Found by testing against real data: the query "why memory stocks
    crashed" scored ZERO matches against a dataset that contains
    "Memory Stocks Are Crashing". The literal string "crashed" appeared
    in 2 titles, "crashing" in 3, "crash" in 9 — three different forms
    of one word, counted as three unrelated terms. Likewise "stocks"
    matched 121 titles while "stock" matched 171.

    Stemming only the QUERY side is deliberate and safe here, because
    matching is by substring: shortening "crashing" to "crash" still
    matches titles containing crash, crashed, crashing and crashes,
    while shortening a title would gain nothing. Over-stemming toward a
    shorter prefix therefore only ever increases recall — the real risk
    is short roots colliding ("art" inside "smart"), which the minimum
    length guard handles.
    """
    w = word.lower()
    if len(w) >= 6 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) >= 5 and w.endswith("ed"):
        w = w[:-2]
    if len(w) >= 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def find_intent_gaps(harvest, video_df, min_query_len=3):
    """
    Cross-reference harvested queries against the video dataset.

    A query people type that maps to few existing videos is the
    interesting case — real demand, thin supply. This is a crude
    lexical match, not semantic: it counts how many video titles
    contain all the query's significant words (stemmed, see _stem).

    Being crude matters here and is stated rather than hidden. A video
    titled "The DRAM Cycle Explained" does answer "how do memory
    prices work" but shares no words with it, so it would be scored as
    unserved. Treat low coverage as a prompt to look, not a verdict.
    """
    if video_df is None or video_df.empty or "title" not in video_df.columns:
        return []
    titles = [str(t).lower() for t in video_df["title"].fillna("")]
    rows = []
    for query, weight in harvest.get("queries", {}).items():
        words = [_stem(w) for w in _tokenize(query)
                 if w not in _STOPWORDS and w not in QUERY_FILLER
                 and len(w) >= min_query_len]
        words = [w for w in words if len(w) >= min_query_len]
        if not words:
            continue
        matches = sum(1 for t in titles if all(w in t for w in words))
        rows.append({
            "query": query,
            "autocomplete_weight": weight,
            "videos_matching": matches,
            "coverage": round(matches / max(len(titles), 1), 4),
        })
    # Rank: frequently-suggested queries with few matching videos first.
    rows.sort(key=lambda r: (-r["autocomplete_weight"], r["videos_matching"]))
    return rows


def export_intent_report(seeds, video_df, niche_name, alphabet=True,
                         depth=1, stem="intent_gaps", title=None) -> dict:
    """
    Harvest, cross-reference, and write <stem>.md + <stem>.csv.

    stem is parameterised so a cluster-focused run can be saved
    alongside a general one instead of overwriting it. The two answer
    different questions and are worth keeping side by side:

      intent_gaps        seeded from the terms that DEFINE the niche —
                         "what does this market search for at all"
      cluster_intent     seeded from the breakout cluster topics —
                         "what does the market search for around the
                         parts that are already winning"
    """
    harvest = harvest_autocomplete(seeds, alphabet=alphabet, depth=depth)
    if not harvest["queries"]:
        print("  (no autocomplete results — endpoint unreachable or blocked)")
        return {}

    gaps = find_intent_gaps(harvest, video_df)
    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "signals")
    md_path = os.path.join(out_dir, f"{stem}.md")
    csv_path = os.path.join(out_dir, f"{stem}.csv")

    pd.DataFrame(gaps).to_csv(csv_path, index=False)

    unserved = [g for g in gaps if g["videos_matching"] == 0]
    thin = [g for g in gaps if 1 <= g["videos_matching"] <= 3]

    L = [f"# {title or 'Search intent gaps'} — {niche_name}", ""]
    L.append(f"Seeded from: {', '.join(seeds[:12])}"
             + (f" (+{len(seeds)-12} more)" if len(seeds) > 12 else ""))
    L.append("")
    L.append(f"Harvested **{len(harvest['queries']):,} distinct queries** "
             f"from YouTube autocomplete across "
             f"{harvest['attempted']} lookups"
             + (f" ({harvest['failed']} failed)" if harvest["failed"] else "")
             + ".")
    L.append("")
    L.append("These are real strings people type into the search box. "
             "Coverage is how many videos in the collected dataset have "
             "titles containing all of the query's significant words.")
    L.append("")
    L.append("> **This match is lexical, not semantic.** A video called "
             "\"The DRAM Cycle Explained\" genuinely answers \"how do "
             "memory prices work\" but shares no words with it, so it "
             "would show here as unserved. Low coverage is a reason to "
             "go look, not proof of a gap.")
    L.append("")

    if unserved:
        L.append(f"## No matching video titles ({len(unserved)})")
        L.append("")
        L.append("| query | suggested by N lookups |")
        L.append("|---|---|")
        for g in unserved[:40]:
            L.append(f"| {g['query']} | {g['autocomplete_weight']} |")
        L.append("")

    if thin:
        L.append(f"## Thin coverage — 1 to 3 matching titles ({len(thin)})")
        L.append("")
        L.append("| query | suggested by | matching videos |")
        L.append("|---|---|---|")
        for g in thin[:40]:
            L.append(f"| {g['query']} | {g['autocomplete_weight']} | "
                     f"{g['videos_matching']} |")
        L.append("")

    L.append("## Hand this to an AI")
    L.append("")
    L.append("Paste this file into Claude or ChatGPT and ask:")
    L.append("")
    L.append("1. Which of these queries are **genuinely unserved** versus "
             "which are answered by existing videos that just use "
             "different words? Use your own knowledge of the topic.")
    L.append("2. Group the unserved queries into **intent clusters** — "
             "what does the person searching each group actually want?")
    L.append("3. For the strongest cluster, what would a video that "
             "*actually* answers it look like?")
    L.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return {"md": md_path, "csv": csv_path,
            "queries": len(harvest["queries"]),
            "unserved": len(unserved)}


def suggest_seeds(niche_name, max_clusters=6):
    """
    Where autocomplete seeds should come from, in priority order.

    1. THE BUILD'S OWN SEED QUERIES. The niche was collected using a
       specific set of terms ("personal finance, investing, stock
       market, ..."), stored in the dataset metadata. Those terms
       DEFINE the niche, so they are the correct starting point.
       Option 7 previously defaulted to the niche DISPLAY NAME instead
       — for a niche called "finance" built from five specific seeds,
       that threw away four fifths of the definition.

    2. DISCOVERED CLUSTER TOPICS. After a discovery run, the clusters
       are the phrases that are actually winning in this niche.
       Expanding those with autocomplete answers a sharper question
       than the build seeds do: not "what is this niche about" but
       "what is the language people use around the parts of it that
       are working". A cluster called "memory crash" expands into
       "memory crash 2026", "memory crash explained" — watching data
       and searching data pointed at the same thing.

    Returns (seeds, source_description). Falls back to the niche name
    only when neither source is available.
    """
    seeds, notes = [], []

    meta = None
    try:
        import collector as _c
        meta = _c.load_metadata(niche_name)
    except Exception:
        pass
    if meta and meta.get("query"):
        build_seeds = [p.strip() for p in str(meta["query"]).split(",") if p.strip()]
        if build_seeds:
            seeds.extend(build_seeds)
            notes.append(f"{len(build_seeds)} build seed(s)")

    # Cluster topics from the most recent discovery run, if one exists.
    try:
        import csv as _csv
        from config import EXPORTS_DIR as _E
        from paths import export_dir_for as _edf
        p = os.path.join(_edf(_E, niche_name, "breakouts"), "breakout_clusters.csv")
        if os.path.exists(p):
            with open(p, newline="", encoding="utf-8") as f:
                rows = list(_csv.DictReader(f))
            tops = [r.get("topic") for r in rows[:max_clusters] if r.get("topic")]
            new = [t for t in tops if t not in seeds]
            if new:
                seeds.extend(new)
                notes.append(f"{len(new)} cluster topic(s)")
    except Exception:
        pass

    if not seeds:
        seeds = [niche_name]
        notes.append("niche name (no build metadata or clusters found)")
    return seeds, " + ".join(notes)


def cluster_seeds(niche_name, max_clusters=10):
    """
    Cluster topics ONLY, from the most recent discovery run.

    Returns [] when no discovery has been run — the caller uses that to
    send the user back to the menu rather than silently falling back to
    the niche name and producing a report labelled "clusters" that
    contains nothing of the sort.
    """
    try:
        import csv as _csv
        p = os.path.join(export_dir_for(EXPORTS_DIR, niche_name, "breakouts"),
                         "breakout_clusters.csv")
        if not os.path.exists(p):
            return []
        with open(p, newline="", encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
        return [r["topic"] for r in rows[:max_clusters] if r.get("topic")]
    except Exception:
        return []


# ============================================================
# REDDIT SPOTTER
# ============================================================

def parse_reddit_listing(text):
    """
    Extract posts from Reddit's public JSON listing.

    Split from the fetch so the shape handling is testable offline.
    Returns [{title, score, created_utc, num_comments, permalink}].
    """
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    children = (data.get("data") or {}).get("children") or []
    posts = []
    for c in children:
        d = c.get("data") or {}
        if not d.get("title"):
            continue
        posts.append({
            "title": d.get("title", ""),
            "score": int(d.get("score", 0) or 0),
            "created_utc": float(d.get("created_utc", 0) or 0),
            "num_comments": int(d.get("num_comments", 0) or 0),
            "permalink": d.get("permalink", ""),
            "subreddit": d.get("subreddit", ""),
        })
    return posts


def fetch_subreddit(subreddit, listing="hot", limit=100):
    """
    Fetch one subreddit listing. Returns [] on any failure.

    listing: "hot" for what's currently prominent, "new" for raw
    incoming, "rising" for posts gaining traction fast. "rising" is the
    most useful for early detection but returns fewer items.
    """
    url = (f"https://www.reddit.com/r/{urllib.parse.quote(subreddit)}/"
           f"{listing}.json?limit={int(limit)}")
    text = _get(url, headers={"User-Agent": REDDIT_UA})
    time.sleep(REDDIT_DELAY)
    return parse_reddit_listing(text)


def score_post_velocity(post, now=None):
    """
    Upvotes per hour since posting.

    Same shape of idea as outlier_ratio for videos: raw score rewards
    whatever has been up longest, velocity rewards what is climbing
    now. A 6-hour-old post at 400 points is a stronger signal than a
    3-day-old post at 900.
    """
    now = now or time.time()
    age_hours = max((now - post.get("created_utc", now)) / 3600.0, 0.5)
    return post.get("score", 0) / age_hours


def find_early_signals(posts, video_df, min_score=REDDIT_MIN_SCORE,
                       top_terms=40):
    """
    Terms hot on Reddit but rare in the YouTube dataset.

    Weighted by post velocity so a term riding a fast-climbing thread
    outranks one mentioned in an old popular post. Cross-referenced
    against video titles the same crude lexical way as intent gaps —
    same caveat applies.
    """
    now = time.time()
    weighted = Counter()
    for p in posts:
        if p.get("score", 0) < min_score:
            continue
        v = score_post_velocity(p, now)
        for w in _tokenize(p.get("title", "")):
            if w in _STOPWORDS or len(w) < 4:
                continue
            weighted[w] += v

    if not weighted:
        return []

    titles = ([str(t).lower() for t in video_df["title"].fillna("")]
              if video_df is not None and "title" in getattr(video_df, "columns", [])
              else [])
    n_titles = max(len(titles), 1)

    rows = []
    for term, weight in weighted.most_common(top_terms * 3):
        in_videos = sum(1 for t in titles if term in t)
        rows.append({
            "term": term,
            "reddit_weight": round(weight, 1),
            "videos_mentioning": in_videos,
            "video_share": round(in_videos / n_titles, 4),
            # High reddit weight, low video presence = the interesting
            # quadrant. +1 avoids division by zero and damps the
            # advantage of terms with literally zero coverage, which are
            # often just noise words.
            "gap_score": round(weight / (in_videos + 1), 1),
        })
    rows.sort(key=lambda r: -r["gap_score"])
    return rows[:top_terms]


def export_reddit_report(subreddits, video_df, niche_name,
                         listing="hot", limit=100) -> dict:
    """Fetch subreddits, find early signals, write reddit_signals.md."""
    if isinstance(subreddits, str):
        subreddits = [s.strip() for s in subreddits.split(",") if s.strip()]

    all_posts = []
    reached, unreachable = [], []
    for sub in subreddits:
        print(f"  fetching r/{sub} ({listing})...")
        posts = fetch_subreddit(sub, listing=listing, limit=limit)
        if posts:
            reached.append(sub)
            all_posts.extend(posts)
        else:
            unreachable.append(sub)

    if not all_posts:
        print("  (no posts retrieved — Reddit unreachable, rate-limited, "
              "or all subreddit names wrong)")
        return {}

    signals = find_early_signals(all_posts, video_df)
    out_dir = export_dir_for(EXPORTS_DIR, niche_name, "signals")
    md_path = os.path.join(out_dir, "reddit_signals.md")
    csv_path = os.path.join(out_dir, "reddit_signals.csv")
    pd.DataFrame(signals).to_csv(csv_path, index=False)

    now = time.time()
    hot_posts = sorted(all_posts, key=lambda p: -score_post_velocity(p, now))[:20]

    L = [f"# Reddit early signals — {niche_name}", ""]
    L.append(f"Read **{len(all_posts):,} posts** from "
             f"{len(reached)} subreddit(s): "
             + ", ".join(f"r/{s}" for s in reached))
    if unreachable:
        L.append("")
        L.append("> Could not reach: "
                 + ", ".join(f"r/{s}" for s in unreachable)
                 + ". Reddit rate-limits unauthenticated reads; try again "
                   "in a few minutes or reduce the subreddit list.")
    L.append("")
    L.append("## What this is for")
    L.append("")
    L.append("Discussion moves before video production does — someone "
             "has to notice a topic, script it, record it and edit it. "
             "Terms with high Reddit velocity and low YouTube presence "
             "are candidates for covering before the wave arrives.")
    L.append("")
    L.append("> **Caveat worth keeping in mind.** The premise that "
             "social chatter reliably precedes YouTube coverage by a "
             "predictable interval is widely repeated but, as far as I "
             "know, never actually demonstrated. It is plausible for "
             "news-driven topics and probably false for evergreen ones. "
             "Treat what follows as leads to investigate.")
    L.append("")

    if signals:
        L.append("## Terms hot on Reddit, thin on YouTube")
        L.append("")
        L.append("| term | reddit velocity | videos mentioning | gap score |")
        L.append("|---|---|---|---|")
        for s in signals[:30]:
            L.append(f"| {s['term']} | {s['reddit_weight']} | "
                     f"{s['videos_mentioning']} | {s['gap_score']} |")
        L.append("")

    L.append("## Fastest-climbing posts right now")
    L.append("")
    for p in hot_posts:
        vel = score_post_velocity(p, now)
        title = str(p["title"])[:95]
        L.append(f"- **{title}**")
        L.append(f"    r/{p['subreddit']} · {p['score']:,} points · "
                 f"{vel:.0f} pts/hr · {p['num_comments']} comments")
        if p.get("permalink"):
            L.append(f"    https://www.reddit.com{p['permalink']}")
    L.append("")
    L.append("## Hand this to an AI")
    L.append("")
    L.append("1. Which of these terms represent a **real emerging topic** "
             "versus subreddit in-jokes, tickers or recurring noise?")
    L.append("2. For the genuine ones, is this a news spike that will be "
             "stale in a week, or the start of something durable?")
    L.append("3. What would a video covering the strongest one look "
             "like, and how soon would it need to ship?")
    L.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return {"md": md_path, "csv": csv_path,
            "posts": len(all_posts), "signals": len(signals),
            "unreachable": unreachable}
