from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .settings import settings


def local_today() -> date:
    return datetime.now(ZoneInfo(settings.app_timezone)).date()
