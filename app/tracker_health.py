from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .settings import settings
from .sqlite_connection import ClosingConnection
from .demo_cleanup import DEMO_VEHICLE_IDENTIFIERS


def _connect() -> sqlite3.Connection:
    db_path = Path(settings.tracker_health_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_tracker_health_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tracker_health_state (
                identifier TEXT PRIMARY KEY,
                first_observed_at TEXT NOT NULL,
                last_observed_at TEXT NOT NULL,
                provider_status TEXT,
                effective_status TEXT NOT NULL,
                health_level TEXT NOT NULL,
                health_score INTEGER NOT NULL,
                position_id TEXT,
                position_time TEXT,
                position_update_time TEXT,
                communication_time TEXT,
                latitude REAL,
                longitude REAL,
                speed REAL,
                ignition INTEGER,
                satellite INTEGER,
                rssi INTEGER,
                last_coordinate_change_at TEXT,
                last_new_position_at TEXT,
                same_position_checks INTEGER NOT NULL DEFAULT 0,
                warning_codes TEXT NOT NULL DEFAULT '[]',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                recommended_action TEXT,
                raw_summary_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS tracker_health_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                provider_status TEXT,
                effective_status TEXT NOT NULL,
                health_level TEXT NOT NULL,
                position_id TEXT,
                position_time TEXT,
                position_update_time TEXT,
                communication_time TEXT,
                latitude REAL,
                longitude REAL,
                speed REAL,
                ignition INTEGER,
                satellite INTEGER,
                rssi INTEGER,
                position_changed INTEGER NOT NULL,
                distance_from_previous_m REAL,
                communication_age_minutes REAL,
                position_age_minutes REAL,
                same_position_minutes REAL,
                warning_codes TEXT NOT NULL DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_tracker_observation_vehicle_time
            ON tracker_health_observations(identifier, observed_at);
            """
        )
        if not settings.demo_mode:
            placeholders = ",".join("?" for _ in DEMO_VEHICLE_IDENTIFIERS)
            identifiers = tuple(DEMO_VEHICLE_IDENTIFIERS)
            conn.execute(
                f"DELETE FROM tracker_health_state WHERE UPPER(REPLACE(identifier, '-', '')) IN ({placeholders})",
                identifiers,
            )
            conn.execute(
                f"DELETE FROM tracker_health_observations WHERE UPPER(REPLACE(identifier, '-', '')) IN ({placeholders})",
                identifiers,
            )
        conn.commit()


def _parse_datetime(value: Any) -> datetime | None:
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
    return dt.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_int(value: Any) -> int | None:
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


def _distance_m(
    lat1: float | None,
    lon1: float | None,
    lat2: float | None,
    lon2: float | None,
) -> float | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    radius = 6371008.8
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def _minutes_since(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    return round(max(0.0, (now - value).total_seconds() / 60), 1)


def _warning(
    code: str,
    severity: str,
    message: str,
    action: str,
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "action": action,
    }


def _recommended_action(warnings: list[dict[str, str]]) -> str:
    if not warnings:
        return "Nenhuma ação necessária. Continue acompanhando as atualizações."
    priorities = {"CRITICO": 3, "ATENCAO": 2, "INFO": 1}
    most_severe = max(
        warnings,
        key=lambda item: priorities.get(item.get("severity", "INFO"), 0),
    )
    return most_severe["action"]


def assess_live_position(live: dict[str, Any]) -> dict[str, Any]:
    """
    Compara a resposta atual da Selsyn com as observações anteriores.

    O status exibido pelo sistema deixa de confiar apenas em MOVING_ON.
    Movimento só é confirmado quando uma nova posição ou deslocamento real
    também é observado.
    """
    init_tracker_health_db()

    now = datetime.now(timezone.utc)
    identifier = str(live.get("identifier") or "SEM_IDENTIFICACAO").strip()
    provider_status = str(
        live.get("provider_status") or live.get("status") or "UNKNOWN"
    ).upper()

    lat = _float(live.get("latitude"))
    lon = _float(live.get("longitude"))
    speed = _float(live.get("speed")) or 0.0
    ignition = _bool_int(live.get("ignition"))
    satellite = _int(live.get("satellite"))
    rssi = _int(live.get("rssi"))
    position_id = (
        None
        if live.get("position_id") in (None, "")
        else str(live.get("position_id"))
    )

    communication_dt = _parse_datetime(
        live.get("communication_time") or live.get("last_communication")
    )
    position_dt = _parse_datetime(
        live.get("position_time") or live.get("position_update_time")
    )
    position_update_dt = _parse_datetime(live.get("position_update_time"))

    communication_age = _minutes_since(communication_dt, now)
    position_age = _minutes_since(position_dt, now)
    provider_moving = provider_status.startswith("MOVING")
    provider_offline = bool(live.get("offline")) or provider_status == "OFF_LINE"

    with _connect() as conn:
        previous = conn.execute(
            """
            SELECT *
            FROM tracker_health_state
            WHERE UPPER(identifier) = UPPER(?)
            """,
            (identifier,),
        ).fetchone()

        previous_dict = dict(previous) if previous else {}
        previous_lat = _float(previous_dict.get("latitude"))
        previous_lon = _float(previous_dict.get("longitude"))
        previous_position_id = previous_dict.get("position_id")
        previous_position_time = previous_dict.get("position_time")
        distance_from_previous = _distance_m(
            previous_lat,
            previous_lon,
            lat,
            lon,
        )

        token_changed = False
        if position_id and previous_position_id:
            token_changed = position_id != str(previous_position_id)
        elif position_dt and previous_position_time:
            token_changed = _iso(position_dt) != previous_position_time

        coordinate_changed = bool(
            distance_from_previous is not None
            and distance_from_previous
            >= settings.tracker_movement_distance_threshold_m
        )
        position_record_changed = bool(token_changed)
        position_changed = coordinate_changed

        first_observed_at = (
            previous_dict.get("first_observed_at") or now.isoformat()
        )

        if not previous:
            last_coordinate_change_at = now
            last_new_position_at = position_dt or now
            same_checks = 0
        elif coordinate_changed:
            last_coordinate_change_at = now
            last_new_position_at = position_dt or now
            same_checks = 0
        else:
            last_coordinate_change_at = (
                _parse_datetime(previous_dict.get("last_coordinate_change_at"))
                or now
            )
            if position_record_changed:
                last_new_position_at = position_dt or now
            else:
                last_new_position_at = (
                    _parse_datetime(previous_dict.get("last_new_position_at"))
                    or position_dt
                    or now
                )
            same_checks = int(previous_dict.get("same_position_checks") or 0) + 1

        same_position_minutes = round(
            max(
                0.0,
                (now - last_coordinate_change_at).total_seconds() / 60,
            ),
            1,
        )

        warnings: list[dict[str, str]] = []

        valid_coordinates = (
            lat is not None
            and lon is not None
            and -90 <= lat <= 90
            and -180 <= lon <= 180
            and not (abs(lat) < 0.000001 and abs(lon) < 0.000001)
        )

        if provider_offline:
            warnings.append(
                _warning(
                    "OFFLINE_INFORMADO",
                    "CRITICO",
                    "A Selsyn informou que o rastreador está offline.",
                    "Verifique alimentação, chip, cobertura e instalação do rastreador.",
                )
            )
        elif (
            communication_age is not None
            and communication_age
            > settings.tracker_communication_stale_minutes
        ):
            warnings.append(
                _warning(
                    "SEM_COMUNICACAO_RECENTE",
                    "CRITICO",
                    (
                        "A última comunicação tem "
                        f"{communication_age:.0f} minutos."
                    ),
                    "Verifique alimentação, chip, operadora e cobertura GPRS.",
                )
            )

        if not valid_coordinates:
            warnings.append(
                _warning(
                    "SEM_COORDENADA_VALIDA",
                    "CRITICO",
                    "A resposta não contém coordenadas GPS válidas.",
                    "Solicite teste da antena GPS e da instalação do equipamento.",
                )
            )
        elif (
            position_age is not None
            and position_age > settings.tracker_position_stale_minutes
        ):
            if (
                communication_age is not None
                and communication_age
                <= settings.tracker_communication_stale_minutes
            ):
                warnings.append(
                    _warning(
                        "COMUNICA_MAS_NAO_ATUALIZA_GPS",
                        "CRITICO",
                        (
                            "O equipamento comunica com a plataforma, mas a "
                            f"posição GPS tem {position_age:.0f} minutos."
                        ),
                        "Verifique antena GPS, satélites, firmware e intervalo de transmissão.",
                    )
                )
            else:
                warnings.append(
                    _warning(
                        "POSICAO_DESATUALIZADA",
                        "CRITICO",
                        (
                            "A última posição GPS tem "
                            f"{position_age:.0f} minutos."
                        ),
                        "Verifique comunicação e obtenção de posição GPS no equipamento.",
                    )
                )

        if satellite is not None and satellite < settings.tracker_minimum_satellites:
            warnings.append(
                _warning(
                    "SINAL_GPS_FRACO",
                    "ATENCAO",
                    f"O rastreador informa apenas {satellite} satélite(s).",
                    "Revise o local de instalação e a antena GPS.",
                )
            )

        movement_claimed = (
            provider_moving
            or speed >= settings.tracker_movement_speed_threshold_kmh
        )

        severe_codes = {
            item["code"]
            for item in warnings
            if item["severity"] == "CRITICO"
        }

        if (
            movement_claimed
            and valid_coordinates
            and not severe_codes
            and not position_changed
            and same_checks >= 2
            and same_position_minutes >= settings.tracker_gps_freeze_minutes
        ):
            warnings.append(
                _warning(
                    "GPS_CONGELADO_EM_MOVIMENTO",
                    "CRITICO",
                    (
                        f"A Selsyn informa {provider_status}, mas a coordenada "
                        f"não mudou por {same_position_minutes:.0f} minutos."
                    ),
                    "Peça ao fornecedor para verificar IMEI, fix GPS, antena, firmware e configuração de envio.",
                )
            )
        elif (
            movement_claimed
            and valid_coordinates
            and not severe_codes
            and not coordinate_changed
        ):
            warnings.append(
                _warning(
                    "MOVIMENTO_NAO_CONFIRMADO",
                    "ATENCAO",
                    (
                        f"A Selsyn informa {provider_status} e velocidade de "
                        f"{speed:.0f} km/h, porém a coordenada ainda não mudou "
                        "o suficiente para confirmar deslocamento real."
                    ),
                    "Aguarde novas posições; se persistir, revise GPS, ignição e sensor de movimento.",
                )
            )

        critical = any(item["severity"] == "CRITICO" for item in warnings)
        attention = any(item["severity"] == "ATENCAO" for item in warnings)

        if provider_offline or "SEM_COMUNICACAO_RECENTE" in severe_codes:
            effective_status = "SEM COMUNICAÇÃO"
        elif "SEM_COORDENADA_VALIDA" in severe_codes:
            effective_status = "SEM POSIÇÃO GPS"
        elif (
            "COMUNICA_MAS_NAO_ATUALIZA_GPS"
            in {item["code"] for item in warnings}
        ):
            effective_status = "COMUNICAÇÃO ATIVA / GPS DESATUALIZADO"
        elif "POSICAO_DESATUALIZADA" in severe_codes:
            effective_status = "POSIÇÃO DESATUALIZADA"
        elif (
            "GPS_CONGELADO_EM_MOVIMENTO"
            in {item["code"] for item in warnings}
        ):
            effective_status = "GPS CONGELADO"
        elif movement_claimed and position_changed and valid_coordinates:
            effective_status = "EM MOVIMENTO CONFIRMADO"
        elif provider_moving:
            effective_status = "MOVIMENTO NÃO CONFIRMADO"
        elif ignition == 1:
            effective_status = "PARADO / IGNIÇÃO LIGADA"
        elif ignition == 0:
            effective_status = "PARADO / IGNIÇÃO DESLIGADA"
        else:
            effective_status = "PARADO / IGNIÇÃO NÃO INFORMADA"

        if critical:
            health_level = "CRITICO"
            health_score = 25
        elif attention:
            health_level = "ATENCAO"
            health_score = 65
        else:
            health_level = "SAUDAVEL"
            health_score = 100

        warning_codes = [item["code"] for item in warnings]
        recommended_action = _recommended_action(warnings)

        observation_payload = {
            "identifier": identifier,
            "provider_status": provider_status,
            "effective_status": effective_status,
            "health_level": health_level,
            "health_score": health_score,
            "position_id": position_id,
            "position_time": _iso(position_dt),
            "position_update_time": _iso(position_update_dt),
            "communication_time": _iso(communication_dt),
            "latitude": lat,
            "longitude": lon,
            "speed": speed,
            "ignition": ignition,
            "satellite": satellite,
            "rssi": rssi,
            "last_coordinate_change_at": _iso(last_coordinate_change_at),
            "last_new_position_at": _iso(last_new_position_at),
            "same_position_checks": same_checks,
            "warning_codes": json.dumps(warning_codes, ensure_ascii=False),
            "warnings_json": json.dumps(warnings, ensure_ascii=False),
            "recommended_action": recommended_action,
            "raw_summary_json": json.dumps(
                {
                    "tempo_ultima_posicao": live.get("tempo_ultima_posicao"),
                    "real_time": live.get("real_time"),
                    "model": live.get("model"),
                    "protocol": live.get("protocol"),
                },
                ensure_ascii=False,
                default=str,
            ),
        }

        conn.execute(
            """
            INSERT INTO tracker_health_state (
                identifier, first_observed_at, last_observed_at,
                provider_status, effective_status, health_level,
                health_score, position_id, position_time,
                position_update_time, communication_time, latitude,
                longitude, speed, ignition, satellite, rssi,
                last_coordinate_change_at, last_new_position_at,
                same_position_checks, warning_codes, warnings_json,
                recommended_action, raw_summary_json
            )
            VALUES (
                :identifier, :first_observed_at, :last_observed_at,
                :provider_status, :effective_status, :health_level,
                :health_score, :position_id, :position_time,
                :position_update_time, :communication_time, :latitude,
                :longitude, :speed, :ignition, :satellite, :rssi,
                :last_coordinate_change_at, :last_new_position_at,
                :same_position_checks, :warning_codes, :warnings_json,
                :recommended_action, :raw_summary_json
            )
            ON CONFLICT(identifier) DO UPDATE SET
                last_observed_at = excluded.last_observed_at,
                provider_status = excluded.provider_status,
                effective_status = excluded.effective_status,
                health_level = excluded.health_level,
                health_score = excluded.health_score,
                position_id = excluded.position_id,
                position_time = excluded.position_time,
                position_update_time = excluded.position_update_time,
                communication_time = excluded.communication_time,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                speed = excluded.speed,
                ignition = excluded.ignition,
                satellite = excluded.satellite,
                rssi = excluded.rssi,
                last_coordinate_change_at = excluded.last_coordinate_change_at,
                last_new_position_at = excluded.last_new_position_at,
                same_position_checks = excluded.same_position_checks,
                warning_codes = excluded.warning_codes,
                warnings_json = excluded.warnings_json,
                recommended_action = excluded.recommended_action,
                raw_summary_json = excluded.raw_summary_json
            """,
            {
                **observation_payload,
                "first_observed_at": first_observed_at,
                "last_observed_at": now.isoformat(),
            },
        )

        last_observation = conn.execute(
            """
            SELECT observed_at
            FROM tracker_health_observations
            WHERE UPPER(identifier) = UPPER(?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (identifier,),
        ).fetchone()

        should_insert = True
        if last_observation:
            last_observed_dt = _parse_datetime(last_observation["observed_at"])
            if last_observed_dt:
                seconds = (now - last_observed_dt).total_seconds()
                should_insert = (
                    seconds >= settings.tracker_observation_min_interval_seconds
                    or position_record_changed
                    or coordinate_changed
                    or critical
                )

        if should_insert:
            conn.execute(
                """
                INSERT INTO tracker_health_observations (
                    identifier, observed_at, provider_status,
                    effective_status, health_level, position_id,
                    position_time, position_update_time,
                    communication_time, latitude, longitude, speed,
                    ignition, satellite, rssi, position_changed,
                    distance_from_previous_m,
                    communication_age_minutes, position_age_minutes,
                    same_position_minutes, warning_codes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    now.isoformat(),
                    provider_status,
                    effective_status,
                    health_level,
                    position_id,
                    _iso(position_dt),
                    _iso(position_update_dt),
                    _iso(communication_dt),
                    lat,
                    lon,
                    speed,
                    ignition,
                    satellite,
                    rssi,
                    int(position_changed),
                    distance_from_previous,
                    communication_age,
                    position_age,
                    same_position_minutes,
                    json.dumps(warning_codes, ensure_ascii=False),
                ),
            )

        cutoff = (now - timedelta(days=settings.tracker_observation_retention_days)).isoformat()
        conn.execute(
            """
            DELETE FROM tracker_health_observations
            WHERE observed_at < ?
            """,
            (cutoff,),
        )
        conn.commit()

    result = dict(live)
    result.update(
        {
            "provider_status": provider_status,
            "status": effective_status,
            "effective_status": effective_status,
            "health_level": health_level,
            "health_score": health_score,
            "warnings": warnings,
            "warning_codes": warning_codes,
            "recommended_action": recommended_action,
            "position_changed": coordinate_changed,
            "position_record_changed": position_record_changed,
            "distance_from_previous_m": (
                round(distance_from_previous, 1)
                if distance_from_previous is not None
                else None
            ),
            "same_position_checks": same_checks,
            "same_position_minutes": same_position_minutes,
            "communication_age_minutes": communication_age,
            "position_age_minutes": position_age,
            "last_coordinate_change_at": _iso(last_coordinate_change_at),
            "last_new_position_at": _iso(last_new_position_at),
            "diagnostic_generated_at": now.isoformat(),
        }
    )
    return result


def _decode_state(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("warning_codes", "warnings_json", "raw_summary_json"):
        try:
            item[key] = json.loads(item.get(key) or "[]")
        except json.JSONDecodeError:
            item[key] = []
    item["warnings"] = item.pop("warnings_json", [])
    item["raw_summary"] = item.pop("raw_summary_json", {})
    item["ignition"] = (
        None if item.get("ignition") is None else bool(item["ignition"])
    )
    return item


def list_tracker_health() -> list[dict[str, Any]]:
    init_tracker_health_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM tracker_health_state
            ORDER BY
                CASE health_level
                    WHEN 'CRITICO' THEN 1
                    WHEN 'ATENCAO' THEN 2
                    ELSE 3
                END,
                identifier
            """
        ).fetchall()
    return [_decode_state(row) for row in rows]


def get_tracker_health(identifier: str) -> dict[str, Any] | None:
    init_tracker_health_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM tracker_health_state
            WHERE UPPER(identifier) = UPPER(?)
            """,
            (identifier,),
        ).fetchone()
    return _decode_state(row) if row else None


def list_tracker_observations(
    identifier: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_tracker_health_db()
    safe_limit = max(1, min(int(limit), 1000))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM tracker_health_observations
            WHERE UPPER(identifier) = UPPER(?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (identifier, safe_limit),
        ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        try:
            item["warning_codes"] = json.loads(
                item.get("warning_codes") or "[]"
            )
        except json.JSONDecodeError:
            item["warning_codes"] = []
        item["ignition"] = (
            None if item.get("ignition") is None else bool(item["ignition"])
        )
        item["position_changed"] = bool(item.get("position_changed"))
        result.append(item)
    return result


def tracker_health_summary() -> dict[str, Any]:
    rows = list_tracker_health()
    return {
        "total": len(rows),
        "healthy": sum(item["health_level"] == "SAUDAVEL" for item in rows),
        "attention": sum(item["health_level"] == "ATENCAO" for item in rows),
        "critical": sum(item["health_level"] == "CRITICO" for item in rows),
        "moving_confirmed": sum(
            item["effective_status"] == "EM MOVIMENTO CONFIRMADO"
            for item in rows
        ),
        "gps_frozen": sum(
            "GPS_CONGELADO_EM_MOVIMENTO" in item.get("warning_codes", [])
            for item in rows
        ),
        "stale_position": sum(
            any(
                code in item.get("warning_codes", [])
                for code in (
                    "COMUNICA_MAS_NAO_ATUALIZA_GPS",
                    "POSICAO_DESATUALIZADA",
                )
            )
            for item in rows
        ),
    }
