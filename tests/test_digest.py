"""
Tests for digest.py — attention score formula and anchor floor.

Targets the pure functions: _attention_score and the anchor-floor
logic inside build_digest.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import pytest

from digest import (
    _attention_score, build_digest, _percentile_filter,
    ATTENTION_ACCESSIBILITY_WEIGHT as W,
    ATTENTION_SHARE_FLOOR,
    ANCHOR_FLOOR,
)


# ============================================================
# _attention_score
# ============================================================

class TestAttentionScore:

    def test_zero_share_not_zero(self):
        """A cluster with share=0 must NOT score zero — the floor
        prevents zero-out."""
        cluster = {"cluster_score": 85.0, "small_creator_share": 0.0}
        score = _attention_score(cluster)
        assert score > 0, "Zero share produced zero attention score"

    def test_zero_share_floor_value(self):
        """With share=0, accessibility uses ATTENTION_SHARE_FLOOR."""
        cluster = {"cluster_score": 85.0, "small_creator_share": 0.0}
        score = _attention_score(cluster)
        expected_access = 100.0 * math.sqrt(ATTENTION_SHARE_FLOOR)
        expected = round((1 - W) * 85.0 + W * expected_access, 2)
        assert abs(score - expected) < 0.01

    def test_high_breakout_beats_mediocre(self):
        """An explosive cluster (breakout 85) should beat a mediocre one
        (breakout 35) even if the mediocre one has higher share.

        This was the 'breakout suppression' bug in the old formula."""
        explosive = {"cluster_score": 85.0, "small_creator_share": 0.12}
        mediocre = {"cluster_score": 35.0, "small_creator_share": 0.70}
        assert _attention_score(explosive) > _attention_score(mediocre)

    def test_higher_share_is_better_same_score(self):
        """At equal cluster_score, higher share → higher attention."""
        low_share = {"cluster_score": 70.0, "small_creator_share": 0.10}
        high_share = {"cluster_score": 70.0, "small_creator_share": 0.60}
        assert _attention_score(high_share) > _attention_score(low_share)

    def test_diminishing_returns_on_share(self):
        """Going from 0.05 → 0.30 should have a bigger impact than
        0.50 → 0.75 (sqrt shape)."""
        base = {"cluster_score": 70.0}
        s1 = _attention_score({**base, "small_creator_share": 0.05})
        s2 = _attention_score({**base, "small_creator_share": 0.30})
        s3 = _attention_score({**base, "small_creator_share": 0.50})
        s4 = _attention_score({**base, "small_creator_share": 0.75})
        low_jump = s2 - s1
        high_jump = s4 - s3
        assert low_jump > high_jump

    def test_uses_cluster_score_not_avg_breakout(self):
        """cluster_score (which includes channel evidence) takes
        precedence over avg_breakout."""
        cluster = {"cluster_score": 80.0, "avg_breakout": 50.0,
                   "small_creator_share": 0.30}
        score = _attention_score(cluster)
        # If it used avg_breakout (50) instead of cluster_score (80),
        # the result would be lower
        access = 100.0 * math.sqrt(0.30)
        from_cs = round((1 - W) * 80.0 + W * access, 2)
        from_ab = round((1 - W) * 50.0 + W * access, 2)
        assert score == from_cs
        assert score > from_ab

    def test_falls_back_to_avg_breakout(self):
        """When cluster_score is missing, uses avg_breakout."""
        cluster = {"avg_breakout": 60.0, "small_creator_share": 0.40}
        score = _attention_score(cluster)
        access = 100.0 * math.sqrt(0.40)
        expected = round((1 - W) * 60.0 + W * access, 2)
        assert abs(score - expected) < 0.01

    def test_score_range(self):
        """Result should be in roughly 0-100 range."""
        low = _attention_score({"cluster_score": 0.0,
                                "small_creator_share": 0.0})
        high = _attention_score({"cluster_score": 100.0,
                                 "small_creator_share": 1.0})
        assert low >= 0
        assert high <= 100.1


# ============================================================
# PERCENTILE FILTER
# ============================================================

class TestPercentileFilter:

    def test_empty_input(self):
        cutoff, survivors = _percentile_filter([])
        assert cutoff == 0.0
        assert survivors == []

    def test_drops_below_cutoff(self):
        clusters = [{"videos": i} for i in range(1, 21)]
        cutoff, survivors = _percentile_filter(clusters, pct=0.50)
        assert all(c["videos"] >= cutoff for c in survivors)

    def test_per_file_independence(self):
        """Two files with different video counts should get different
        cutoffs — this is the core per-file principle."""
        small_file = [{"videos": i} for i in range(1, 11)]
        big_file = [{"videos": i * 10} for i in range(1, 11)]
        cut_s, _ = _percentile_filter(small_file)
        cut_b, _ = _percentile_filter(big_file)
        assert cut_b > cut_s
