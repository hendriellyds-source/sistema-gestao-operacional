from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import settings
from .sqlite_connection import ClosingConnection


_request_lock = threading.Lock()
_last_request_at = 0.0


def _connect() -> sqlite3.Connection:
    path = Path(settings.geocoding_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def init_geocoding_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reverse_geocode_cache (
                cache_key TEXT PRIMARY KEY,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                display_name TEXT,
                short_name TEXT,
                road TEXT,
                neighbourhood TEXT,
                city TEXT,
                state TEXT,
                postcode TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _cache_key(latitude: float, longitude: float) -> str:
    precision = max(4, min(6, settings.geocoding_coordinate_precision))
    return f"{round(float(latitude), precision):.{precision}f},{round(float(longitude), precision):.{precision}f}"


def google_maps_url(latitude: float, longitude: float) -> str:
    query = urllib.parse.quote(f"{latitude},{longitude}")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def google_street_view_url(latitude: float, longitude: float) -> str:
    viewpoint = urllib.parse.quote(f"{latitude},{longitude}")
    return (
        "https://www.google.com/maps/@?api=1&map_action=pano"
        f"&viewpoint={viewpoint}"
    )


def _build_short_name(address: dict[str, Any], display_name: str | None) -> str:
    road = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("residential")
        or address.get("highway")
    )
    number = address.get("house_number")
    locality = (
        address.get("suburb")
        or address.get("neighbourhood")
        or address.get("quarter")
        or address.get("city_district")
    )
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
    )

    parts: list[str] = []
    if road:
        parts.append(f"{road}{', ' + str(number) if number else ''}")
    if locality and locality not in parts:
        parts.append(str(locality))
    if city and city not in parts:
        parts.append(str(city))

    if parts:
        return " — ".join(parts[:3])

    if display_name:
        return ", ".join(display_name.split(",")[:3]).strip()

    return "Local não identificado"


def get_cached_location(latitude: float, longitude: float) -> dict[str, Any] | None:
    init_geocoding_db()
    key = _cache_key(latitude, longitude)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM reverse_geocode_cache WHERE cache_key=?",
            (key,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result.update(
        {
            "maps_url": google_maps_url(latitude, longitude),
            "street_view_url": google_street_view_url(latitude, longitude),
            "source": "CACHE",
        }
    )
    return result



def search_location(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search an address, CEP or place name using Nominatim.

    Returns normalized candidates with decimal coordinates. The function uses
    the same rate limit and user-agent policy as reverse geocoding.
    """
    text = str(query or "").strip()
    if len(text) < 3:
        return []
    if not settings.reverse_geocoding_enabled:
        return []

    safe_limit = max(1, min(int(limit or 5), 10))
    global _last_request_at
    with _request_lock:
        wait_seconds = settings.nominatim_min_interval_seconds - (
            time.monotonic() - _last_request_at
        )
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        params = urllib.parse.urlencode(
            {
                "format": "jsonv2",
                "q": text,
                "addressdetails": 1,
                "limit": safe_limit,
                "countrycodes": "br",
                "accept-language": "pt-BR",
            }
        )
        request = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={
                "User-Agent": settings.nominatim_user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=settings.geocoding_timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            _last_request_at = time.monotonic()
            return []
        _last_request_at = time.monotonic()

    results: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        try:
            latitude = float(item.get("lat"))
            longitude = float(item.get("lon"))
        except (TypeError, ValueError):
            continue
        address = item.get("address") or {}
        display_name = item.get("display_name") or text
        results.append(
            {
                "latitude": latitude,
                "longitude": longitude,
                "display_name": display_name,
                "short_name": _build_short_name(address, display_name),
                "postcode": address.get("postcode"),
                "city": (
                    address.get("city")
                    or address.get("town")
                    or address.get("village")
                    or address.get("municipality")
                ),
                "state": address.get("state"),
                "maps_url": google_maps_url(latitude, longitude),
                "street_view_url": google_street_view_url(latitude, longitude),
                "source": "NOMINATIM_SEARCH",
            }
        )
    return results

def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any]:
    cached = get_cached_location(latitude, longitude)
    if cached:
        return cached

    fallback = {
        "latitude": latitude,
        "longitude": longitude,
        "display_name": None,
        "short_name": "Local não identificado",
        "maps_url": google_maps_url(latitude, longitude),
        "street_view_url": google_street_view_url(latitude, longitude),
        "source": "FALLBACK",
    }

    if not settings.reverse_geocoding_enabled:
        return fallback

    global _last_request_at
    with _request_lock:
        wait_seconds = settings.nominatim_min_interval_seconds - (
            time.monotonic() - _last_request_at
        )
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        params = urllib.parse.urlencode(
            {
                "format": "jsonv2",
                "lat": f"{float(latitude):.7f}",
                "lon": f"{float(longitude):.7f}",
                "addressdetails": 1,
                "zoom": 18,
                "accept-language": "pt-BR",
            }
        )
        request = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/reverse?{params}",
            headers={
                "User-Agent": settings.nominatim_user_agent,
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=settings.geocoding_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            _last_request_at = time.monotonic()
            return fallback

        _last_request_at = time.monotonic()

    address = payload.get("address") or {}
    display_name = payload.get("display_name")
    short_name = _build_short_name(address, display_name)
    key = _cache_key(latitude, longitude)

    record = {
        "cache_key": key,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "display_name": display_name,
        "short_name": short_name,
        "road": address.get("road"),
        "neighbourhood": (
            address.get("suburb")
            or address.get("neighbourhood")
            or address.get("quarter")
        ),
        "city": (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
        ),
        "state": address.get("state"),
        "postcode": address.get("postcode"),
        "raw_json": json.dumps(payload, ensure_ascii=False),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reverse_geocode_cache (
                cache_key, latitude, longitude, display_name, short_name,
                road, neighbourhood, city, state, postcode, raw_json, updated_at
            ) VALUES (
                :cache_key, :latitude, :longitude, :display_name, :short_name,
                :road, :neighbourhood, :city, :state, :postcode, :raw_json,
                :updated_at
            )
            ON CONFLICT(cache_key) DO UPDATE SET
                display_name=excluded.display_name,
                short_name=excluded.short_name,
                road=excluded.road,
                neighbourhood=excluded.neighbourhood,
                city=excluded.city,
                state=excluded.state,
                postcode=excluded.postcode,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            record,
        )
        conn.commit()

    return {
        **record,
        "maps_url": google_maps_url(latitude, longitude),
        "street_view_url": google_street_view_url(latitude, longitude),
        "source": "NOMINATIM",
    }
