"""
Config Change Detector — detects when config.json universe bounds have changed.

Used by refresh.py and universe.py to trigger a universe refresh whenever
min/max market cap is edited, without waiting for the normal 7-day staleness
cadence.

The hash file (data/.universe_config_hash) stores a hash of the universe
bounds from the last successful universe build. If the current config.json
doesn't match the stored hash, the universe is considered stale regardless
of file age.
"""

import os
import json
import hashlib

HASH_FILE = "data/.universe_config_hash"
CONFIG_FILE = "config.json"


def _get_current_config_hash():
    """Compute a hash of the current universe bounds in config.json."""
    try:
        config_path = CONFIG_FILE
        if not os.path.exists(config_path):
            # Try relative to this file (for imports from src/data/)
            config_path = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
            )
        if not os.path.exists(config_path):
            return None

        with open(config_path) as f:
            cfg = json.load(f)

        universe_cfg = cfg.get("universe", {})
        # Hash only the bounds — ignore notes or other keys
        bounds = {
            "min_market_cap": universe_cfg.get("min_market_cap"),
            "max_market_cap": universe_cfg.get("max_market_cap"),
        }
        # Deterministic JSON string for hashing
        return hashlib.sha256(
            json.dumps(bounds, sort_keys=True).encode()
        ).hexdigest()[:16]

    except (json.JSONDecodeError, KeyError, OSError):
        return None


def universe_config_changed():
    """
    Return True if the universe bounds in config.json differ from the
    last successful universe build (i.e. the hash file is missing or
    doesn't match). Safe to call at any time — returns True on any error
    (fail-open: if unsure, refresh).
    """
    current_hash = _get_current_config_hash()
    if current_hash is None:
        return False  # Can't read config — don't force a refresh

    if not os.path.exists(HASH_FILE):
        return True  # No hash file yet — first run or deleted

    try:
        with open(HASH_FILE) as f:
            stored_hash = f.read().strip()
        return stored_hash != current_hash
    except OSError:
        return True


def save_universe_config_hash():
    """
    Save the current config hash after a successful universe build.
    Call this from universe.py after saving universe.parquet.
    """
    current_hash = _get_current_config_hash()
    if current_hash is None:
        return

    os.makedirs(os.path.dirname(HASH_FILE) or ".", exist_ok=True)
    with open(HASH_FILE, "w") as f:
        f.write(current_hash)
