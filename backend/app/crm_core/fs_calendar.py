"""Service calendars - the schedule hours, working days and holidays a
response time is counted in (Siebel's FS Holiday API: a 4-hour response
logged at 16:30 on a Friday is committed for Monday 12:30).

Times are stored in UTC like every other timestamp in BRT 360; a calendar
reads its hours in its service region's time zone ("UTC+05:30" or an IANA
name such as "Asia/Kolkata" where the server has the tz database).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.I)


def zone(name: str | None) -> dt.tzinfo:
    text = (name or "").strip()
    if not text or text.upper() in ("UTC", "GMT", "Z"):
        return dt.timezone.utc
    m = _OFFSET.match(text)
    if m:
        delta = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return dt.timezone(-delta if m.group(1) == "-" else delta)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:  # unknown name or no tz database on this machine
        return dt.timezone.utc


def minutes_of(hm: str | None, default: int) -> int:
    try:
        h, m = (hm or "").split(":")
        return max(0, min(24 * 60, int(h) * 60 + int(m)))
    except ValueError:
        return default


def days_of(text: str | None) -> set[int]:
    out = {int(p) for p in (text or "").split(",") if p.strip().isdigit() and 0 <= int(p) <= 6}
    return out or {0, 1, 2, 3, 4}


def holidays_of(text: str | None) -> set[dt.date]:
    try:
        return {dt.date.fromisoformat(d) for d in json.loads(text or "[]")}
    except (ValueError, TypeError):
        return set()


@dataclass
class Calendar:
    start: int = 8 * 60  # minutes after local midnight
    end: int = 17 * 60
    days: set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4})
    holidays: set[dt.date] = field(default_factory=set)
    always: bool = False  # 24x7
    tz: dt.tzinfo = dt.timezone.utc

    @classmethod
    def of(cls, region=None, engineer=None, always: bool = False) -> "Calendar":
        start = minutes_of(getattr(engineer, "day_start", None) or getattr(region, "day_start", None), 8 * 60)
        end = minutes_of(getattr(engineer, "day_end", None) or getattr(region, "day_end", None), 17 * 60)
        days = days_of(getattr(engineer, "work_days", None) or getattr(region, "work_days", None))
        holidays = holidays_of(getattr(region, "holidays_json", None))
        return cls(start, max(end, start + 15), days, holidays, always, zone(getattr(region, "timezone", None)))

    # UTC (naive, as stored) <-> local wall clock (naive)
    def local(self, utc: dt.datetime) -> dt.datetime:
        return utc.replace(tzinfo=dt.timezone.utc).astimezone(self.tz).replace(tzinfo=None)

    def utc(self, local: dt.datetime) -> dt.datetime:
        return local.replace(tzinfo=self.tz).astimezone(dt.timezone.utc).replace(tzinfo=None)

    def local_date(self, utc: dt.datetime) -> dt.date:
        return self.local(utc).date()

    def is_working_day(self, day: dt.date) -> bool:
        return self.always or (day.weekday() in self.days and day not in self.holidays)

    def local_window(self, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        midnight = dt.datetime.combine(day, dt.time())
        if self.always:
            return midnight, midnight + dt.timedelta(days=1)
        if not self.is_working_day(day):
            return None
        return midnight + dt.timedelta(minutes=self.start), midnight + dt.timedelta(minutes=self.end)

    def window(self, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        """The open hours of one local day, in UTC - or None on a day off."""
        win = self.local_window(day)
        return (self.utc(win[0]), self.utc(win[1])) if win else None

    def add_hours(self, start: dt.datetime, hours: float) -> dt.datetime:
        """`hours` of service time after `start` (UTC), counted only inside open hours."""
        if self.always:
            return start + dt.timedelta(hours=hours)
        remaining = dt.timedelta(hours=max(hours, 0))
        cursor = self.local(start)
        for _ in range(800):  # a bound, not a limit anyone reaches
            win = self.local_window(cursor.date())
            if win is None or cursor >= win[1]:
                cursor = dt.datetime.combine(cursor.date() + dt.timedelta(days=1), dt.time())
                continue
            cursor = max(cursor, win[0])
            available = win[1] - cursor
            if remaining <= available:
                return self.utc(cursor + remaining)
            remaining -= available
            cursor = dt.datetime.combine(cursor.date() + dt.timedelta(days=1), dt.time())
        return self.utc(cursor)

    def open_at(self, moment: dt.datetime) -> bool:
        local = self.local(moment)
        win = self.local_window(local.date())
        return bool(win and win[0] <= local < win[1])
