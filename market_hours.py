"""US market-hours awareness for the stock/index perps.

The xyz perps trade 24/7 but the underlying market doesn't — off-hours books are
thinner and spreads wider, so alerts get tagged. BTC is natively 24/7.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
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


def closing_soon(minutes: int = 15, now: datetime | None = None) -> bool:
    """True only in the last `minutes` of NYSE RTH (e.g. 15:45-16:00 ET) —
    used by the bigswing tier to flatten a full-balance position ONCE, right
    before an overnight/weekend window, rather than continuously while
    already off-hours (which would flatten a position the instant it's
    opened outside RTH). Weekday close only; Friday's window also covers the
    weekend gap."""
    ny = (now or datetime.now(tz=_NY)).astimezone(_NY)
    if ny.weekday() >= 5:
        return False
    close_dt = ny.replace(hour=_CLOSE.hour, minute=_CLOSE.minute, second=0, microsecond=0)
    window_start = close_dt - timedelta(minutes=minutes)
    return window_start <= ny < close_dt


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
    monday_155pm = datetime(2026, 7, 13, 15, 50, tzinfo=_NY)
    friday_155pm = datetime(2026, 7, 17, 15, 50, tzinfo=_NY)
    assert is_rth(monday_noon_et) is True
    assert is_rth(saturday) is False
    assert is_rth(monday_night) is False
    assert off_hours_tag("BTC", saturday) == ""
    assert off_hours_tag("xyz:NVDA", monday_noon_et) == ""
    assert off_hours_tag("xyz:NVDA", saturday) != ""
    assert closing_soon(now=monday_noon_et) is False
    assert closing_soon(now=monday_155pm) is True
    assert closing_soon(now=friday_155pm) is True
    assert closing_soon(now=saturday) is False
    print("self-checks pass")
    print("right now: RTH =", is_rth(), "| NVDA tag:", off_hours_tag("xyz:NVDA") or "(none)")
