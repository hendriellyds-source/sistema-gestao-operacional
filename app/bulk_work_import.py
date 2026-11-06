from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .access_control import ADMIN_ROLES, require_roles


router = APIRouter(prefix="/api/works/bulk", tags=["Cadastro de empresas e obras"])


class BulkWorkPreviewPayload(BaseModel):
    text: str = Field(min_length=1, max_length=250_000)


def _spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFD", _spaces(value))
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn").upper()


def parse_brazilian_currency(value: Any) -> Decimal | None:
    text = _spaces(value)
    if not text:
        return None
    text = re.sub(r"(?i)R\$", "", text).replace(" ", "")
    if not re.fullmatch(r"[+-]?[0-9.,]+", text):
        raise ValueError("valor monetário inválido")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1 or re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("valor monetário inválido") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("o valor não pode ser negativo")
    return amount.quantize(Decimal("0.01"))


def parse_bulk_company_work_text(text: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    ignored_blank_lines = 0

    for line_number, raw_line in enumerate(str(text or "").splitlines(), start=1):
        if not raw_line.strip():
            ignored_blank_lines += 1
            continue
        columns = raw_line.split("\t")
        if len(columns) == 1:
            columns = re.split(r"\s{2,}|;", raw_line)
        columns = [column.strip() for column in columns]
        while columns and not columns[-1]:
            columns.pop()

        errors: list[str] = []
        warnings: list[str] = []
        company = _spaces(columns[0]) if columns else ""
        work = _spaces(columns[1]) if len(columns) > 1 else ""
        daily_rate: Decimal | None = None
        half_daily_rate: Decimal | None = None

        if len(columns) > 4:
            errors.append("a linha possui mais de quatro colunas")
        if not company:
            errors.append("empresa não informada")
        if not work:
            errors.append("obra não informada")
        try:
            daily_rate = parse_brazilian_currency(columns[2] if len(columns) > 2 else "")
        except ValueError as exc:
            errors.append(f"diária: {exc}")
        try:
            half_daily_rate = parse_brazilian_currency(columns[3] if len(columns) > 3 else "")
        except ValueError as exc:
            errors.append(f"meia diária: {exc}")
        if daily_rate is None:
            warnings.append("diária não informada; será gravada como R$ 0,00")
        if half_daily_rate is None:
            warnings.append("meia diária não informada; será gravada como R$ 0,00")

        duplicate_of = None
        if company and work:
            row_key = (_key(company), _key(work))
            duplicate_of = seen.get(row_key)
            if duplicate_of is None:
                seen[row_key] = line_number
            else:
                errors.append(f"obra repetida no texto (igual à linha {duplicate_of})")

        rows.append(
            {
                "line_number": line_number,
                "company": company,
                "work": work,
                "daily_rate": float(daily_rate or 0),
                "half_daily_rate": float(half_daily_rate or 0),
                "errors": errors,
                "warnings": warnings,
                "valid": not errors,
                "duplicate_of": duplicate_of,
            }
        )

    return {
        "rows": rows,
        "valid_rows": sum(1 for row in rows if row["valid"]),
        "invalid_rows": sum(1 for row in rows if not row["valid"]),
        "ignored_blank_lines": ignored_blank_lines,
    }


@router.post("/preview")
def preview_bulk_company_works(
    payload: BulkWorkPreviewPayload,
    request: Request,
) -> dict[str, Any]:
    require_roles(request, ADMIN_ROLES)
    return parse_bulk_company_work_text(payload.text)
