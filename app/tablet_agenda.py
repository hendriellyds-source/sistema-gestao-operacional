"""Tablet notebook: durable ink, review, and idempotent schedule publication."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .access_control import SYSTEM_STATE_ROLES, require_roles
from .system_state import _connect, get_system_state, init_system_state


router = APIRouter(prefix="/api/tablet-agenda", tags=["Agenda do tablet"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_day(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(422, "Data inválida.") from exc


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(422, "Identificador inválido.") from exc


def _init() -> None:
    init_system_state()
    with _connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tablet_ink (
                id TEXT PRIMARY KEY,
                day TEXT NOT NULL,
                points_json TEXT NOT NULL,
                color TEXT NOT NULL,
                width REAL NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tablet_ink_day ON tablet_ink(day);
            CREATE TABLE IF NOT EXISTS tablet_review (
                id TEXT PRIMARY KEY,
                day TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                text_original TEXT NOT NULL,
                team_id INTEGER,
                company_id INTEGER,
                work_id INTEGER,
                scheduled_time TEXT,
                daily_type TEXT NOT NULL DEFAULT 'Inteira',
                observation TEXT NOT NULL DEFAULT '',
                schedule_id INTEGER,
                unregistered_work_text TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tablet_review_day ON tablet_review(day);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tablet_review_line ON tablet_review(day,line_number);
            CREATE TABLE IF NOT EXISTS tablet_alias (
                alias TEXT NOT NULL,
                kind TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                PRIMARY KEY(alias,kind,target_id)
            );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(tablet_review)")}
        if "unregistered_work_text" not in columns:
            db.execute("ALTER TABLE tablet_review ADD COLUMN unregistered_work_text TEXT NOT NULL DEFAULT ''")
        if "extra_teams_json" not in columns:
            db.execute("ALTER TABLE tablet_review ADD COLUMN extra_teams_json TEXT NOT NULL DEFAULT '[]'")
        if "include_financial" not in columns:
            db.execute("ALTER TABLE tablet_review ADD COLUMN include_financial INTEGER NOT NULL DEFAULT 1")
        if "price_override" not in columns:
            db.execute("ALTER TABLE tablet_review ADD COLUMN price_override REAL")
        db.execute("""CREATE TABLE IF NOT EXISTS tablet_schedule_tombstone (
            review_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL
        )""")
        _repair_zero_line_prices(db)


def _repair_zero_line_prices(db) -> None:
    """Recover tablet prices lost by the former slash-only price parser."""
    db.execute("""CREATE TABLE IF NOT EXISTS tablet_migration (
        key TEXT PRIMARY KEY, applied_at TEXT NOT NULL
    )""")
    if db.execute("SELECT 1 FROM tablet_migration WHERE key='spaced-line-prices-v1'").fetchone():
        return
    db.execute("BEGIN IMMEDIATE")
    state_row = db.execute("SELECT * FROM system_state WHERE id=1").fetchone()
    if state_row:
        state = json.loads(state_row["state_json"])
        entries = {str(item.get("id")): item for item in state.get("workScheduleEntries") or []}
        works = {str(item.get("id")): item for item in state.get("works") or []}
        invoiced = {str(item_id) for invoice in state.get("invoices") or []
                    for item_id in invoice.get("scheduleEntryIds") or []}
        for review in db.execute("""SELECT id,schedule_id,text_original,unregistered_work_text,daily_type,price_override
                                  FROM tablet_review WHERE schedule_id IS NOT NULL""").fetchall():
            entry = entries.get(str(review["schedule_id"]))
            price = _parse_line_price(review["text_original"])
            if (not entry or not price or review["price_override"] is not None
                    or str(entry.get("id")) in invoiced or float(entry.get("value") or 0) != 0):
                continue
            mode, amount = price
            work = works.get(str(entry.get("workId")))
            base = float((work or {}).get("halfDailyRate" if review["daily_type"] == "Meia" else "dailyRate") or 0)
            if review["daily_type"] == "Quarto":
                base = float((work or {}).get("dailyRate") or 0) / 4
            entry["value"] = round(amount if mode == "total" else base + amount, 2)
            entry["financialPending"] = False
            entry["tabletPriceMode"] = mode
            entry["tabletPriceAdjustment"] = amount
            old_name = review["unregistered_work_text"] or ""
            price_suffix = re.search(r"(?:/|\s+)(\+?\s*(?:R\$\s*)?\d[\d.,]*)\s*$",
                                     review["text_original"], re.I)
            cleaned_name = (re.sub(r"(?:/|\s+)\+?\s*(?:R\$\s*)?\d[\d.,]*\s*$", "", old_name, flags=re.I).strip()
                            if price_suffix and old_name.endswith(price_suffix.group(1).strip()) else old_name)
            if old_name and cleaned_name:
                entry["tabletWorkText"] = cleaned_name
                db.execute("UPDATE tablet_review SET unregistered_work_text=? WHERE id=?", (cleaned_name, review["id"]))
        _write_schedule_state(db, state_row, state, {"profile_id": "tablet-price-repair"})
    db.execute("INSERT INTO tablet_migration(key,applied_at) VALUES (?,?)", ("spaced-line-prices-v1", _now()))


class Stroke(BaseModel):
    id: str
    points: list[tuple[float, float]] = Field(min_length=1, max_length=2000)
    color: str = Field(default="#17394b", pattern=r"^#[0-9a-fA-F]{6}$")
    width: float = Field(default=2.7, ge=0.5, le=15)


class Review(BaseModel):
    id: str
    line_number: int = Field(ge=0, le=19)
    text_original: str = Field(default="", max_length=500)
    team_id: int | None = None
    company_id: int | None = None
    work_id: int | None = None
    scheduled_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_type: str = Field(default="Inteira", pattern=r"^(Inteira|Meia|Quarto)$")
    observation: str = Field(default="", max_length=1000)
    unregistered_work_text: str = Field(default="", max_length=200)
    extra_teams: list[dict] = Field(default_factory=list, max_length=12)
    include_financial: bool = True
    price_override: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Alias(BaseModel):
    alias: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(team|company|work)$")
    target_id: int


class ScheduleEdit(BaseModel):
    company_id: int | None = None
    work_id: int | None = None
    value: float = Field(ge=0, allow_inf_nan=False)
    status: str = Field(pattern=r"^(Planejado|Confirmado|Cancelado)$")
    include_financial: bool = True
    scheduled_time: str = Field(default="", pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$")
    observation: str = Field(default="", max_length=1000)


def _catalog() -> dict:
    data = get_system_state().get("data") or {}
    return {
        "teams": data.get("workTeams") or [],
        "companies": data.get("companies") or [],
        "works": data.get("works") or [],
    }


@router.get("/search")
def search_schedule(request: Request, q: str = Query(min_length=2, max_length=120)) -> dict:
    require_roles(request, SYSTEM_STATE_ROLES)
    state = get_system_state().get("data") or {}
    works = {str(item.get("id")): item for item in state.get("works") or []}
    companies = {str(item.get("id")): item for item in state.get("companies") or []}
    teams = {str(item.get("id")): item for item in state.get("workTeams") or []}
    needle = _fold(q.strip())
    if len(needle) < 2:
        raise HTTPException(422, "Digite ao menos duas letras.")
    matches = []
    for entry in state.get("workScheduleEntries") or []:
        if entry.get("appointmentExtraTeam"):
            continue
        work = works.get(str(entry.get("workId")))
        company = companies.get(str(entry.get("companyId") or (work or {}).get("companyId")))
        work_name = (work or {}).get("name") or entry.get("tabletWorkText") or entry.get("historicalWorkText") or "Obra não cadastrada"
        company_name = (company or {}).get("name") or "Empresa não informada"
        if not any(needle in _fold(value) for value in (work_name, company_name)):
            continue
        matches.append({
            "id": entry.get("id"), "date": entry.get("date"), "work": work_name,
            "company": company_name, "team": (teams.get(str(entry.get("teamId"))) or {}).get("name") or "Equipe não informada",
            "company_id": entry.get("companyId") or (work or {}).get("companyId"),
            "work_id": entry.get("workId") or None,
            "value": entry.get("value") or 0, "status": entry.get("status") or "Planejado",
            "include_financial": entry.get("generateFinancial") is not False,
            "scheduled_time": entry.get("scheduledTime") or "",
            "observation": entry.get("observation") or "",
            "financial_pending": bool(entry.get("financialPending")),
        })
    matches.sort(key=lambda item: (str(item["date"]), int(item["id"] or 0)), reverse=True)
    return {"items": matches, "days": len({item["date"] for item in matches})}


@router.patch("/schedule/{entry_id}")
def edit_schedule_entry(entry_id: int, payload: ScheduleEdit, request: Request) -> dict:
    user = require_roles(request, SYSTEM_STATE_ROLES)
    _init()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        state_row = db.execute("SELECT * FROM system_state WHERE id=1").fetchone()
        if not state_row:
            raise HTTPException(404, "Agenda não encontrada.")
        state = json.loads(state_row["state_json"])
        entries = state.get("workScheduleEntries") or []
        selected = next((item for item in entries if int(item.get("id") or 0) == entry_id), None)
        if not selected:
            raise HTTPException(404, "Serviço não encontrado.")
        group_id = int(selected.get("appointmentGroupId") or selected["id"])
        group = [item for item in entries if int(item.get("appointmentGroupId") or item.get("id") or 0) == group_id]
        entry = next((item for item in group if not item.get("appointmentExtraTeam")), selected)
        if entry.get("historicalPending"):
            raise HTTPException(409, "Revise o lançamento histórico no sistema antes de editá-lo.")
        company = next((item for item in state.get("companies") or []
                        if str(item.get("id")) == str(payload.company_id)), None)
        work = next((item for item in state.get("works") or []
                     if str(item.get("id")) == str(payload.work_id)), None)
        if not company or (payload.work_id is not None and
                           (not work or str(work.get("companyId")) != str(company.get("id")))):
            raise HTTPException(422, "Escolha uma empresa e uma obra vinculada a ela.")
        if not work and not entry.get("registrationPending"):
            raise HTTPException(422, "Selecione a obra para este serviço.")
        if work and any(item not in group and item.get("date") == entry.get("date")
                        and item.get("status") != "Cancelado" and item.get("type") == "Obra"
                        and str(item.get("workId")) == str(work["id"])
                        and str(item.get("teamId")) == str(entry.get("teamId"))
                        for item in entries):
            raise HTTPException(409, "Esta equipe já tem a obra agendada nesta data.")
        if payload.include_financial and entry.get("registrationPending") and payload.value <= 0:
            raise HTTPException(422, "Informe um valor maior que zero para faturar esta obra.")
        changed = (round(float(entry.get("value") or 0), 2) != round(payload.value, 2)
                   or str(entry.get("companyId") or "") != str(payload.company_id)
                   or str(entry.get("workId") or "") != str(payload.work_id or "")
                   or entry.get("status") != payload.status
                   or (entry.get("generateFinancial") is not False) != payload.include_financial
                   or (entry.get("scheduledTime") or "") != payload.scheduled_time
                   or (entry.get("observation") or "") != payload.observation)
        group_ids = {str(item.get("id")) for item in group}
        if changed and any(group_ids.intersection(map(str, invoice.get("scheduleEntryIds") or []))
                           for invoice in state.get("invoices") or []):
            raise HTTPException(409, "Este serviço já está em uma cobrança. Retire-o da cobrança antes de editar.")
        if payload.status == "Confirmado" and entry.get("status") != "Confirmado":
            blocked = {"Rascunho", "Solicitada", "Aguardando RH", "Recusada", "Cancelada"}
            for member in group:
                team = next((item for item in state.get("workTeams") or []
                             if str(item.get("id")) == str(member.get("teamId"))), {})
                for vacation in state.get("vacations") or []:
                    if (vacation.get("status") not in blocked
                            and vacation.get("employeeId") in (team.get("employeeIds") or [])
                            and str(vacation.get("start") or "") <= str(member.get("date") or "") <= str(vacation.get("end") or "")):
                        raise HTTPException(409, "Há funcionário da equipe em férias nesta data.")
        entry["value"] = round(payload.value, 2)
        entry["scheduledTime"] = payload.scheduled_time
        entry["observation"] = payload.observation
        for member in group:
            member["companyId"] = company["id"]
            member["workId"] = work["id"] if work else ""
            member["registrationPending"] = not bool(work)
            member["billingEntityId"] = (work or {}).get("billingEntityId") or ""
            if work:
                member["location"] = work.get("location") or ""
                member["tabletWorkText"] = ""
            member["status"] = payload.status
            member["generateFinancial"] = payload.include_financial
            member["financialPending"] = bool(member.get("registrationPending") and member is entry and payload.value <= 0)
        if entry.get("tabletAgendaId"):
            review_note = payload.observation.split(" | Agenda tablet:", 1)[0]
            db.execute("""UPDATE tablet_review SET company_id=?,work_id=?,unregistered_work_text=?,
                price_override=?,include_financial=?,scheduled_time=?,observation=? WHERE id=?""",
                       (company["id"], work["id"] if work else None,
                        "" if work else entry.get("tabletWorkText") or "", payload.value,
                        int(payload.include_financial), payload.scheduled_time, review_note,
                        str(entry["tabletAgendaId"])))
        _write_schedule_state(db, state_row, state, user)
    return {"ok": True, "id": entry.get("id")}


@router.get("/catalog")
def catalog(request: Request) -> dict:
    require_roles(request, SYSTEM_STATE_ROLES)
    _init()
    with _connect() as db:
        aliases = [dict(row) for row in db.execute(
            "SELECT alias,kind,target_id FROM tablet_alias ORDER BY alias"
        )]
    return {**_catalog(), "aliases": aliases}


@router.get("/pages/{day}")
def read_page(day: str, request: Request) -> dict:
    require_roles(request, SYSTEM_STATE_ROLES)
    day = _valid_day(day)
    _init()
    with _connect() as db:
        strokes = [{**dict(row), "points": json.loads(row["points_json"])} for row in db.execute(
            "SELECT * FROM tablet_ink WHERE day=? ORDER BY created_at,id", (day,)
        )]
        reviews = [dict(row) for row in db.execute(
            "SELECT * FROM tablet_review WHERE day=? ORDER BY updated_at,id", (day,)
        )]
        for review in reviews:
            review["extra_teams"] = json.loads(review.pop("extra_teams_json", "[]") or "[]")
    for stroke in strokes:
        del stroke["points_json"]
    state = get_system_state().get("data") or {}
    entries = [item for item in state.get("workScheduleEntries") or [] if item.get("date") == day]
    clients = {str(item.get("id")): item for item in state.get("projectClients") or []}
    requests = [
        {"id": item.get("id"), "client_name": clients.get(str(item.get("clientId")), {}).get("name", "Cliente"),
         "service_type": clients.get(str(item.get("clientId")), {}).get("serviceType", ""),
         "location": clients.get(str(item.get("clientId")), {}).get("landLocation", ""),
         "requested_at": item.get("requestedAt", "")}
        for item in state.get("projectScheduleRequests") or [] if item.get("status") == "Pendente"
    ]
    return {"day": day, "strokes": strokes, "reviews": reviews,
            "schedule_entries": entries, "pending_requests": requests}


@router.post("/pages/{day}/strokes")
def add_stroke(day: str, stroke: Stroke, request: Request) -> dict:
    user = require_roles(request, SYSTEM_STATE_ROLES)
    day = _valid_day(day)
    stroke_id = _uuid(stroke.id)
    if any(x < 0 or x > 1000 or y < 0 or y > 2000 for x, y in stroke.points):
        raise HTTPException(422, "Traço fora da página.")
    _init()
    with _connect() as db:
        db.execute("""INSERT OR IGNORE INTO tablet_ink
            (id,day,points_json,color,width,created_by,created_at)
            VALUES (?,?,?,?,?,?,?)""", (
            stroke_id, day, json.dumps(stroke.points), stroke.color, stroke.width,
            str(user.get("profile_id") or user.get("auth_user_id") or ""), _now(),
        ))
    return {"ok": True, "id": stroke_id}


@router.delete("/pages/{day}/strokes/{stroke_id}")
def delete_stroke(day: str, stroke_id: str, request: Request) -> dict:
    require_roles(request, SYSTEM_STATE_ROLES)
    day = _valid_day(day)
    stroke_id = _uuid(stroke_id)
    _init()
    with _connect() as db:
        db.execute("UPDATE tablet_ink SET deleted=1 WHERE id=? AND day=?", (stroke_id, day))
    return {"ok": True}


@router.put("/pages/{day}/reviews")
def save_review(day: str, payload: Review, request: Request) -> dict:
    user = require_roles(request, SYSTEM_STATE_ROLES)
    day = _valid_day(day)
    review_id = _uuid(payload.id)
    if not payload.work_id and _parse_line_price(payload.text_original):
        suffix = re.search(r"(?:/|\s+)(\+?\s*(?:R\$\s*)?\d[\d.,]*)\s*$",
                           payload.text_original, re.I)
        if suffix and payload.unregistered_work_text.strip().endswith(suffix.group(1).strip()):
            payload.unregistered_work_text = re.sub(
                r"(?:/|\s+)\+?\s*(?:R\$\s*)?\d[\d.,]*\s*$", "",
                payload.unregistered_work_text, flags=re.I).strip()
    _init()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT schedule_id,price_override,include_financial,text_original FROM tablet_review WHERE id=? AND day=?",
                              (review_id, day)).fetchone()
        db.execute("""INSERT INTO tablet_review
            (id,day,line_number,text_original,team_id,company_id,work_id,scheduled_time,daily_type,observation,unregistered_work_text,extra_teams_json,include_financial,price_override,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            text_original=excluded.text_original,team_id=excluded.team_id,
            company_id=excluded.company_id,work_id=excluded.work_id,
            scheduled_time=excluded.scheduled_time,daily_type=excluded.daily_type,
            observation=excluded.observation,unregistered_work_text=excluded.unregistered_work_text,
            extra_teams_json=excluded.extra_teams_json,
            include_financial=excluded.include_financial,price_override=excluded.price_override,
            updated_at=excluded.updated_at""", (
            review_id, day, payload.line_number, payload.text_original, payload.team_id, payload.company_id,
            payload.work_id, payload.scheduled_time, payload.daily_type,
            payload.observation, payload.unregistered_work_text.strip(),
            json.dumps(payload.extra_teams, ensure_ascii=False), int(payload.include_financial), payload.price_override, _now(),
        ))
        state_row = db.execute("SELECT * FROM system_state WHERE id=1").fetchone()
        if not state_row:
            return {"ok": True, "id": review_id, "schedule_id": None}
        state = json.loads(state_row["state_json"])
        entries = state.setdefault("workScheduleEntries", [])
        schedule_id = existing["schedule_id"] if existing else None
        entry = next((item for item in entries if str(item.get("id")) == str(schedule_id)), None)
        blank = not payload.text_original.strip() and not payload.unregistered_work_text.strip()
        if blank:
            if entry:
                group_id = int(entry.get("appointmentGroupId") or entry.get("id") or 0)
                grouped = [item for item in entries if int(item.get("appointmentGroupId") or item.get("id") or 0) == group_id]
                group_ids = {str(item.get("id")) for item in grouped}
                if any(group_ids.intersection(str(value) for value in (invoice.get("scheduleEntryIds") or []))
                       for invoice in state.get("invoices") or []):
                    raise HTTPException(409, "Este agendamento já está em uma cobrança. Retire-o da cobrança antes de apagar a linha.")
                entries[:] = [item for item in entries if str(item.get("id")) not in group_ids]
            db.execute("UPDATE tablet_review SET schedule_id=NULL WHERE id=?", (review_id,))
            db.execute("INSERT OR REPLACE INTO tablet_schedule_tombstone(review_id,deleted_at) VALUES (?,?)",
                       (review_id, _now()))
            schedule_id = None
        elif payload.work_id or payload.unregistered_work_text.strip():
            teams = state.get("workTeams") or []
            works = state.get("works") or []
            companies = state.get("companies") or []
            team = next((x for x in teams if str(x.get("id")) == str(payload.team_id)), None)
            work = next((x for x in works if str(x.get("id")) == str(payload.work_id)), None)
            company_id = work.get("companyId") if work else payload.company_id
            company = next((x for x in companies if str(x.get("id")) == str(company_id)), None)
            linked_existing = False
            if not entry and team:
                candidates = [item for item in entries
                              if item.get("date") == day and item.get("type") == "Obra"
                              and item.get("status") != "Cancelado"
                              and not item.get("tabletAgendaId")
                              and str(item.get("teamId")) == str(team["id"])
                              and ((work and str(item.get("workId")) == str(work["id"]))
                                   or (not work and str(item.get("tabletWorkText") or "").casefold()
                                       == payload.unregistered_work_text.strip().casefold()))]
                if len(candidates) == 1:
                    entry = candidates[0]
                    schedule_id = entry["id"]
                    linked_existing = True
            if not entry:
                schedule_id = max(time.time_ns() // 1000,
                                  max((int(x.get("id") or 0) for x in entries), default=0) + 1)
                entry = {"id": schedule_id, "status": "Planejado"}
                entries.append(entry)
            rate_key = "halfDailyRate" if payload.daily_type == "Meia" else "dailyRate"
            normal_value = float(work.get(rate_key) or 0) if work else 0
            if payload.daily_type == "Quarto" and work:
                normal_value = float(work.get("dailyRate") or 0) / 4
            line_price = _parse_line_price(payload.text_original)
            if line_price:
                price_mode, price_amount = line_price
                normal_value = price_amount if price_mode == "total" else normal_value + price_amount
            if payload.price_override is not None:
                normal_value = payload.price_override
            tablet_price_unchanged = (existing is not None
                and existing["price_override"] == payload.price_override
                and existing["text_original"] == payload.text_original)
            preserve_value = (linked_existing or (entry.get("tabletAgendaId") == review_id
                and str(entry.get("workId") or "") == str(work.get("id") if work else "")
                and entry.get("dailyType") == payload.daily_type)) and (
                    tablet_price_unchanged or (not line_price and payload.price_override is None and not entry.get("tabletPriceMode")))
            proposed_value = entry.get("value", 0) if preserve_value else normal_value
            include_financial = (entry.get("generateFinancial", payload.include_financial)
                if existing is not None and bool(existing["include_financial"]) == payload.include_financial
                else payload.include_financial)
            if any(str(entry.get("id")) == str(value)
                   for invoice in state.get("invoices") or []
                   for value in (invoice.get("scheduleEntryIds") or [])) and (
                round(float(entry.get("value") or 0), 2) != round(float(proposed_value or 0), 2)
                or include_financial is False
            ):
                raise HTTPException(409, "Este serviço já está em uma cobrança. Retire-o da cobrança antes de alterar valor ou faturamento.")
            entry.update({
                "date": day, "teamId": team["id"] if team else "", "type": "Obra",
                "companyId": company["id"] if company else "", "workId": work["id"] if work else "",
                "dailyType": payload.daily_type,
                "value": proposed_value,
                "billingEntityId": entry.get("billingEntityId", ""),
                "client": entry.get("client", ""),
                "location": (work.get("location") or "") if work else "",
                "serviceType": "", "scheduledTime": payload.scheduled_time or "",
                "observation": " | ".join(filter(None, [payload.observation,
                    f"Agenda tablet: {payload.text_original}"])),
                "tabletAgendaId": review_id,
                "tabletWorkText": "" if work else payload.unregistered_work_text.strip(),
                "registrationPending": not bool(work),
                "financialPending": not bool(work) and float(proposed_value or 0) <= 0,
                "generateFinancial": include_financial,
                "tabletPriceMode": line_price[0] if line_price else "",
                "tabletPriceAdjustment": line_price[1] if line_price else 0,
            })
            db.execute("UPDATE tablet_review SET schedule_id=? WHERE id=?", (schedule_id, review_id))
            db.execute("DELETE FROM tablet_schedule_tombstone WHERE review_id=?", (review_id,))
            group_id = int(entry.get("appointmentGroupId") or entry["id"])
            entry["appointmentGroupId"] = group_id
            entry["appointmentExtraTeam"] = False
            selected_extras = {}
            for item in payload.extra_teams:
                try:
                    extra_team_id = int(item.get("team_id"))
                except (TypeError, ValueError):
                    continue
                if extra_team_id == int(team["id"] if team else -1):
                    continue
                extra_team = next((candidate for candidate in teams
                                   if int(candidate.get("id", -1)) == extra_team_id), None)
                if extra_team:
                    selected_extras[extra_team_id] = (extra_team, str(item.get("amount") or "").strip())
            existing_extras = [item for item in entries
                               if item.get("appointmentExtraTeam")
                               and int(item.get("appointmentGroupId") or 0) == group_id]
            keep_extra_ids = set()
            for extra_team_id, (extra_team, amount_text) in selected_extras.items():
                if not amount_text:
                    continue
                charge = _parse_extra_charge(amount_text, work)
                if charge <= 0:
                    raise HTTPException(422, f"Informe um adicional válido para {extra_team.get('name', 'a equipe')}; use valor em reais ou fração, como 1/4.")
                linked = next((item for item in existing_extras
                               if str(item.get("teamId")) == str(extra_team_id)), None)
                if linked and round(float(linked.get("value") or 0), 2) != round(charge, 2) and any(
                    str(linked.get("id")) == str(value)
                    for invoice in state.get("invoices") or []
                    for value in (invoice.get("scheduleEntryIds") or [])
                ):
                    raise HTTPException(409, "O adicional desta equipe já está vinculado a uma cobrança e não pode ser alterado.")
                if not linked and any(candidate.get("date") == day
                    and candidate.get("type") == "Obra" and candidate.get("status") != "Cancelado"
                    and str(candidate.get("teamId")) == str(extra_team_id)
                    and str(candidate.get("workId")) == str(work.get("id") if work else "")
                    for candidate in entries):
                    raise HTTPException(409, f"A equipe {extra_team.get('name', 'adicional')} já tem esta obra agendada neste dia.")
                extra_id = linked["id"] if linked else max(time.time_ns() // 1000,
                    max((int(candidate.get("id") or 0) for candidate in entries), default=0) + 1)
                extra_entry = linked or {"id": extra_id, "status": entry.get("status", "Planejado")}
                extra_entry.update({
                    "date": day, "teamId": extra_team_id, "type": "Obra",
                    "companyId": company["id"] if company else "",
                    "workId": work["id"] if work else "",
                    "dailyType": payload.daily_type, "value": charge,
                    "billingEntityId": entry.get("billingEntityId", ""),
                    "client": "", "location": work.get("location") or "" if work else "",
                    "serviceType": "", "scheduledTime": payload.scheduled_time or "",
                    "observation": " | ".join(filter(None, [payload.observation,
                        f"Equipe de apoio de {extra_team.get('name', '')}",
                        f"Agenda tablet: {payload.text_original}"])),
                    "tabletAgendaId": review_id, "appointmentGroupId": group_id,
                    "appointmentExtraTeam": True,
                    "generateFinancial": include_financial,
                })
                if not linked:
                    entries.append(extra_entry)
                keep_extra_ids.add(int(extra_id))
            removed_extras = [item for item in existing_extras
                              if int(item.get("id") or 0) not in keep_extra_ids]
            blocked = [item for item in removed_extras
                       if any(str(item.get("id")) == str(value)
                              for invoice in state.get("invoices") or []
                              for value in (invoice.get("scheduleEntryIds") or []))]
            if blocked:
                raise HTTPException(409, "Uma equipe adicional está vinculada a uma cobrança. Retire-a da cobrança antes de alterar as equipes.")
            if removed_extras:
                removed_ids = {item["id"] for item in removed_extras}
                entries[:] = [item for item in entries if item.get("id") not in removed_ids]
        if blank or payload.work_id or payload.unregistered_work_text.strip():
            _write_schedule_state(db, state_row, state, user)
    return {"ok": True, "id": review_id, "schedule_id": schedule_id}


@router.post("/pages/{day}/publish-pending")
def publish_pending_reviews(day: str, request: Request) -> dict:
    """Bring reviews saved by older clients into the shared schedule."""
    require_roles(request, SYSTEM_STATE_ROLES)
    day = _valid_day(day)
    _init()
    with _connect() as db:
        rows = [dict(row) for row in db.execute("""
            SELECT * FROM tablet_review WHERE day=? AND schedule_id IS NULL
            AND (TRIM(text_original) != '' OR TRIM(unregistered_work_text) != '')
            ORDER BY line_number
        """, (day,))]
        aliases = [dict(row) for row in db.execute("SELECT alias FROM tablet_alias")]
    known = {_fold(str(item.get("name") or ""))
             for group in _catalog().values() for item in group}
    known.update(_fold(item["alias"]) for item in aliases)
    published = 0
    for row in rows:
        if not row["work_id"] and not row["unregistered_work_text"] and row["team_id"]:
            tokens = [part.strip() for part in row["text_original"].split("/") if part.strip()]
            unknown = [part for index, part in enumerate(tokens)
                       if _fold(part) not in known
                       and not re.fullmatch(r"\d{1,2}[:h.]\d{2}|meia|inteira", _fold(part))
                       and not (index == 0 and len(part) <= 3)]
            if len(tokens) >= 2 and len(unknown) == 1:
                row["unregistered_work_text"] = unknown[0][:200]
        if not row["work_id"] and not row["unregistered_work_text"]:
            continue
        result = save_review(day, Review.model_validate(row), request)
        if result["schedule_id"] is not None:
            published += 1
    return {"ok": True, "published": published}


def _write_schedule_state(db, state_row, state: dict, user: dict) -> None:
    serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    if digest == state_row["state_hash"]:
        return
    timestamp = _now()
    db.execute("""INSERT INTO system_state_history
        (state_json,state_hash,revision,source,updated_by,created_at)
        VALUES (?,?,?,?,?,?)""", (
        state_row["state_json"], state_row["state_hash"], state_row["revision"],
        state_row["source"], state_row["updated_by"], state_row["updated_at"],
    ))
    db.execute("""UPDATE system_state SET state_json=?,state_hash=?,revision=?,
        source=?,updated_by=?,updated_at=? WHERE id=1""", (
        serialized, digest, int(state_row["revision"]) + 1,
        "agenda-tablet", str(user.get("profile_id") or ""), timestamp,
    ))
    db.execute("""DELETE FROM system_state_history WHERE id NOT IN
        (SELECT id FROM system_state_history ORDER BY revision DESC LIMIT 15)""")


@router.post("/aliases")
def add_alias(payload: Alias, request: Request) -> dict:
    require_roles(request, SYSTEM_STATE_ROLES)
    names = {"team": "teams", "company": "companies", "work": "works"}
    if not any(int(item.get("id", -1)) == payload.target_id for item in _catalog()[names[payload.kind]]):
        raise HTTPException(422, "Cadastro não encontrado.")
    alias = " ".join(payload.alias.strip().split()).casefold()
    _init()
    with _connect() as db:
        existing = db.execute("SELECT target_id FROM tablet_alias WHERE alias=? AND kind=?",
                              (alias, payload.kind)).fetchone()
        if existing and existing["target_id"] != payload.target_id:
            raise HTTPException(409, "Esta sigla já está vinculada a outro cadastro deste tipo.")
        db.execute("INSERT OR IGNORE INTO tablet_alias(alias,kind,target_id) VALUES (?,?,?)",
                   (alias, payload.kind, payload.target_id))
    return {"ok": True}


@router.delete("/aliases")
def delete_alias(payload: Alias, request: Request) -> dict:
    require_roles(request, SYSTEM_STATE_ROLES)
    alias = " ".join(payload.alias.strip().split()).casefold()
    _init()
    with _connect() as db:
        db.execute("DELETE FROM tablet_alias WHERE alias=? AND kind=? AND target_id=?",
                   (alias, payload.kind, payload.target_id))
    return {"ok": True}


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _parse_extra_charge(value: str, work: dict | None) -> float:
    raw = str(value or "").strip().replace("R$", "").replace(" ", "")
    fraction = re.fullmatch(r"(\d+(?:[,.]\d+)?)\s*/\s*(\d+(?:[,.]\d+)?)", raw)
    if fraction:
        numerator = float(fraction.group(1).replace(",", "."))
        denominator = float(fraction.group(2).replace(",", "."))
        daily_rate = float((work or {}).get("dailyRate") or 0)
        return daily_rate * numerator / denominator if denominator > 0 and daily_rate > 0 else 0
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return 0


def _parse_line_price(text: str) -> tuple[str, float] | None:
    if re.search(r"/\s*1\s*/\s*[24]\s*$", str(text or "")):
        return None
    match = re.search(r"(?:/|\s+)(\+?\s*(?:R\$\s*)?\d[\d.,]*)\s*$", str(text or ""), re.I)
    if not match:
        return None
    raw = re.sub(r"\s+", "", match.group(1)).replace("R$", "")
    mode = "surcharge" if raw.startswith("+") else "total"
    if mode == "surcharge":
        raw = raw[1:]
    if not re.fullmatch(r"\d+(?:[.,]\d{1,2}|\.\d{3})*(?:,\d{1,2})?", raw):
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return (mode, amount) if amount > 0 else None


@router.get("/interpret")
def interpret(text: str, request: Request) -> dict:
    require_roles(request, SYSTEM_STATE_ROLES)
    text = text[:500]
    normalized = _fold(text)
    catalog_data = _catalog()
    _init()
    with _connect() as db:
        aliases = [dict(row) for row in db.execute("SELECT * FROM tablet_alias")]
    matches = {"team": [], "company": [], "work": []}
    for kind, key in (("team", "teams"), ("company", "companies"), ("work", "works")):
        for item in catalog_data[key]:
            names = [item.get("name", "")]
            names += [a["alias"] for a in aliases if a["kind"] == kind and a["target_id"] == item.get("id")]
            if any(name and re.search(r"(?<!\w)" + re.escape(_fold(name)) + r"(?!\w)", normalized) for name in names):
                matches[kind].append({"id": item["id"], "name": item.get("name", "")})
    if len(matches["work"]) == 1:
        work = next((item for item in catalog_data["works"]
                     if item.get("id") == matches["work"][0]["id"]), None)
        related = next((item for item in catalog_data["companies"]
                        if work and str(item.get("id")) == str(work.get("companyId"))), None)
        if related and not matches["company"]:
            matches["company"] = [{"id": related["id"], "name": related.get("name", "")}]
    hour = re.search(r"(?<!\d)([01]?\d|2[0-3])[:h.]([0-5]\d)(?!\d)", normalized)
    quarter_day = bool(re.search(r"(?<!\d)1\s*/\s*4(?!\d)|(?<!\w)quarto(?!\w)", normalized))
    half_day = bool(re.search(r"(?<!\w)meia(?!\w)|(?<!\d)1\s*/\s*2(?!\d)", normalized))
    return {
        "matches": matches,
        "scheduled_time": f"{int(hour[1]):02d}:{hour[2]}" if hour else None,
        "daily_type": "Quarto" if quarter_day else "Meia" if half_day else "Inteira",
    }


@router.post("/pages/{day}/reviews/{review_id}/confirm")
def confirm_review(day: str, review_id: str, request: Request) -> dict:
    user = require_roles(request, SYSTEM_STATE_ROLES)
    day = _valid_day(day)
    review_id = _uuid(review_id)
    _init()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        review = db.execute("SELECT * FROM tablet_review WHERE id=? AND day=?", (review_id, day)).fetchone()
        if not review:
            raise HTTPException(404, "Linha não encontrada.")
        if review["schedule_id"]:
            return {"ok": True, "schedule_id": review["schedule_id"], "already_confirmed": True}
        state_row = db.execute("SELECT * FROM system_state WHERE id=1").fetchone()
        if not state_row:
            raise HTTPException(409, "Cadastros ainda não disponíveis no servidor.")
        state = json.loads(state_row["state_json"])
        teams = state.get("workTeams") or []
        companies = state.get("companies") or []
        works = state.get("works") or []
        team = next((x for x in teams if x.get("id") == review["team_id"]), None)
        company = next((x for x in companies if x.get("id") == review["company_id"]), None)
        work = next((x for x in works if x.get("id") == review["work_id"]), None)
        if not all((team, company, work)) or str(work.get("companyId")) != str(company.get("id")):
            raise HTTPException(422, "Confira equipe, empresa e obra antes de confirmar.")
        entries = state.setdefault("workScheduleEntries", [])
        if any(
            str(entry.get("teamId")) == str(team["id"])
            and str(entry.get("workId")) == str(work["id"])
            and entry.get("date") == day
            and entry.get("type") == "Obra"
            for entry in entries
        ):
            raise HTTPException(409, "Esta equipe e obra já têm um agendamento nesta data.")
        # Microsecond timestamps are exact JavaScript integers through 2255.
        # They keep an old workspace tab's next sequential ID from colliding.
        schedule_id = max(time.time_ns() // 1000,
                          max((int(x.get("id") or 0) for x in entries), default=0) + 1)
        value_key = "halfDailyRate" if review["daily_type"] == "Meia" else "dailyRate"
        value = float(work.get(value_key) or 0)
        if review["daily_type"] == "Quarto":
            value = float(work.get("dailyRate") or 0) / 4
        line_price = _parse_line_price(review["text_original"])
        if line_price:
            value = line_price[1] if line_price[0] == "total" else value + line_price[1]
        if review["price_override"] is not None:
            value = float(review["price_override"])
        entry = {
            "id": schedule_id, "date": day, "teamId": team["id"], "type": "Obra",
            "companyId": company["id"], "workId": work["id"],
            "dailyType": review["daily_type"], "value": value,
            "status": "Planejado", "billingEntityId": "", "client": "",
            "generateFinancial": bool(review["include_financial"]),
            "location": work.get("location") or "", "serviceType": "",
            "observation": " | ".join(filter(None, [review["observation"],
                f"Agenda tablet: {review['text_original']}"])),
            "scheduledTime": review["scheduled_time"] or "",
            "tabletAgendaId": review_id,
            "tabletPriceMode": line_price[0] if line_price else "",
            "tabletPriceAdjustment": line_price[1] if line_price else 0,
        }
        entries.append(entry)
        serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        timestamp = _now()
        db.execute("""INSERT INTO system_state_history
            (state_json,state_hash,revision,source,updated_by,created_at)
            VALUES (?,?,?,?,?,?)""", (
            state_row["state_json"], state_row["state_hash"], state_row["revision"],
            state_row["source"], state_row["updated_by"], state_row["updated_at"],
        ))
        db.execute("""UPDATE system_state SET state_json=?,state_hash=?,revision=?,
            source=?,updated_by=?,updated_at=? WHERE id=1""", (
            serialized, digest, int(state_row["revision"]) + 1,
            "agenda-tablet", str(user.get("profile_id") or ""), timestamp,
        ))
        db.execute("UPDATE tablet_review SET schedule_id=?,updated_at=? WHERE id=?",
                   (schedule_id, timestamp, review_id))
        db.execute("""DELETE FROM system_state_history WHERE id NOT IN
            (SELECT id FROM system_state_history ORDER BY revision DESC LIMIT 15)""")
    return {"ok": True, "schedule_id": schedule_id}
