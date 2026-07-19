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
    """Accumulates quota usage for one collector run."""

    def __init__(self):
        self._used: int = 0
        self._breakdown: dict[str, int] = {}
        self._warned: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        self._maybe_warn()
        return cost

    def summary(self) -> dict:
        """Return a dict describing current usage."""
        return {
            "used": self._used,
            "limit": QUOTA_DAILY_LIMIT,
            "remaining_estimate": QUOTA_DAILY_LIMIT - self._used,
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
