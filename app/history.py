from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import settings
from .sqlite_connection import ClosingConnection



def get_app_timezone():
    """Retorna o fuso configurado ou UTC-03:00 como fallback no Windows."""
    try:
        return ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=-3))


def _connect() -> sqlite3.Connection:
    db_path = Path(settings.history_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_history_db() -> None:
    with _connect() as conn:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id INTEGER NOT NULL UNIQUE,
                position_time TEXT NOT NULL,
                create_date TEXT,
                rastreavel TEXT NOT NULL,
                imei TEXT,
                position_type TEXT,
                motorista TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                speed REAL,
                course REAL,
                provider_distance REAL,
                drive_hours REAL,
                altitude REAL,
                power REAL,
                battery REAL,
                real_time INTEGER,
                ignition INTEGER,
                locked INTEGER,
                satellite INTEGER,
                rssi INTEGER,
                info TEXT,
                raw_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_positions_vehicle_time
            ON positions (rastreavel, position_time);

            CREATE INDEX IF NOT EXISTS idx_positions_time
            ON positions (position_time);

            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                date_from TEXT,
                date_to TEXT,
                pages_requested INTEGER NOT NULL DEFAULT 0,
                records_received INTEGER NOT NULL DEFAULT 0,
                records_saved INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                error_message TEXT
            );
            '''
        )
        conn.commit()


def _parse_bool(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(bool(value))
    text = str(value).strip().lower()
    if text in {"true", "1", "sim", "yes", "on"}:
        return 1
    if text in {"false", "0", "nao", "não", "no", "off"}:
        return 0
    return None


def _parse_datetime(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).isoformat()


def _finite_float(value: Any, *, minimum: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or (minimum is not None and parsed < minimum):
        return None
    return parsed


def normalize_export_position(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        external_id = int(item.get("id"))
        latitude = float(item.get("latitude"))
        longitude = float(item.get("longitude"))
    except (TypeError, ValueError):
        return None
    if (
        external_id <= 0
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
        or (abs(latitude) < 1e-9 and abs(longitude) < 1e-9)
    ):
        return None

    position_time = _parse_datetime(item.get("time") or item.get("createDate"))
    if not position_time:
        return None

    rastreavel = str(item.get("rastreavel") or "").strip()
    if not rastreavel:
        rastreavel = "SEM_IDENTIFICACAO"

    return {
        "external_id": external_id,
        "position_time": position_time,
        "create_date": _parse_datetime(item.get("createDate")),
        "rastreavel": rastreavel,
        "imei": str(item.get("imei") or "") or None,
        "position_type": item.get("type"),
        "motorista": item.get("motorista"),
        "latitude": latitude,
        "longitude": longitude,
        "speed": _finite_float(item.get("speed"), minimum=0),
        "course": _finite_float(item.get("course"), minimum=0),
        "provider_distance": _finite_float(item.get("distance"), minimum=0),
        "drive_hours": _finite_float(item.get("driveHours"), minimum=0),
        "altitude": _finite_float(item.get("altitude")),
        "power": _finite_float(item.get("power"), minimum=0),
        "battery": _finite_float(item.get("battery"), minimum=0),
        "real_time": _parse_bool(item.get("realTime")),
        "ignition": _parse_bool(item.get("ignition")),
        "locked": _parse_bool(item.get("locked")),
        "satellite": item.get("satellite"),
        "rssi": item.get("rssi"),
        "info": item.get("info"),
        "raw_json": json.dumps(item, ensure_ascii=False, default=str),
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_positions(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    normalized: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        position = normalize_export_position(item)
        if position:
            normalized.append(position)

    if not normalized:
        return {"received": 0, "saved": 0}

    sql = '''
        INSERT INTO positions (
            external_id,
            position_time,
            create_date,
            rastreavel,
            imei,
            position_type,
            motorista,
            latitude,
            longitude,
            speed,
            course,
            provider_distance,
            drive_hours,
            altitude,
            power,
            battery,
            real_time,
            ignition,
            locked,
            satellite,
            rssi,
            info,
            raw_json,
            imported_at
        )
        VALUES (
            :external_id,
            :position_time,
            :create_date,
            :rastreavel,
            :imei,
            :position_type,
            :motorista,
            :latitude,
            :longitude,
            :speed,
            :course,
            :provider_distance,
            :drive_hours,
            :altitude,
            :power,
            :battery,
            :real_time,
            :ignition,
            :locked,
            :satellite,
            :rssi,
            :info,
            :raw_json,
            :imported_at
        )
        ON CONFLICT(external_id) DO UPDATE SET
            position_time = excluded.position_time,
            create_date = excluded.create_date,
            rastreavel = excluded.rastreavel,
            imei = excluded.imei,
            position_type = excluded.position_type,
            motorista = excluded.motorista,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            speed = excluded.speed,
            course = excluded.course,
            provider_distance = excluded.provider_distance,
            drive_hours = excluded.drive_hours,
            altitude = excluded.altitude,
            power = excluded.power,
            battery = excluded.battery,
            real_time = excluded.real_time,
            ignition = excluded.ignition,
            locked = excluded.locked,
            satellite = excluded.satellite,
            rssi = excluded.rssi,
            info = excluded.info,
            raw_json = excluded.raw_json,
            imported_at = excluded.imported_at
    '''

    with _connect() as conn:
        before = conn.total_changes
        conn.executemany(sql, normalized)
        conn.commit()
        saved = conn.total_changes - before

    return {"received": len(normalized), "saved": saved}


def get_max_external_id() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(external_id), 0) AS value FROM positions"
        ).fetchone()
    return int(row["value"])


def set_sync_state(key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            '''
            INSERT INTO sync_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            ''',
            (key, value, now),
        )
        conn.commit()


def get_sync_state() -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, value, updated_at FROM sync_state ORDER BY key"
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS count FROM positions"
        ).fetchone()["count"]
        vehicles = conn.execute(
            "SELECT COUNT(DISTINCT rastreavel) AS count FROM positions"
        ).fetchone()["count"]
        latest = conn.execute(
            "SELECT MAX(position_time) AS value FROM positions"
        ).fetchone()["value"]

    state = {
        row["key"]: {
            "value": row["value"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }

    return {
        "positions": total,
        "vehicles": vehicles,
        "latest_position_time": latest,
        "max_external_id": get_max_external_id(),
        "state": state,
    }


def start_import_log(date_from: str, date_to: str) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            '''
            INSERT INTO import_log (started_at, date_from, date_to)
            VALUES (?, ?, ?)
            ''',
            (
                datetime.now(timezone.utc).isoformat(),
                date_from,
                date_to,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def finish_import_log(
    import_id: int,
    *,
    pages_requested: int,
    records_received: int,
    records_saved: int,
    completed: bool,
    error_message: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            UPDATE import_log
            SET
                finished_at = ?,
                pages_requested = ?,
                records_received = ?,
                records_saved = ?,
                completed = ?,
                error_message = ?
            WHERE id = ?
            ''',
            (
                datetime.now(timezone.utc).isoformat(),
                pages_requested,
                records_received,
                records_saved,
                int(completed),
                error_message,
                import_id,
            ),
        )
        conn.commit()


def list_vehicles() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            '''
            SELECT
                rastreavel,
                COUNT(*) AS position_count,
                MIN(position_time) AS first_position,
                MAX(position_time) AS last_position
            FROM positions
            GROUP BY rastreavel
            ORDER BY rastreavel
            '''
        ).fetchall()
    return [dict(row) for row in rows]


def _haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    radius_km = 6371.0088

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def filter_position_points(
    points: Iterable[dict[str, Any]],
    *,
    maximum_segment_speed_kmh: float = 160.0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Descarta coordenadas inválidas, duplicadas e saltos GPS impossíveis."""
    cleaned: list[dict[str, Any]] = []
    stats = {"invalid": 0, "duplicates": 0, "outliers": 0}
    for raw in points:
        point = dict(raw)
        latitude = _finite_float(point.get("latitude"))
        longitude = _finite_float(point.get("longitude"))
        if (
            latitude is None
            or longitude is None
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
            or (abs(latitude) < 1e-9 and abs(longitude) < 1e-9)
        ):
            stats["invalid"] += 1
            continue
        try:
            current_time = datetime.fromisoformat(str(point["position_time"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            stats["invalid"] += 1
            continue
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        point["latitude"] = latitude
        point["longitude"] = longitude
        point["speed"] = _finite_float(point.get("speed"), minimum=0) or 0.0
        point["_parsed_time"] = current_time.astimezone(timezone.utc)
        if point["speed"] > 240:
            stats["outliers"] += 1
            continue

        if cleaned:
            previous = cleaned[-1]

            delta_seconds = (
                point["_parsed_time"] - previous["_parsed_time"]
            ).total_seconds()

            distance_km = _haversine_km(
                previous["latitude"],
                previous["longitude"],
                latitude,
                longitude,
            )

            if delta_seconds < 0:
                stats["invalid"] += 1
                continue

            previous_speed = (
                _finite_float(previous.get("speed"), minimum=0) or 0.0
            )

            current_ignition = bool(point.get("ignition"))
            previous_ignition = bool(previous.get("ignition"))

            if (
                delta_seconds <= 120
                and distance_km <= 0.03
                and point["speed"] <= 3
                and previous_speed <= 3
                and current_ignition == previous_ignition
            ):
                stats["duplicates"] += 1
                continue

            if (
                delta_seconds <= 2
                and distance_km <= 0.003
                and current_ignition == previous_ignition
            ):
                stats["duplicates"] += 1
                continue

            calculated_speed = (
                distance_km / (delta_seconds / 3600)
                if delta_seconds > 0
                else (float("inf") if distance_km > 0.03 else 0.0)
            )

            if (
                distance_km > 0.05
                and calculated_speed > maximum_segment_speed_kmh
            ):
                stats["outliers"] += 1
                continue

            point["segment_distance_m"] = round(distance_km * 1000, 1)
            point["segment_speed_kmh"] = round(calculated_speed, 1)

        else:
            point["segment_distance_m"] = 0.0
            point["segment_speed_kmh"] = 0.0

        cleaned.append(point)

    for point in cleaned:
        point.pop("_parsed_time", None)

    return cleaned, stats



def downsample_route_points(
    points: list[dict[str, Any]], max_points: int
) -> list[dict[str, Any]]:
    if len(points) <= max_points:
        return list(points)
    important = {0, len(points) - 1}
    for index in range(1, len(points)):
        previous = points[index - 1]
        current = points[index]
        previous_moving = float(previous.get("speed") or 0) > 2
        current_moving = float(current.get("speed") or 0) > 2
        if previous_moving != current_moving or previous.get("ignition") != current.get("ignition"):
            important.update({index - 1, index})
        try:
            gap = (
                datetime.fromisoformat(str(current["position_time"]))
                - datetime.fromisoformat(str(previous["position_time"]))
            ).total_seconds()
            if gap > 300:
                important.update({index - 1, index})
        except ValueError:
            pass
    remaining = max(0, max_points - len(important))
    if remaining:
        step = max(1, math.ceil(len(points) / remaining))
        important.update(range(0, len(points), step))
    selected = sorted(important)
    if len(selected) > max_points:
        # Mantém início/fim e transições, reduzindo apenas o excesso intermediário.
        stride = math.ceil((len(selected) - 2) / max(1, max_points - 2))
        selected = [selected[0], *selected[1:-1:stride], selected[-1]][:max_points]
        if selected[-1] != len(points) - 1:
            selected[-1] = len(points) - 1
    return [points[index] for index in selected]


def _local_iso_to_utc(date_value: str, *, end_of_day: bool) -> str:
    tz = get_app_timezone()
    hour = 23 if end_of_day else 0
    minute = 59 if end_of_day else 0
    second = 59 if end_of_day else 0
    microsecond = 999999 if end_of_day else 0

    local_dt = datetime.fromisoformat(date_value).replace(
        hour=hour,
        minute=minute,
        second=second,
        microsecond=microsecond,
        tzinfo=tz,
    )
    return local_dt.astimezone(timezone.utc).isoformat()


def local_date_bounds_utc(date_from: str, date_to: str) -> tuple[str, str]:
    tz = get_app_timezone()
    start_date = datetime.fromisoformat(date_from).date()
    end_date = datetime.fromisoformat(date_to).date() + timedelta(days=1)
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=tz)
    end_exclusive = datetime.combine(end_date, datetime.min.time(), tzinfo=tz)
    return (
        start.astimezone(timezone.utc).isoformat(),
        end_exclusive.astimezone(timezone.utc).isoformat(),
    )


def provider_date_range(date_from: str, date_to: str) -> tuple[str, str]:
    start = _local_iso_to_utc(date_from, end_of_day=False)
    end = _local_iso_to_utc(date_to, end_of_day=True)

    def provider_format(value: str) -> str:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")

    return provider_format(start), provider_format(end)


def validate_date_range(date_from: str, date_to: str) -> None:
    try:
        start = datetime.fromisoformat(date_from).date()
        end = datetime.fromisoformat(date_to).date()
    except ValueError as exc:
        raise ValueError("Datas inválidas. Use o formato AAAA-MM-DD.") from exc

    if end < start:
        raise ValueError("A data final não pode ser anterior à data inicial.")

    days = (end - start).days + 1
    if days > settings.history_max_range_days:
        raise ValueError(
            f"Importe no máximo {settings.history_max_range_days} dias por vez."
        )


def query_history(
    identifier: str,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    validate_date_range(date_from, date_to)

    start_utc, end_utc = local_date_bounds_utc(date_from, date_to)

    with _connect() as conn:
        rows = conn.execute(
            '''
            SELECT
                external_id,
                position_time,
                create_date,
                rastreavel,
                imei,
                position_type,
                motorista,
                latitude,
                longitude,
                speed,
                course,
                provider_distance,
                drive_hours,
                altitude,
                power,
                battery,
                real_time,
                ignition,
                locked,
                satellite,
                rssi,
                info
            FROM positions
            WHERE UPPER(rastreavel) = UPPER(?)
              AND position_time >= ?
              AND position_time < ?
            ORDER BY position_time, external_id
            ''',
            (identifier, start_utc, end_utc),
        ).fetchall()

    raw_points = [dict(row) for row in rows]
    points, quality = filter_position_points(raw_points)

    if not points:
        return {
            "identifier": identifier,
            "date_from": date_from,
            "date_to": date_to,
            "total_points": 0,
            "displayed_points": 0,
            "raw_points": len(raw_points),
            "discarded_points": sum(quality.values()),
            "quality": quality,
            "summary": {
                "distance_km": 0,
                "duration_minutes": 0,
                "moving_minutes": 0,
                "stopped_minutes": 0,
                "unknown_minutes": 0,
                "stops": 0,
                "max_speed": 0,
                "average_moving_speed": 0,
                "first_time": None,
                "last_time": None,
            },
            "points": [],
        }

    distance_km = 0.0
    moving_seconds = 0.0
    stopped_seconds = 0.0
    unknown_seconds = 0.0
    stop_count = 0
    current_stop_seconds = 0.0
    speeds_moving: list[float] = []

    parsed_times: list[datetime] = []
    for point in points:
        parsed_times.append(datetime.fromisoformat(point["position_time"]))

    for index in range(1, len(points)):
        previous = points[index - 1]
        current = points[index]

        segment_km = _haversine_km(
            float(previous["latitude"]),
            float(previous["longitude"]),
            float(current["latitude"]),
            float(current["longitude"]),
        )

        delta_seconds = max(
            0.0,
            (parsed_times[index] - parsed_times[index - 1]).total_seconds(),
        )

        speed = float(current["speed"] or 0)
        calculated_speed = segment_km / (delta_seconds / 3600) if delta_seconds else 0.0
        moving = speed > 2 or (segment_km >= 0.03 and calculated_speed >= 3)

        if delta_seconds > 900:
            unknown_seconds += delta_seconds
            current_stop_seconds = 0.0
            continue
        if moving or segment_km > 0.015:
            distance_km += segment_km

        if moving:
            moving_seconds += delta_seconds
            speeds_moving.append(speed)

            if current_stop_seconds >= 300:
                stop_count += 1
            current_stop_seconds = 0.0
        else:
            stopped_seconds += delta_seconds
            current_stop_seconds += delta_seconds

    if current_stop_seconds >= 300:
        stop_count += 1

    duration_seconds = max(
        0.0,
        (parsed_times[-1] - parsed_times[0]).total_seconds(),
    )

    max_speed = max(float(point["speed"] or 0) for point in points)
    average_moving_speed = (
        sum(speeds_moving) / len(speeds_moving)
        if speeds_moving
        else 0.0
    )

    max_points = max(100, settings.history_max_map_points)
    displayed = downsample_route_points(points, max_points)

    local_tz = get_app_timezone()

    for point in displayed:
        utc_dt = datetime.fromisoformat(point["position_time"])
        point["local_time"] = utc_dt.astimezone(local_tz).isoformat()
        point["ignition"] = (
            None if point["ignition"] is None else bool(point["ignition"])
        )
        point["locked"] = (
            None if point["locked"] is None else bool(point["locked"])
        )
        point["real_time"] = (
            None if point["real_time"] is None else bool(point["real_time"])
        )

    return {
        "identifier": identifier,
        "date_from": date_from,
        "date_to": date_to,
        "total_points": len(points),
        "displayed_points": len(displayed),
        "raw_points": len(raw_points),
        "discarded_points": sum(quality.values()),
        "quality": quality,
        "summary": {
            "distance_km": round(distance_km, 2),
            "duration_minutes": round(duration_seconds / 60, 1),
            "moving_minutes": round(moving_seconds / 60, 1),
            "stopped_minutes": round(stopped_seconds / 60, 1),
            "unknown_minutes": round(unknown_seconds / 60, 1),
            "stops": stop_count,
            "max_speed": round(max_speed, 1),
            "average_moving_speed": round(average_moving_speed, 1),
            "first_time": points[0]["position_time"],
            "last_time": points[-1]["position_time"],
        },
        "points": displayed,
    }


def seed_demo_history() -> None:
    if get_max_external_id() > 0:
        return

    tz = get_app_timezone()
    base_local = datetime.now(tz).replace(
        hour=7,
        minute=30,
        second=0,
        microsecond=0,
    )

    demo: list[dict[str, Any]] = []
    routes = {
        "ABC1D23": [
            (-27.5954, -48.5480),
            (-27.5975, -48.5520),
            (-27.6000, -48.5575),
            (-27.6040, -48.5620),
            (-27.6080, -48.5660),
            (-27.6115, -48.5705),
            (-27.6150, -48.5750),
            (-27.6180, -48.5790),
            (-27.6205, -48.5830),
            (-27.6230, -48.5870),
        ],
        "DEF4G56": [
            (-27.6466, -48.6704),
            (-27.6440, -48.6660),
            (-27.6400, -48.6620),
            (-27.6360, -48.6580),
            (-27.6320, -48.6540),
            (-27.6280, -48.6500),
            (-27.6240, -48.6460),
            (-27.6200, -48.6420),
        ],
    }

    external_id = 900000

    for vehicle_index, (vehicle, route) in enumerate(routes.items()):
        for point_index, (lat, lon) in enumerate(route):
            current = base_local + timedelta(
                days=-1,
                minutes=(vehicle_index * 15) + point_index * 12,
            )
            speed = 0 if point_index in {0, len(route) - 1, 4} else 38 + point_index
            demo.append(
                {
                    "id": external_id,
                    "time": current.astimezone(timezone.utc).isoformat(),
                    "createDate": current.astimezone(timezone.utc).isoformat(),
                    "type": "DEMO",
                    "imei": 100000000000000 + vehicle_index,
                    "motorista": "Motorista de demonstração",
                    "rastreavel": vehicle,
                    "latitude": lat,
                    "longitude": lon,
                    "speed": speed,
                    "course": 120,
                    "distance": point_index * 1000,
                    "driveHours": point_index * 600,
                    "power": 13.7,
                    "battery": 4.1,
                    "satellite": 12,
                    "rssi": 80,
                    "realTime": True,
                    "ignition": speed > 0,
                    "locked": False,
                    "info": "Dado fictício para validação da tela.",
                }
            )
            external_id += 1

    upsert_positions(demo)
    set_sync_state("demo_seeded", "true")
