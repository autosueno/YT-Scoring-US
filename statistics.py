"""
statistics.py

Distribution-level statistics for a niche/topic video DataFrame.

Owns everything that summarizes a metric ACROSS videos — percentiles,
mean/median/std, MAD, IQR, CV, skew. This is deliberately separate from
metrics.py (which computes PER-VIDEO features) and from collector.py
(which only collects, saves, loads, and archives).

Output schema (one row per numeric metric):
    Metric, Mean, Median, Std, Min, Max, IQR, MAD, CV, Skewness,
    P1, P5, P10, P20, P25, P30, P40, P50, P60, P70, P75, P80, P90, P95, P99

baseline.py reads the resulting statistics.csv back in for percentile
lookups, so the column set here and the percentile points there must
stay in sync.

Public API:
    generate_statistics(df) -> stats_df      (pure; no I/O)
    save_statistics(df, path) -> stats_df     (generates, then writes CSV)

Note: named `statistics.py`, which shadows the standard-library
`statistics` module for absolute imports within this package's
directory. Nothing in this project imports the stdlib module, so there
is no conflict; be aware of it if you add code that needs stdlib
`statistics`.
"""

import os

import pandas as pd

from config import STATISTICS_PERCENTILE_POINTS


# Numeric columns that are categorical-in-disguise (IDs, hour/month
# buckets) — summarizing them as distributions is meaningless.
_STATS_EXCLUDE_COLUMNS = {"category_id", "upload_hour", "upload_month"}

# Percentile points stored per metric. Imported from config so this
# module (the writer) and baseline.py (the reader) can never drift apart.
_PERCENTILE_POINTS = STATISTICS_PERCENTILE_POINTS


def generate_statistics(df):
    """
    Compute one summary row per numeric metric and return the stats
    DataFrame. Pure function — performs no disk I/O.

    Boolean feature columns (small_creator, has_tutorial, ...) are
    EXCLUDED explicitly: pandas counts bools as numeric, but a
    distribution over True/False is meaningless — the mean of a bool is a
    RATE and belongs in insights.py's summaries, not here. Only genuinely
    continuous metrics get percentile treatment.
    """
    numeric = df.select_dtypes(include="number")
    bool_cols = set(df.select_dtypes(include="bool").columns)
    numeric_cols = [
        c for c in numeric.columns
        if c not in _STATS_EXCLUDE_COLUMNS and c not in bool_cols
    ]

    rows = []
    for col in numeric_cols:
        series = df[col].dropna()
        if series.empty:
            continue

        mean = series.mean()
        median = series.median()
        std = series.std()
        p25 = series.quantile(0.25)
        p75 = series.quantile(0.75)
        mad = (series - median).abs().median()
        cv = (std / mean) if mean else None

        row = {
            "Metric": col,
            "N": int(series.count()),   # sample size behind this row
            "Mean": mean,
            "Median": median,
            "Std": std,
            "Min": series.min(),
            "Max": series.max(),
            "IQR": p75 - p25,
            "MAD": mad,
            "CV": cv,
            "Skewness": series.skew(),
        }
        for p in _PERCENTILE_POINTS:
            row[f"P{p}"] = series.quantile(p / 100)

        rows.append(row)

    return pd.DataFrame(rows)


def save_statistics(df, path):
    """
    Generate statistics from the video DataFrame `df` and write them to
    `path` as CSV. Returns the stats DataFrame.
    """
    stats_df = generate_statistics(df)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stats_df.to_csv(path, index=False)
    return stats_df
