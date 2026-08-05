"""
api_keys.py

Multi-key YouTube API management: load up to MAX_API_KEYS keys from the
environment, rotate to the next key when one returns quotaExceeded, and
signal cleanly when ALL keys are exhausted so the collector can stop
gracefully instead of crashing.

.env keys recognized (via python-dotenv loaded in main.py, or real env):
    YOUTUBE_API_KEY        -> treated as key 1
    YOUTUBE_API_KEY_1..N   -> explicit slots (N = MAX_API_KEYS)

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
from contextlib import contextmanager

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (MAX_API_KEYS, DAILY_QUOTA_PER_KEY,
                    RESERVED_KEYS_FOR_TOPICS, ALLOW_RESERVE_FALLBACK,
                    SEED_CHAIN_STRICT)


class AllKeysExhausted(Exception):
    """Raised when every configured API key has hit its daily quota."""


class SeedKeysExhausted(AllKeysExhausted):
    """
    Raised when ONE seed's own key pair is spent, while other keys live.

    A subclass of AllKeysExhausted on purpose: every existing handler that
    degrades gracefully on quota death (return partial ids, bank partial
    rows) keeps working untouched. What the subclass adds is the ability
    for the SEED LOOP to tell the two situations apart — this seed is
    finished, versus the whole build is finished. The old code could not,
    so one seed running dry ended the entire build even though four
    untouched key pairs were sitting there.
    """


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
                f"YOUTUBE_API_KEY_1..YOUTUBE_API_KEY_{MAX_API_KEYS}) in your .env."
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
        self._warned_reserve = False
        self._seed_chain = None
        # Set only inside unchained(); lets cheap videos.list enrichment
        # cross a seed's key boundary when searching may not.
        self._unchained = False

        # Restore today's known-dead keys. Quota is Google's and lasts a
        # Pacific day; our counters lasted only this process, so every
        # launch used to rediscover the same dead keys by walking into
        # them. See quota_state.py for why deadness is restored but the
        # unit estimate is not trusted.
        try:
            import quota_state
            dead, used, note = quota_state.load(self._keys)
            self._dead = dead
            self._used = used
            self._state_note = note
            if note:
                print(f"  {note}")
        except Exception:
            self._state_note = None
        # Start on a key that is actually alive, rather than on #1 by
        # position and discovering it is spent one request later.
        first = self._first_live()
        if first is not None:
            self._index = first

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls):
        """
        Collect keys from env: YOUTUBE_API_KEY_1..N, then bare YOUTUBE_API_KEY.

        ORDER MATTERS AND IS NOT COSMETIC. Seeds are paired to keys by
        position (seed 1 -> keys #1 and #6), and every log line names keys
        by that position. If the bare YOUTUBE_API_KEY were loaded FIRST and
        held a value different from YOUTUBE_API_KEY_1, every numbered key
        would shift one slot: "key #6" in the logs would be the key you
        named _5 in .env, and the last numbered key would be silently
        truncated away by the MAX_API_KEYS cut. Debugging a quota problem
        against a key list that is off by one is worse than having no log
        at all — so the NUMBERED keys define the positions, and the bare
        key is only appended as an extra if it is not already among them.
        """
        keys = []
        for i in range(1, MAX_API_KEYS + 1):
            k = os.getenv(f"YOUTUBE_API_KEY_{i}")
            if k and k.strip() and k.strip() not in keys:
                keys.append(k.strip())

        base = os.getenv("YOUTUBE_API_KEY")
        if base and base.strip():
            base = base.strip()
            if base not in keys:
                if keys:
                    print(f"  Note: YOUTUBE_API_KEY holds a key not present in "
                          f"YOUTUBE_API_KEY_1..{MAX_API_KEYS}; "
                          f"appending it as key #{len(keys) + 1}.")
                keys.append(base)

        if len(keys) > MAX_API_KEYS:
            print(f"  ⚠️  {len(keys)} keys found but MAX_API_KEYS is "
                  f"{MAX_API_KEYS} — ignoring the last "
                  f"{len(keys) - MAX_API_KEYS}.")
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

    def seed_key_chain(self, seed_index: int, n_seeds: int):
        """
        The ordered list of keys belonging to ONE seed.

        Keys are dealt round-robin by STRIDE, so with 12 keys, 2 reserved
        for topic grading (leaving 10 build keys) and 5 seeds:

            seed 1 -> keys #1, #6
            seed 2 -> keys #2, #7
            seed 3 -> keys #3, #8
            seed 4 -> keys #4, #9
            seed 5 -> keys #5, #10
            (keys #11, #12 never touched by a build)

        Each seed owns a PRIMARY and its OWN BACKUP. When seed 1 exhausts
        key #1 it moves to key #6 — never to key #2, which belongs to
        seed 2.

        That distinction is the whole point. Falling through to the
        neighbouring key "works", but a greedy first seed then spends the
        second seed's budget, the second spends the third's, and the
        damage cascades until the last seed collects nothing. Observed on
        a real run: one seed walked #1 -> #2 -> #3 -> #4 -> #5 before any
        other seed had started, and four seeds were skipped outright.

        HOW MANY BACKUPS YOU ACTUALLY GET is pure arithmetic:

            backups per seed = floor(build_keys / n_seeds) - 1

        With 10 build keys and 5 seeds that is 1 backup each, as above.
        With 5 build keys and 5 seeds it is ZERO — the chain is a single
        key and there is nothing to fall back to. The earlier version of
        this docstring described the 10-key layout while the config
        allowed only 5 build keys, which made the code look broken when
        it was merely under-supplied. `capacity_note()` now states the
        real number out loud at build time.
        """
        order = self.build_key_order()
        if not order or n_seeds <= 0:
            return order
        stride = min(n_seeds, len(order))
        return [order[i] for i in range(seed_index % stride, len(order), stride)]

    def capacity_note(self, n_seeds: int) -> str:
        """
        Plain statement of the key layout for this build — printed BEFORE
        any quota is spent, so an under-supplied run is visible at the
        top of the log instead of being inferred from a cascade later.
        """
        order = self.build_key_order()
        n_build = len(order)
        n_res = self.total_keys - n_build
        if n_seeds <= 0 or not n_build:
            return "No build keys available."
        n_live = sum(1 for i in order if not self._dead[i])
        per_seed = n_build // n_seeds
        lines = [f"  Key plan: {self.total_keys} keys — {n_build} for the "
                 f"build, {n_res} reserved for grading."]
        if per_seed >= 2:
            lines.append(f"  {n_seeds} seeds x {per_seed} keys each "
                         f"(1 primary + {per_seed - 1} backup).")
        elif per_seed == 1:
            lines.append(f"  ⚠️  {n_seeds} seeds, {n_build} build keys — "
                         f"1 key each, NO BACKUPS. A seed that runs dry "
                         f"stops there.")
        else:
            lines.append(f"  ⚠️  {n_seeds} seeds but only {n_build} build "
                         f"keys — seeds {n_build + 1}+ SHARE keys with "
                         f"earlier seeds and will find them spent.")
        # Counting configured keys is what made the old forecast fiction.
        # If today has already spent most of them, say so BEFORE the
        # build commits to a target it cannot reach.
        if n_live < n_build:
            lines.append(f"  ⚠️  Only {n_live} of {n_build} build keys are "
                         f"live today — the rest were spent earlier in "
                         f"this quota day.")
            if n_live < n_seeds:
                lines.append(f"      That is fewer than the {n_seeds} seeds, "
                             f"so some seeds will be skipped. A partial "
                             f"build is thinner AND differently shaped than "
                             f"a full one; consider waiting for the reset.")
        return "\n".join(lines)

    def use_key_for_seed(self, seed_index: int, n_seeds: int = None) -> bool:
        """
        Point this manager at a seed's own key chain.

        With n_seeds given, the seed gets a primary plus every stride-th
        key after it as backups, and exhausting the primary moves it to
        its own backup rather than to another seed's.

        Without n_seeds, the original one-key-per-seed behaviour applies.

        Returns True if the seed has at least one live key of its own.
        Returning False (rather than quietly borrowing) matters: the old
        version fell through to _first_live() when the whole chain was
        dead, which handed the seed a key belonging to a DIFFERENT seed
        and re-opened the cascade at the assignment step — before a
        single request had been made, and without printing anything.
        """
        if not self._keys:
            return False
        if n_seeds:
            chain = self.seed_key_chain(seed_index, n_seeds)
            self._seed_chain = chain
            want = next((k for k in chain if not self._dead[k]), None)
            if want is None and not SEED_CHAIN_STRICT:
                want = self._first_live()
        else:
            self._seed_chain = None
            want = seed_index % len(self._keys)
            if self._dead[want]:
                want = self._first_live(prefer_from=want)
        self._pinned = want
        if want is not None and want != self._index:
            self._index = want
            self._client = None
        return want is not None

    def release_pin(self):
        """
        Return to normal sequential rotation (used after a build).

        Clears the seed CHAIN as well as the pin. Clearing only the pin
        was a real leak: _first_live consults _seed_chain before anything
        else, so a manager reused after a build stayed silently confined
        to the LAST SEED'S key pair. In option 2 that path is live — a
        stale niche triggers a rebuild, and the topic collection that
        follows would then run on seed 5's spent keys while ten healthy
        ones sat idle.
        """
        self._pinned = None
        self._seed_chain = None

    @contextmanager
    def unchained(self):
        """
        Temporarily lift the seed-chain wall. For CHEAP calls only.

        The motivating failure: a seed spent ~1,400 units of search
        (100/page) gathering 654 video ids, then could not afford the
        videos.list calls needed to turn them into rows — which cost 1
        unit per 50 ids, about 14 units total. Every one of those 654
        videos was discarded. The expensive step succeeded and starved
        the step that was a HUNDRED TIMES cheaper.

        Enrichment is therefore allowed to draw on any live build key,
        because the isolation is there to stop a seed eating another
        seed's SEARCH budget, and 14 units is not that. Searching stays
        strictly chained.
        """
        prev = getattr(self, "_unchained", False)
        self._unchained = True
        try:
            yield self
        finally:
            self._unchained = prev

    def use_reserved_keys(self):
        """
        Start on the reserved tail keys (topic grading / refreshes). These
        are the keys builds never touch, so cheap operations always have
        quota available.
        """
        if RESERVED_KEYS_FOR_TOPICS <= 0 or len(self._keys) <= RESERVED_KEYS_FOR_TOPICS:
            return
        # Drop any build-time state first. Without this the chain check
        # inside _first_live wins and hands back a spent BUILD key — the
        # exact opposite of what this method is for.
        self._seed_chain = None
        self._pinned = None
        self._hard_reserve = False
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
        # A seed's own chain is tried IN ORDER: primary first, then that
        # seed's own backup.
        chain = getattr(self, "_seed_chain", None)
        if chain:
            for i in chain:
                if not self._dead[i]:
                    return i
            # Chain spent. Under SEED_CHAIN_STRICT this is a WALL, not a
            # doorway: return None so this seed ends here and the next
            # seed starts on its own untouched pair.
            #
            # The previous behaviour fell through to the code below and
            # took whatever key was live — which is precisely the cascade
            # the chain exists to prevent. It made the isolation
            # decorative: seeds were "assigned" keys they were free to
            # ignore the moment their own ran out.
            #
            # _unchained is the one sanctioned exception, used for
            # videos.list enrichment (see unchained()).
            if SEED_CHAIN_STRICT and not getattr(self, "_unchained", False):
                return None
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

        # Every non-reserved key is dead. With ALLOW_RESERVE_FALLBACK the
        # reserved tail becomes available rather than sitting idle while
        # the build stops — but loudly, and once only, because spending
        # it is exactly what leaves a later topic grade with nothing.
        if (self._hard_reserve and ALLOW_RESERVE_FALLBACK
                and RESERVED_KEYS_FOR_TOPICS > 0):
            cut = n - RESERVED_KEYS_FOR_TOPICS
            for i in range(cut, n):
                if not self._dead[i]:
                    if not self._warned_reserve:
                        print()
                        print("  " + "-" * 56)
                        print(f"  All {cut} build keys are spent. Falling back to "
                              f"reserved key #{i + 1}.")
                        print("  ALLOW_RESERVE_FALLBACK is on, so the build "
                              "continues — but the")
                        print("  quota set aside for topic grading is now being "
                              "consumed. Option 2")
                        print("  may have nothing left today.")
                        print("  " + "-" * 56)
                        print()
                        self._warned_reserve = True
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
                self._persist_state()
                nxt = self._first_live(prefer_from=self._index + 1)

                # The percentage is SESSION-LOCAL — units spent by THIS
                # process only. A key spent by an earlier run today dies
                # here reporting "0 units, 0%", which reads like a bug in
                # our accounting rather than a key that was already gone
                # before we started. Say which it is.
                used = self._used[self._index]
                pct = 100.0 * used / DAILY_QUOTA_PER_KEY if DAILY_QUOTA_PER_KEY else 0
                if used == 0:
                    spent = "spent before this run started (0 units used here)"
                else:
                    spent = f"{used:,} units this run, {pct:.0f}% of daily"

                chain = getattr(self, "_seed_chain", None)
                if nxt is not None:
                    where = f"falling back to key #{nxt + 1}."
                elif chain and not self._unchained_ok():
                    where = (f"all {len(chain)} of this seed's keys are "
                             f"spent, moving to the next seed.")
                else:
                    where = "no live keys left."
                print(f"⚠️  API key #{self._index + 1} is out of quota "
                      f"({spent}) — {where}")

                if nxt is None:
                    # Only a GLOBAL wipe-out may poison the manager. A
                    # seed hitting the end of its own chain must leave
                    # every other pair usable — the old code set
                    # _index past the end here, which made `exhausted`
                    # permanently True and skipped every remaining seed
                    # while healthy keys sat untouched.
                    if chain and not self._unchained_ok() and self.live_keys:
                        raise SeedKeysExhausted(
                            "This seed's assigned keys (#"
                            + ", #".join(str(i + 1) for i in chain)
                            + ") are exhausted; "
                            f"{self.live_keys} key(s) remain for other seeds."
                        )
                    self._index = len(self._keys)
                    break
                self._index = nxt
                self._client = None  # force rebuild on the new key
        raise AllKeysExhausted(
            f"All {len(self._keys)} API key(s) have exhausted their daily quota."
        )

    def _unchained_ok(self) -> bool:
        return bool(getattr(self, "_unchained", False))

    def _persist_state(self):
        """Record deadness for the rest of this quota day. Best effort."""
        try:
            import quota_state
            quota_state.save(self._keys, self._dead, self._used)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            self._client = build(
                "youtube", "v3", developerKey=self._keys[self._index]
            )
        return self._client
