"""
quota_tracker.py

Tracks YouTube Data API v3 quota usage within a single run.

Design decisions:
  - Stateless between runs: quota resets at midnight Pacific — a
    cross-run counter would need network sync or timezone logic that
    adds more complexity than it saves. Use quota_history.csv (written
    by collector.py) to audit usage over time.
  - In-process only: one QuotaTracker instance per collector run.
    Pass it through the call stack rather than using a global.
  - Warning on threshold: emits a console warning when cumulative usage
    in this run crosses QUOTA_WARN_THRESHOLD. Does NOT hard-stop —
    it's better to finish a build than leave a half-written dataset.

Usage:
    tracker = QuotaTracker()
    tracker.charge("search.list", count=40)   # 40 pages × 100 = 4 000
    tracker.charge("videos.list")             # 1 call  × 1   = 1
    print(tracker.summary())
"""

from config import QUOTA_COSTS, QUOTA_DAILY_LIMIT, QUOTA_WARN_THRESHOLD


class QuotaTracker:
    """Counts API units. Optionally reports each charge to an
    ApiKeyManager so per-key usage (units AND percent) can be shown."""

    """Accumulates quota usage for one collector run."""

    def __init__(self):
        self._used: int = 0
        self._breakdown: dict[str, int] = {}
        self._warned: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attach_keys(self, keys):
        """Link a key manager so charges are attributed per key."""
        self._keys = keys

    def charge(self, operation: str, count: int = 1) -> int:
        """
        Record `count` calls of `operation` and return the units charged.

        Args:
            operation: One of the keys in config.QUOTA_COSTS
                       (e.g. "search.list", "videos.list").
            count:     Number of API calls made.

        Returns:
            Units charged for this charge() call.

        Raises:
            ValueError: If `operation` is not in QUOTA_COSTS.
        """
        if operation not in QUOTA_COSTS:
            raise ValueError(
                f"Unknown operation {operation!r}. "
                f"Valid operations: {list(QUOTA_COSTS)}"
            )
        cost = QUOTA_COSTS[operation] * count
        self._used += cost
        self._breakdown[operation] = self._breakdown.get(operation, 0) + cost
        # Attribute the spend to whichever key is currently active, so
        # usage can be reported per key as units AND percent.
        keys = getattr(self, "_keys", None)
        if keys is not None:
            keys.note_units(cost)
        self._maybe_warn()
        return cost

    def summary(self) -> dict:
        """Return a dict describing current usage."""
        keys = getattr(self, "_keys", None)
        capacity = keys.total_capacity if keys is not None else QUOTA_DAILY_LIMIT
        return {
            "used": self._used,
            "limit": QUOTA_DAILY_LIMIT,
            "capacity": capacity,
            "pct_used": (100.0 * self._used / capacity) if capacity else 0.0,
            "remaining_estimate": capacity - self._used,
            "per_key": keys.usage_report() if keys is not None else None,
            "breakdown": dict(self._breakdown),
        }

    def __str__(self) -> str:
        s = self.summary()
        lines = [
            f"Quota used this run: {s['used']:,} / {s['limit']:,} units",
            f"  Estimated remaining today: {s['remaining_estimate']:,}",
        ]
        for op, units in s["breakdown"].items():
            lines.append(f"    {op}: {units:,} units")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _maybe_warn(self):
        if not self._warned and self._used >= QUOTA_WARN_THRESHOLD:
            self._warned = True
            print(
                f"\n⚠️  Quota warning: {self._used:,} units used in this run "
                f"(threshold: {QUOTA_WARN_THRESHOLD:,} / {QUOTA_DAILY_LIMIT:,} daily limit). "
                "Future calls may fail if today's quota is already partially consumed.\n"
            )
