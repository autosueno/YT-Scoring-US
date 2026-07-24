"""
api_keys.py

Multi-key YouTube API management: load up to MAX_API_KEYS keys from the
environment, rotate to the next key when one returns quotaExceeded, and
signal cleanly when ALL keys are exhausted so the collector can stop
gracefully instead of crashing.

.env keys recognized (via python-dotenv loaded in main.py, or real env):
    YOUTUBE_API_KEY        -> treated as key 1
    YOUTUBE_API_KEY_1..3   -> explicit slots

Usage:
    keys = ApiKeyManager.from_env()
    result = keys.execute(lambda yt: yt.search().list(...))
    # -> returns the API response, rotating keys behind the scenes.
    # -> raises AllKeysExhausted when every key is out of quota.

Design notes:
  - execute() takes a CALLABLE that receives a built client and returns a
    request, so rotation can transparently rebuild the client on the new
    key and retry the same request.
  - Only quota errors rotate. Other API errors (bad request, not found)
    propagate — retrying those on another key would just waste quota.
"""

import json
import os

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import MAX_API_KEYS, DAILY_QUOTA_PER_KEY, RESERVED_KEYS_FOR_TOPICS


class AllKeysExhausted(Exception):
    """Raised when every configured API key has hit its daily quota."""


# Google reason codes that mean "this key is spent / throttled".
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                  "userRateLimitExceeded"}


def _is_quota_error(err: HttpError) -> bool:
    """
    True if the HttpError is a daily-quota / rate exhaustion error.

    Google's YouTube API returns quota errors as HTTP 403 with a structured
    reason ("quotaExceeded" / "rateLimitExceeded" / ...), but under
    real-world conditions also returns HTTP 429 ("Too Many Requests") with
    the same reason strings — 429 is literally the canonical rate-limit
    status code, and a real production trace showed this happening. An
    earlier version accepted only 403; a 429 with a valid quota reason
    would fall through, the retry loop wouldn't trigger, and the caller
    would see the raw HttpError even though healthy sibling keys were
    still available.

    Prefers Google's STRUCTURED payload; falls back to substring matching
    when the body isn't parseable JSON.
    """
    if err.resp is None or err.resp.status not in (403, 429):
        return False

    body = (err.content or b"").decode("utf-8", errors="ignore")

    try:
        payload = json.loads(body)
        error = payload.get("error", {})
        reasons = {e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)}
        if error.get("status"):
            reasons.add(error["status"])
        if reasons:
            return bool(reasons & _QUOTA_REASONS) or "RESOURCE_EXHAUSTED" in reasons
    except (ValueError, AttributeError):
        pass   # not JSON — fall through to text matching

    return any(reason in body for reason in _QUOTA_REASONS)


class ApiKeyManager:
    def __init__(self, keys):
        if not keys:
            raise ValueError(
                "No YouTube API keys found. Set YOUTUBE_API_KEY (or "
                "YOUTUBE_API_KEY_1..YOUTUBE_API_KEY_7) in your .env."
            )
        self._keys = list(keys)
        self._index = 0
        self._client = None
        # Per-key accounting so usage can be reported as units AND percent.
        self._used = [0] * len(self._keys)
        self._dead = [False] * len(self._keys)
        self._pinned = None      # seed-assigned key index, if any
        # Hard reservation: when True, build operations refuse to touch
        # the reserved tail keys even if the non-reserved ones are dead.
        # Previously the reservation was soft — build_key_order preferred
        # the front but fell through to reserved keys when quota ran low,
        # which meant a large build could leave topic grading with no
        # keys at all. That is exactly the failure mode we hit in
        # production. Enabled via reserve_for_topics() before a build.
        self._hard_reserve = False

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls):
        """Collect keys from env: YOUTUBE_API_KEY + YOUTUBE_API_KEY_1..N."""
        keys = []
        base = os.getenv("YOUTUBE_API_KEY")
        if base:
            keys.append(base.strip())
        for i in range(1, MAX_API_KEYS + 1):
            k = os.getenv(f"YOUTUBE_API_KEY_{i}")
            if k and k.strip() not in keys:
                keys.append(k.strip())
        return cls(keys[:MAX_API_KEYS])

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    @property
    def current_key_number(self) -> int:
        """1-based index of the key currently in use."""
        return self._index + 1

    @property
    def exhausted(self) -> bool:
        return all(self._dead) or self._index >= len(self._keys)

    # ------------------------------------------------------------------
    # Per-seed key assignment
    # ------------------------------------------------------------------

    def use_key_for_seed(self, seed_index: int):
        """
        Pin this manager to a dedicated key for one seed (seed 0 -> key 0,
        seed 1 -> key 1, ...). Guarantees a greedy early seed cannot spend
        a later seed's budget. If that key is already dead, the next live
        key is used instead — isolation should never cost capacity.
        """
        if not self._keys:
            return
        want = seed_index % len(self._keys)
        if self._dead[want]:
            want = self._first_live(prefer_from=want)
        self._pinned = want
        if want is not None and want != self._index:
            self._index = want
            self._client = None

    def release_pin(self):
        """Return to normal sequential rotation (used for topic grading)."""
        self._pinned = None

    def use_reserved_keys(self):
        """
        Start on the reserved tail keys (topic grading / refreshes). These
        are the keys builds avoid until last, so cheap operations always
        have quota available.
        """
        if RESERVED_KEYS_FOR_TOPICS <= 0 or len(self._keys) <= RESERVED_KEYS_FOR_TOPICS:
            return
        start = len(self._keys) - RESERVED_KEYS_FOR_TOPICS
        idx = self._first_live(prefer_from=start)
        if idx is not None:
            self._index = idx
            self._client = None

    def reserve_for_topics(self, enable: bool = True):
        """
        Enable the HARD reservation for a niche build.

        With this on, the last RESERVED_KEYS_FOR_TOPICS keys are treated
        as if they don't exist for build purposes: build_key_order won't
        include them, and quota-exhaustion rotation stops at the cut
        instead of falling through to them. The result is that topic
        grading is guaranteed to have at least the reserved keys' full
        daily budget available, even after a build that consumed
        everything else.

        Call this at the top of a niche build; the reservation only
        applies for that operation.
        """
        self._hard_reserve = enable and RESERVED_KEYS_FOR_TOPICS > 0

    def build_key_order(self):
        """
        Indices a niche BUILD should prefer.

        With hard reservation ON: only non-reserved keys, and if all of
        them are dead the build stops rather than eating into the reserve.
        With hard reservation OFF (the default, used by e.g. topic
        grading itself): every key is fair game.
        """
        n = len(self._keys)
        if RESERVED_KEYS_FOR_TOPICS <= 0 or n <= RESERVED_KEYS_FOR_TOPICS:
            return list(range(n))
        cut = n - RESERVED_KEYS_FOR_TOPICS
        if self._hard_reserve:
            return list(range(cut))                    # reserved tail off-limits
        return list(range(cut)) + list(range(cut, n))  # soft: front, then reserve

    def _first_live(self, prefer_from=0):
        n = len(self._keys)
        # In hard-reserve mode, treat the reserved tail as if it doesn't
        # exist. This is the enforcement point — rotation triggered by a
        # quota error will now fall off the end (raising AllKeysExhausted)
        # instead of quietly draining the reserved keys.
        if self._hard_reserve and RESERVED_KEYS_FOR_TOPICS > 0:
            cut = n - RESERVED_KEYS_FOR_TOPICS
            candidates = list(range(prefer_from, cut)) + list(range(0, min(prefer_from, cut)))
        else:
            candidates = list(range(prefer_from, n)) + list(range(0, prefer_from))
        for i in candidates:
            if not self._dead[i]:
                return i
        return None

    # ------------------------------------------------------------------
    # Usage reporting
    # ------------------------------------------------------------------

    def note_units(self, units: int):
        """Record quota spent on the CURRENT key (called by the tracker)."""
        if 0 <= self._index < len(self._used):
            self._used[self._index] += units

    def usage_report(self) -> str:
        """Human-readable per-key usage: units and percent of daily quota."""
        bits = []
        for i, u in enumerate(self._used):
            pct = 100.0 * u / DAILY_QUOTA_PER_KEY if DAILY_QUOTA_PER_KEY else 0
            state = "DEAD" if self._dead[i] else f"{pct:.0f}%"
            bits.append(f"key{i+1}: {u:,}/{DAILY_QUOTA_PER_KEY:,} ({state})")
        return " | ".join(bits)

    @property
    def total_used(self) -> int:
        return sum(self._used)

    @property
    def total_capacity(self) -> int:
        return DAILY_QUOTA_PER_KEY * len(self._keys)

    @property
    def live_keys(self) -> int:
        return sum(1 for d in self._dead if not d)

    # ------------------------------------------------------------------
    # Execution with rotation
    # ------------------------------------------------------------------

    def execute(self, make_request):
        """
        Run `make_request(client)` -> request, execute it, and return the
        response. On quotaExceeded, rotate to the next key and retry the
        SAME request. Raises AllKeysExhausted when no keys remain.
        Non-quota HttpErrors propagate unchanged.
        """
        while not self.exhausted:
            try:
                return make_request(self._get_client()).execute()
            except HttpError as err:
                if not _is_quota_error(err):
                    raise
                self._dead[self._index] = True
                nxt = self._first_live(prefer_from=self._index + 1)
                pct = 100.0 * self._used[self._index] / DAILY_QUOTA_PER_KEY \
                    if DAILY_QUOTA_PER_KEY else 0
                print(
                    f"⚠️  API key #{self._index + 1} is out of quota "
                    f"({self._used[self._index]:,} units, {pct:.0f}%) — "
                    + (f"falling back to key #{nxt + 1}."
                       if nxt is not None else "no live keys left.")
                )
                if nxt is None:
                    self._index = len(self._keys)
                    break
                self._index = nxt
                self._client = None  # force rebuild on the new key
        raise AllKeysExhausted(
            f"All {len(self._keys)} API key(s) have exhausted their daily quota."
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            self._client = build(
                "youtube", "v3", developerKey=self._keys[self._index]
            )
        return self._client
