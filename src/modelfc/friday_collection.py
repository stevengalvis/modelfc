"""Offline Friday review policy. No scheduler, provider, or activation side effects."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
SATURDAY_RESERVE = 4  # held back from Friday; not a new allowance
AFTERNOON_LIMIT = 2  # second slots shared with Saturday, never a third pass


def friday_slot(now):
    if now.tzinfo is None:
        raise ValueError("aware clock required")
    local = now.astimezone(NEW_YORK)
    if local.weekday() != 4:
        return None
    phase = "morning" if 7 <= local.hour < 9 else "afternoon" if 15 <= local.hour < 17 else None
    return (local.date() + timedelta(days=1), phase) if phase else None


def review_deadlines(saturday):
    friday = saturday - timedelta(days=1)
    if saturday.weekday() != 5:
        raise ValueError("Saturday required")
    return tuple(datetime(friday.year, friday.month, friday.day, hour, tzinfo=NEW_YORK)
                 .astimezone(timezone.utc) for hour in (9, 17))


def project_month(*, weekends, fixtures=12, afternoon=2, saturday=10,
                  midweek=0, other_discovery=0, wasted_reservations=0):
    """Request envelope: two Friday discovery queries, two morning batches,
    one afternoon batch and ceil(Saturday / 7) batches, each with metadata.
    Midweek is an explicit all-inclusive request allowance, not fixture count.
    Other discovery includes ordinary Friday and empty/midweek days.
    """
    if any(type(v) is not int or v < 0 for v in
           (weekends, fixtures, afternoon, saturday, midweek, other_discovery, wasted_reservations)):
        raise ValueError("nonnegative integer counts required")
    if fixtures > 13 or afternoon > AFTERNOON_LIMIT or afternoon + saturday > fixtures:
        raise ValueError("two batch / two observation policy exceeded")
    morning_batches = 1 if fixtures <= 6 else 2
    metadata = morning_batches + bool(afternoon) + (saturday + 6) // 7
    per_weekend = 2 + fixtures + afternoon + saturday + metadata
    total = weekends * per_weekend + midweek + other_discovery + wasted_reservations
    return {"per_weekend": per_weekend, "projected_requests": total,
            "hard_cap": 180, "headroom": 180 - total,
            "coverage_budget_blocked": total > 180}
