from __future__ import annotations

import io
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader


DATE_RE = r"\d{2}/\d{2}/\d{4}"
DATA_DIR = Path(
    os.getenv("EMPLOYEE_IMPORTS_ROOT_PATH", "data/exam_imports")
)


def _spaces(value: str) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "")).strip()


def _fix_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    replacements = {
        "A quisi tiv os": "Aquisitivos",
        "SIL VA": "SILVA",
        "SIL V A": "SILVA",
        "TOPOGRAFIA  LTDA": "TOPOGRAFIA LTDA",
        "Lim.S/DobraAdmissão": "Lim.S/Dobra Admissão",
        "Período (inicial/final)Faltas": "Período (inicial/final) Faltas",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,})\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ])\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ])\b", lambda m: "".join(m.groups()), text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _iso_date(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(DATE_RE, value)
    if not match:
        return ""
    try:
        return datetime.strptime(match.group(0), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def _pt_number(value: str | None) -> float | None:
    raw = _spaces(value or "")
    if not raw:
        return None
    try:
        return float(raw.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _parse_row(line: str) -> dict[str, Any] | None:
    line = _spaces(line)
    pattern = re.compile(
        rf"^(?P<contract>\d+)\s+"
        rf"(?P<name>.+?)\s*"
        rf"(?P<limit>{DATE_RE})\s*"
        rf"(?P<admission>{DATE_RE})\s*"
        rf"(?P<period_start>{DATE_RE})\s*(?:à|a)\s*"
        rf"(?P<period_end>{DATE_RE})"
        rf"(?P<tail>.*)$",
        re.I,
    )
    found = pattern.match(line)
    if not found:
        return None
    name = _spaces(found.group("name"))
    if len(name) < 3 or not re.search(r"[A-Za-zÁ-ÿ]", name):
        return None
    tail = _spaces(found.group("tail"))
    values = re.findall(r"\d+[.,]\d{2}", tail)
    type_match = re.search(r"\s([A-Z])\s+(\d{1,3})\s*$", tail)
    faltas = _pt_number(values[0]) if len(values) >= 3 else None
    saldo = _pt_number(values[-2]) if len(values) >= 2 else None
    pago = _pt_number(values[-1]) if len(values) >= 1 else None
    return {
        "contract": found.group("contract"),
        "name": name,
        "limitWithoutDouble": _iso_date(found.group("limit")),
        "admission": _iso_date(found.group("admission")),
        "periodStart": _iso_date(found.group("period_start")),
        "periodEnd": _iso_date(found.group("period_end")),
        "absences": faltas,
        "balanceDays": saldo,
        "paidDays": pago,
        "periodType": type_match.group(1) if type_match else "",
        "quantity": int(type_match.group(2)) if type_match else None,
        "rawValues": values,
        "rawTail": tail,
    }


def parse_exam_roster_pdf(content: bytes, filename: str) -> dict[str, Any]:
    reader = PdfReader(io.BytesIO(content))
    if not reader.pages:
        raise ValueError("O PDF não possui páginas legíveis.")
    texts = [_fix_text(page.extract_text() or "") for page in reader.pages]
    full_text = "\n".join(texts)

    title = ""
    title_match = re.search(r"(Períodos\s+Aquisitivos.*?Data Base:\s*\d{2}/\d{2}/\d{4})", full_text, re.I)
    if title_match:
        title = _spaces(title_match.group(1))
    company = ""
    company_match = re.search(r"^\s*0*(\d+)-(.+?)\s+\d{2}/\d{2}/\d{4}", full_text, re.M)
    company_code = company_match.group(1) if company_match else ""
    company = _spaces(company_match.group(2)) if company_match else ""
    base_date = _iso_date(re.search(r"Data Base:\s*([^\n]+)", full_text, re.I).group(1)) if re.search(r"Data Base:\s*([^\n]+)", full_text, re.I) else ""
    range_match = re.search(r"entre\s+({0})\s+at[eé]\s+({0})".format(DATE_RE), full_text, re.I)

    rows: list[dict[str, Any]] = []
    for text in texts:
        for raw_line in text.splitlines():
            row = _parse_row(raw_line)
            if row:
                rows.append(row)
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        unique[(row["contract"], row["name"].upper())] = row
    employees = list(unique.values())
    if not employees:
        raise ValueError("O PDF foi lido, mas não encontrei a tabela de funcionários. Este importador aceita relatórios com contrato, nome, admissão e período aquisitivo.")

    return {
        "ok": True,
        "filename": filename,
        "pageCount": len(reader.pages),
        "reportType": "periodos_aquisitivos_ferias",
        "reportTitle": title or "Relatório de funcionários",
        "companyCode": company_code,
        "company": company,
        "baseDate": base_date,
        "rangeStart": _iso_date(range_match.group(1)) if range_match else "",
        "rangeEnd": _iso_date(range_match.group(2)) if range_match else "",
        "employeeCount": len(employees),
        "employees": employees,
    }


def archive_exam_roster_pdf(content: bytes, filename: str) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "relatorio.pdf"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = DATA_DIR / f"{stamp}_{safe_name}"
    destination.write_bytes(content)
    return destination.as_posix()
