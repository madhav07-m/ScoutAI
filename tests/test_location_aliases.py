"""
Tests for app/location_aliases.py — the metro-area alias matching used
by the Companies window's location search filter.

Run with:
    python -m pytest tests/test_location_aliases.py -v
or standalone (no pytest needed):
    python tests/test_location_aliases.py

These have zero external dependencies (no fitz/chromadb/sentence-
transformers/Gemini needed) so they're cheap to run anywhere, anytime,
including in CI.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from app.location_aliases import location_matches

# (query, location, expected_match) — grouped by what each case proves.
CASES = [
    # --- Alias-group matches (the actual point of this module) ---
    ("Bay Area", "San Jose, CA, United States", True),
    ("San Francisco", "Bay Area, CA", True),
    ("Silicon Valley", "Cupertino, CA", True),
    ("NYC", "Manhattan, New York", True),
    ("New York", "NYC", True),
    ("DC Metro", "Arlington, VA", True),
    ("Washington DC", "DC Metro Area", True),
    ("Bengaluru", "Bangalore, Karnataka, India", True),
    ("Mumbai", "Bombay, Maharashtra", True),
    ("Hyderabad", "Secunderabad, Telangana", True),
    ("Remote", "Fully Remote", True),
    ("WFH", "Remote - US", True),
    ("LA", "Santa Monica, CA", True),
    ("Los Angeles", "Culver City, CA", True),

    # --- Negative cases: the critical safety property. Alias grouping
    # must NEVER cross into unrelated places just because they're both
    # cities / sound similar. This is the whole reason a curated table
    # was used instead of embedding similarity. ---
    ("Bangalore", "Bangkok, Thailand", False),
    ("Seattle", "San Francisco, CA", False),
    ("London", "New York, NY", False),
    ("Mumbai", "Delhi, India", False),
    ("Pune", "Mumbai, Maharashtra", False),
    ("Chennai", "Hyderabad, Telangana", False),

    # --- Case-insensitivity, whitespace, and empty-input edge cases ---
    ("bangalore", "BANGALORE, INDIA", True),
    ("  Bay Area  ", "San Francisco", True),
    ("", "San Francisco", False),
    ("Bay Area", "", False),

    # --- Plain substring matches that don't need any alias group ---
    ("Austin", "Austin, TX", True),
    ("Portland", "Portland, Maine", True),
]


def run():
    passed, failed = 0, 0
    for query, location, expected in CASES:
        result = location_matches(query, location)
        ok = result == expected
        passed += ok
        failed += not ok
        status = "PASS" if ok else "FAIL"
        print(f"{status}: location_matches({query!r}, {location!r}) = {result} (expected {expected})")
    print(f"\n{passed} passed, {failed} failed out of {len(CASES)}")
    return failed == 0


# pytest entry point, if pytest is available
def test_location_matches():
    for query, location, expected in CASES:
        assert location_matches(query, location) == expected, (
            f"location_matches({query!r}, {location!r}) should be {expected}"
        )


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
