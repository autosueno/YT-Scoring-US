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
    n_matched = len(matched & set(int(i) for i in scored_df.index))
    return {
        "videos": total,
        "matched": n_matched,
        "unmatched": total - n_matched,
        "matched_share": n_matched / total,
        "unmatched_share": (total - n_matched) / total,
        "clusters": len(clusters),
    }


def coverage_note(cov):
    """A sentence for the report, or None when there is nothing to say."""
    if not cov:
        return None
    from calibration import CONTAMINATION_WARN_SHARE
    if cov["unmatched_share"] < CONTAMINATION_WARN_SHARE:
        return None
    return (
        f"{cov['unmatched_share']:.0%} of videos ({cov['unmatched']:,} of "
        f"{cov['videos']:,}) matched none of this niche's {cov['clusters']} "
        f"topic clusters. Some of that is normal variety, but a share this "
        f"large suggests the collection is broader than the niche — which "
        f"inflates the decent-to-hit ratio. Treat the spread reading as an "
        f"upper bound."
    )
