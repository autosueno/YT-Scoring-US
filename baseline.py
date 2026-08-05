"""
baseline.py

Loads a niche's pre-computed statistics (datasets/<niche>/statistics.csv)
and exposes per-metric percentile lookups for the grader.

Responsibilities:
  - Parse statistics.csv once and index it by metric name.
  - Answer "at what percentile does value X fall for metric M?"
  - Answer "what is the Pnn value for metric M?"
  - Expose the full set of available metrics.

Does NOT:
  - Call the YouTube API.
  - Compute metrics (that's metrics.py).
  - Collect or save data (that's collector.py).
  - Score or grade anything (that's grader.py).

The statistics.csv written by collector.py has one row per metric with
columns: Metric, Mean, Median, Std, Min, Max, IQR, MAD, CV, Skewness,
P1, P5, P10, P20, P25, P30, P40, P50, P60, P70, P75, P80, P90, P95, P99.
"""

import json
import os

import pandas as pd

from paths import statistics_path, insights_path
from config import STATISTICS_PERCENTILE_POINTS

# Percentile columns present in statistics.csv, in ascending order.
# Imported from config so the reader (here) and the writer
# (statistics.py) can never drift apart.
_PERCENTILE_COLS = STATISTICS_PERCENTILE_POINTS


class Baseline:
    """
    Holds the statistical profile for a single niche.

    Instantiate once per grading session; reuse across all topic lookups.

        baseline = Baseline("personal finance")
        pct = baseline.percentile_of("views_per_hour", 120.5)
        p90 = baseline.percentile_value("views_per_hour", 90)
    """

    def __init__(self, niche_name: str):
        self.niche_name = niche_name
        self._stats: dict[str, dict] = {}   # metric → {P1:v, P5:v, ..., Mean:v, ...}
        self._competition: dict = {}        # niche-level competition_summary
        self._content: dict = {}            # niche-level title_pattern_summary
        self._supply: dict = {}             # niche-level supply_summary
        self._momentum: dict = {}           # niche-level momentum_summary
        self._opportunity: dict = {}        # niche-level opportunity_indicators
        self._load(niche_name)
        self._load_insights(niche_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def metrics(self) -> list[str]:
        """Names of all metrics available in this baseline."""
        return list(self._stats.keys())

    def has_metric(self, metric: str) -> bool:
        return metric in self._stats

    def competition(self) -> dict:
        """Niche-level competition_summary (from insights.json), or {}."""
        return dict(self._competition)

    def content_profile(self) -> dict:
        """Niche-level title_pattern_summary (from insights.json), or {}."""
        return dict(self._content)

    def supply(self) -> dict:
        """Niche-level supply_summary (from insights.json), or {}."""
        return dict(self._supply)

    def momentum(self) -> dict:
        """Niche-level momentum_summary (from insights.json), or {}."""
        return dict(self._momentum)

    def opportunity_indicators(self) -> dict:
        """Niche-level opportunity_indicators (from insights.json), or {}."""
        return dict(self._opportunity)

    def percentile_of(self, metric: str, value: float) -> float:
        """
        Estimate the percentile (0–100) at which `value` falls within
        the niche distribution for `metric`.

        Uses linear interpolation between the stored percentile points.
        Returns 0 if value <= P1, 100 if value >= P99.
        """
        if metric not in self._stats:
            raise KeyError(f"Metric {metric!r} not in baseline for {self.niche_name!r}.")
        row = self._stats[metric]

        # Build parallel arrays of (percentile, value) knots.
        xs = []   # percentile points
        ys = []   # metric values at those points
        for p in _PERCENTILE_COLS:
            key = f"P{p}"
            # pd.notna (not np.isnan) — statistics.csv can contain "",
            # None or "N/A" in a percentile cell, and np.isnan raises
            # TypeError on those instead of simply reporting "missing".
            if key in row and pd.notna(row[key]):
                try:
                    ys.append(float(row[key]))
                except (TypeError, ValueError):
                    continue   # unparseable cell -> treat as missing
                xs.append(p)

        if not xs:
            return 50.0  # degenerate — no percentile data

        if value <= ys[0]:
            return float(xs[0])
        if value >= ys[-1]:
            return float(xs[-1])

        # Binary search for the bracket, then interpolate.
        for i in range(len(ys) - 1):
            if ys[i] <= value <= ys[i + 1]:
                span_v = ys[i + 1] - ys[i]
                span_p = xs[i + 1] - xs[i]
                if span_v == 0:
                    return float(xs[i])
                frac = (value - ys[i]) / span_v
                return float(xs[i] + frac * span_p)

        return 50.0  # fallback (shouldn't reach)

    def percentile_value(self, metric: str, percentile: int) -> float:
        """
        Return the stored value at a specific percentile point.

        `percentile` must be one of: 1, 5, 10, 20, 25, 30, 40, 50,
        60, 70, 75, 80, 90, 95, 99.
        """
        if metric not in self._stats:
            raise KeyError(f"Metric {metric!r} not in baseline for {self.niche_name!r}.")
        key = f"P{percentile}"
        row = self._stats[metric]
        if key not in row:
            raise ValueError(
                f"Percentile P{percentile} not stored. "
                f"Available: {[f'P{p}' for p in _PERCENTILE_COLS]}"
            )
        return float(row[key])

    def stat(self, metric: str, stat_name: str) -> float:
        """
        Return a named statistic for a metric.

        `stat_name` is a column from statistics.csv: 'Mean', 'Median',
        'Std', 'Min', 'Max', 'IQR', 'MAD', 'CV', 'Skewness'.
        """
        if metric not in self._stats:
            raise KeyError(f"Metric {metric!r} not in baseline for {self.niche_name!r}.")
        row = self._stats[metric]
        if stat_name not in row:
            raise KeyError(f"Stat {stat_name!r} not found for metric {metric!r}.")
        return float(row[stat_name])

    def median(self, metric: str) -> float:
        """Convenience wrapper for stat(metric, 'Median')."""
        return self.stat(metric, "Median")

    def mean(self, metric: str) -> float:
        """Convenience wrapper for stat(metric, 'Mean')."""
        return self.stat(metric, "Mean")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self, niche_name: str):
        path = statistics_path(niche_name)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No statistics file found for niche {niche_name!r} at {path}. "
                "Run collector.build_niche_database() first."
            )
        df = pd.read_csv(path)
        if "Metric" not in df.columns:
            raise ValueError(f"statistics.csv at {path} is missing the 'Metric' column.")

        # Fail fast on a malformed/stale statistics file rather than
        # deferring the error to some later percentile_value() call.
        required = {f"P{p}" for p in _PERCENTILE_COLS} | {"Mean", "Median"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(
                f"statistics.csv at {path} is missing required column(s): "
                f"{missing}. It was probably written by an older version — "
                "rebuild the niche database to regenerate it."
            )

        for _, row in df.iterrows():
            metric = str(row["Metric"])
            self._stats[metric] = row.to_dict()

    def _load_insights(self, niche_name: str):
        """
        Load niche-level competition + content profiles from insights.json.

        Optional: a niche built before these summaries existed simply yields
        empty dicts, and the grader falls back to neutral scores for the
        pillars that depend on them.
        """
        path = insights_path(niche_name)
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        self._competition = data.get("competition_summary", {}) or {}
        self._content = data.get("title_pattern_summary", {}) or {}
        self._supply = data.get("supply_summary", {}) or {}
        self._momentum = data.get("momentum_summary", {}) or {}
        self._opportunity = data.get("opportunity_indicators", {}) or {}


# ============================================================
# FACTORY / CONVENIENCE
# ============================================================

def from_frame(df, label: str = "in-memory") -> Baseline:
    """
    Build a Baseline directly from a DataFrame, without touching disk.

    Needed for multi-window grading, where each window needs its own
    reference population. Writing three temporary niches to disk and
    loading them back would work but leaves debris and is slower for no
    benefit — the statistics and insights are computed the same way
    either way.
    """
    import statistics as _sm
    import insights as _ins

    bl = Baseline.__new__(Baseline)
    bl.niche_name = label
    bl._stats = {}
    bl._competition = {}
    bl._content = {}
    bl._supply = {}
    bl._momentum = {}
    bl._opportunity = {}

    stats = _sm.generate_statistics(df)
    for _, row in stats.iterrows():
        bl._stats[str(row["Metric"])] = row.to_dict()

    ins = _ins.generate_insights(df)
    bl._competition = ins.get("competition_summary", {}) or {}
    bl._content = ins.get("title_pattern_summary", {}) or {}
    bl._supply = ins.get("supply_summary", {}) or {}
    bl._momentum = ins.get("momentum_summary", {}) or {}
    bl._opportunity = ins.get("opportunity_indicators", {}) or {}
    return bl


def load(niche_name: str) -> Baseline:
    """
    Load and return a Baseline for `niche_name`.

    This is the primary entry point for grader.py:

        from baseline import load
        bl = load("personal finance")
    """
    return Baseline(niche_name)
