"""
quota_state.py — remember which keys are already spent, across runs.

THE PROBLEM
-----------
ApiKeyManager tracks usage in two plain lists built in __init__:

    self._used = [0] * len(keys)
    self._dead = [False] * len(keys)

They exist for the lifetime of the process and no longer. Google's
counters do not: quota is per key, per day, and resets at midnight
Pacific. So every launch begins convinced that twelve exhausted keys are
twelve fresh ones.

The visible symptom was a message that read as nonsense —

    API key #2 is out of quota (0 units, 0%)

— both halves true, because nothing had been spent ON THIS RUN.

The expensive symptom is the forecast. A build prints "5 seeds x 1300 =
6,500 videos" by counting keys, not LIVE keys. If eight were drained
this morning, that plan is fiction: the build commits to a target it
cannot reach, dies partway, and leaves a dataset that is not merely
smaller but differently SHAPED — the collector allocates evenly across
date windows, so an early death means the oldest windows were never
sampled at all. Comparing that against a complete build reads the
truncation as a property of the market.

WHY THIS STORES DEADNESS, NOT UNITS
-----------------------------------
Deadness is an observed fact: Google returned quotaExceeded for that
key. Units are our own estimate from QUOTA_COSTS, and estimates drift —
retries, calls charged differently than assumed, anything spent by
another tool sharing the key. Restoring an estimate as though it were
fact would stack a second false confidence on top of the one being
fixed. So deadness is authoritative and restored; units are advisory,
stored for reporting and never used to declare a key finished.

WHY A PACIFIC DATE
------------------
The quota day is Google's, not the machine's. Lagos is 8 hours ahead of
Pacific, so a local date would roll over mid-afternoon and wrongly
declare every key fresh while Google still considers them spent. The
comparison is done on the Pacific calendar date; a stored day that is
not today's is discarded wholesale.

WHAT IT STILL CANNOT SEE
------------------------
This file is local. Keys used from a second machine, or by anything
outside this engine, leave no trace here. It makes the engine honest
about itself, which is most of the problem and not all of it.
"""

import json
import os
from datetime import datetime, timedelta, timezone

STATE_FILENAME = "quota_state.json"
SCHEMA = 2


def _state_path(path=None):
    return path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), STATE_FILENAME)


def pacific_date():
    """
    Today's date on Google's quota clock, as 'YYYY-MM-DD'.

    Uses the real zone when the tzdata is present so DST is handled;
    falls back to a fixed -8 offset otherwise. The fallback is wrong for
    one hour a day during DST, which can only ever make the engine treat
    a key as spent slightly too long — the safe direction to be wrong.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")


def _key_id(key: str) -> str:
    """
    Identify a key without storing it.

    The file sits in the working folder and may be shared or pasted into
    a chat. Last six characters are enough to tell twelve keys apart and
    useless to anyone who finds them.
    """
    k = str(key or "")
    return k[-6:] if len(k) >= 6 else k


def load(keys, path=None):
    """
    Restore today's state for these keys.

    Returns (dead_flags, used_units, note). On any problem — missing
    file, corrupt JSON, wrong schema, yesterday's date — returns all-live
    and says why. Failing open matters: a bad state file must never stop
    a build from running, only from being over-optimistic.
    """
    n = len(keys)
    blank = ([False] * n, [0] * n, None)
    p = _state_path(path)
    if not os.path.exists(p):
        return blank
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ([False] * n, [0] * n, "quota_state.json unreadable — ignoring it.")

    if data.get("schema") != SCHEMA:
        return blank
    today = pacific_date()
    if data.get("pacific_date") != today:
        return ([False] * n, [0] * n,
                f"Quota state was from {data.get('pacific_date')}; "
                f"it is now {today} in Pacific — keys treated as reset.")

    entries = data.get("keys", {})
    dead = [bool(entries.get(_key_id(k), {}).get("dead")) for k in keys]
    used = [int(entries.get(_key_id(k), {}).get("used") or 0) for k in keys]
    n_dead = sum(dead)
    note = None
    if n_dead:
        spent = ", ".join(f"#{i + 1}" for i, d in enumerate(dead) if d)
        note = (f"{n_dead} of {n} key(s) already spent today: {spent}. "
                f"Recorded earlier in this quota day, not re-tested.")
    return dead, used, note


def save(keys, dead, used, path=None):
    """
    Write today's state. Never raises — a failure to persist is a lost
    optimisation, not a lost build, and must not interrupt one.
    """
    p = _state_path(path)
    payload = {
        "schema": SCHEMA,
        "pacific_date": pacific_date(),
        "written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "keys": {
            _key_id(k): {"dead": bool(dead[i]),
                         "used": int(used[i]),
                         "slot": i + 1}
            for i, k in enumerate(keys)
        },
    }
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, p)   # atomic: a crash mid-write cannot corrupt it
        return True
    except OSError:
        return False


def clear(path=None):
    """Forget everything — for when keys are replaced or quota is raised."""
    p = _state_path(path)
    try:
        os.remove(p)
        return True
    except OSError:
        return False
