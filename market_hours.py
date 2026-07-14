"""US market-hours awareness for the stock/index perps.

The xyz perps trade 24/7 but the underlying market doesn't — off-hours books are
thinner and spreads wider, so alerts get tagged. BTC is natively 24/7.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def is_rth(now: datetime | None = None) -> bool:
    """True during NYSE regular trading hours (Mon-Fri 9:30-16:00 ET).

    Ignores exchange holidays — acceptable for alert tagging.
    """
    ny = (now or datetime.now(tz=_NY)).astimezone(_NY)
    if ny.weekday() >= 5:  # Sat/Sun
        return False
    return _OPEN <= ny.time() < _CLOSE


def off_hours_tag(coin: str, now: datetime | None = None) -> str:
    """Warning line for stock/index perps outside RTH; empty otherwise."""
    if not coin.startswith("xyz:"):
        return ""  # BTC etc: natively 24/7, no underlying to be closed
    if is_rth(now):
        return ""
    return ("⚠️ OFF-HOURS: US market closed — thinner book, wider spreads, "
            "gap risk at the open.")


if __name__ == "__main__":
    from datetime import timedelta, timezone

    # quick self-checks against fixed instants
    monday_noon_et = datetime(2026, 7, 13, 12, 0, tzinfo=_NY)
    saturday = datetime(2026, 7, 11, 12, 0, tzinfo=_NY)
    monday_night = datetime(2026, 7, 13, 22, 0, tzinfo=_NY)
    assert is_rth(monday_noon_et) is True
    assert is_rth(saturday) is False
    assert is_rth(monday_night) is False
    assert off_hours_tag("BTC", saturday) == ""
    assert off_hours_tag("xyz:NVDA", monday_noon_et) == ""
    assert off_hours_tag("xyz:NVDA", saturday) != ""
    print("self-checks pass")
    print("right now: RTH =", is_rth(), "| NVDA tag:", off_hours_tag("xyz:NVDA") or "(none)")
