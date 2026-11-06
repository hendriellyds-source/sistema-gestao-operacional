from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import settings
from .sqlite_connection import ClosingConnection
from .demo_cleanup import DEMO_VEHICLE_IDENTIFIERS
from .history import filter_position_points, local_date_bounds_utc


def app_timezone():
    try:
        return ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=-3))


def connect() -> sqlite3.Connection:
    db_path = Path(settings.history_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_operations_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vehicle_profiles (
                identifier TEXT PRIMARY KEY,
                description TEXT,
                brand TEXT,
                model TEXT,
                year INTEGER,
                category TEXT,
                color TEXT,
                department TEXT,
                team_id INTEGER,
                driver TEXT,
                driver_employee_id INTEGER,
                chassis TEXT,
                renavam TEXT,
                initial_odometer REAL,
                initial_hour_meter REAL,
                notes TEXT,
                hidden INTEGER NOT NULL DEFAULT 0,
                permanently_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                local_override INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS geofences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                radius_m REAL NOT NULL DEFAULT 150,
                fence_type TEXT NOT NULL DEFAULT 'OBRA',
                allowed INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                address TEXT,
                postal_code TEXT,
                coordinate_text TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts_cache (
                external_id INTEGER PRIMARY KEY,
                event_time TEXT,
                identifier TEXT,
                driver TEXT,
                event_type TEXT,
                status TEXT,
                description TEXT,
                latitude REAL,
                longitude REAL,
                monitored INTEGER,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_vehicle_time
                ON alerts_cache(identifier, event_time);
            CREATE INDEX IF NOT EXISTS idx_alerts_status_time
                ON alerts_cache(status, event_time);

            CREATE TABLE IF NOT EXISTS fuel_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                fuel_type TEXT NOT NULL DEFAULT 'DIESEL',
                liters REAL NOT NULL DEFAULT 0,
                total_value REAL NOT NULL DEFAULT 0,
                price_per_liter REAL,
                odometer REAL,
                supplier TEXT,
                driver TEXT,
                notes TEXT,
                source TEXT NOT NULL DEFAULT 'LOCAL',
                external_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fuel_vehicle_date
                ON fuel_entries(identifier, entry_date);

            CREATE TABLE IF NOT EXISTS maintenance_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                maintenance_type TEXT NOT NULL DEFAULT 'OUTROS',
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'CONCLUIDA',
                value REAL NOT NULL DEFAULT 0,
                odometer REAL,
                hour_meter REAL,
                supplier TEXT,
                next_date TEXT,
                next_odometer REAL,
                notes TEXT,
                source TEXT NOT NULL DEFAULT 'LOCAL',
                external_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_maintenance_vehicle_date
                ON maintenance_entries(identifier, entry_date);

            CREATE TABLE IF NOT EXISTS expense_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                expense_type TEXT NOT NULL DEFAULT 'OUTROS',
                description TEXT NOT NULL,
                value REAL NOT NULL DEFAULT 0,
                supplier TEXT,
                driver TEXT,
                notes TEXT,
                source TEXT NOT NULL DEFAULT 'LOCAL',
                external_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_expense_vehicle_date
                ON expense_entries(identifier, entry_date);

            CREATE TABLE IF NOT EXISTS vehicle_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                document_type TEXT NOT NULL,
                document_number TEXT,
                issue_date TEXT,
                expiry_date TEXT,
                value REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ATIVO',
                notes TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_vehicle_expiry
                ON vehicle_documents(identifier, expiry_date);

            CREATE TABLE IF NOT EXISTS vehicle_debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                debt_type TEXT NOT NULL DEFAULT 'OUTRO',
                description TEXT NOT NULL,
                reference_year INTEGER,
                due_date TEXT,
                amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDENTE',
                paid_date TEXT,
                payment_method TEXT,
                source TEXT NOT NULL DEFAULT 'MANUAL',
                source_document_id INTEGER,
                expense_entry_id INTEGER,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vehicle_debts_vehicle_due
                ON vehicle_debts(identifier, due_date, status);

            CREATE TABLE IF NOT EXISTS usage_rules (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                work_start TEXT NOT NULL,
                work_end TEXT NOT NULL,
                allow_weekends INTEGER NOT NULL,
                speed_limit_kmh REAL NOT NULL,
                idle_limit_minutes INTEGER NOT NULL,
                stop_minimum_minutes INTEGER NOT NULL,
                trip_end_minutes INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                browser_enabled INTEGER NOT NULL DEFAULT 1,
                webhook_url TEXT,
                notify_critical_only INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operations_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(vehicle_profiles)").fetchall()
        }
        if "hidden" not in columns:
            conn.execute(
                "ALTER TABLE vehicle_profiles ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
            )
        if "team_id" not in columns:
            conn.execute("ALTER TABLE vehicle_profiles ADD COLUMN team_id INTEGER")
        if "driver_employee_id" not in columns:
            conn.execute("ALTER TABLE vehicle_profiles ADD COLUMN driver_employee_id INTEGER")
        if "permanently_deleted" not in columns:
            conn.execute("ALTER TABLE vehicle_profiles ADD COLUMN permanently_deleted INTEGER NOT NULL DEFAULT 0")
        if "deleted_at" not in columns:
            conn.execute("ALTER TABLE vehicle_profiles ADD COLUMN deleted_at TEXT")
        if "local_override" not in columns:
            conn.execute("ALTER TABLE vehicle_profiles ADD COLUMN local_override INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "UPDATE vehicle_profiles SET permanently_deleted=1, deleted_at=COALESCE(deleted_at, updated_at) WHERE hidden=1"
        )
        geofence_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(geofences)").fetchall()
        }
        if "address" not in geofence_columns:
            conn.execute("ALTER TABLE geofences ADD COLUMN address TEXT")
        if "postal_code" not in geofence_columns:
            conn.execute("ALTER TABLE geofences ADD COLUMN postal_code TEXT")
        if "coordinate_text" not in geofence_columns:
            conn.execute("ALTER TABLE geofences ADD COLUMN coordinate_text TEXT")
        conn.execute(
            """
            INSERT OR IGNORE INTO usage_rules (
                id, work_start, work_end, allow_weekends,
                speed_limit_kmh, idle_limit_minutes,
                stop_minimum_minutes, trip_end_minutes, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                settings.default_work_start,
                settings.default_work_end,
                int(settings.default_allow_weekends),
                settings.default_speed_limit_kmh,
                settings.default_idle_limit_minutes,
                settings.default_stop_minimum_minutes,
                settings.default_trip_end_minutes,
                now_iso(),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO notification_settings (
                id, browser_enabled, webhook_url,
                notify_critical_only, updated_at
            ) VALUES (1, 1, NULL, 1, ?)
            """,
            (now_iso(),),
        )
        migration = conn.execute(
            "SELECT value FROM operations_meta WHERE key='schema_version'"
        ).fetchone()
        version = int(migration["value"]) if migration and str(migration["value"]).isdigit() else 0
        if version < 6:
            conn.execute(
                """
                UPDATE usage_rules
                SET work_start='04:00', work_end='20:00', allow_weekends=0,
                    updated_at=?
                WHERE id=1
                """,
                (now_iso(),),
            )
            if not settings.demo_mode:
                for demo_identifier in ("ABC1D23", "DEF4G56"):
                    if _table_exists(conn, "positions"):
                        conn.execute(
                            "DELETE FROM positions WHERE UPPER(rastreavel)=UPPER(?)",
                            (demo_identifier,),
                        )
                    conn.execute(
                        "DELETE FROM vehicle_profiles WHERE UPPER(identifier)=UPPER(?)",
                        (demo_identifier,),
                    )
            conn.execute(
                """
                INSERT INTO operations_meta (key, value, updated_at)
                VALUES ('schema_version', '6', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value='6', updated_at=excluded.updated_at
                """,
                (now_iso(),),
            )
        if not settings.demo_mode:
            _purge_demo_rows(conn)
        conn.commit()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _purge_demo_rows(conn: sqlite3.Connection) -> None:
    placeholders = ",".join("?" for _ in DEMO_VEHICLE_IDENTIFIERS)
    identifiers = tuple(DEMO_VEHICLE_IDENTIFIERS)
    if _table_exists(conn, "positions"):
        conn.execute(
            f"DELETE FROM positions WHERE UPPER(REPLACE(rastreavel, '-', '')) IN ({placeholders}) OR UPPER(position_type)='DEMO'",
            identifiers,
        )
    for table in (
        "vehicle_profiles",
        "fuel_entries",
        "maintenance_entries",
        "expense_entries",
        "vehicle_documents",
        "vehicle_debts",
    ):
        conn.execute(
            f"DELETE FROM {table} WHERE UPPER(REPLACE(identifier, '-', '')) IN ({placeholders})",
            identifiers,
        )
    conn.execute(
        "DELETE FROM vehicle_profiles WHERE UPPER(COALESCE(description,'')) LIKE '%DEMONSTR%' OR UPPER(COALESCE(notes,'')) LIKE '%DADO FICT%'")
    conn.execute(
        "DELETE FROM fuel_entries WHERE UPPER(COALESCE(source,'')) IN ('DEMO','DEMONSTRACAO') OR UPPER(COALESCE(notes,'')) LIKE '%REGISTRO FICT%'")
    conn.execute(
        "DELETE FROM maintenance_entries WHERE UPPER(COALESCE(source,'')) IN ('DEMO','DEMONSTRACAO') OR UPPER(COALESCE(notes,'')) LIKE '%REGISTRO FICT%'")
    conn.execute(
        "DELETE FROM expense_entries WHERE UPPER(COALESCE(source,'')) IN ('DEMO','DEMONSTRACAO') OR UPPER(COALESCE(notes,'')) LIKE '%REGISTRO FICT%'")
    conn.execute(
        "DELETE FROM vehicle_documents WHERE UPPER(COALESCE(document_number,'')) LIKE 'DEMO-%' OR UPPER(COALESCE(notes,'')) LIKE '%FICT%'")
    conn.execute(
        "DELETE FROM vehicle_debts WHERE UPPER(COALESCE(source,'')) IN ('DEMO','DEMONSTRACAO') OR UPPER(COALESCE(notes,'')) LIKE '%FICT%'")
    conn.execute(
        f"DELETE FROM alerts_cache WHERE UPPER(REPLACE(COALESCE(identifier,''), '-', '')) IN ({placeholders}) OR UPPER(COALESCE(description,'')) LIKE '%DEMONSTRAT%'",
        identifiers,
    )
    conn.execute(
        "DELETE FROM geofences WHERE UPPER(COALESCE(notes,'')) LIKE '%DEMONSTRAT%' OR UPPER(name)='OBRA DEMONSTRATIVA'"
    )
    conn.execute("DELETE FROM operations_meta WHERE key LIKE 'demo_%'")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def date_bounds(date_from: str, date_to: str) -> tuple[str, str]:
    try:
        start_date = date.fromisoformat(date_from)
        end_date = date.fromisoformat(date_to)
    except ValueError as exc:
        raise ValueError("Datas inválidas. Use AAAA-MM-DD.") from exc
    if end_date < start_date:
        raise ValueError("A data final não pode ser anterior à data inicial.")
    tz = app_timezone()
    start = datetime.combine(start_date, time.min, tzinfo=tz)
    end = datetime.combine(end_date, time.max, tzinfo=tz)
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_km(lat1, lon1, lat2, lon2) * 1000


def get_positions(identifier: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
    start, end = local_date_bounds_utc(date_from, date_to)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT external_id, position_time, create_date, rastreavel,
                   imei, position_type, motorista, latitude, longitude,
                   speed, course, provider_distance, drive_hours,
                   altitude, power, battery, real_time, ignition, locked,
                   satellite, rssi, info
            FROM positions
            WHERE UPPER(rastreavel) = UPPER(?)
              AND position_time >= ? AND position_time < ?
            ORDER BY position_time, external_id
            """,
            (identifier, start, end),
        ).fetchall()
    cleaned, _quality = filter_position_points(dict(row) for row in rows)
    return cleaned


def get_all_positions(date_from: str, date_to: str) -> list[dict[str, Any]]:
    start, end = local_date_bounds_utc(date_from, date_to)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT external_id, position_time, rastreavel, motorista,
                   latitude, longitude, speed, ignition, locked,
                   power, battery, satellite, rssi
            FROM positions
            WHERE position_time >= ? AND position_time < ?
            ORDER BY rastreavel, position_time, external_id
            """,
            (start, end),
        ).fetchall()
    by_vehicle: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        point = dict(row)
        by_vehicle.setdefault(str(point.get("rastreavel") or ""), []).append(point)
    cleaned: list[dict[str, Any]] = []
    for vehicle_points in by_vehicle.values():
        valid_points, _quality = filter_position_points(vehicle_points)
        cleaned.extend(valid_points)
    return sorted(
        cleaned,
        key=lambda point: (
            str(point.get("rastreavel") or ""),
            str(point.get("position_time") or ""),
            int(point.get("external_id") or 0),
        ),
    )


def _point_speed(point: dict[str, Any]) -> float:
    try:
        return max(0.0, float(point.get("speed") or 0))
    except (TypeError, ValueError):
        return 0.0


def _point_time(point: dict[str, Any]) -> datetime:
    return parse_iso(point.get("position_time")) or datetime.now(timezone.utc)


def _is_moving(point: dict[str, Any], previous: dict[str, Any] | None = None) -> bool:
    if _point_speed(point) > 2:
        return True
    if previous:
        seconds = max(1.0, (_point_time(point) - _point_time(previous)).total_seconds())
        meters = distance_m(
            float(previous["latitude"]), float(previous["longitude"]),
            float(point["latitude"]), float(point["longitude"]),
        )
        return meters >= 80 and seconds <= 300
    return False


def calculate_stops(identifier: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
    points = get_positions(identifier, date_from, date_to)
    rules = get_usage_rules()
    minimum_seconds = int(rules["stop_minimum_minutes"]) * 60
    stops: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []

    def finish_stop() -> None:
        nonlocal active
        if len(active) < 2:
            active = []
            return
        start_dt = _point_time(active[0])
        end_dt = _point_time(active[-1])
        duration = (end_dt - start_dt).total_seconds()
        if duration >= minimum_seconds:
            lat = sum(float(p["latitude"]) for p in active) / len(active)
            lon = sum(float(p["longitude"]) for p in active) / len(active)
            fence = find_geofence_for_point(lat, lon)
            stops.append(
                {
                    "start_time": start_dt.isoformat(),
                    "end_time": end_dt.isoformat(),
                    "duration_minutes": round(duration / 60, 1),
                    "latitude": lat,
                    "longitude": lon,
                    "ignition_on": any(bool(p.get("ignition")) for p in active),
                    "geofence": fence,
                    "point_count": len(active),
                }
            )
        active = []

    for index, point in enumerate(points):
        previous = points[index - 1] if index else None
        if not _is_moving(point, previous):
            if active and (_point_time(point) - _point_time(active[-1])).total_seconds() > 900:
                finish_stop()
            active.append(point)
        else:
            finish_stop()
    finish_stop()
    return stops


def calculate_trips(identifier: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
    points = get_positions(identifier, date_from, date_to)
    rules = get_usage_rules()
    end_after_seconds = int(rules["trip_end_minutes"]) * 60
    trips: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    stopped_since: datetime | None = None

    def finish_trip() -> None:
        nonlocal active, stopped_since
        if len(active) < 2:
            active = []
            stopped_since = None
            return
        start_dt = _point_time(active[0])
        end_dt = _point_time(active[-1])
        distance = 0.0
        moving_seconds = 0.0
        max_speed = 0.0
        speeds: list[float] = []
        for idx in range(1, len(active)):
            a, b = active[idx - 1], active[idx]
            dt_seconds = max(0.0, (_point_time(b) - _point_time(a)).total_seconds())
            segment_km = haversine_km(
                float(a["latitude"]), float(a["longitude"]),
                float(b["latitude"]), float(b["longitude"]),
            )
            if segment_km < 5:
                distance += segment_km
            speed = _point_speed(b)
            max_speed = max(max_speed, speed)
            if speed > 2:
                moving_seconds += dt_seconds
                speeds.append(speed)
        start_fence = find_geofence_for_point(float(active[0]["latitude"]), float(active[0]["longitude"]))
        end_fence = find_geofence_for_point(float(active[-1]["latitude"]), float(active[-1]["longitude"]))
        duration_seconds = max(0.0, (end_dt - start_dt).total_seconds())
        trips.append(
            {
                "trip_id": f"trip-{len(trips) + 1}",
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "duration_minutes": round(duration_seconds / 60, 1),
                "moving_minutes": round(moving_seconds / 60, 1),
                "distance_km": round(distance, 2),
                "max_speed": round(max_speed, 1),
                "average_moving_speed": round(sum(speeds) / len(speeds), 1) if speeds else 0,
                "start_latitude": float(active[0]["latitude"]),
                "start_longitude": float(active[0]["longitude"]),
                "end_latitude": float(active[-1]["latitude"]),
                "end_longitude": float(active[-1]["longitude"]),
                "start_geofence": start_fence,
                "end_geofence": end_fence,
                "driver": next((p.get("motorista") for p in active if p.get("motorista")), None),
                "point_count": len(active),
            }
        )
        active = []
        stopped_since = None

    for index, point in enumerate(points):
        previous = points[index - 1] if index else None
        moving = _is_moving(point, previous)
        if moving:
            if not active and previous:
                active.append(previous)
            active.append(point)
            stopped_since = None
        elif active:
            active.append(point)
            if stopped_since is None:
                stopped_since = _point_time(point)
            elif (_point_time(point) - stopped_since).total_seconds() >= end_after_seconds:
                # Retira a longa cauda parada, mantendo apenas o primeiro ponto de parada.
                cutoff = stopped_since
                active = [p for p in active if _point_time(p) <= cutoff]
                finish_trip()
    finish_trip()
    return trips




def _geofence_type(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("fence_type") or "").strip().upper()


def _stop_category(stop: dict[str, Any]) -> tuple[str, str]:
    fence_type = _geofence_type(stop.get("geofence"))
    if fence_type == "OBRA":
        return "WORK", "Na obra"
    if fence_type in {"CASA", "SEDE"}:
        return "HOME", "Em casa/sede durante o período"
    if fence_type == "POSTO":
        return "OTHER", "Posto/abastecimento"
    if fence_type == "OFICINA":
        return "OTHER", "Oficina"
    return "OTHER", "Outra parada"


def calculate_operational_period(
    identifier: str,
    date_from: str,
    date_to: str,
    *,
    trips: list[dict[str, Any]] | None = None,
    stops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resume somente o período entre a primeira saída e a chegada final de cada dia.

    O objetivo é não contabilizar como tempo parado as horas em que o veículo ficou
    estacionado em casa antes do início da operação ou depois do retorno.
    """
    trips_data = list(trips if trips is not None else calculate_trips(identifier, date_from, date_to))
    stops_data = list(stops if stops is not None else calculate_stops(identifier, date_from, date_to))
    tz = app_timezone()
    minimum_seconds = max(60, int(get_usage_rules()["stop_minimum_minutes"]) * 60)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for trip in trips_data:
        start_dt = parse_iso(trip.get("start_time"))
        end_dt = parse_iso(trip.get("end_time"))
        if not start_dt or not end_dt or end_dt <= start_dt:
            continue
        day = start_dt.astimezone(tz).date().isoformat()
        trip["operational_day"] = day
        grouped.setdefault(day, []).append(trip)

    periods: list[dict[str, Any]] = []
    for day, day_trips in sorted(grouped.items()):
        day_trips.sort(key=lambda item: item.get("start_time") or "")
        first = day_trips[0]
        last = day_trips[-1]
        start_dt = parse_iso(first.get("start_time"))
        end_dt = parse_iso(last.get("end_time"))
        if not start_dt or not end_dt or end_dt <= start_dt:
            continue
        start_type = _geofence_type(first.get("start_geofence"))
        end_type = _geofence_type(last.get("end_geofence"))
        periods.append(
            {
                "date": day,
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "duration_minutes": round((end_dt - start_dt).total_seconds() / 60, 1),
                "started_at_home": start_type in {"CASA", "SEDE"},
                "returned_home": end_type in {"CASA", "SEDE"},
                "calculation_basis": (
                    "SAIDA_CASA_SEDE" if start_type in {"CASA", "SEDE"}
                    else "PRIMEIRO_MOVIMENTO_CONFIRMADO"
                ),
                "trip_count": len(day_trips),
            }
        )

    operational_stops: list[dict[str, Any]] = []
    for original_index, stop in enumerate(stops_data):
        stop_start = parse_iso(stop.get("start_time"))
        stop_end = parse_iso(stop.get("end_time"))
        if not stop_start or not stop_end or stop_end <= stop_start:
            continue

        for period in periods:
            period_start = parse_iso(period["start_time"])
            period_end = parse_iso(period["end_time"])
            if not period_start or not period_end:
                continue
            overlap_start = max(stop_start, period_start)
            overlap_end = min(stop_end, period_end)
            overlap_seconds = (overlap_end - overlap_start).total_seconds()
            if overlap_seconds < minimum_seconds:
                continue

            category, category_label = _stop_category(stop)
            clipped = dict(stop)
            clipped.update(
                {
                    "start_time": overlap_start.isoformat(),
                    "end_time": overlap_end.isoformat(),
                    "duration_minutes": round(overlap_seconds / 60, 1),
                    "original_start_time": stop.get("start_time"),
                    "original_end_time": stop.get("end_time"),
                    "original_duration_minutes": stop.get("duration_minutes"),
                    "operational_day": period["date"],
                    "category": category,
                    "category_label": category_label,
                    "clipped_to_operational_period": (
                        overlap_start != stop_start or overlap_end != stop_end
                    ),
                    "source_stop_index": original_index,
                }
            )
            operational_stops.append(clipped)

    operational_stops.sort(key=lambda item: item.get("start_time") or "")

    operational_minutes = sum(float(item["duration_minutes"]) for item in periods)
    moving_minutes = sum(float(item.get("moving_minutes") or 0) for item in trips_data)
    stopped_minutes = sum(float(item.get("duration_minutes") or 0) for item in operational_stops)
    work_minutes = sum(
        float(item.get("duration_minutes") or 0)
        for item in operational_stops
        if item.get("category") == "WORK"
    )
    home_minutes = sum(
        float(item.get("duration_minutes") or 0)
        for item in operational_stops
        if item.get("category") == "HOME"
    )
    other_minutes = sum(
        float(item.get("duration_minutes") or 0)
        for item in operational_stops
        if item.get("category") == "OTHER"
    )
    unclassified_minutes = max(0.0, operational_minutes - moving_minutes - stopped_minutes)

    first_period = periods[0] if periods else None
    last_period = periods[-1] if periods else None
    distance_km = sum(float(item.get("distance_km") or 0) for item in trips_data)
    max_speed = max((float(item.get("max_speed") or 0) for item in trips_data), default=0.0)

    return {
        "summary": {
            "departure_time": first_period["start_time"] if first_period else None,
            "arrival_time": last_period["end_time"] if last_period else None,
            "operational_minutes": round(operational_minutes, 1),
            "moving_minutes": round(moving_minutes, 1),
            "stopped_minutes": round(stopped_minutes, 1),
            "work_stopped_minutes": round(work_minutes, 1),
            "other_stopped_minutes": round(other_minutes, 1),
            "home_stopped_minutes": round(home_minutes, 1),
            "unclassified_minutes": round(unclassified_minutes, 1),
            "distance_km": round(distance_km, 2),
            "max_speed": round(max_speed, 1),
            "stops": len(operational_stops),
            "period_days": len(periods),
            "calculation_basis": (
                first_period["calculation_basis"] if first_period else "SEM_MOVIMENTO_CONFIRMADO"
            ),
            "returned_home": bool(last_period and last_period["returned_home"]),
        },
        "periods": periods,
        "stops": operational_stops,
    }


def get_vehicle_profile(identifier: str) -> dict[str, Any]:
    normalized_identifier = str(identifier or "").strip().upper()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM vehicle_profiles WHERE UPPER(identifier)=UPPER(?)",
            (normalized_identifier,),
        ).fetchone()
    if row is not None:
        result = dict(row)
        result["hidden"] = bool(result.get("hidden"))
        result["permanently_deleted"] = bool(result.get("permanently_deleted"))
        result["local_override"] = bool(result.get("local_override"))
        return result
    return {
        "identifier": normalized_identifier,
        "description": None,
        "brand": None,
        "model": None,
        "year": None,
        "category": "VEICULO",
        "color": None,
        "department": None,
        "team_id": None,
        "driver": None,
        "driver_employee_id": None,
        "chassis": None,
        "renavam": None,
        "initial_odometer": 0,
        "initial_hour_meter": 0,
        "notes": None,
        "hidden": False,
        "permanently_deleted": False,
        "deleted_at": None,
        "local_override": False,
        "updated_at": None,
    }


def save_vehicle_profile(identifier: str, data: dict[str, Any]) -> dict[str, Any]:
    normalized_identifier = str(identifier or "").strip().upper()
    if not normalized_identifier:
        raise ValueError("Identificador do veículo não informado.")
    current = get_vehicle_profile(normalized_identifier)
    fields = [
        "description", "brand", "model", "year", "category", "color",
        "department", "team_id", "driver", "driver_employee_id", "chassis", "renavam",
        "initial_odometer", "initial_hour_meter", "notes", "hidden",
        "permanently_deleted", "deleted_at", "local_override",
    ]
    merged = {**current, **data}
    # Toda edição manual vira uma substituição local persistente. Atualizações da Selsyn
    # continuam alimentando posição/status, mas não apagam os dados administrativos.
    merged["local_override"] = True
    values = [
        int(bool(merged.get(field)))
        if field in {"hidden", "permanently_deleted", "local_override"}
        else merged.get(field)
        for field in fields
    ]
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO vehicle_profiles (identifier, {', '.join(fields)}, updated_at)
            VALUES (?, {', '.join('?' for _ in fields)}, ?)
            ON CONFLICT(identifier) DO UPDATE SET
            {', '.join(f'{field}=excluded.{field}' for field in fields)},
            updated_at=excluded.updated_at
            """,
            [normalized_identifier, *values, now_iso()],
        )
        conn.commit()
    return get_vehicle_profile(normalized_identifier)


def is_vehicle_hidden(identifier: str) -> bool:
    profile = get_vehicle_profile(identifier)
    return bool(profile.get("hidden") or profile.get("permanently_deleted"))


def list_hidden_vehicles() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM vehicle_profiles
            WHERE hidden=1 OR permanently_deleted=1
            ORDER BY COALESCE(deleted_at, updated_at) DESC, COALESCE(description, identifier)
            """
        ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["hidden"] = True
        item["permanently_deleted"] = True
        item["local_override"] = bool(item.get("local_override"))
    return result


def hide_vehicle(identifier: str, purge_local_data: bool = False) -> dict[str, Any]:
    normalized_identifier = str(identifier or "").strip().upper()
    profile = get_vehicle_profile(normalized_identifier)
    deleted_at = now_iso()
    saved = save_vehicle_profile(
        normalized_identifier,
        {
            **profile,
            "hidden": True,
            "permanently_deleted": True,
            "deleted_at": deleted_at,
        },
    )

    # A exclusão é permanente na lista ativa, mas os históricos financeiros,
    # manutenções e documentos são preservados para auditoria. Quando solicitado,
    # apenas posições e alertas técnicos podem ser limpos.
    if purge_local_data:
        with connect() as conn:
            conn.execute(
                "DELETE FROM positions WHERE UPPER(rastreavel)=UPPER(?)",
                (normalized_identifier,),
            )
            conn.execute(
                "DELETE FROM alerts_cache WHERE UPPER(identifier)=UPPER(?)",
                (normalized_identifier,),
            )
            conn.commit()

    return saved


def restore_vehicle(identifier: str) -> dict[str, Any]:
    raise ValueError(
        "Este veículo foi excluído permanentemente do sistema local e não pode ser restaurado pela interface."
    )


def visible_identifiers_from_positions(date_from: str, date_to: str) -> list[str]:
    identifiers = sorted(
        {
            str(item["rastreavel"])
            for item in get_all_positions(date_from, date_to)
            if not is_vehicle_hidden(str(item["rastreavel"]))
        }
    )
    return identifiers

def list_geofences(active_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM geofences"
    params: tuple[Any, ...] = ()
    if active_only:
        query += " WHERE active=1"
    query += " ORDER BY name"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["allowed"] = bool(item["allowed"])
        item["active"] = bool(item["active"])
    return result


def create_geofence(data: dict[str, Any]) -> dict[str, Any]:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO geofences (
                name, latitude, longitude, radius_m, fence_type,
                allowed, active, address, postal_code, coordinate_text, notes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"], float(data["latitude"]), float(data["longitude"]),
                float(data.get("radius_m", 150)), data.get("fence_type", "OBRA"),
                int(bool(data.get("allowed", True))), int(bool(data.get("active", True))),
                data.get("address"), data.get("postal_code"),
                data.get("coordinate_text"), data.get("notes"), now_iso(), now_iso(),
            ),
        )
        conn.commit()
        fence_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM geofences WHERE id=?", (fence_id,)).fetchone()
    return dict(row)


def update_geofence(fence_id: int, data: dict[str, Any]) -> dict[str, Any]:
    current = next((item for item in list_geofences() if item["id"] == fence_id), None)
    if not current:
        raise KeyError("Cerca virtual não encontrada.")
    merged = {**current, **data}
    with connect() as conn:
        conn.execute(
            """
            UPDATE geofences SET name=?, latitude=?, longitude=?, radius_m=?,
                fence_type=?, allowed=?, active=?, address=?, postal_code=?,
                coordinate_text=?, notes=?, updated_at=?
            WHERE id=?
            """,
            (
                merged["name"], float(merged["latitude"]), float(merged["longitude"]),
                float(merged["radius_m"]), merged["fence_type"],
                int(bool(merged["allowed"])), int(bool(merged["active"])),
                merged.get("address"), merged.get("postal_code"),
                merged.get("coordinate_text"), merged.get("notes"),
                now_iso(), fence_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM geofences WHERE id=?", (fence_id,)).fetchone()
    return dict(row)


def delete_geofence(fence_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM geofences WHERE id=?", (fence_id,))
        conn.commit()


def find_geofence_for_point(latitude: float, longitude: float) -> dict[str, Any] | None:
    for fence in list_geofences(active_only=True):
        if distance_m(latitude, longitude, float(fence["latitude"]), float(fence["longitude"])) <= float(fence["radius_m"]):
            return fence
    return None


def geofence_activity(identifier: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
    points = get_positions(identifier, date_from, date_to)
    fences = list_geofences(active_only=True)
    events: list[dict[str, Any]] = []
    previous_inside: set[int] = set()
    for point in points:
        current_inside: set[int] = set()
        for fence in fences:
            if distance_m(
                float(point["latitude"]), float(point["longitude"]),
                float(fence["latitude"]), float(fence["longitude"]),
            ) <= float(fence["radius_m"]):
                current_inside.add(int(fence["id"]))
        for fence_id in current_inside - previous_inside:
            fence = next(f for f in fences if int(f["id"]) == fence_id)
            events.append({"event": "ENTRY", "time": point["position_time"], "fence": fence, "latitude": point["latitude"], "longitude": point["longitude"]})
        for fence_id in previous_inside - current_inside:
            fence = next(f for f in fences if int(f["id"]) == fence_id)
            events.append({"event": "EXIT", "time": point["position_time"], "fence": fence, "latitude": point["latitude"], "longitude": point["longitude"]})
        previous_inside = current_inside
    return events


def get_usage_rules() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM usage_rules WHERE id=1").fetchone()
    result = dict(row)
    result["allow_weekends"] = bool(result["allow_weekends"])
    return result


def save_usage_rules(data: dict[str, Any]) -> dict[str, Any]:
    current = get_usage_rules()
    merged = {**current, **data}
    with connect() as conn:
        conn.execute(
            """
            UPDATE usage_rules SET work_start=?, work_end=?, allow_weekends=?,
                speed_limit_kmh=?, idle_limit_minutes=?, stop_minimum_minutes=?,
                trip_end_minutes=?, updated_at=? WHERE id=1
            """,
            (
                merged["work_start"], merged["work_end"], int(bool(merged["allow_weekends"])),
                float(merged["speed_limit_kmh"]), int(merged["idle_limit_minutes"]),
                int(merged["stop_minimum_minutes"]), int(merged["trip_end_minutes"]), now_iso(),
            ),
        )
        conn.commit()
    return get_usage_rules()


def usage_violations(identifier: str, date_from: str, date_to: str) -> list[dict[str, Any]]:
    points = get_positions(identifier, date_from, date_to)
    rules = get_usage_rules()
    tz = app_timezone()
    start_h, start_m = map(int, rules["work_start"].split(":"))
    end_h, end_m = map(int, rules["work_end"].split(":"))
    allowed_fences = [f for f in list_geofences(active_only=True) if f["allowed"]]
    violations: list[dict[str, Any]] = []

    def add(
        kind: str,
        category: str,
        severity: str,
        point: dict[str, Any],
        description: str,
        value: Any = None,
    ) -> None:
        violations.append(
            {
                "type": kind,
                "category": category,
                "severity": severity,
                "time": point["position_time"],
                "identifier": identifier,
                "latitude": point["latitude"],
                "longitude": point["longitude"],
                "description": description,
                "value": value,
            }
        )

    idle_start: dict[str, Any] | None = None
    outside_active = False
    after_hours_active = False
    weekend_active = False
    speeding_active = False

    for index, point in enumerate(points):
        previous = points[index - 1] if index else None
        local_dt = _point_time(point).astimezone(tz)
        moving = _is_moving(point, previous)
        local_minutes = local_dt.hour * 60 + local_dt.minute
        work_start_minutes = start_h * 60 + start_m
        work_end_minutes = end_h * 60 + end_m
        weekend = local_dt.weekday() >= 5

        weekday_after_hours = (
            moving
            and not weekend
            and (
                local_minutes < work_start_minutes
                or local_minutes > work_end_minutes
            )
        )
        if weekday_after_hours and not after_hours_active:
            add(
                "FORA_HORARIO_DIA_UTIL",
                "Fora do horário",
                "ATENCAO",
                point,
                (
                    "Uso em dia útil fora do horário permitido "
                    f"({rules['work_start']} às {rules['work_end']})."
                ),
            )
        after_hours_active = weekday_after_hours

        weekend_use = moving and weekend
        if weekend_use and not weekend_active:
            day_name = "sábado" if local_dt.weekday() == 5 else "domingo"
            add(
                "USO_FINAL_SEMANA",
                "Final de semana",
                "ATENCAO",
                point,
                f"Uso do veículo no {day_name}.",
            )
        weekend_active = weekend_use

        speed = _point_speed(point)
        speeding = speed > float(rules["speed_limit_kmh"])
        if speeding and not speeding_active:
            add(
                "EXCESSO_VELOCIDADE",
                "Excesso de velocidade",
                "CRITICO",
                point,
                (
                    f"Velocidade de {speed:.1f} km/h acima do limite "
                    f"de {float(rules['speed_limit_kmh']):.0f} km/h."
                ),
                speed,
            )
        speeding_active = speeding

        if bool(point.get("ignition")) and not moving:
            if idle_start is None:
                idle_start = point
            elapsed = (
                _point_time(point) - _point_time(idle_start)
            ).total_seconds() / 60
            if elapsed >= int(rules["idle_limit_minutes"]):
                add(
                    "MARCHA_LENTA",
                    "Ignição sem deslocamento",
                    "ATENCAO",
                    idle_start,
                    (
                        "Ignição ligada sem deslocamento por pelo menos "
                        f"{elapsed:.0f} minutos."
                    ),
                    round(elapsed, 1),
                )
                idle_start = point
        else:
            idle_start = None

        if allowed_fences and moving:
            inside_any = any(
                distance_m(
                    float(point["latitude"]),
                    float(point["longitude"]),
                    float(f["latitude"]),
                    float(f["longitude"]),
                ) <= float(f["radius_m"])
                for f in allowed_fences
            )
            if not inside_any and not outside_active:
                add(
                    "FORA_AREA_PERMITIDA",
                    "Fora da área permitida",
                    "ATENCAO",
                    point,
                    "Veículo fora das áreas permitidas cadastradas.",
                )
            outside_active = not inside_any
        else:
            outside_active = False

    return violations

def _alert_severity(event_type: str | None) -> str:
    critical = {"PANIC", "SECURITY_ATTACK", "COLLISION_ALARM", "SPEED_LIMIT", "DRIVER_AUTHORIZATION"}
    warning = {"OFFLINE_TIMEOUT", "ENERGY_SOURCE", "DEVICE_BATTERY", "GPS", "STOPPED_OVERTIME", "MECHANICAL_ISSUE", "DEVICE_ISSUE", "GEO_FENCE", "BEHAVIOR"}
    value = str(event_type or "").upper()
    if value in critical:
        return "CRITICO"
    if value in warning:
        return "ATENCAO"
    return "INFORMATIVO"


def normalize_alert(item: dict[str, Any]) -> dict[str, Any] | None:
    raw_id = item.get("id") or item.get("alertaId") or item.get("eventoId")
    try:
        external_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    position = item.get("posicao") or item.get("position") or {}
    rastreavel = item.get("rastreavel") or item.get("identificador") or item.get("veiculo")
    if isinstance(rastreavel, dict):
        rastreavel = rastreavel.get("identificador") or rastreavel.get("descricao") or rastreavel.get("id")
    driver = item.get("motorista")
    if isinstance(driver, dict):
        driver = driver.get("nome") or driver.get("descricao") or driver.get("id")
    event_type = item.get("tipo") or item.get("tipoAlerta") or item.get("agrupador") or item.get("evento")
    if isinstance(event_type, dict):
        event_type = event_type.get("nome") or event_type.get("descricao") or event_type.get("id")
    status = item.get("status") or "NEW"
    event_time = item.get("data") or item.get("dataEvento") or item.get("time") or item.get("createDate")
    description = item.get("descricao") or item.get("description") or item.get("mensagem") or str(event_type or "Evento")
    latitude = item.get("latitude", position.get("latitude"))
    longitude = item.get("longitude", position.get("longitude"))
    return {
        "external_id": external_id,
        "event_time": event_time,
        "identifier": str(rastreavel) if rastreavel is not None else None,
        "driver": str(driver) if driver is not None else None,
        "event_type": str(event_type) if event_type is not None else None,
        "status": str(status),
        "description": str(description),
        "latitude": latitude,
        "longitude": longitude,
        "monitored": bool(item.get("monitoramento", item.get("monitorado", False))),
        "severity": _alert_severity(str(event_type) if event_type is not None else None),
        "raw": item,
    }


def upsert_alerts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    normalized = [alert for item in items if isinstance(item, dict) and (alert := normalize_alert(item))]
    if not normalized:
        return {"received": 0, "saved": 0}
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT INTO alerts_cache (
                external_id, event_time, identifier, driver, event_type,
                status, description, latitude, longitude, monitored,
                raw_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
                event_time=excluded.event_time,
                identifier=excluded.identifier,
                driver=excluded.driver,
                event_type=excluded.event_type,
                status=excluded.status,
                description=excluded.description,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                monitored=excluded.monitored,
                raw_json=excluded.raw_json,
                synced_at=excluded.synced_at
            """,
            [
                (
                    a["external_id"], a["event_time"], a["identifier"], a["driver"],
                    a["event_type"], a["status"], a["description"], a["latitude"],
                    a["longitude"], int(a["monitored"]),
                    json.dumps(a["raw"], ensure_ascii=False, default=str), now_iso(),
                )
                for a in normalized
            ],
        )
        conn.commit()
        saved = conn.total_changes - before
    return {"received": len(normalized), "saved": saved}


def list_cached_alerts(
    date_from: str,
    date_to: str,
    identifier: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    start, end = date_bounds(date_from, date_to)
    conditions = ["COALESCE(event_time, synced_at) BETWEEN ? AND ?"]
    params: list[Any] = [start, end]
    if identifier:
        conditions.append("UPPER(identifier)=UPPER(?)")
        params.append(identifier)
    if status:
        conditions.append("UPPER(status)=UPPER(?)")
        params.append(status)
    if event_type:
        conditions.append("UPPER(event_type)=UPPER(?)")
        params.append(event_type)
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM alerts_cache
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(event_time, synced_at) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["monitored"] = bool(item["monitored"])
        item["severity"] = _alert_severity(item.get("event_type"))
        result.append(item)
    return result


def update_cached_alert_status(external_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE alerts_cache SET status=?, synced_at=? WHERE external_id=?", (status, now_iso(), external_id))
        conn.commit()


def driving_score(identifier: str, date_from: str, date_to: str) -> dict[str, Any]:
    points = get_positions(identifier, date_from, date_to)
    violations = usage_violations(identifier, date_from, date_to)
    alerts = list_cached_alerts(date_from, date_to, identifier=identifier, limit=5000)
    harsh = 0
    for idx in range(1, len(points)):
        seconds = (_point_time(points[idx]) - _point_time(points[idx - 1])).total_seconds()
        if 2 <= seconds <= 60:
            delta = abs(_point_speed(points[idx]) - _point_speed(points[idx - 1]))
            if delta >= 25:
                harsh += 1
    speed_events = sum(1 for v in violations if v["type"] == "EXCESSO_VELOCIDADE")
    idle_events = sum(1 for v in violations if v["type"] == "MARCHA_LENTA")
    after_hours = sum(1 for v in violations if v["type"] == "FORA_HORARIO")
    critical_alerts = sum(1 for a in alerts if a["severity"] == "CRITICO")
    warning_alerts = sum(1 for a in alerts if a["severity"] == "ATENCAO")
    penalty = speed_events * 5 + harsh * 3 + idle_events * 2 + after_hours * 3 + critical_alerts * 6 + warning_alerts
    score = max(0, min(100, 100 - penalty))
    rating = "EXCELENTE" if score >= 90 else "BOM" if score >= 75 else "ATENCAO" if score >= 60 else "CRITICO"
    trips = calculate_trips(identifier, date_from, date_to)
    return {
        "identifier": identifier,
        "score": score,
        "rating": rating,
        "speeding_events": speed_events,
        "harsh_events_estimated": harsh,
        "idle_events": idle_events,
        "after_hours_events": after_hours,
        "critical_alerts": critical_alerts,
        "warning_alerts": warning_alerts,
        "distance_km": round(sum(t["distance_km"] for t in trips), 2),
        "trip_count": len(trips),
        "methodology": "Pontuação local calculada com posições e alertas disponíveis; eventos bruscos são estimados pela variação de velocidade.",
    }


def _insert_local(table: str, columns: list[str], data: dict[str, Any]) -> dict[str, Any]:
    values = [data.get(column) for column in columns]
    with connect() as conn:
        cursor = conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}, created_at) VALUES ({', '.join('?' for _ in columns)}, ?)",
            [*values, now_iso()],
        )
        conn.commit()
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def _list_local(table: str, date_column: str, date_from: str, date_to: str, identifier: str | None = None) -> list[dict[str, Any]]:
    conditions = [f"{date_column} BETWEEN ? AND ?"]
    params: list[Any] = [date_from, date_to]
    if identifier:
        conditions.append("UPPER(identifier)=UPPER(?)")
        params.append(identifier)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(conditions)} ORDER BY {date_column} DESC, id DESC",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _delete_local(table: str, record_id: int) -> None:
    with connect() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
        conn.commit()


def add_fuel(data: dict[str, Any]) -> dict[str, Any]:
    liters = float(data.get("liters") or 0)
    total = float(data.get("total_value") or 0)
    if liters > 0 and not data.get("price_per_liter"):
        data["price_per_liter"] = round(total / liters, 4)
    return _insert_local("fuel_entries", ["identifier", "entry_date", "fuel_type", "liters", "total_value", "price_per_liter", "odometer", "supplier", "driver", "notes", "source", "external_id"], data)


def list_fuel(date_from: str, date_to: str, identifier: str | None = None) -> list[dict[str, Any]]:
    return _list_local("fuel_entries", "entry_date", date_from, date_to, identifier)


def delete_fuel(record_id: int) -> None:
    _delete_local("fuel_entries", record_id)


def add_maintenance(data: dict[str, Any]) -> dict[str, Any]:
    return _insert_local("maintenance_entries", ["identifier", "entry_date", "maintenance_type", "description", "status", "value", "odometer", "hour_meter", "supplier", "next_date", "next_odometer", "notes", "source", "external_id"], data)


def list_maintenance(date_from: str, date_to: str, identifier: str | None = None) -> list[dict[str, Any]]:
    return _list_local("maintenance_entries", "entry_date", date_from, date_to, identifier)


def list_calendar_maintenance(date_from: str, date_to: str) -> list[dict[str, Any]]:
    # Calendar queries must also find a next_date whose original entry is outside
    # the selected month. This is read-only and keeps the source table authoritative.
    date.fromisoformat(date_from)
    date.fromisoformat(date_to)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM maintenance_entries
            WHERE (entry_date BETWEEN ? AND ?)
               OR (next_date BETWEEN ? AND ?)
            ORDER BY COALESCE(next_date, entry_date), id
            """,
            (date_from, date_to, date_from, date_to),
        ).fetchall()
    cleaned, _quality = filter_position_points(dict(row) for row in rows)
    return cleaned


def delete_maintenance(record_id: int) -> None:
    _delete_local("maintenance_entries", record_id)


def add_expense(data: dict[str, Any]) -> dict[str, Any]:
    return _insert_local("expense_entries", ["identifier", "entry_date", "expense_type", "description", "value", "supplier", "driver", "notes", "source", "external_id"], data)


def list_expenses(date_from: str, date_to: str, identifier: str | None = None) -> list[dict[str, Any]]:
    return _list_local("expense_entries", "entry_date", date_from, date_to, identifier)


def delete_expense(record_id: int) -> None:
    _delete_local("expense_entries", record_id)


def add_document(data: dict[str, Any]) -> dict[str, Any]:
    return _insert_local("vehicle_documents", ["identifier", "document_type", "document_number", "issue_date", "expiry_date", "value", "status", "notes"], data)


def list_documents(identifier: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    condition = ""
    if identifier:
        condition = " WHERE UPPER(identifier)=UPPER(?)"
        params.append(identifier)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM vehicle_documents{condition} ORDER BY COALESCE(expiry_date, '9999-12-31'), id DESC", params).fetchall()
    result = [dict(row) for row in rows]
    today = date.today()
    for item in result:
        if item.get("expiry_date"):
            try:
                expiry = date.fromisoformat(item["expiry_date"])
                item["days_to_expiry"] = (expiry - today).days
                item["calculated_status"] = "VENCIDO" if expiry < today else "VENCE_EM_BREVE" if (expiry - today).days <= 30 else "ATIVO"
            except ValueError:
                item["days_to_expiry"] = None
                item["calculated_status"] = item.get("status")
        else:
            item["days_to_expiry"] = None
            item["calculated_status"] = item.get("status")
    return result


def delete_document(record_id: int) -> None:
    _delete_local("vehicle_documents", record_id)


def _decorate_vehicle_debt(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    raw_status = str(result.get("status") or "PENDENTE").strip().upper()
    today = date.today()
    days_to_due = None
    calculated = raw_status
    if raw_status == "PAGO":
        calculated = "PAGO"
    elif raw_status == "CANCELADO":
        calculated = "CANCELADO"
    elif result.get("due_date"):
        try:
            due = date.fromisoformat(str(result["due_date"]))
            days_to_due = (due - today).days
            if days_to_due < 0:
                calculated = "VENCIDO"
            elif days_to_due <= 30:
                calculated = "VENCE_EM_BREVE"
            else:
                calculated = "PENDENTE"
        except ValueError:
            calculated = raw_status or "PENDENTE"
    else:
        calculated = "PENDENTE"
    result["calculated_status"] = calculated
    result["days_to_due"] = days_to_due
    return result


def get_vehicle_debt(record_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM vehicle_debts WHERE id=?", (int(record_id),)).fetchone()
    if row is None:
        raise KeyError("Débito não encontrado.")
    return _decorate_vehicle_debt(dict(row))


def list_vehicle_debts(identifier: str | None = None, include_paid: bool = True) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if identifier:
        conditions.append("UPPER(identifier)=UPPER(?)")
        params.append(str(identifier).strip())
    if not include_paid:
        conditions.append("UPPER(status) NOT IN ('PAGO', 'CANCELADO')")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM vehicle_debts{where} ORDER BY CASE WHEN due_date IS NULL OR due_date='' THEN 1 ELSE 0 END, due_date, id DESC",
            params,
        ).fetchall()
    return [_decorate_vehicle_debt(dict(row)) for row in rows]


def _vehicle_debt_duplicate(conn: sqlite3.Connection, data: dict[str, Any], ignore_id: int | None = None) -> sqlite3.Row | None:
    conditions = [
        "UPPER(identifier)=UPPER(?)",
        "UPPER(TRIM(description))=UPPER(TRIM(?))",
        "COALESCE(due_date, '')=COALESCE(?, '')",
        "ABS(amount-?) < 0.005",
        "UPPER(status) <> 'CANCELADO'",
    ]
    params: list[Any] = [
        str(data.get("identifier") or "").strip(),
        str(data.get("description") or "").strip(),
        data.get("due_date") or None,
        float(data.get("amount") or 0),
    ]
    if ignore_id is not None:
        conditions.append("id<>?")
        params.append(int(ignore_id))
    return conn.execute(
        f"SELECT * FROM vehicle_debts WHERE {' AND '.join(conditions)} ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()


def add_vehicle_debt(data: dict[str, Any], skip_duplicate: bool = True) -> dict[str, Any]:
    payload = dict(data)
    payload["identifier"] = str(payload.get("identifier") or "").strip().upper()
    payload["description"] = str(payload.get("description") or "").strip()
    payload["debt_type"] = str(payload.get("debt_type") or "OUTRO").strip().upper()
    payload["status"] = str(payload.get("status") or "PENDENTE").strip().upper()
    payload["source"] = str(payload.get("source") or "MANUAL").strip().upper()
    payload["amount"] = round(float(payload.get("amount") or 0), 2)
    if not payload["identifier"]:
        raise ValueError("Veículo não informado.")
    if not payload["description"]:
        raise ValueError("Descrição do débito não informada.")
    if payload["amount"] < 0:
        raise ValueError("O valor do débito não pode ser negativo.")
    timestamp = now_iso()
    with connect() as conn:
        existing = _vehicle_debt_duplicate(conn, payload)
        if existing is not None and skip_duplicate:
            result = _decorate_vehicle_debt(dict(existing))
            result["duplicate"] = True
            return result
        cursor = conn.execute(
            """
            INSERT INTO vehicle_debts (
                identifier, debt_type, description, reference_year, due_date,
                amount, status, paid_date, payment_method, source,
                source_document_id, expense_entry_id, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["identifier"], payload["debt_type"], payload["description"],
                payload.get("reference_year"), payload.get("due_date") or None,
                payload["amount"], payload["status"], payload.get("paid_date") or None,
                payload.get("payment_method") or None, payload["source"],
                payload.get("source_document_id"), payload.get("expense_entry_id"),
                payload.get("notes") or None, timestamp, timestamp,
            ),
        )
        record_id = int(cursor.lastrowid)
        conn.commit()
    result = get_vehicle_debt(record_id)
    result["duplicate"] = False
    return result


def add_vehicle_debts_bulk(items: list[dict[str, Any]]) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        try:
            result = add_vehicle_debt(item, skip_duplicate=True)
            if result.get("duplicate"):
                duplicates.append(result)
            else:
                created.append(result)
        except Exception as exc:
            errors.append({"index": index, "message": str(exc), "item": item})
    return {
        "ok": not errors,
        "created": created,
        "duplicates": duplicates,
        "errors": errors,
        "created_count": len(created),
        "duplicate_count": len(duplicates),
        "error_count": len(errors),
    }


def update_vehicle_debt(record_id: int, data: dict[str, Any]) -> dict[str, Any]:
    current = get_vehicle_debt(record_id)
    payload = {**current, **data}
    payload["identifier"] = str(payload.get("identifier") or "").strip().upper()
    payload["description"] = str(payload.get("description") or "").strip()
    payload["debt_type"] = str(payload.get("debt_type") or "OUTRO").strip().upper()
    payload["status"] = str(payload.get("status") or "PENDENTE").strip().upper()
    payload["source"] = str(payload.get("source") or "MANUAL").strip().upper()
    payload["amount"] = round(float(payload.get("amount") or 0), 2)
    with connect() as conn:
        duplicate = _vehicle_debt_duplicate(conn, payload, ignore_id=int(record_id))
        if duplicate is not None:
            raise ValueError("Já existe um débito igual para este veículo, vencimento e valor.")
        conn.execute(
            """
            UPDATE vehicle_debts SET
                identifier=?, debt_type=?, description=?, reference_year=?, due_date=?,
                amount=?, status=?, paid_date=?, payment_method=?, source=?,
                source_document_id=?, expense_entry_id=?, notes=?, updated_at=?
            WHERE id=?
            """,
            (
                payload["identifier"], payload["debt_type"], payload["description"],
                payload.get("reference_year"), payload.get("due_date") or None,
                payload["amount"], payload["status"], payload.get("paid_date") or None,
                payload.get("payment_method") or None, payload["source"],
                payload.get("source_document_id"), payload.get("expense_entry_id"),
                payload.get("notes") or None, now_iso(), int(record_id),
            ),
        )
        conn.commit()
    return get_vehicle_debt(record_id)


def mark_vehicle_debt_paid(
    record_id: int,
    paid_date: str,
    payment_method: str | None = None,
    register_expense: bool = True,
) -> dict[str, Any]:
    debt = get_vehicle_debt(record_id)
    expense_entry_id = debt.get("expense_entry_id")
    if register_expense and not expense_entry_id:
        expense = add_expense(
            {
                "identifier": debt["identifier"],
                "entry_date": paid_date,
                "expense_type": debt.get("debt_type") or "IMPOSTO",
                "description": f"Pagamento de débito: {debt['description']}",
                "value": float(debt.get("amount") or 0),
                "supplier": None,
                "driver": None,
                "notes": f"Gerado pelo débito #{record_id}.",
                "source": "DEBITO_VEICULO",
                "external_id": str(record_id),
            }
        )
        expense_entry_id = expense.get("id")
    return update_vehicle_debt(
        record_id,
        {
            "status": "PAGO",
            "paid_date": paid_date,
            "payment_method": payment_method or "NÃO INFORMADO",
            "expense_entry_id": expense_entry_id,
        },
    )


def delete_vehicle_debt(record_id: int) -> None:
    _delete_local("vehicle_debts", record_id)


def cost_summary(identifier: str | None, date_from: str, date_to: str) -> dict[str, Any]:
    fuels = list_fuel(date_from, date_to, identifier)
    maintenances = list_maintenance(date_from, date_to, identifier)
    expenses = list_expenses(date_from, date_to, identifier)
    fuel_total = sum(float(x.get("total_value") or 0) for x in fuels)
    maintenance_total = sum(float(x.get("value") or 0) for x in maintenances)
    expense_total = sum(float(x.get("value") or 0) for x in expenses)
    liters = sum(float(x.get("liters") or 0) for x in fuels)
    distance = 0.0
    identifiers: list[str]
    if identifier:
        identifiers = [identifier]
    else:
        identifiers = sorted({str(row["rastreavel"]) for row in get_all_positions(date_from, date_to)})
    for item in identifiers:
        distance += sum(t["distance_km"] for t in calculate_trips(item, date_from, date_to))
    total = fuel_total + maintenance_total + expense_total
    return {
        "identifier": identifier,
        "date_from": date_from,
        "date_to": date_to,
        "fuel_total": round(fuel_total, 2),
        "maintenance_total": round(maintenance_total, 2),
        "expense_total": round(expense_total, 2),
        "total": round(total, 2),
        "liters": round(liters, 2),
        "distance_km": round(distance, 2),
        "cost_per_km": round(total / distance, 2) if distance > 0 else None,
        "km_per_liter": round(distance / liters, 2) if liters > 0 else None,
        "records": {"fuel": len(fuels), "maintenance": len(maintenances), "expenses": len(expenses)},
    }



def build_daily_report(identifier: str, report_date: str) -> dict[str, Any]:
    profile = get_vehicle_profile(identifier)
    trips = calculate_trips(identifier, report_date, report_date)
    raw_stops = calculate_stops(identifier, report_date, report_date)
    operational = calculate_operational_period(
        identifier,
        report_date,
        report_date,
        trips=trips,
        stops=raw_stops,
    )
    stops = operational["stops"]
    operational_summary = operational["summary"]
    violations = usage_violations(identifier, report_date, report_date)
    tz = app_timezone()

    def local_iso(value: str | None) -> str | None:
        parsed = parse_iso(value)
        return parsed.astimezone(tz).isoformat() if parsed else None

    for trip in trips:
        trip["local_date"] = (
            parse_iso(trip["start_time"]).astimezone(tz).date().isoformat()
            if parse_iso(trip["start_time"])
            else report_date
        )
        trip["start_local_time"] = local_iso(trip.get("start_time"))
        trip["end_local_time"] = local_iso(trip.get("end_time"))

    for stop in stops:
        stop["local_date"] = (
            parse_iso(stop["start_time"]).astimezone(tz).date().isoformat()
            if parse_iso(stop["start_time"])
            else report_date
        )
        stop["start_local_time"] = local_iso(stop.get("start_time"))
        stop["end_local_time"] = local_iso(stop.get("end_time"))

    obra_stops = [
        stop
        for stop in stops
        if str((stop.get("geofence") or {}).get("fence_type", "")).upper()
        == "OBRA"
    ]
    home_stops = [
        stop
        for stop in stops
        if str((stop.get("geofence") or {}).get("fence_type", "")).upper()
        in {"CASA", "SEDE"}
    ]

    departure_home = trips[0]["start_local_time"] if trips else None

    arrival_work = obra_stops[0]["start_local_time"] if obra_stops else None
    if arrival_work is None:
        first_work_trip = next(
            (
                trip
                for trip in trips
                if str((trip.get("end_geofence") or {}).get("fence_type", "")).upper()
                == "OBRA"
            ),
            None,
        )
        arrival_work = (
            first_work_trip["end_local_time"]
            if first_work_trip
            else (trips[0]["end_local_time"] if trips else None)
        )

    departure_work = obra_stops[-1]["end_local_time"] if obra_stops else None
    if departure_work is None:
        last_departure_trip = next(
            (
                trip
                for trip in reversed(trips)
                if str((trip.get("start_geofence") or {}).get("fence_type", "")).upper()
                == "OBRA"
            ),
            None,
        )
        departure_work = (
            last_departure_trip["start_local_time"]
            if last_departure_trip
            else (trips[-1]["start_local_time"] if len(trips) > 1 else None)
        )

    arrival_home = home_stops[-1]["end_local_time"] if home_stops else None
    if arrival_home is None:
        last_home_trip = next(
            (
                trip
                for trip in reversed(trips)
                if str((trip.get("end_geofence") or {}).get("fence_type", "")).upper()
                in {"CASA", "SEDE"}
            ),
            None,
        )
        arrival_home = (
            last_home_trip["end_local_time"]
            if last_home_trip
            else (trips[-1]["end_local_time"] if len(trips) > 1 else None)
        )

    timeline: list[dict[str, Any]] = []
    for trip in trips:
        timeline.append(
            {
                "kind": "TRIP",
                "time": trip["start_time"],
                "start_time": trip["start_local_time"],
                "end_time": trip["end_local_time"],
                "duration_minutes": trip["duration_minutes"],
                "distance_km": trip["distance_km"],
                "start_latitude": trip["start_latitude"],
                "start_longitude": trip["start_longitude"],
                "end_latitude": trip["end_latitude"],
                "end_longitude": trip["end_longitude"],
                "start_geofence": trip.get("start_geofence"),
                "end_geofence": trip.get("end_geofence"),
            }
        )
    for stop in stops:
        timeline.append(
            {
                "kind": "STOP",
                "time": stop["start_time"],
                "start_time": stop["start_local_time"],
                "end_time": stop["end_local_time"],
                "duration_minutes": stop["duration_minutes"],
                "latitude": stop["latitude"],
                "longitude": stop["longitude"],
                "geofence": stop.get("geofence"),
                "ignition_on": stop.get("ignition_on"),
                "category": stop.get("category"),
                "category_label": stop.get("category_label"),
            }
        )
    timeline.sort(key=lambda item: item["time"])

    return {
        "identifier": identifier,
        "vehicle_name": profile.get("description") or identifier,
        "driver": profile.get("driver"),
        "report_date": report_date,
        "departure_home": departure_home,
        "arrival_work": arrival_work,
        "departure_work": departure_work,
        "arrival_home": arrival_home,
        "distance_km": round(sum(item["distance_km"] for item in trips), 2),
        "moving_minutes": operational_summary["moving_minutes"],
        "stopped_minutes": operational_summary["stopped_minutes"],
        "work_stopped_minutes": operational_summary["work_stopped_minutes"],
        "other_stopped_minutes": operational_summary["other_stopped_minutes"],
        "home_stopped_minutes": operational_summary["home_stopped_minutes"],
        "unclassified_minutes": operational_summary["unclassified_minutes"],
        "operational_minutes": operational_summary["operational_minutes"],
        "operational_periods": operational["periods"],
        "calculation_basis": operational_summary["calculation_basis"],
        "trips": trips,
        "stops": stops,
        "timeline": timeline,
        "violations": violations,
    }

def vehicle_insights(identifier: str, date_from: str, date_to: str) -> dict[str, Any]:
    points = get_positions(identifier, date_from, date_to)
    trips = calculate_trips(identifier, date_from, date_to)
    stops = calculate_stops(identifier, date_from, date_to)
    score = driving_score(identifier, date_from, date_to)
    costs = cost_summary(identifier, date_from, date_to)
    violations = usage_violations(identifier, date_from, date_to)
    documents = list_documents(identifier)
    latest = points[-1] if points else None
    provider_distance_values = [float(p["provider_distance"]) for p in points if p.get("provider_distance") is not None]
    odometer_estimated = None
    profile = get_vehicle_profile(identifier)
    if provider_distance_values:
        odometer_estimated = max(provider_distance_values)
    elif profile.get("initial_odometer") is not None:
        odometer_estimated = float(profile.get("initial_odometer") or 0) + sum(t["distance_km"] for t in trips)
    return {
        "profile": profile,
        "period": {"date_from": date_from, "date_to": date_to},
        "latest_saved_position": latest,
        "trip_count": len(trips),
        "stop_count": len(stops),
        "distance_km": round(sum(t["distance_km"] for t in trips), 2),
        "moving_minutes": round(sum(t["moving_minutes"] for t in trips), 1),
        "max_speed": max([t["max_speed"] for t in trips], default=0),
        "odometer_estimated": odometer_estimated,
        "driving_score": score,
        "costs": costs,
        "violations_count": len(violations),
        "documents_due": [d for d in documents if d.get("calculated_status") in {"VENCIDO", "VENCE_EM_BREVE"}],
        "recent_trips": trips[-5:][::-1],
        "recent_stops": stops[-5:][::-1],
    }


def dashboard_summary(date_from: str, date_to: str) -> dict[str, Any]:
    positions = [
        item
        for item in get_all_positions(date_from, date_to)
        if not is_vehicle_hidden(str(item["rastreavel"]))
    ]
    identifiers = sorted({str(p["rastreavel"]) for p in positions})
    with connect() as conn:
        profiles_count = conn.execute(
            "SELECT COUNT(*) AS c FROM vehicle_profiles WHERE hidden=0"
        ).fetchone()["c"]
    vehicle_count = max(profiles_count, len(identifiers))
    total_distance = 0.0
    total_trips = 0
    total_stops = 0
    total_violations = 0
    scores = []
    for identifier in identifiers:
        trips = calculate_trips(identifier, date_from, date_to)
        total_distance += sum(t["distance_km"] for t in trips)
        total_trips += len(trips)
        total_stops += len(calculate_stops(identifier, date_from, date_to))
        total_violations += len(usage_violations(identifier, date_from, date_to))
        scores.append(driving_score(identifier, date_from, date_to)["score"])
    alerts = list_cached_alerts(date_from, date_to, limit=10000)
    costs = cost_summary(None, date_from, date_to)
    documents = list_documents()
    return {
        "vehicle_count": max(vehicle_count, len(identifiers)),
        "vehicles_with_data": len(identifiers),
        "position_count": len(positions),
        "distance_km": round(total_distance, 2),
        "trip_count": total_trips,
        "stop_count": total_stops,
        "violations_count": total_violations,
        "alerts_count": len(alerts),
        "critical_alerts": sum(1 for a in alerts if a["severity"] == "CRITICO"),
        "open_alerts": sum(1 for a in alerts if str(a.get("status", "")).upper() in {"NEW", "OPENED"}),
        "average_driving_score": round(sum(scores) / len(scores), 1) if scores else None,
        "costs": costs,
        "documents_due": sum(1 for d in documents if d.get("calculated_status") in {"VENCIDO", "VENCE_EM_BREVE"}),
    }


def seed_demo_operations() -> None:
    init_operations_db()
    if list_geofences():
        return
    create_geofence({"name": "Sede da empresa", "latitude": -27.5954, "longitude": -48.5480, "radius_m": 350, "fence_type": "SEDE", "allowed": True, "active": True, "notes": "Cerca demonstrativa"})
    create_geofence({"name": "Obra demonstrativa", "latitude": -27.6230, "longitude": -48.5870, "radius_m": 280, "fence_type": "OBRA", "allowed": True, "active": True, "notes": "Cerca demonstrativa"})
    for identifier, model, driver in [("ABC1D23", "Fiat Strada", "Motorista A"), ("DEF4G56", "Volkswagen Saveiro", "Motorista B")]:
        save_vehicle_profile(identifier, {"description": "Veículo demonstrativo", "brand": model.split()[0], "model": model, "year": 2024, "category": "VEICULO", "color": "Branco", "department": "Topografia", "driver": driver, "initial_odometer": 50000, "initial_hour_meter": 0, "notes": "Dados fictícios"})
    today = date.today()
    add_fuel({"identifier": "ABC1D23", "entry_date": (today - timedelta(days=2)).isoformat(), "fuel_type": "DIESEL_S10", "liters": 42.5, "total_value": 255.0, "price_per_liter": 6.0, "odometer": 50120, "supplier": "Posto demonstração", "driver": "Motorista A", "notes": "Registro fictício", "source": "LOCAL", "external_id": None})
    add_maintenance({"identifier": "ABC1D23", "entry_date": (today - timedelta(days=15)).isoformat(), "maintenance_type": "PROGRAMADA", "description": "Troca de óleo e filtros", "status": "CONCLUIDA", "value": 680.0, "odometer": 49800, "hour_meter": None, "supplier": "Oficina demonstração", "next_date": (today + timedelta(days=75)).isoformat(), "next_odometer": 59800, "notes": "Registro fictício", "source": "LOCAL", "external_id": None})
    add_expense({"identifier": "DEF4G56", "entry_date": (today - timedelta(days=1)).isoformat(), "expense_type": "PEDAGIO", "description": "Pedágio de demonstração", "value": 18.60, "supplier": "Concessionária", "driver": "Motorista B", "notes": "Registro fictício", "source": "LOCAL", "external_id": None})
    add_document({"identifier": "ABC1D23", "document_type": "LICENCIAMENTO", "document_number": "DEMO-2026", "issue_date": today.isoformat(), "expiry_date": (today + timedelta(days=20)).isoformat(), "value": 0, "status": "ATIVO", "notes": "Documento fictício"})
