from __future__ import annotations

import asyncio
import base64
import binascii
import csv
import io
import json
import zipfile
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .demo_data import alerts as demo_alerts
from .demo_data import live_positions as demo_live_positions
from .bulk_work_import import router as bulk_work_import_router
from .history import (
    finish_import_log,
    get_max_external_id,
    get_sync_state,
    init_history_db,
    list_vehicles as list_history_vehicles,
    provider_date_range,
    query_history,
    seed_demo_history,
    set_sync_state,
    start_import_log,
    upsert_positions,
    validate_date_range,
)
from .operations import (
    add_document,
    add_expense,
    add_fuel,
    add_maintenance,
    add_vehicle_debt,
    add_vehicle_debts_bulk,
    build_daily_report,
    calculate_operational_period,
    calculate_stops,
    calculate_trips,
    cost_summary,
    create_geofence,
    dashboard_summary,
    delete_document,
    delete_expense,
    delete_fuel,
    delete_geofence,
    delete_maintenance,
    delete_vehicle_debt,
    driving_score,
    find_geofence_for_point,
    geofence_activity,
    get_usage_rules,
    get_vehicle_profile,
    hide_vehicle,
    init_operations_db,
    is_vehicle_hidden,
    list_hidden_vehicles,
    list_cached_alerts,
    list_documents,
    list_expenses,
    list_fuel,
    list_geofences,
    list_maintenance,
    list_vehicle_debts,
    mark_vehicle_debt_paid,
    normalize_alert,
    save_usage_rules,
    restore_vehicle,
    save_vehicle_profile,
    seed_demo_operations,
    update_cached_alert_status,
    update_geofence,
    update_vehicle_debt,
    upsert_alerts,
    usage_violations,
    vehicle_insights,
)
from .geocoding import (
    get_cached_location,
    google_maps_url,
    google_street_view_url,
    init_geocoding_db,
    reverse_geocode,
    search_location,
)
from .document_storage import (
    decode_document_content,
    delete_document as delete_stored_document,
    document_path,
    init_document_storage,
    list_documents as list_stored_documents,
    purge_demo_vehicle_documents,
    save_document as save_stored_document,
)
from .employee_import import archive_employee_pdf, parse_employee_registration_pdf
from .exam_import import archive_exam_roster_pdf, parse_exam_roster_pdf
from .vacation_import import archive_vacation_roster_pdf, parse_vacation_roster_pdf
from .employee_portal import (
    add_employee_file,
    admin_state_summary,
    append_employee_message,
    authenticate_employee,
    employee_for_token,
    employee_view,
    get_employee_file,
    init_employee_portal_db,
    load_portal_state,
    logout_token,
    mark_entry_messages_read,
    sync_portal_state,
)
from .selsyn_client import SelsynClient, SelsynError
from .settings import PUBLIC_DEMO_EMAIL, PUBLIC_DEMO_PASSWORD, settings
from .profile_photos import router as profile_photos_router
from .aline_projects import (
    aline_health,
    init_aline_projects,
    router as aline_router,
)
from .aline_email import (
    aline_email_interval_seconds,
    init_aline_email,
    router as aline_email_router,
    sync_aline_email_once,
)
from .system_state import (
    filter_state_for_user,
    get_system_state,
    init_system_state,
    purge_demo_system_state,
    router as system_state_router,
    state_health,
)
from .auth import (
    COOKIE_NAME,
    init_auth,
    router as auth_router,
    user_from_session,
)
from .access_control import module_for_path, path_is_allowed
from .employee_certificates import router as employee_certificates_router
from .financial_calendar import router as financial_calendar_router
from .whatsapp_routes import router as whatsapp_router
from .cora_routes import init_cora_webhooks, router as cora_router
from .tablet_agenda import router as tablet_agenda_router

from .tracker_health import (
    assess_live_position,
    get_tracker_health,
    init_tracker_health_db,
    list_tracker_health,
    list_tracker_observations,
    tracker_health_summary,
)


templates = Jinja2Templates(directory="app/templates")
client = SelsynClient()
position_sync_lock = asyncio.Lock()
alert_sync_lock = asyncio.Lock()




class EmployeeLoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=240)


class EmployeeMessagePayload(BaseModel):
    schedule_entry_id: int
    text: str = Field(min_length=1, max_length=4000)


class EmployeeReadPayload(BaseModel):
    schedule_entry_id: int


class EmployeeFilePayload(BaseModel):
    schedule_entry_id: int
    name: str = Field(min_length=1, max_length=240)
    mime_type: str = Field(default="application/octet-stream", max_length=160)
    size: int = Field(ge=0)
    data_url: str = Field(min_length=10)


class EmployeePortalSyncPayload(BaseModel):
    data: dict[str, Any]


class EmployeePdfImportPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    data_url: str = Field(min_length=20, max_length=30_000_000)


class HistoryImportRequest(BaseModel):
    date_from: str
    date_to: str


class EventAction(BaseModel):
    comment: str = Field(default="", max_length=2000)
    status: str | None = None


class VehicleProfilePayload(BaseModel):
    description: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    category: str | None = "VEICULO"
    color: str | None = None
    department: str | None = None
    team_id: int | None = None
    driver: str | None = None
    driver_employee_id: int | None = None
    chassis: str | None = None
    renavam: str | None = None
    initial_odometer: float | None = 0
    initial_hour_meter: float | None = 0
    notes: str | None = None


class GeofencePayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float = Field(default=150, ge=20, le=50000)
    fence_type: str = Field(default="OBRA", max_length=40)
    allowed: bool = True
    active: bool = True
    address: str | None = Field(default=None, max_length=500)
    postal_code: str | None = Field(default=None, max_length=30)
    coordinate_text: str | None = Field(default=None, max_length=150)
    notes: str | None = Field(default=None, max_length=1000)


class UsageRulesPayload(BaseModel):
    work_start: str
    work_end: str
    allow_weekends: bool
    speed_limit_kmh: float = Field(ge=10, le=250)
    idle_limit_minutes: int = Field(ge=1, le=720)
    stop_minimum_minutes: int = Field(ge=1, le=180)
    trip_end_minutes: int = Field(ge=1, le=180)


class FuelPayload(BaseModel):
    identifier: str
    entry_date: str
    fuel_type: str = "DIESEL"
    liters: float = Field(ge=0)
    total_value: float = Field(ge=0)
    price_per_liter: float | None = Field(default=None, ge=0)
    odometer: float | None = Field(default=None, ge=0)
    supplier: str | None = None
    driver: str | None = None
    notes: str | None = None


class MaintenancePayload(BaseModel):
    identifier: str
    entry_date: str
    maintenance_type: str = "OUTROS"
    description: str
    status: str = "CONCLUIDA"
    value: float = Field(default=0, ge=0)
    odometer: float | None = Field(default=None, ge=0)
    hour_meter: float | None = Field(default=None, ge=0)
    supplier: str | None = None
    next_date: str | None = None
    next_odometer: float | None = Field(default=None, ge=0)
    notes: str | None = None


class ExpensePayload(BaseModel):
    identifier: str
    entry_date: str
    expense_type: str = "OUTROS"
    description: str
    value: float = Field(default=0, ge=0)
    supplier: str | None = None
    driver: str | None = None
    notes: str | None = None


class VehicleDebtPayload(BaseModel):
    identifier: str = Field(min_length=1, max_length=80)
    debt_type: str = Field(default="OUTRO", min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    reference_year: int | None = Field(default=None, ge=1900, le=2200)
    due_date: str | None = None
    amount: float = Field(default=0, ge=0)
    status: str = Field(default="PENDENTE", max_length=40)
    paid_date: str | None = None
    payment_method: str | None = Field(default=None, max_length=100)
    source: str = Field(default="MANUAL", max_length=80)
    source_document_id: int | None = None
    notes: str | None = Field(default=None, max_length=2000)


class VehicleDebtBulkPayload(BaseModel):
    items: list[VehicleDebtPayload] = Field(min_length=1, max_length=200)


class VehicleDebtPaymentPayload(BaseModel):
    paid_date: str
    payment_method: str | None = Field(default=None, max_length=100)
    register_expense: bool = True


class DocumentPayload(BaseModel):
    identifier: str
    document_type: str
    document_number: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    value: float = Field(default=0, ge=0)
    status: str = "ATIVO"
    notes: str | None = None


class StoredDocumentPayload(BaseModel):
    owner_type: Literal["vehicle", "employee"]
    owner_key: str = Field(min_length=1, max_length=160)
    owner_name: str | None = Field(default=None, max_length=240)
    category: Literal["DOCUMENTO", "EXAME", "CERTIFICADO"] = "DOCUMENTO"
    document_type: str = Field(default="DOCUMENTO", min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=240)
    document_number: str | None = Field(default=None, max_length=160)
    issue_date: str | None = None
    expiry_date: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
    filename: str = Field(min_length=1, max_length=240)
    mime_type: str | None = Field(default=None, max_length=160)
    content_base64: str = Field(min_length=1)


class VehicleDebtImageImportPayload(BaseModel):
    identifier: str = Field(min_length=1, max_length=80)
    items: list[VehicleDebtPayload] = Field(default_factory=list, max_length=200)
    document: StoredDocumentPayload | None = None
    source_document_id: int | None = None


class VehicleProfileWithDocumentPayload(BaseModel):
    profile: VehicleProfilePayload
    crlv: StoredDocumentPayload | None = None


def extract_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("content", "items", "data", "resultados", "rastreaveis", "eventos"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [data]
    return []


def nested_value(item: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = item
        found = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current[part]
        if found and current is not None:
            return current
    return None


def normalize_live_position(item: dict[str, Any]) -> dict[str, Any]:
    position = (
        item.get("ultimaPosicao")
        or item.get("position")
        or item.get("posicao")
        or item
    )
    rastreavel = (
        item.get("rastreavel")
        if isinstance(item.get("rastreavel"), dict)
        else {}
    )
    device = (
        item.get("dispositivo")
        if isinstance(item.get("dispositivo"), dict)
        else {}
    )
    identifier = (
        item.get("identificador")
        or item.get("formattedIdentificador")
        or item.get("placa")
        or rastreavel.get("identificador")
        or item.get("rastreavel")
        or "Sem identificação"
    )
    provider_status = (
        item.get("status")
        or item.get("situacao")
        or "UNKNOWN"
    )
    communication_time = (
        item.get("timeUltimaComunicacao")
        or item.get("ultimaComunicacao")
    )
    position_update_time = item.get("timeAtualizacaoPosicao")
    position_time = (
        position.get("time")
        or position.get("createDate")
        or position_update_time
    )
    return {
        "id": item.get("id") or rastreavel.get("id"),
        "vehicle_id": (
            item.get("rastreavelId")
            or rastreavel.get("id")
            or item.get("idRastreavel")
        ),
        "identifier": str(identifier),
        "description": (
            item.get("descricao")
            or item.get("description")
            or rastreavel.get("descricao")
            or ""
        ),
        "provider_status": str(provider_status),
        "status": str(provider_status),
        "offline": bool(
            item.get("offLine", item.get("offline", False))
        ),
        "last_communication": (
            communication_time
            or position_update_time
            or position_time
        ),
        "communication_time": communication_time,
        "position_update_time": position_update_time,
        "position_time": position_time,
        "position_id": position.get("id"),
        "tempo_ultima_posicao": item.get("tempoUltimaPosicao"),
        "latitude": position.get(
            "latitude",
            position.get("lat"),
        ),
        "longitude": position.get(
            "longitude",
            position.get("lng"),
        ),
        "speed": position.get(
            "speed",
            position.get("velocidade", 0),
        ),
        "course": position.get(
            "course",
            position.get("direcao"),
        ),
        "ignition": position.get(
            "ignition",
            position.get("ignicao"),
        ),
        "locked": position.get("locked"),
        "real_time": position.get("realTime"),
        "imei": (
            position.get("imei")
            or position.get("deviceId")
            or device.get("imei")
            or item.get("imei")
        ),
        "driver": (
            item.get("motorista")
            or position.get("motorista")
        ),
        "power": position.get("power"),
        "battery": position.get("battery"),
        "satellite": position.get("satellite"),
        "rssi": position.get("rssi"),
        "odometer": (
            position.get("distance")
            or position.get("odometro")
        ),
        "drive_hours": (
            position.get("driveHours")
            or position.get("horimetro")
        ),
        "protocol": position.get("protocol"),
        "model": position.get("model"),
        "raw": item,
    }


def normalize_and_assess(item: dict[str, Any]) -> dict[str, Any]:
    live = assess_live_position(normalize_live_position(item))
    if settings.demo_mode:
        live["effective_status"] = "EM MOVIMENTO SIMULADO" if float(live.get("speed") or 0) > 0 else "PARADO SIMULADO"
    profile = get_vehicle_profile(live["identifier"])
    live["profile"] = profile
    live["display_name"] = profile.get("description") or live.get("description") or live["identifier"]
    live["driver"] = profile.get("driver") or live.get("driver")
    live["brand"] = profile.get("brand")
    live["vehicle_model"] = profile.get("model")
    live["maps_url"] = None
    live["street_view_url"] = None
    latitude = live.get("latitude")
    longitude = live.get("longitude")
    if latitude is not None and longitude is not None:
        try:
            lat = float(latitude)
            lon = float(longitude)
            fence = find_geofence_for_point(lat, lon)
            live["current_geofence"] = fence
            live["location_label"] = fence.get("name") if fence else None
            live["maps_url"] = google_maps_url(lat, lon)
            live["street_view_url"] = google_street_view_url(lat, lon)
            if not fence:
                cached = get_cached_location(lat, lon)
                if cached:
                    live["location_label"] = cached.get("short_name")
        except (TypeError, ValueError):
            pass
    return live


def iso_provider_range(date_from: str, date_to: str) -> tuple[str, str]:
    validate_date_range(date_from, date_to)
    return provider_date_range(date_from, date_to)


async def sync_incremental_positions() -> dict[str, Any]:
    if settings.demo_mode:
        seed_demo_history()
        return {"demo_mode": True, "records_received": 0, "records_saved": 0, "batches": 0}
    if position_sync_lock.locked():
        return {"skipped": True, "message": "Já existe uma sincronização de posições em andamento."}
    async with position_sync_lock:
        batches = total_received = total_saved = 0
        last_id = get_max_external_id()
        for _ in range(settings.history_incremental_max_batches):
            response = await client.export_positions(last_id, size=settings.history_incremental_batch_size, page=0)
            positions = extract_list(response)
            batches += 1
            result = upsert_positions(positions)
            total_received += result["received"]
            total_saved += result["saved"]
            ids = [int(item["id"]) for item in positions if str(item.get("id", "")).isdigit()]
            if ids:
                last_id = max(last_id, max(ids))
                set_sync_state("last_incremental_external_id", str(last_id))
            if len(positions) < settings.history_incremental_batch_size:
                break
            await asyncio.sleep(0.5)
        set_sync_state("last_incremental_result", f"batches={batches};received={total_received};saved={total_saved}")
        return {"batches": batches, "records_received": total_received, "records_saved": total_saved, "last_external_id": last_id}


async def sync_alerts(date_from: str, date_to: str) -> dict[str, Any]:
    if settings.demo_mode:
        result = upsert_alerts(demo_alerts())
        return {**result, "demo_mode": True}
    if alert_sync_lock.locked():
        return {"skipped": True, "message": "Já existe uma sincronização de alertas em andamento."}
    provider_start, provider_end = iso_provider_range(date_from, date_to)
    async with alert_sync_lock:
        received = saved = pages = 0
        for page in range(20):
            response = await client.search_alerts(provider_start, provider_end, page=page, size=500)
            alerts = extract_list(response)
            result = upsert_alerts(alerts)
            received += result["received"]
            saved += result["saved"]
            pages += 1
            if len(alerts) < 500:
                break
            await asyncio.sleep(0.4)
        return {"records_received": received, "records_saved": saved, "pages": pages}


async def auto_sync_loop() -> None:
    while True:
        try:
            if settings.history_auto_sync and not settings.demo_mode and settings.selsyn_api_key:
                await sync_incremental_positions()
        except Exception as exc:
            set_sync_state("last_auto_sync_error", str(exc))
        await asyncio.sleep(max(settings.history_sync_interval_seconds, 60))


async def tracker_health_loop() -> None:
    """Consulta a última posição em intervalo controlado para detectar GPS congelado."""
    while True:
        try:
            if settings.demo_mode:
                data = demo_live_positions()
            elif settings.selsyn_api_key:
                data = await client.live_positions()
            else:
                data = []

            for item in extract_list(data):
                normalize_and_assess(item)

            if data:
                set_sync_state(
                    "last_tracker_health_check",
                    datetime.now(timezone.utc).isoformat(),
                )
        except Exception as exc:
            set_sync_state("last_tracker_health_error", str(exc))

        await asyncio.sleep(max(settings.poll_interval_seconds, 60))


async def aline_email_loop() -> None:
    """Consulta periodicamente a caixa Terra quando a automacao estiver ativa."""
    while True:
        interval = settings.aline_email_default_interval_seconds
        try:
            await asyncio.to_thread(sync_aline_email_once)
            interval = aline_email_interval_seconds()
        except Exception:
            pass
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_history_db()
    init_operations_db()
    init_geocoding_db()
    init_tracker_health_db()
    init_employee_portal_db()
    init_document_storage()
    if not settings.demo_mode:
        purge_demo_vehicle_documents()
    init_aline_projects()
    init_aline_email()
    init_system_state()
    init_cora_webhooks()
    if not settings.demo_mode:
        purge_demo_system_state()
    init_auth()
    if settings.demo_mode:
        seed_demo_history()
        seed_demo_operations()
        upsert_alerts(demo_alerts())
        from .demo_seed import seed_demo_state
        seed_demo_state()
    sync_task = asyncio.create_task(auto_sync_loop())
    tracker_task = asyncio.create_task(tracker_health_loop())
    background_tasks = [sync_task, tracker_task]
    if not settings.demo_mode:
        background_tasks.append(asyncio.create_task(aline_email_loop()))
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Sistema de Gestão Operacional — Demo", description="Case técnico de portfólio com dados fictícios e integrações desabilitadas por padrão.", version="3.0.0-demo", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.include_router(aline_router)
app.include_router(aline_email_router)
app.include_router(system_state_router)
from .historical_vision import router as historical_vision_router
app.include_router(historical_vision_router)
app.include_router(auth_router)
app.include_router(employee_certificates_router)
app.include_router(financial_calendar_router)
app.include_router(bulk_work_import_router)
app.include_router(profile_photos_router)
app.include_router(whatsapp_router)
app.include_router(cora_router)
app.include_router(tablet_agenda_router)


@app.middleware("http")
async def require_system_login(request: Request, call_next):
    path = request.url.path
    # Public portfolio visitors can explore synthetic data without changing
    # the shared demo database. Keep authentication available.
    if settings.demo_mode and settings.public_demo_read_only:
        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            if path not in {"/api/auth/login", "/api/auth/logout"}:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Demonstração pública em modo somente leitura."},
                )
    public_exact = {
        "/",
        "/login",
        "/privacy",
        "/terms",
        "/data-deletion",
        "/integracao-whatsapp",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/health",
        "/healthz",
        "/api/whatsapp/webhook",
        "/api/cora/webhook",
        "/app",
        "/meta/whatsapp/callback",
    }
    employee_portal_public = path.startswith("/api/employee-portal/") and not path.startswith(
        "/api/employee-portal/admin/"
    )
    if path in public_exact or path.startswith("/assets/") or employee_portal_public:
        return await call_next(request)

    user = user_from_session(request.cookies.get(COOKIE_NAME))
    if not user:
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Faça login para continuar."},
            )
        destination = "/login?next=/agenda-tablet" if path.startswith("/agenda-tablet") else "/login"
        return RedirectResponse(destination, status_code=303)

    if not path_is_allowed(user, path):
        module = module_for_path(path) or "recurso"
        return JSONResponse(
            status_code=403,
            content={"detail": f"Este perfil não possui acesso ao módulo {module}."},
        )
    request.state.system_user = user
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if user_from_session(request.cookies.get(COOKIE_NAME)):
        destination = "/agenda-tablet" if request.query_params.get("next") == "/agenda-tablet" else "/workspace"
        return RedirectResponse(destination, status_code=303)
    demo_credentials = None
    if (
        settings.demo_mode
        and settings.demo_email == PUBLIC_DEMO_EMAIL
        and settings.demo_password == PUBLIC_DEMO_PASSWORD
    ):
        demo_credentials = {"email": PUBLIC_DEMO_EMAIL, "password": PUBLIC_DEMO_PASSWORD}
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "tablet_login": request.query_params.get("next") == "/agenda-tablet",
            "demo_credentials": demo_credentials,
        },
    )


@app.get("/agenda-tablet", response_class=HTMLResponse)
async def tablet_agenda_page(request: Request):
    return templates.TemplateResponse(request=request, name="tablet_agenda.html", context={})


@app.get("/agenda-tablet/sw.js")
async def tablet_agenda_service_worker():
    from fastapi.responses import FileResponse
    return FileResponse("assets/tablet_agenda_sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/", response_class=HTMLResponse)
async def institutional_home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="institutional.html",
        context={
            "contact_email": settings.public_contact_email,
            "contact_phone": settings.public_contact_phone,
            "whatsapp_url": settings.public_whatsapp_url or None,
            "instagram_handle": settings.public_instagram_handle,
            "instagram_url": settings.public_instagram_url,
        },
    )


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="privacy.html",
        context={
            "contact_email": settings.public_contact_email,
            "contact_phone": settings.public_contact_phone,
            "whatsapp_url": settings.public_whatsapp_url,
        },
    )


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="terms.html",
        context={
            "contact_email": settings.public_contact_email,
            "contact_phone": settings.public_contact_phone,
            "whatsapp_url": settings.public_whatsapp_url,
        },
    )


@app.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="data_deletion.html",
        context={
            "contact_email": settings.public_contact_email,
            "contact_phone": settings.public_contact_phone,
            "whatsapp_url": settings.public_whatsapp_url,
        },
    )


@app.get("/workspace", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = request.state.system_user
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "demo_mode": settings.demo_mode,
            "poll_interval_seconds": max(settings.poll_interval_seconds, 5),
            "history_max_range_days": settings.history_max_range_days,
            "employee_portal_enabled": True,
            "system_state": filter_state_for_user(get_system_state(), user),
            "current_user": user,
        },
    )


@app.get("/rh/employees", response_class=HTMLResponse)
@app.get("/rh/exams", response_class=HTMLResponse)
@app.get("/rh/vacations", response_class=HTMLResponse)
@app.get("/rh/certificates", response_class=HTMLResponse)
@app.get("/rh/clinics", response_class=HTMLResponse)
@app.get("/finance/calendar", response_class=HTMLResponse)
@app.get("/fleet", response_class=HTMLResponse)
@app.get("/admin/users", response_class=HTMLResponse)
async def protected_workspace_page(request: Request):
    return await dashboard(request)


@app.get("/healthz", include_in_schema=False)
async def liveness():
    # No database initialization, external calls or credentials in this probe.
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "version": "3.0.0-demo",
        "demo_mode": settings.demo_mode,
        "base_url": settings.selsyn_base_url,
        "api_key_configured": bool(settings.selsyn_api_key),
        "authentication": "header x-api-key",
        "blocking_commands_integrated": False,
        "history_auto_sync": settings.history_auto_sync,
        "tracker_diagnostics_enabled": True,
        "tracker_health_page_visible": False,
        "tracker_health": tracker_health_summary(),
        "employee_portal": admin_state_summary(),
        "employee_app_url": "/app",
        "aline_projects": aline_health(),
        "system_state": state_health(),
        "document_storage": {
            "ok": True,
            "root": settings.documents_root_path,
            "max_bytes": settings.document_file_max_bytes,
        },
    }


def _bearer_token(authorization: str | None) -> str:
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sessão de funcionário não informada.")
    token = value.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sessão de funcionário inválida.")
    return token


def _employee_id_from_authorization(authorization: str | None) -> tuple[str, int]:
    token = _bearer_token(authorization)
    employee_id = employee_for_token(token)
    if employee_id is None:
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre novamente.")
    return token, employee_id

@app.get("/meta/whatsapp/callback", response_class=HTMLResponse)
async def meta_whatsapp_callback():
    return """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Gestão Operacional Topografia | WhatsApp</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f4f7f9;
                color: #17324d;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
            }

            .card {
                background: white;
                max-width: 520px;
                margin: 24px;
                padding: 40px;
                border-radius: 16px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
                text-align: center;
            }

            h1 {
                margin-top: 0;
            }

            p {
                line-height: 1.6;
            }
        </style>
    </head>

    <body>
        <div class="card">
            <h1>Gestão Operacional Topografia</h1>
            <p>
                Conexão com o WhatsApp Business processada.
            </p>
            <p>
                Você já pode fechar esta página e retornar ao sistema.
            </p>
        </div>
    </body>
    </html>
    """    


@app.get("/app", response_class=HTMLResponse)
async def employee_app(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="employee_app.html",
        context={
            "employee_file_max_bytes": settings.employee_file_max_bytes,
            "current_user": user_from_session(request.cookies.get(COOKIE_NAME)),
        },
    )


@app.post("/api/employee-portal/login")
async def employee_portal_login(payload: EmployeeLoginPayload):
    authenticated = authenticate_employee(payload.username, payload.password)
    if authenticated is None:
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
    token, employee_id = authenticated
    view = employee_view(employee_id)
    if view is None:
        raise HTTPException(status_code=403, detail="Funcionário sem acesso ao aplicativo.")
    return {"ok": True, "token": token, **view}


@app.get("/api/employee-portal/me")
async def employee_portal_me(authorization: str | None = Header(default=None)):
    _, employee_id = _employee_id_from_authorization(authorization)
    view = employee_view(employee_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Funcionário não localizado.")
    return {"ok": True, **view}


@app.post("/api/employee-portal/logout")
async def employee_portal_logout(authorization: str | None = Header(default=None)):
    token = _bearer_token(authorization)
    logout_token(token)
    return {"ok": True}


@app.post("/api/employee-portal/messages")
async def employee_portal_message(
    payload: EmployeeMessagePayload,
    authorization: str | None = Header(default=None),
):
    _, employee_id = _employee_id_from_authorization(authorization)
    message = append_employee_message(employee_id, payload.schedule_entry_id, payload.text)
    if message is None:
        raise HTTPException(status_code=404, detail="Obra ou conversa não localizada para este funcionário.")
    view = employee_view(employee_id)
    return {"ok": True, "message": message, **(view or {})}


@app.post("/api/employee-portal/messages/read")
async def employee_portal_messages_read(
    payload: EmployeeReadPayload,
    authorization: str | None = Header(default=None),
):
    _, employee_id = _employee_id_from_authorization(authorization)
    if not mark_entry_messages_read(employee_id, payload.schedule_entry_id):
        raise HTTPException(status_code=404, detail="Obra não localizada para este funcionário.")
    return {"ok": True}


@app.post("/api/employee-portal/files")
async def employee_portal_file_upload(
    payload: EmployeeFilePayload,
    authorization: str | None = Header(default=None),
):
    _, employee_id = _employee_id_from_authorization(authorization)
    try:
        record = add_employee_file(
            employee_id,
            payload.schedule_entry_id,
            payload.name,
            payload.mime_type,
            payload.size,
            payload.data_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Obra não localizada para este funcionário.")
    view = employee_view(employee_id)
    return {"ok": True, "file": record, **(view or {})}


@app.get("/api/employee-portal/files/{file_id}")
async def employee_portal_file(
    file_id: int,
    authorization: str | None = Header(default=None),
):
    _, employee_id = _employee_id_from_authorization(authorization)
    record = get_employee_file(employee_id, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Arquivo não localizado.")
    return record


@app.post("/api/employee-portal/admin/sync")
async def employee_portal_admin_sync(
    payload: EmployeePortalSyncPayload,
    request: Request,
):
    role_code = request.state.system_user["role_code"]
    allowed_keys = {
        "financial": {
    "employees",
    "clinics",
    "exams",
    "employeeAppFiles",
},
        "helena": {
            "employees", "workTeams", "companies", "works",
            "workScheduleEntries", "projectClients",
        },
        "aline": set(),
    }.get(role_code)
    data = payload.data
    if allowed_keys is not None:
        data = {key: value for key, value in payload.data.items() if key in allowed_keys}
    return sync_portal_state(data)


@app.get("/api/employee-portal/admin/state")
async def employee_portal_admin_state(request: Request):
    state = load_portal_state()
    role_code = request.state.system_user["role_code"]
    return {
        "ok": True,
        "updatedAt": state.get("serverUpdatedAt"),
        "projectClients": state.get("projectClients", []) if role_code in {"admin", "director", "helena"} else [],
        "employeeAppFiles": state.get("employeeAppFiles", []) if role_code in {"admin", "director", "financial"} else [],
        "summary": admin_state_summary(state),
    }


@app.post("/api/employees/import-pdf/preview")
async def employee_pdf_import_preview(
    payload: EmployeePdfImportPayload,
):
    filename = Path(payload.filename).name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Selecione um arquivo PDF.")
    try:
        encoded = payload.data_url.split(",", 1)[1] if "," in payload.data_url else payload.data_url
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="O conteúdo do PDF é inválido.") from exc
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="O PDF deve ter no máximo 20 MB.")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="O arquivo enviado não possui uma estrutura PDF válida.")
    try:
        result = parse_employee_registration_pdf(content, filename)
        result["archivePath"] = archive_employee_pdf(content, filename)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Não foi possível ler a ficha: {exc}") from exc


@app.post("/api/exams/import-pdf/preview")
async def exam_pdf_import_preview(
    payload: EmployeePdfImportPayload,
):
    filename = Path(payload.filename).name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Selecione um arquivo PDF.")
    try:
        encoded = payload.data_url.split(",", 1)[1] if "," in payload.data_url else payload.data_url
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="O conteúdo do PDF é inválido.") from exc
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="O PDF deve ter no máximo 20 MB.")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="O arquivo enviado não possui uma estrutura PDF válida.")
    try:
        result = parse_exam_roster_pdf(content, filename)
        result["archivePath"] = archive_exam_roster_pdf(content, filename)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Não foi possível ler o relatório: {exc}") from exc


@app.post("/api/vacations/import-pdf/preview")
async def vacation_pdf_import_preview(
    payload: EmployeePdfImportPayload,
):
    filename = Path(payload.filename).name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Selecione um arquivo PDF.")
    try:
        encoded = payload.data_url.split(",", 1)[1] if "," in payload.data_url else payload.data_url
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="O conteúdo do PDF é inválido.") from exc
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="O PDF deve ter no máximo 20 MB.")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="O arquivo enviado não possui uma estrutura PDF válida.")
    try:
        result = parse_vacation_roster_pdf(content, filename)
        result["archivePath"] = archive_vacation_roster_pdf(content, filename)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Não foi possível ler o relatório de férias: {exc}") from exc


@app.get("/api/connection-test")
async def connection_test():
    if settings.demo_mode:
        return {"ok": True, "demo_mode": True, "message": "Desative o modo demonstração para testar a Selsyn."}
    try:
        live = await client.live_positions()
        return {"ok": True, "live_response_type": type(live).__name__, "vehicles_returned": len(extract_list(live))}
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/vehicles/live")
async def vehicles_live():
    try:
        data = demo_live_positions() if settings.demo_mode else await client.live_positions()
        vehicles = [normalize_and_assess(item) for item in extract_list(data)]
        return [
            item for item in vehicles
            if not is_vehicle_hidden(item["identifier"])
        ]
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/vehicles")
async def vehicles():
    history = {
        str(item["rastreavel"]).upper(): item
        for item in list_history_vehicles()
        if not is_vehicle_hidden(str(item["rastreavel"]))
    }
    live_items: list[dict[str, Any]] = []
    try:
        data = demo_live_positions() if settings.demo_mode else await client.live_positions()
        live_items = [
            normalize_and_assess(item)
            for item in extract_list(data)
        ]
        live_items = [
            item for item in live_items
            if not is_vehicle_hidden(item["identifier"])
        ]
    except SelsynError:
        live_items = []

    identifiers = set(history)
    identifiers.update(item["identifier"].upper() for item in live_items)
    result = []
    for identifier in sorted(identifiers):
        live = next(
            (
                item for item in live_items
                if item["identifier"].upper() == identifier
            ),
            None,
        )
        stored = history.get(identifier, {})
        raw_identifier = (
            live["identifier"]
            if live
            else stored.get("rastreavel", identifier)
        )
        profile = get_vehicle_profile(raw_identifier)
        result.append(
            {
                "identifier": raw_identifier,
                "display_name": (
                    profile.get("description")
                    or (live or {}).get("description")
                    or raw_identifier
                ),
                "live": live,
                "profile": profile,
                "has_history": bool(stored.get("position_count", 0)),
                "last_position": stored.get("last_position"),
            }
        )
    return result


@app.get("/api/vehicles/hidden")
async def hidden_vehicles():
    return list_hidden_vehicles()


@app.delete("/api/vehicles/{identifier}")
async def delete_vehicle(identifier: str, purge_local_data: bool = False):
    profile = hide_vehicle(identifier, purge_local_data=purge_local_data)
    return {
        "ok": True,
        "identifier": identifier,
        "hidden": True,
        "purged_local_data": purge_local_data,
        "profile": profile,
        "message": (
            "Veículo excluído permanentemente da lista local. "
            "O rastreador continua existindo na Selsyn, mas não voltará a aparecer com as atualizações."
        ),
    }


@app.post("/api/vehicles/{identifier}/restore")
async def restore_vehicle_route(identifier: str):
    raise HTTPException(
        status_code=409,
        detail=(
            "Este veículo foi excluído permanentemente. "
            "Para evitar que volte após atualizações da Selsyn, a restauração pela interface foi desativada."
        ),
    )


@app.get("/api/vehicles/{identifier}/live")
async def vehicle_live(identifier: str):
    if is_vehicle_hidden(identifier):
        raise HTTPException(status_code=404, detail="Veículo removido do sistema local.")
    if settings.demo_mode:
        match = next((item for item in demo_live_positions() if item.get("identificador") == identifier.upper()), None)
        if not match:
            raise HTTPException(status_code=404, detail="Veículo não encontrado na demonstração.")
        return normalize_and_assess(match)
    try:
        data = await client.live_position_by_identifier(identifier)
        return normalize_and_assess(data if isinstance(data, dict) else {})
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@app.get("/api/location/search")
async def location_search(q: str = Query(min_length=3, max_length=300), limit: int = Query(default=5, ge=1, le=10)):
    results = await asyncio.to_thread(search_location, q, limit)
    return {"query": q, "results": results}


@app.get("/api/location/reverse")
async def reverse_location(latitude: float, longitude: float):
    fence = find_geofence_for_point(latitude, longitude)
    if fence:
        return {
            "short_name": fence["name"],
            "display_name": fence["name"],
            "geofence": fence,
            "maps_url": google_maps_url(latitude, longitude),
            "street_view_url": google_street_view_url(latitude, longitude),
            "source": "GEOFENCE",
        }
    return await asyncio.to_thread(reverse_geocode, latitude, longitude)


async def enrich_location(latitude: float, longitude: float) -> dict[str, Any]:
    fence = find_geofence_for_point(latitude, longitude)
    if fence:
        return {
            "name": fence["name"],
            "full_name": fence["name"],
            "geofence": fence,
            "maps_url": google_maps_url(latitude, longitude),
            "street_view_url": google_street_view_url(latitude, longitude),
            "source": "GEOFENCE",
        }
    location = await asyncio.to_thread(reverse_geocode, latitude, longitude)
    return {
        "name": location.get("short_name") or "Local não identificado",
        "full_name": location.get("display_name") or location.get("short_name"),
        "geofence": None,
        "maps_url": location["maps_url"],
        "street_view_url": location["street_view_url"],
        "source": location.get("source"),
    }


async def enrich_trips_and_stops(
    trips_data: list[dict[str, Any]],
    stops_data: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    location_cache: dict[tuple[float, float], dict[str, Any]] = {}

    async def lookup(lat: float, lon: float) -> dict[str, Any]:
        key = (round(float(lat), 5), round(float(lon), 5))
        if key not in location_cache:
            location_cache[key] = await enrich_location(float(lat), float(lon))
        return location_cache[key]

    for trip in trips_data:
        trip["start_location"] = await lookup(
            trip["start_latitude"], trip["start_longitude"]
        )
        trip["end_location"] = await lookup(
            trip["end_latitude"], trip["end_longitude"]
        )

    for stop in stops_data:
        stop["location"] = await lookup(stop["latitude"], stop["longitude"])

    return trips_data, stops_data


def attach_trip_route_details(
    route_points: list[dict[str, Any]],
    trips_data: list[dict[str, Any]],
    stops_data: list[dict[str, Any]],
) -> None:
    def parsed(value: Any) -> datetime | None:
        try:
            result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    for index, trip in enumerate(trips_data):
        trip.setdefault("trip_id", f"trip-{index + 1}")
        start = parsed(trip.get("start_time"))
        end = parsed(trip.get("end_time"))
        next_trip = trips_data[index + 1] if index + 1 < len(trips_data) else None
        next_start = parsed(next_trip.get("start_time")) if next_trip else None
        if start and end:
            trip["route_points"] = [
                point
                for point in route_points
                if (point_time := parsed(point.get("position_time")))
                and start <= point_time <= end
            ]
        else:
            trip["route_points"] = []
        stop_after = next(
            (
                stop
                for stop in stops_data
                if (stop_start := parsed(stop.get("start_time")))
                and end
                and stop_start >= end
                and (next_start is None or stop_start <= next_start)
            ),
            None,
        )
        trip["stop_after"] = stop_after
        trip["resume"] = (
            {
                "time": next_trip.get("start_time"),
                "latitude": next_trip.get("start_latitude"),
                "longitude": next_trip.get("start_longitude"),
                "location": next_trip.get("start_location"),
            }
            if next_trip
            else None
        )


@app.get("/api/routes/{identifier}/details")
async def route_details(identifier: str, date_from: str, date_to: str):
    if is_vehicle_hidden(identifier):
        raise HTTPException(status_code=404, detail="Veículo removido do sistema local.")
    try:
        history = query_history(identifier, date_from, date_to)
        trips_data = calculate_trips(identifier, date_from, date_to)
        raw_stops = calculate_stops(identifier, date_from, date_to)
        operational = calculate_operational_period(
            identifier,
            date_from,
            date_to,
            trips=trips_data,
            stops=raw_stops,
        )
        stops_data = operational["stops"]
        trips_data, stops_data = await enrich_trips_and_stops(
            trips_data, stops_data
        )
        attach_trip_route_details(history["points"], trips_data, stops_data)
        days: dict[str, dict[str, Any]] = {}
        for trip in trips_data:
            day = trip.get("operational_day") or str(trip["start_time"])[:10]
            days.setdefault(day, {"date": day, "trips": [], "stops": []})[
                "trips"
            ].append(trip)
        for stop in stops_data:
            day = stop.get("operational_day") or str(stop["start_time"])[:10]
            days.setdefault(day, {"date": day, "trips": [], "stops": []})[
                "stops"
            ].append(stop)
        # O mapa precisa receber todos os pontos GPS da data escolhida. A versão
        # anterior recortava os pontos somente aos períodos operacionais e, em
        # alguns dias, esse recorte deixava apenas uma coordenada visível.
        # O resumo operacional continua excluindo a madrugada e o período depois
        # da chegada final, mas a geometria do mapa representa o histórico completo.
        return {
            "identifier": identifier,
            "summary": operational["summary"],
            "operational_periods": operational["periods"],
            "route_points": history["points"],
            "route_total_points": history["total_points"],
            "route_displayed_points": history["displayed_points"],
            "route_raw_points": history.get("raw_points", history["total_points"]),
            "route_discarded_points": history.get("discarded_points", 0),
            "route_quality": history.get("quality", {}),
            "history_summary": history["summary"],
            "trips": trips_data,
            "stops": stops_data,
            "days": sorted(days.values(), key=lambda item: item["date"]),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/reports/daily/{identifier}")
async def daily_report_data(identifier: str, report_date: str):
    report = build_daily_report(identifier, report_date)
    report["trips"], report["stops"] = await enrich_trips_and_stops(
        report["trips"], report["stops"]
    )
    for item in report["timeline"]:
        if item["kind"] == "STOP":
            matching = next(
                (
                    stop for stop in report["stops"]
                    if stop["start_time"] == item["time"]
                ),
                None,
            )
            if matching:
                item["location"] = matching.get("location")
        else:
            matching = next(
                (
                    trip for trip in report["trips"]
                    if trip["start_time"] == item["time"]
                ),
                None,
            )
            if matching:
                item["start_location"] = matching.get("start_location")
                item["end_location"] = matching.get("end_location")
    return report


@app.get("/reports/daily/{identifier}", response_class=HTMLResponse)
async def daily_report_page(request: Request, identifier: str, report_date: str):
    report = await daily_report_data(identifier, report_date)
    return templates.TemplateResponse(
        request=request,
        name="daily_report.html",
        context={"report": report, "current_user": request.state.system_user},
    )


@app.get("/api/tracker-health")
async def tracker_health_list():
    return {
        "summary": tracker_health_summary(),
        "vehicles": list_tracker_health(),
        "thresholds": {
            "communication_stale_minutes": (
                settings.tracker_communication_stale_minutes
            ),
            "position_stale_minutes": (
                settings.tracker_position_stale_minutes
            ),
            "gps_freeze_minutes": settings.tracker_gps_freeze_minutes,
            "movement_distance_threshold_m": (
                settings.tracker_movement_distance_threshold_m
            ),
            "movement_speed_threshold_kmh": (
                settings.tracker_movement_speed_threshold_kmh
            ),
            "minimum_satellites": settings.tracker_minimum_satellites,
        },
    }


@app.post("/api/tracker-health/refresh")
async def tracker_health_refresh():
    try:
        data = (
            demo_live_positions()
            if settings.demo_mode
            else await client.live_positions()
        )
        vehicles = [
            normalize_and_assess(item)
            for item in extract_list(data)
        ]
        return {
            "ok": True,
            "vehicles_checked": len(vehicles),
            "summary": tracker_health_summary(),
            "vehicles": vehicles,
        }
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/tracker-health/{identifier}")
async def tracker_health_vehicle(identifier: str):
    item = get_tracker_health(identifier)
    if not item:
        raise HTTPException(
            status_code=404,
            detail=(
                "Ainda não há observações suficientes para este veículo. "
                "Atualize o monitoramento."
            ),
        )
    return item


@app.get("/api/tracker-health/{identifier}/observations")
async def tracker_health_observations(
    identifier: str,
    limit: int = Query(default=100, ge=1, le=1000),
):
    return list_tracker_observations(identifier, limit)


@app.get("/api/vehicles/{identifier}/provider-details")
async def vehicle_provider_details(identifier: str):
    if settings.demo_mode:
        return {"identificador": identifier.upper(), "descricao": "Cadastro de demonstração", "ativo": True}
    try:
        return await client.rastreavel_by_identifier(identifier)
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/vehicles/{identifier}/profile")
async def vehicle_profile(identifier: str):
    return get_vehicle_profile(identifier)


@app.put("/api/vehicles/{identifier}/profile")
async def update_vehicle_profile_route(identifier: str, payload: VehicleProfilePayload):
    return save_vehicle_profile(identifier, payload.model_dump())


@app.put("/api/vehicles/{identifier}/profile-with-document")
async def update_vehicle_profile_with_document_route(
    identifier: str, payload: VehicleProfileWithDocumentPayload
):
    stored = None
    validated: tuple[bytes, str, str] | None = None
    if payload.crlv is not None:
        if payload.crlv.owner_type != "vehicle":
            raise HTTPException(status_code=400, detail="O CRLV deve pertencer a um veículo.")
        try:
            validated = decode_document_content(
                payload.crlv.filename, payload.crlv.content_base64
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        profile = save_vehicle_profile(identifier, payload.profile.model_dump())
        if payload.crlv is not None and validated is not None:
            content, filename, detected_mime = validated
            stored = save_stored_document(
                owner_type="vehicle",
                owner_key=str(identifier).strip().upper(),
                owner_name=profile.get("description") or str(identifier).strip().upper(),
                document_type=payload.crlv.document_type or "CRLV",
                title=payload.crlv.title or "CRLV",
                document_number=payload.crlv.document_number or profile.get("renavam"),
                issue_date=payload.crlv.issue_date,
                expiry_date=payload.crlv.expiry_date,
                notes=payload.crlv.notes,
                filename=filename,
                content=content,
                mime_type=payload.crlv.mime_type or detected_mime,
            )
        return {"ok": True, "profile": profile, "document": stored}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/vehicles/{identifier}/insights")
async def vehicle_insights_route(identifier: str, date_from: str, date_to: str):
    try:
        return vehicle_insights(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/dashboard/summary")
async def dashboard_summary_route(date_from: str, date_to: str):
    try:
        return dashboard_summary(date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/history/status")
async def history_status():
    return {**get_sync_state(), "demo_mode": settings.demo_mode, "auto_sync": settings.history_auto_sync}


@app.post("/api/history/sync-now")
async def history_sync_now():
    try:
        return await sync_incremental_positions()
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/history/import")
async def import_history(payload: HistoryImportRequest):
    try:
        validate_date_range(payload.date_from, payload.date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if settings.demo_mode:
        seed_demo_history()
        return {"ok": True, "demo_mode": True, "completed": True, "records_received": 0, "records_saved": 0, "message": "O histórico fictício já está disponível."}
    if position_sync_lock.locked():
        raise HTTPException(status_code=409, detail="Já existe uma sincronização em andamento.")
    provider_start, provider_end = provider_date_range(payload.date_from, payload.date_to)
    import_id = start_import_log(payload.date_from, payload.date_to)
    pages = received = saved = 0
    completed = False
    async with position_sync_lock:
        try:
            for page in range(settings.history_import_max_pages):
                response = await client.export_positions(
                    0,
                    size=settings.history_import_page_size,
                    page=page,
                    date_from=provider_start,
                    date_to=provider_end,
                )
                positions = extract_list(response)
                pages += 1
                result = upsert_positions(positions)
                received += result["received"]
                saved += result["saved"]
                if len(positions) < settings.history_import_page_size:
                    completed = True
                    break
                await asyncio.sleep(0.5)
            finish_import_log(import_id, pages_requested=pages, records_received=received, records_saved=saved, completed=completed)
            return {
                "ok": True,
                "completed": completed,
                "pages_requested": pages,
                "records_received": received,
                "records_saved": saved,
                "message": "Importação concluída." if completed else "O limite de páginas foi atingido; reduza o período ou repita a importação.",
            }
        except SelsynError as exc:
            finish_import_log(import_id, pages_requested=pages, records_received=received, records_saved=saved, completed=False, error_message=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/history/{identifier}")
async def vehicle_history(identifier: str, date_from: str, date_to: str):
    try:
        return query_history(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/trips/{identifier}")
async def trips(identifier: str, date_from: str, date_to: str):
    try:
        return calculate_trips(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/stops/{identifier}")
async def stops(identifier: str, date_from: str, date_to: str):
    try:
        return calculate_stops(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/alerts/sync")
async def alerts_sync(date_from: str, date_to: str):
    try:
        return await sync_alerts(date_from, date_to)
    except (ValueError, SelsynError) as exc:
        raise HTTPException(status_code=502 if isinstance(exc, SelsynError) else 400, detail=str(exc)) from exc


@app.get("/api/alerts")
async def alerts(
    date_from: str,
    date_to: str,
    identifier: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
):
    try:
        return list_cached_alerts(date_from, date_to, identifier, status, event_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/alerts/{event_id}")
async def event_by_id(event_id: int):
    if settings.demo_mode:
        match = next((normalize_alert(item) for item in demo_alerts() if int(item.get("id", -1)) == event_id), None)
        if not match:
            raise HTTPException(status_code=404, detail="Evento não encontrado.")
        return match
    try:
        return await client.event_by_id(event_id)
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/alerts/{event_id}/comment")
async def add_alert_comment(event_id: int, action: EventAction):
    if settings.demo_mode:
        return {"ok": True, "demo_mode": True, "comment": action.comment}
    try:
        return await client.add_event_comment(event_id, action.comment)
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/alerts/{event_id}/status/{status}")
async def move_alert(event_id: int, status: Literal["NONE", "NEW", "OPENED", "CLOSED", "ARCHIVED"]):
    if settings.demo_mode:
        update_cached_alert_status(event_id, status)
        return {"ok": True, "demo_mode": True, "status": status}
    try:
        response = await client.move_event(event_id, status)
        update_cached_alert_status(event_id, status)
        return response
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/alerts/{event_id}/close")
async def close_alert(event_id: int, action: EventAction):
    if settings.demo_mode:
        update_cached_alert_status(event_id, "CLOSED")
        return {"ok": True, "demo_mode": True, "status": "CLOSED"}
    try:
        response = await client.close_event(event_id, action.comment)
        update_cached_alert_status(event_id, "CLOSED")
        return response
    except SelsynError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/geofences")
async def geofences():
    return list_geofences()


@app.post("/api/geofences")
async def add_geofence(payload: GeofencePayload):
    return create_geofence(payload.model_dump())


@app.put("/api/geofences/{fence_id}")
async def edit_geofence(fence_id: int, payload: GeofencePayload):
    try:
        return update_geofence(fence_id, payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/geofences/{fence_id}")
async def remove_geofence(fence_id: int):
    delete_geofence(fence_id)
    return {"ok": True}


@app.get("/api/geofences/activity/{identifier}")
async def fence_activity(identifier: str, date_from: str, date_to: str):
    try:
        return geofence_activity(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/usage/rules")
async def usage_rules():
    return get_usage_rules()


@app.put("/api/usage/rules")
async def update_usage_rules(payload: UsageRulesPayload):
    return save_usage_rules(payload.model_dump())


@app.get("/api/usage/violations/{identifier}")
async def violations(identifier: str, date_from: str, date_to: str):
    try:
        return usage_violations(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/driving-score/{identifier}")
async def score(identifier: str, date_from: str, date_to: str):
    try:
        return driving_score(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/fuel")
async def fuel(date_from: str, date_to: str, identifier: str | None = None):
    return list_fuel(date_from, date_to, identifier)


@app.post("/api/fuel")
async def create_fuel(payload: FuelPayload):
    return add_fuel({**payload.model_dump(), "source": "LOCAL", "external_id": None})


@app.delete("/api/fuel/{record_id}")
async def remove_fuel(record_id: int):
    delete_fuel(record_id)
    return {"ok": True}


@app.get("/api/maintenance")
async def maintenance(date_from: str, date_to: str, identifier: str | None = None):
    return list_maintenance(date_from, date_to, identifier)


@app.post("/api/maintenance")
async def create_maintenance(payload: MaintenancePayload):
    return add_maintenance({**payload.model_dump(), "source": "LOCAL", "external_id": None})


@app.delete("/api/maintenance/{record_id}")
async def remove_maintenance(record_id: int):
    delete_maintenance(record_id)
    return {"ok": True}


@app.get("/api/expenses")
async def expenses(date_from: str, date_to: str, identifier: str | None = None):
    return list_expenses(date_from, date_to, identifier)


@app.post("/api/expenses")
async def create_expense(payload: ExpensePayload):
    return add_expense({**payload.model_dump(), "source": "LOCAL", "external_id": None})


@app.delete("/api/expenses/{record_id}")
async def remove_expense(record_id: int):
    delete_expense(record_id)
    return {"ok": True}


@app.get("/api/vehicle-debts")
async def vehicle_debts(identifier: str | None = None, include_paid: bool = True):
    return list_vehicle_debts(identifier, include_paid=include_paid)


@app.post("/api/vehicle-debts")
async def create_vehicle_debt(payload: VehicleDebtPayload):
    try:
        return add_vehicle_debt(payload.model_dump(), skip_duplicate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/vehicle-debts/bulk")
async def create_vehicle_debts_bulk(payload: VehicleDebtBulkPayload):
    return add_vehicle_debts_bulk([item.model_dump() for item in payload.items])


@app.post("/api/vehicle-debts/import-image")
async def import_vehicle_debts_image(payload: VehicleDebtImageImportPayload):
    identifier = payload.identifier.strip().upper()
    if not identifier:
        raise HTTPException(status_code=400, detail="Veículo não informado.")

    saved_document = None
    if payload.document is not None:
        document = payload.document
        if document.owner_type != "vehicle":
            raise HTTPException(status_code=400, detail="O arquivo deve pertencer a um veículo.")
        if document.owner_key.strip().upper() != identifier:
            raise HTTPException(status_code=400, detail="O veículo do arquivo não corresponde ao veículo dos débitos.")
        try:
            content, filename, detected_mime = decode_document_content(
                document.filename, document.content_base64
            )
            saved_document = save_stored_document(
                owner_type="vehicle",
                owner_key=identifier,
                owner_name=document.owner_name,
                document_type=document.document_type or "CONSULTA DE DÉBITOS",
                title=document.title,
                document_number=document.document_number,
                issue_date=document.issue_date,
                expiry_date=document.expiry_date,
                notes=document.notes,
                filename=filename,
                content=content,
                mime_type=document.mime_type or detected_mime,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Não foi possível salvar a imagem: {exc}") from exc

    items: list[dict[str, Any]] = []
    for item in payload.items:
        row = item.model_dump()
        row["identifier"] = identifier
        row["source"] = "IMAGEM_OCR"
        row["source_document_id"] = (
            saved_document.get("id") if saved_document else payload.source_document_id
        )
        items.append(row)

    result = add_vehicle_debts_bulk(items) if items else {
        "ok": True,
        "created": [],
        "duplicates": [],
        "errors": [],
        "created_count": 0,
        "duplicate_count": 0,
        "error_count": 0,
    }
    return {
        **result,
        "document_saved": saved_document is not None,
        "document": saved_document,
    }


@app.put("/api/vehicle-debts/{record_id}")
async def edit_vehicle_debt(record_id: int, payload: VehicleDebtPayload):
    try:
        return update_vehicle_debt(record_id, payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/vehicle-debts/{record_id}/pay")
async def pay_vehicle_debt(record_id: int, payload: VehicleDebtPaymentPayload):
    try:
        return mark_vehicle_debt_paid(
            record_id,
            payload.paid_date,
            payload.payment_method,
            payload.register_expense,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/vehicle-debts/{record_id}")
async def remove_vehicle_debt(record_id: int):
    delete_vehicle_debt(record_id)
    return {"ok": True}


@app.get("/api/documents")
async def documents(identifier: str | None = None):
    return list_documents(identifier)


@app.post("/api/documents")
async def create_document(payload: DocumentPayload):
    return add_document(payload.model_dump())


@app.delete("/api/documents/{record_id}")
async def remove_document(record_id: int):
    delete_document(record_id)
    return {"ok": True}


@app.get("/api/stored-documents")
async def stored_documents(
    owner_type: str | None = None,
    owner_key: str | None = None,
    category: Literal["DOCUMENTO", "EXAME", "CERTIFICADO"] | None = None,
):
    return list_stored_documents(
        owner_type=owner_type,
        owner_key=owner_key,
        category=category,
    )


@app.post("/api/stored-documents")
async def create_stored_document(payload: StoredDocumentPayload):
    try:
        content, filename, detected_mime = decode_document_content(
            payload.filename, payload.content_base64
        )
        return save_stored_document(
            owner_type=payload.owner_type,
            owner_key=payload.owner_key,
            owner_name=payload.owner_name,
            category=payload.category,
            document_type=payload.document_type,
            title=payload.title,
            document_number=payload.document_number,
            issue_date=payload.issue_date,
            expiry_date=payload.expiry_date,
            notes=payload.notes,
            filename=filename,
            content=content,
            mime_type=payload.mime_type or detected_mime,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/stored-documents/export")
async def export_stored_documents(
    owner_type: Literal["vehicle", "employee"],
    owner_key: str = Query(min_length=1, max_length=160),
    category: Literal["DOCUMENTO", "EXAME", "CERTIFICADO"] | None = None,
):
    documents = list_stored_documents(
        owner_type=owner_type,
        owner_key=owner_key,
        category=category,
    )
    if not documents:
        raise HTTPException(
            status_code=404,
            detail="Nenhum arquivo encontrado para esta exportação.",
        )
    total_size = sum(int(item.get("size_bytes") or 0) for item in documents)
    if total_size > 250 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="A exportação ultrapassa 250 MB. Exporte uma categoria por vez.",
        )

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in documents:
            try:
                _, path = document_path(int(item["id"]))
            except (KeyError, FileNotFoundError, ValueError):
                continue
            category_folder = str(item.get("category") or "DOCUMENTO").title()
            original_name = Path(str(item.get("original_name") or path.name)).name
            bundle.write(
                path,
                arcname=f"{category_folder}/{int(item['id']):04d}_{original_name}",
            )

    if not archive.tell():
        raise HTTPException(
            status_code=404,
            detail="Os arquivos desta exportação não foram localizados.",
        )
    archive.seek(0)
    safe_owner = "".join(
        character if character.isalnum() else "_" for character in owner_key
    ).strip("_")[:80] or "arquivos"
    category_suffix = f"_{category.lower()}" if category else ""
    filename = f"{owner_type}_{safe_owner}{category_suffix}.zip"
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/stored-documents/{record_id}/download")
async def download_stored_document(record_id: int):
    try:
        item, path = document_path(record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=item.get("mime_type") or "application/octet-stream",
        filename=item.get("original_name") or path.name,
    )


@app.delete("/api/stored-documents/{record_id}")
async def remove_stored_document(record_id: int):
    try:
        item = delete_stored_document(record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "document": item}


@app.get("/api/costs/summary")
async def costs(date_from: str, date_to: str, identifier: str | None = None):
    try:
        return cost_summary(identifier, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/provider-dashboard/{dashboard_type}")
async def provider_dashboard(
    dashboard_type: Literal["current", "fuel", "maintenance", "expenses", "driving-vehicle", "driving-driver"],
    date_from: str,
    date_to: str,
    vehicle_id: int | None = None,
):
    if settings.demo_mode:
        return {"available": False, "demo_mode": True, "message": "O dashboard oficial da Selsyn só é consultado no modo real."}
    try:
        if dashboard_type == "current":
            data = await client.dashboard_current(date_from, date_to)
        else:
            provider_start, provider_end = iso_provider_range(date_from, date_to)
            if dashboard_type == "fuel":
                data = await client.dashboard_fuel(provider_start, provider_end, vehicle_id)
            elif dashboard_type == "maintenance":
                data = await client.dashboard_maintenance(provider_start, provider_end, vehicle_id)
            elif dashboard_type == "expenses":
                data = await client.dashboard_expenses(provider_start, provider_end, vehicle_id)
            elif dashboard_type == "driving-vehicle":
                data = await client.dashboard_driving(provider_start, provider_end, "rastreavel")
            else:
                data = await client.dashboard_driving(provider_start, provider_end, "motorista")
        return {"available": True, "data": data}
    except SelsynError as exc:
        return {"available": False, "message": str(exc), "permission_may_be_required": True}


@app.get("/api/reports/vehicle.csv")
async def vehicle_report_csv(identifier: str, date_from: str, date_to: str):
    insights = vehicle_insights(identifier, date_from, date_to)
    trips_data = calculate_trips(identifier, date_from, date_to)
    stops_data = calculate_stops(identifier, date_from, date_to)
    violations_data = usage_violations(identifier, date_from, date_to)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["RELATÓRIO DO VEÍCULO", identifier])
    writer.writerow(["Período", f"{date_from} a {date_to}"])
    writer.writerow(["Distância (km)", insights["distance_km"]])
    writer.writerow(["Viagens", insights["trip_count"]])
    writer.writerow(["Paradas", insights["stop_count"]])
    writer.writerow(["Nota de condução", insights["driving_score"]["score"]])
    writer.writerow(["Custo total", insights["costs"]["total"]])
    writer.writerow([])
    writer.writerow(["VIAGENS"])
    writer.writerow(["Início", "Fim", "Distância km", "Duração min", "Velocidade máxima", "Origem", "Destino"])
    for trip in trips_data:
        writer.writerow([trip["start_time"], trip["end_time"], trip["distance_km"], trip["duration_minutes"], trip["max_speed"], (trip.get("start_geofence") or {}).get("name"), (trip.get("end_geofence") or {}).get("name")])
    writer.writerow([])
    writer.writerow(["PARADAS"])
    writer.writerow(["Início", "Fim", "Duração min", "Local", "Ignição ligada"])
    for stop in stops_data:
        writer.writerow([stop["start_time"], stop["end_time"], stop["duration_minutes"], (stop.get("geofence") or {}).get("name"), stop["ignition_on"]])
    writer.writerow([])
    writer.writerow(["OCORRÊNCIAS DE USO"])
    writer.writerow(["Data", "Tipo", "Severidade", "Descrição"])
    for item in violations_data:
        writer.writerow([item["time"], item["type"], item["severity"], item["description"]])
    output.seek(0)
    filename = f"relatorio_{identifier}_{date_from}_{date_to}.csv"
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
