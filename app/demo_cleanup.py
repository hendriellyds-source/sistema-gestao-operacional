from __future__ import annotations

import re
import unicodedata
from typing import Any


DEMO_VEHICLE_IDENTIFIERS = frozenset({"ABC1D23", "DEF4G56", "GHI7J89"})
DEMO_GEOFENCE_NAMES = frozenset(
    {"SEDE DA EMPRESA", "OBRA DEMONSTRATIVA", "SEDE GESTAO_OPERACIONAL", "OFICINA PARCEIRA"}
)


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", text).strip().upper()


def is_demo_identifier(value: Any) -> bool:
    return normalized_text(value).replace("-", "") in DEMO_VEHICLE_IDENTIFIERS


def has_demo_marker(value: Any) -> bool:
    text = normalized_text(value)
    return any(
        marker in text
        for marker in (
            "DEMONSTRACAO",
            "DEMONSTRATIVO",
            "DEMONSTRATIVA",
            "REGISTRO FICTICIO",
            "DADO FICTICIO",
            "VEICULO TESTE",
            "VEICULO DE TESTE",
            "MODELO TESTE",
            "AIT-DEMO",
            "DEMO-2026",
        )
    )


def is_demo_vehicle_record(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    for key in ("identifier", "identificador", "plate", "rastreavel", "vehicle_id"):
        if is_demo_identifier(item.get(key)):
            return True
    if normalized_text(item.get("position_type") or item.get("type")) == "DEMO":
        return True
    return any(
        has_demo_marker(item.get(key))
        for key in (
            "source",
            "notes",
            "description",
            "descricao",
            "title",
            "document_number",
            "aitNumber",
            "info",
        )
    )


def cleanup_system_state_data(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    cleaned = dict(data)
    stats: dict[str, int] = {}

    vehicles = list(cleaned.get("vehicles") or [])
    removed_vehicle_ids = {
        int(item.get("id"))
        for item in vehicles
        if isinstance(item, dict)
        and is_demo_vehicle_record(item)
        and str(item.get("id") or "").isdigit()
    }

    def filter_key(key: str, predicate) -> None:
        values = cleaned.get(key)
        if not isinstance(values, list):
            return
        kept = [item for item in values if not predicate(item)]
        if len(kept) != len(values):
            cleaned[key] = kept
            stats[key] = len(values) - len(kept)

    def belongs_to_removed_vehicle(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        value = item.get("vehicleId") or item.get("vehicle_id")
        try:
            return int(value) in removed_vehicle_ids
        except (TypeError, ValueError):
            return False

    filter_key("vehicles", is_demo_vehicle_record)
    for key in ("maintenances", "maintenanceHistory", "maintenanceDrafts", "fines"):
        filter_key(
            key,
            lambda item: belongs_to_removed_vehicle(item)
            or is_demo_vehicle_record(item),
        )
    for key in (
        "fleetTrips",
        "fleetVehicles",
        "fleetStops",
        "fleetDrivingEvents",
        "fleetFuel",
        "fleetExpenses",
        "fleetDocuments",
        "fleetDebts",
        "fleetHiddenVehicles",
        "events",
    ):
        filter_key(
            key,
            lambda item: belongs_to_removed_vehicle(item)
            or is_demo_vehicle_record(item),
        )
    filter_key(
        "fleetGeofences",
        lambda item: isinstance(item, dict)
        and (
            normalized_text(item.get("name")) in DEMO_GEOFENCE_NAMES
            or has_demo_marker(item.get("notes"))
        ),
    )
    return cleaned, stats
