"""
coverage.py — how much of a build actually belongs to its niche.

WHY
---
Search pulls in adjacent content. That is expected and usually harmless:
a "home espresso" search returning a few general coffee videos does not
invalidate anything.

It stops being harmless when the share gets large, because it moves the
spread reading without moving the market. Injecting videos drawn from
OTHER niches into a measured build inflates the decent-to-hit ratio by a
median of 38% at 20% contamination — the number rises for a reason that
has nothing to do with how hard the niche is to enter.

The ranking survives that (rank correlation 0.955, see calibration.py),
which is why the label is still trustworthy. But a reader deserves to
know when a specific niche's number is standing on soft ground, rather
than being handed a figure with invisible error bars.

WHAT IS MEASURED
----------------
Clusters are built from recurring title phrases shared across multiple
channels — CLUSTER_MIN_VIDEOS videos from CLUSTER_MIN_CHANNELS distinct
channels. A video that matches none of its own niche's clusters shares
no recurring vocabulary with the rest of the build.

That is a PROXY, and a loose one. Unmatched does not mean off-topic: a
genuinely novel video in the right niche also matches nothing. So this
warns, it never filters, and the threshold is set high enough
(CONTAMINATION_WARN_SHARE) that ordinary vocabulary variety does not
trip it.
"""


# loose_themes() was built, then removed, then ALMOST rebuilt a second
# time in this same file's history — worth recording why, since it is a
# real methodological near-miss, not just a reverted experiment.
#
# It existed to surface themes hiding below build_clusters' old internal
# top-40 truncation. That truncation was the actual bug (see
# build_clusters' docstring): once fixed, "bronze age" and most other
# real themes show up in the normal, full cluster output on their own —
# verified directly: 'bronze age' appears in the untruncated cluster
# list with 732 videos, 654 channels, cluster_score 52.5.
#
# THE FIRST VERSION OF THIS COMMENT OVERCLAIMED FULL REDUNDANCY. Checked
# more carefully — across 5 real niches, not one bronze-age example —
# whether every raw bigram a loose-matching approach would find is
# actually covered by the full cluster list. Most of what turned up
# uncovered was exactly the low-value filler CLUSTER_WEAK_WORDS is
# designed to reject ("year old", "need know", "should buy"), which
# confirms the redundancy claim for that portion. But a real, specific
# exception was found: phrases where a YEAR is the distinguishing
# content, not filler — "june 1944", "day 1944" — were structurally
# unable to become clusters at all, because build_clusters blanket-
# excluded every digit token, while a loose bigram approach (which
# never filtered digits) would have caught them. That gap was real, not
# imagined, and loose_themes was NOT fully redundant with the pre-fix
# build_clusters on this specific point.
#
# Fixed at the root instead of by resurrecting loose_themes: see
# _is_historical_year in discover.py. build_clusters now lets a 4-digit
# token through when it reads as a historical year (more than ~10 years
# past) rather than blanket-excluding every digit — this let "june 1944"
# etc. form real clusters directly, tested to confirm it does not
# reintroduce recent-year freshness-tag noise ("tutorial 2026") across
# all 5 niches checked. With that fix in place, the loose-bigram
# candidates still uncovered were verified to be filler again, which is
# what makes loose_themes redundant NOW — a claim that took two rounds
# of checking to actually earn, not the one-example version first
# written here.
#
# It was rebuilt once anyway, in a later pass — because a genuine,
# separate bug (loose_themes reported MEAN views, not median, which
# made a badly-underperforming theme look demand-proven) was found and
# fixed without first re-checking whether the function it lived in was
# still needed at all. Caught before shipping by testing against real
# data: the rebuilt version's top result was identical to what
# build_clusters already returns, just with weaker filtering. Reverted
# again. The lesson travels with the code: fix the right layer, and
# check whether the layer still needs to exist before fixing it.


def topic_coverage(scored_df, clusters):
    """
    Share of videos that matched at least one of the niche's own topic
    clusters, and the share that matched none.

    Returns None when clustering did not run or produced nothing — an
    absent measurement, not a coverage of zero. Reporting 0% coverage
    for a build that simply has no clusters would be a false alarm.
    """
    if scored_df is None or len(scored_df) == 0 or not clusters:
        return None
    matched = set()
    for c in clusters:
        for i in c.get("video_indices") or []:
            matched.add(int(i))
    total = int(len(scored_df))
    if total <= 0:
        return None
    idx = set(int(i) for i in scored_df.index)
    n_matched = len(matched & idx)

    # A handful of unmatched titles, so a person can see WHY they didn't
    # cluster rather than trusting a guess about the cause. Checked on
    # ancient_history's real 2,903-video build: 93% unmatched, and a
    # sample of the unmatched titles showed real, on-topic content
    # ("The Bronze Age Collapse", "Roman Rules of War") that simply never
    # repeated a phrase closely enough with another channel to cluster —
    # not contamination. Clustering catches shared WORDING, not shared
    # TOPIC, and a niche full of independently-worded headlines about the
    # same subject can look uncovered while being entirely on-topic. The
    # number alone can't tell those two situations apart; the sample can.
    unmatched_idx = list(idx - matched)
    sample = []
    if unmatched_idx and "title" in scored_df.columns:
        take = scored_df.loc[unmatched_idx, "title"].dropna()
        n = min(5, len(take))
        if n > 0:
            sample = take.sample(n, random_state=0).tolist()

    return {
        "videos": total,
        "matched": n_matched,
        "unmatched": total - n_matched,
        "matched_share": n_matched / total,
        "unmatched_share": (total - n_matched) / total,
        "clusters": len(clusters),
        "unmatched_sample": sample,
    }


def coverage_note(cov):
    """A note for the report, or None when there is nothing to say."""
    if not cov:
        return None
    from calibration import CONTAMINATION_WARN_SHARE
    if cov["unmatched_share"] < CONTAMINATION_WARN_SHARE:
        return None
    lines = [
        f"{cov['unmatched_share']:.0%} of videos ({cov['unmatched']:,} of "
        f"{cov['videos']:,}) matched none of this niche's {cov['clusters']} "
        f"topic clusters."
    ]
    # Two different situations produce this same number, and the count
    # alone cannot tell them apart:
    #   (a) the collection pulled in videos outside the niche, or
    #   (b) every video is on-topic but independently worded, so nothing
    #       repeats closely enough to form a cluster (clustering catches
    #       shared WORDING, not shared TOPIC).
    # Checked directly on a real 93%-unmatched build (ancient_history):
    # it was (b) — "The Bronze Age Collapse", "Roman Rules of War", and
    # similar were all on-topic, just each phrased differently. Asserting
    # (a) as the cause here would have been wrong. So this names both and
    # shows the actual titles, since eyeballing five of them settles it
    # in seconds and a guess cannot.
    lines.append(
        "This could mean the collection is broader than the niche "
        "(inflating the decent-to-hit ratio — treat the spread reading "
        "as an upper bound if so), OR it could mean the content is "
        "on-topic but every creator titles it differently, which is "
        "common in broad, high-variety niches and does not indicate a "
        "problem. Check the sample below to tell which applies here."
    )
    if cov.get("unmatched_sample"):
        lines.append("Unmatched titles, for reference:")
        for t in cov["unmatched_sample"]:
            lines.append(f"  - {t}")
    return "\n".join(lines)
