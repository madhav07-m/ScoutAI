"""
Location alias table for the Companies search bar.

Why this instead of embedding-based semantic location search: full
embedding similarity would reintroduce exactly the problem the
location filter was built to avoid -- "Bangalore" and "Bangkok" are
close-ish in embedding space (both cities, both South/Southeast Asian,
similar token shape) but are NOT the same place, and a semantic match
there would be actively wrong for someone job-hunting. A curated
metro/alias table gets the useful case ("Bay Area" should surface "San
Francisco" postings) without the false-positive risk, at zero
ongoing cost -- no model call, just a dict lookup.

This is intentionally a starting list, not exhaustive. Add more groups
as real search terms reveal gaps; each group is a list of interchangeable
names for one metro area / region, all treated as equivalent for
filtering purposes only (never for ranking).
"""

from typing import Optional

# Each group: a set of names that should all match each other. Keep
# entries lowercase; matching is case-insensitive at lookup time.
LOCATION_ALIAS_GROUPS = [
    {"bay area", "san francisco", "sf", "oakland", "san jose", "palo alto",
     "mountain view", "menlo park", "sunnyvale", "cupertino", "redwood city",
     "south bay", "silicon valley"},
    {"bengaluru", "bangalore", "blr"},
    {"ncr", "delhi", "new delhi", "gurugram", "gurgaon", "noida", "faridabad"},
    {"nyc", "new york", "new york city", "manhattan", "brooklyn"},
    {"greater boston", "boston", "cambridge, ma", "cambridge ma"},
    {"seattle area", "seattle", "bellevue", "redmond"},
    {"greater london", "london"},
    {"mumbai", "bombay"},
    {"bay area, ca", "sf bay area"},
    {"dc metro", "washington dc", "washington, dc", "arlington, va",
     "arlington va"},
    {"la", "los angeles", "santa monica", "culver city"},
    {"chennai", "madras"},
    {"pune"},
    {"hyderabad", "secunderabad"},
    {"remote", "remote - us", "remote - india", "remote (us)", "fully remote",
     "work from home", "wfh"},
]

# Build a lookup: name -> group index, for O(1) group membership checks.
_NAME_TO_GROUP = {}
for _idx, _group in enumerate(LOCATION_ALIAS_GROUPS):
    for _name in _group:
        _NAME_TO_GROUP[_name] = _idx


def location_matches(query: str, location: str) -> bool:
    """True if `location` should count as a match for `query`.

    Matching rules, in order:
    1. Plain case-insensitive substring match (existing behavior,
       always kept -- "Bangalore" still matches "Bangalore, India").
    2. Alias-group membership: if query and location both map to a
       known name in the SAME curated group (e.g. "bay area" and
       "san francisco"), they match even without a substring overlap.
    No fuzzy/embedding similarity is used, so unrelated places (e.g.
    "Bangalore" and "Bangkok") never match just because they're both
    cities -- see module docstring.
    """
    q = query.strip().lower()
    loc = (location or "").strip().lower()
    if not q or not loc:
        return False

    if q in loc:
        return True

    q_group = _NAME_TO_GROUP.get(q)
    if q_group is None:
        return False

    # Check if any alias name in the same group appears as a substring
    # of the location text (locations are often longer strings like
    # "San Francisco, CA, United States").
    for name in LOCATION_ALIAS_GROUPS[q_group]:
        if name in loc:
            return True
    return False
