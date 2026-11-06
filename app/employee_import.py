from __future__ import annotations

import base64
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
    os.getenv("EMPLOYEE_IMPORTS_ROOT_PATH", "data/employee_imports")
)


def _compact_spaces(value: str) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "")).strip()


def _fix_pdf_spacing(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    replacements = {
        "V alidade": "Validade",
        "V enezuelano": "Venezuelano",
        "V isto": "Visto",
        "A venida": "Avenida",
        "Wils on": "Wilson",
        "MA TRIZ": "MATRIZ",
        "NA TA": "NATA",
        "GUST A VO": "GUSTAVO",
        "F ARIAS": "FARIAS",
        "AL VES": "ALVES",
        "SILV A": "SILVA",
        "S IL V A": "SILVA",
        "SIL V A": "SILVA",
        "DA SIL V A": "DA SILVA",
        "ACEV ED O": "ACEVEDO",
        "GAROP ABA": "GAROPABA",
        "VIST A": "VISTA",
        "T ratamento": "Tratamento",
        "T erça": "Terça",
        "T ransf": "Transf",
        "T ab": "Tab",
        "ESPONT ANEO": "ESPONTANEO",
        "Nr .": "Nr.",
        "Hor .": "Hor.",
        "T rans f": "Transf",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Junta palavras claramente quebradas pelo extrator, como SIL V A e ACEV ED O.
    text = re.sub(
        r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,})\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ])\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ])\b",
        lambda m: "".join(m.groups()),
        text,
    )
    text = re.sub(
        r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,})\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2})\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ])\b",
        lambda m: "".join(m.groups()),
        text,
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _iso_date(value: str | None) -> str:
    value = _compact_spaces(value or "")
    if not value:
        return ""
    match = re.search(DATE_RE, value)
    if not match:
        return ""
    try:
        return datetime.strptime(match.group(0), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def _pt_money(value: str | None) -> float:
    raw = _compact_spaces(value or "")
    if not raw:
        return 0.0
    raw = raw.replace("R$", "").replace(".", "").replace(",", ".")
    try:
        return round(float(raw), 2)
    except ValueError:
        return 0.0


def _match(text: str, pattern: str, group: int = 1, flags: int = re.I | re.M) -> str:
    found = re.search(pattern, text, flags)
    if not found:
        return ""
    return _compact_spaces(found.group(group))


def _section(text: str, start: str, end: str | None = None) -> str:
    if end:
        pattern = rf"(?:^|\n){start}\s*\n(?P<body>.*?)(?=\n(?:{end})\s*(?:\n|$))"
    else:
        pattern = rf"(?:^|\n){start}\s*\n(?P<body>.*)$"
    found = re.search(pattern, text, re.I | re.S)
    return found.group("body").strip() if found else ""


def _first_page_employee_section(text: str) -> str:
    marker = re.search(r"^Dados do Empregado\s*$", text, re.I | re.M)
    return text[marker.end():].strip() if marker else text


def _extract_photo(page: Any) -> str:
    # Primeiro tenta extrair imagens JPEG/JP2 diretamente do objeto PDF, sem Pillow.
    direct_candidates: list[tuple[float, bytes, str]] = []
    try:
        resources = page.get("/Resources", {}).get_object()
        xobjects = resources.get("/XObject", {}).get_object()
        for item in xobjects.values():
            stream = item.get_object()
            if str(stream.get("/Subtype", "")) != "/Image":
                continue
            image_filter = stream.get("/Filter")
            filters = [str(value) for value in image_filter] if isinstance(image_filter, list) else [str(image_filter)]
            mime = ""
            if "/DCTDecode" in filters:
                mime = "image/jpeg"
            elif "/JPXDecode" in filters:
                mime = "image/jp2"
            if not mime:
                continue
            raw = bytes(getattr(stream, "_data", b"") or b"")
            if not raw:
                continue
            width = int(stream.get("/Width", 0) or 0)
            height = int(stream.get("/Height", 0) or 0)
            score = max(width * height, len(raw)) * (1.3 if height > width else 1.0)
            direct_candidates.append((score, raw, mime))
    except Exception:
        direct_candidates = []

    if direct_candidates:
        _, raw, mime = max(direct_candidates, key=lambda item: item[0])
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    # Fallback para outros filtros quando Pillow estiver disponível.
    try:
        images = list(page.images)
    except Exception:
        return ""
    if not images:
        return ""

    candidates = []
    for image in images:
        try:
            width, height = image.image.size
            area = width * height
            portrait_bonus = 1.3 if height > width else 1.0
            candidates.append((area * portrait_bonus, image))
        except Exception:
            candidates.append((len(image.data), image))
    _, selected = max(candidates, key=lambda item: item[0])
    name = str(getattr(selected, "name", "image.jpg")).lower()
    mime = "image/png" if name.endswith(".png") else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(selected.data).decode('ascii')}"


def _parse_family(first_text: str) -> list[dict[str, str]]:
    match = re.search(
        r"Ficha Familiar\s+Nome\s+Nascimento\s*Parentesco\s*(?P<body>.*?)(?=\n\s*Biguacu,|\n\s*Cidade Demo,|\n\s*Data da dispensa)",
        first_text,
        re.I | re.S,
    )
    if not match:
        return []
    result: list[dict[str, str]] = []
    for raw_line in match.group("body").splitlines():
        line = _compact_spaces(raw_line)
        if not line:
            continue
        item = re.match(
            rf"^(?P<order>\d+)\s+(?P<name>.+?)(?:\s+(?P<birth>{DATE_RE}))?\s+(?P<relationship>Pai/Mãe|Pai|Mãe|Filho(?:\(a\))?|Cônjuge|Esposo|Esposa|Dependente|Outro)$",
            line,
            re.I,
        )
        if item:
            result.append(
                {
                    "order": item.group("order"),
                    "name": _compact_spaces(item.group("name")),
                    "birth": _iso_date(item.group("birth")),
                    "relationship": _compact_spaces(item.group("relationship")),
                }
            )
        else:
            # Preserva qualquer linha que não tenha sido reconhecida integralmente.
            result.append({"order": "", "name": line, "birth": "", "relationship": ""})
    return result


def _parse_salary_history(all_text: str) -> list[dict[str, Any]]:
    body = _section(all_text, "Salários", "Afastamentos|Cargos")
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        line = _compact_spaces(line)
        if not re.match(rf"^{DATE_RE}", line):
            continue
        match = re.match(
            rf"^(?P<date>{DATE_RE})(?P<effect>{DATE_RE})?\s+\d+\s+(?P<value>[\d.]+,\d{{2}})\s+[-\d.,]+\s+(?P<period>\S+)\s+\d+\s+(?P<reason>.*?)(?:\s+\d+)?$",
            line,
            re.I,
        )
        if not match:
            continue
        rows.append(
            {
                "date": _iso_date(match.group("date")),
                "effectDate": _iso_date(match.group("effect")),
                "value": _pt_money(match.group("value")),
                "period": _compact_spaces(match.group("period")),
                "reason": _compact_spaces(match.group("reason")),
            }
        )
    return rows


def _parse_role_history(all_text: str) -> list[dict[str, str]]:
    body = _section(all_text, "Cargos", "Escalas")
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = _compact_spaces(line)
        if not re.match(rf"^{DATE_RE}", line):
            continue
        match = re.match(
            rf"^(?P<date>{DATE_RE})\s*(?P<code>\d+)\s*(?P<role>.+?)\s+(?P<cbo>\d{{5,6}})\s+\d+\s+(?P<reason>.+)$",
            line,
            re.I,
        )
        if match:
            rows.append(
                {
                    "date": _iso_date(match.group("date")),
                    "code": match.group("code"),
                    "role": _compact_spaces(match.group("role")),
                    "cbo": match.group("cbo"),
                    "reason": _compact_spaces(match.group("reason")),
                }
            )
    return rows


def _parse_schedule_history(all_text: str) -> list[dict[str, str]]:
    body = _section(all_text, "Escalas", "Locais/Filiais")
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = _compact_spaces(line)
        if not re.match(rf"^{DATE_RE}", line):
            continue
        match = re.match(
            rf"^(?P<date>{DATE_RE})\s+(?P<code>\d+)\s+(?P<schedule>(?:\d{{2}}:\d{{2}}\s+){{3}}\d{{2}}:\d{{2}})\s+(?P<weekly>\d{{3}}:\d{{2}})\s+(?P<daily>\d{{3}}:\d{{2}})$",
            line,
        )
        if match:
            rows.append(
                {
                    "date": _iso_date(match.group("date")),
                    "code": match.group("code"),
                    "schedule": _compact_spaces(match.group("schedule")),
                    "weeklyHours": match.group("weekly"),
                    "dailyHours": match.group("daily"),
                }
            )
    return rows


def _parse_leaves(all_text: str) -> list[dict[str, str]]:
    body = _section(all_text, "Afastamentos", "Cargos")
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = _compact_spaces(line)
        match = re.match(
            rf"^(?P<start>{DATE_RE})\s+(?P<end>{DATE_RE})\s+\d+\s+(?P<description>.+)$",
            line,
            re.I,
        )
        if match:
            rows.append(
                {
                    "start": _iso_date(match.group("start")),
                    "end": _iso_date(match.group("end")),
                    "description": _compact_spaces(match.group("description")),
                }
            )
    return rows


def _parse_branch_history(all_text: str) -> list[dict[str, str]]:
    body = _section(all_text, "Locais/Filiais", "Adicionais")
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = _compact_spaces(line)
        if not re.match(rf"^{DATE_RE}", line):
            continue
        rows.append({"date": _iso_date(line), "description": line})
    return rows


def _parse_additional_history(all_text: str) -> list[dict[str, str]]:
    body = _section(all_text, "Adicionais", "Carga Horária")
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = _compact_spaces(line)
        if not re.match(rf"^{DATE_RE}", line):
            continue
        rows.append({"date": _iso_date(line), "description": line})
    return rows


def _parse_work_hours(all_text: str) -> list[dict[str, Any]]:
    body = _section(all_text, "Carga Horária")
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    day_pattern = re.compile(
        r"^(Segunda-Feira|Terça-Feira|Quarta-Feira|Quinta-Feira|Sexta-Feira|Sábado|Domingo)\s*(.*)$",
        re.I,
    )
    time_pattern = re.compile(r"(?P<entry>\d{3}:\d{2})\s+(?P<exit>\d{3}:\d{2})\s+(?P<hours>\d{3}:\d{2})")
    for raw_line in body.splitlines():
        line = _compact_spaces(raw_line)
        if not line or line.lower().startswith("dia entrada"):
            continue
        day = day_pattern.match(line)
        if day:
            current = {"day": day.group(1), "periods": [], "status": ""}
            rows.append(current)
            rest = day.group(2).strip()
            time = time_pattern.search(rest)
            if time:
                current["periods"].append(time.groupdict())
            elif rest:
                current["status"] = rest
            continue
        if current:
            time = time_pattern.search(line)
            if time:
                current["periods"].append(time.groupdict())
            elif line:
                current["status"] = _compact_spaces(f"{current.get('status', '')} {line}")
    return rows


def _employer_data(first_text: str) -> dict[str, str]:
    part = first_text.split("Dados do Empregado", 1)[0]
    return {
        "company": _match(part, r"Empresa:\s*(.*?)\s+N[ºo]\s*\d+\s*$", flags=re.I | re.M),
        "recordNumber": _match(part, r"Empresa:.*?N[ºo]\s*(\d+)", flags=re.I | re.S),
        "cnpj": _match(part, r"CNPJ/CEI:\s*([^\n]+)"),
        "federalActivity": _match(part, r"Ativ Federal:\s*([^\n]+)"),
        "address": _match(part, r"Endereço:\s*([^\n]+)"),
        "neighborhood": _match(part, r"Bairro:\s*([^\n]+)"),
        "city": _match(part, r"Município:\s*([^\n]+)"),
    }


def _parse_record(page_texts: list[str], pages: list[Any], page_numbers: list[int], filename: str) -> dict[str, Any]:
    first_text = _fix_pdf_spacing(page_texts[0])
    all_text = _fix_pdf_spacing("\n".join(page_texts))
    employee_text = _first_page_employee_section(first_text)

    identity = re.search(r"Nome:\s*(.*?)\s+Código:\s*([0-9]+)", employee_text, re.I)
    birth_line = re.search(
        rf"Nascimento:\s*({DATE_RE})\s+Sexo:\s*(.*?)\s+Est\.\s*Civil:\s*(.*?)\s+Raça/Cor:\s*([^\n]*)",
        employee_text,
        re.I,
    )
    natural_line = re.search(r"Naturalidade:\s*(.*?)\s+Nacionalidade:\s*([^\n]*)", employee_text, re.I)
    rg_line = re.search(
        r"RG:\s*(.*?)\s+Órgão:\s*(.*?)\s+Estado:\s*(.*?)\s+Emissão RG:\s*([^\n]*)",
        employee_text,
        re.I,
    )
    ctps_line = re.search(
        r"Número CTPS:\s*(.*?)\s+Série CTPS:\s*(.*?)\s+Estado CTPS:\s*(.*?)\s+Expedição CTPS:\s*([^\n]*)",
        employee_text,
        re.I,
    )
    cnh_line = re.search(
        r"CNH:\s*(.*?)\s+Categoria CNH:\s*(.*?)\s+Validade CNH:\s*([^\n]*)",
        employee_text,
        re.I,
    )
    reservist_line = re.search(
        r"Reservista:[ \t]*([^\n]*?)[ \t]+Categoria:[ \t]*([^\n]*?)[ \t]+Tít\.\s*Eleitoral:[ \t]*([^\n]*?)[ \t]+Zona:[ \t]*([^\n]*?)[ \t]+Seção:[ \t]*([^\n]*)",
        employee_text,
        re.I,
    )
    bank_line = re.search(
        r"Banco:[ \t]*([^\n]*?)[ \t]+Conta:[ \t]*([^\n]*?)[ \t]+Dígito:[ \t]*([^\n]*?)[ \t]+Agência:[ \t]*([^\n]*)",
        employee_text,
        re.I,
    )
    professional_line = re.search(
        r"Cons\.\s*Profis:[ \t]*([^\n]*?)[ \t]+Registro Profis:[ \t]*([^\n]*?)[ \t]+Data Registro:[ \t]*([^\n]*)",
        employee_text,
        re.I,
    )
    fgts_line = re.search(
        r"Optante FGTS:[ \t]*([^\n]*?)[ \t]+Data Opção:[ \t]*([^\n]*?)[ \t]+Conta FGTS:[ \t]*([^\n]*)",
        employee_text,
        re.I,
    )
    remuneration_line = re.search(
        r"Remuneração:\s*([\d.,]+)\s+Modo Pgto:\s*(.*?)\s+Período:\s*([^\n]*)",
        employee_text,
        re.I,
    )
    visa_line = re.search(r"Tipo Visto:[ \t]*([^\n]*?)[ \t]+Número da Portaria:[ \t]*([^\n]*)", employee_text, re.I)
    rne_line = re.search(r"Carteira RNE:[ \t]*([^\n]*?)[ \t]+Data da Portaria:[ \t]*([^\n]*)", employee_text, re.I)

    address = _match(employee_text, r"Endereço:\s*([^\n]+)")
    neighborhood_zip = re.search(r"Bairro:\s*(.*?)\s+CEP:\s*([^\n]+)", employee_text, re.I)

    salary_history = _parse_salary_history(all_text)
    role_history = _parse_role_history(all_text)
    schedule_history = _parse_schedule_history(all_text)
    current_salary = salary_history[-1]["value"] if salary_history else _pt_money(remuneration_line.group(1) if remuneration_line else "")
    current_role = role_history[-1]["role"] if role_history else _match(employee_text, r"Cargo:\s*(.*?)\s+CBO:")
    current_cbo = role_history[-1]["cbo"] if role_history else _match(employee_text, r"Cargo:.*?CBO:\s*([^\n]+)")
    current_schedule = schedule_history[-1]["schedule"] if schedule_history else _match(employee_text, r"Escala:\s*([^\n]+)")
    weekly_hours = schedule_history[-1]["weeklyHours"] if schedule_history else ""

    warnings: list[str] = []
    name = _compact_spaces(identity.group(1)) if identity else ""
    registration = identity.group(2) if identity else ""
    cpf = _match(employee_text, r"CPF:\s*([0-9.\-]+)")
    if not name:
        warnings.append("Nome do funcionário não identificado.")
    if not cpf:
        warnings.append("CPF não identificado.")
    if not registration:
        warnings.append("Código/matrícula não identificado.")

    result: dict[str, Any] = {
        "name": name,
        "code": registration,
        "registration": registration,
        "receiptNumber": _match(employee_text, r"Nr\.\s*Recibo:\s*([^\n]+)"),
        "father": _match(employee_text, r"Pai:\s*(.*?)\s+Nr\.\s*Recibo:"),
        "mother": _match(employee_text, r"Mãe:\s*([^\n]+)"),
        "birth": _iso_date(birth_line.group(1) if birth_line else ""),
        "sex": _compact_spaces(birth_line.group(2)) if birth_line else "",
        "maritalStatus": _compact_spaces(birth_line.group(3)) if birth_line else "",
        "raceColor": _compact_spaces(birth_line.group(4)) if birth_line else "",
        "naturality": _compact_spaces(natural_line.group(1)) if natural_line else "",
        "nationality": _compact_spaces(natural_line.group(2)) if natural_line else "",
        "address": address,
        "neighborhood": _compact_spaces(neighborhood_zip.group(1)) if neighborhood_zip else "",
        "zip": _compact_spaces(neighborhood_zip.group(2)) if neighborhood_zip else "",
        "city": _match(employee_text, r"Município:\s*([^\n]+)\nCPF:"),
        "cpf": cpf,
        "rg": _compact_spaces(rg_line.group(1)) if rg_line else "",
        "rgIssuer": _compact_spaces(rg_line.group(2)) if rg_line else "",
        "rgState": _compact_spaces(rg_line.group(3)) if rg_line else "",
        "rgIssueDate": _iso_date(rg_line.group(4) if rg_line else ""),
        "ctpsNumber": _compact_spaces(ctps_line.group(1)) if ctps_line else "",
        "ctpsSeries": _compact_spaces(ctps_line.group(2)) if ctps_line else "",
        "ctpsState": _compact_spaces(ctps_line.group(3)) if ctps_line else "",
        "ctpsIssueDate": _iso_date(ctps_line.group(4) if ctps_line else ""),
        "pis": _match(employee_text, r"PIS:\s*(.*?)\s+Cadastro PIS:"),
        "pisDate": _iso_date(_match(employee_text, r"Cadastro PIS:\s*([^\n]*)")),
        "education": _match(employee_text, r"Instrução:\s*([^\n]+)"),
        "cnh": _compact_spaces(cnh_line.group(1)) if cnh_line else "",
        "cnhCategory": _compact_spaces(cnh_line.group(2)) if cnh_line else "",
        "cnhValidity": _iso_date(cnh_line.group(3) if cnh_line else ""),
        "reservist": _compact_spaces(reservist_line.group(1)) if reservist_line else "",
        "reservistCategory": _compact_spaces(reservist_line.group(2)) if reservist_line else "",
        "voterTitle": _compact_spaces(reservist_line.group(3)) if reservist_line else "",
        "voterZone": _compact_spaces(reservist_line.group(4)) if reservist_line else "",
        "voterSection": _compact_spaces(reservist_line.group(5)) if reservist_line else "",
        "bank": _compact_spaces(bank_line.group(1)) if bank_line else "",
        "bankAccount": _compact_spaces(bank_line.group(2)) if bank_line else "",
        "bankDigit": _compact_spaces(bank_line.group(3)) if bank_line else "",
        "bankAgency": _compact_spaces(bank_line.group(4)) if bank_line else "",
        "union": _match(employee_text, r"Sindicato:\s*([^\n]+)"),
        "professionalCouncil": _compact_spaces(professional_line.group(1)) if professional_line else "",
        "professionalRegistration": _compact_spaces(professional_line.group(2)) if professional_line else "",
        "professionalRegistrationDate": _iso_date(professional_line.group(3) if professional_line else ""),
        "foreignArrivalDate": _iso_date(_match(employee_text, r"Data Chegada:\s*([^\n]*)")),
        "foreignVisaType": _compact_spaces(visa_line.group(1)) if visa_line else "",
        "foreignOrdinanceNumber": _compact_spaces(visa_line.group(2)) if visa_line else "",
        "rne": _compact_spaces(rne_line.group(1)) if rne_line else "",
        "foreignOrdinanceDate": _iso_date(rne_line.group(2) if rne_line else ""),
        "rneValidity": _iso_date(_match(employee_text, r"Validade RNE:\s*([^\n]*)")),
        "admission": _iso_date(_match(employee_text, r"Admissão:\s*([^\n]+)")),
        "fgtsOptIn": _compact_spaces(fgts_line.group(1)) if fgts_line else "",
        "fgtsDate": _iso_date(fgts_line.group(2) if fgts_line else ""),
        "fgtsAccount": _compact_spaces(fgts_line.group(3)) if fgts_line else "",
        "role": current_role,
        "cbo": current_cbo,
        "unit": _match(employee_text, r"Organograma:\s*([^\n]+)") or "Matriz",
        "sector": "Campo" if "TOPOG" in unicodedata.normalize("NFKD", current_role.upper()).encode("ascii", "ignore").decode("ascii") else "Administrativo",
        "contract": "CLT",
        "salaryAtRecord": _pt_money(remuneration_line.group(1) if remuneration_line else ""),
        "currentSalary": current_salary,
        "paymentMethod": _compact_spaces(remuneration_line.group(2)) if remuneration_line else "",
        "salaryPeriod": _compact_spaces(remuneration_line.group(3)) if remuneration_line else "Mensal",
        "schedule": current_schedule,
        "weeklyHours": weekly_hours,
        "workSchedule": current_schedule,
        "status": "Ativo",
        "risk": "Campo / exposição solar e atividades externas" if "TOPOG" in unicodedata.normalize("NFKD", current_role.upper()).encode("ascii", "ignore").decode("ascii") else "Administrativo / baixo risco",
        "salaryHistory": salary_history,
        "roleHistory": role_history,
        "scheduleHistory": schedule_history,
        "functionalOccurrences": _parse_leaves(all_text),
        "familyMembers": _parse_family(first_text),
        "branchHistory": _parse_branch_history(all_text),
        "additionalHistory": _parse_additional_history(all_text),
        "workHours": _parse_work_hours(all_text),
        "employerData": _employer_data(first_text),
        "photoDataUrl": _extract_photo(pages[0]) if pages else "",
        "email": "",
        "phone": "",
        "appLogin": "",
        "appPassword": "1234",
        "notes": f"Cadastro importado automaticamente de {filename}.",
        "sourceDocument": filename,
        "sourcePages": ", ".join(str(number) for number in page_numbers),
        "imported": True,
        "importedAt": datetime.now().date().isoformat(),
        "importWarnings": warnings,
    }
    return result


def parse_employee_registration_pdf(content: bytes, filename: str) -> dict[str, Any]:
    reader = PdfReader(io.BytesIO(content))
    if not reader.pages:
        raise ValueError("O PDF não possui páginas legíveis.")

    page_texts = [_fix_pdf_spacing(page.extract_text() or "") for page in reader.pages]
    groups: list[tuple[list[str], list[Any], list[int]]] = []
    current_texts: list[str] = []
    current_pages: list[Any] = []
    current_numbers: list[int] = []

    for index, (page, text) in enumerate(zip(reader.pages, page_texts), start=1):
        starts_record = bool(re.search(r"Ficha de Registro de Empregado", text, re.I))
        if starts_record and current_texts:
            groups.append((current_texts, current_pages, current_numbers))
            current_texts, current_pages, current_numbers = [], [], []
        if starts_record or current_texts:
            current_texts.append(text)
            current_pages.append(page)
            current_numbers.append(index)

    if current_texts:
        groups.append((current_texts, current_pages, current_numbers))

    if not groups:
        raise ValueError("Não encontrei o título 'Ficha de Registro de Empregado' no PDF.")

    employees = [
        _parse_record(texts, pages, numbers, filename)
        for texts, pages, numbers in groups
    ]
    employees = [employee for employee in employees if employee.get("name") or employee.get("cpf")]
    if not employees:
        raise ValueError("O arquivo foi lido, mas nenhum funcionário pôde ser identificado.")

    return {
        "ok": True,
        "filename": filename,
        "pageCount": len(reader.pages),
        "employeeCount": len(employees),
        "employees": employees,
    }


def archive_employee_pdf(content: bytes, filename: str) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "ficha.pdf"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = DATA_DIR / f"{stamp}_{safe_name}"
    destination.write_bytes(content)
    return destination.as_posix()
