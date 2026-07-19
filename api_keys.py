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

from config import MAX_API_KEYS


class AllKeysExhausted(Exception):
    """Raised when every configured API key has hit its daily quota."""


# Google reason codes that mean "this key is spent / throttled".
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                  "userRateLimitExceeded"}


def _is_quota_error(err: HttpError) -> bool:
    """
    True if the HttpError is a daily-quota / rate exhaustion error.

    Prefers Google's STRUCTURED error payload
    ({"error": {"errors": [{"reason": "quotaExceeded"}]}}) and only falls
    back to substring matching if the body isn't parseable JSON — string
    matching alone can produce false positives (e.g. a video description
    echoed in an error message) and false negatives (payload shape changes).
    """
    if err.resp is None or err.resp.status != 403:
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
                "YOUTUBE_API_KEY_1..YOUTUBE_API_KEY_3) in your .env."
            )
        self._keys = list(keys)
        self._index = 0
        self._client = None

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
        return self._index >= len(self._keys)

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
                print(
                    f"⚠️  API key #{self.current_key_number} is out of quota — "
                    + (f"rotating to key #{self.current_key_number + 1}."
                       if self._index + 1 < len(self._keys)
                       else "no keys left.")
                )
                self._index += 1
                self._client = None  # force rebuild on the next key
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
