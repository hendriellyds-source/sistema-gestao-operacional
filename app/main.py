"""Aplicação exclusiva de portfólio: sem banco, contas, segredos ou integrações."""
import json
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Sistema de Gestão — Portfólio", version="1.0.0")
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
templates = Jinja2Templates(directory=ROOT / "app" / "templates")
USER = {"id":"demo", "username":"visitante", "email":"", "display_name":"Visitante do portfólio", "role":"diretor", "role_code":"director", "areas":["admin","finance","projectist","aline"], "avatar_url":None}

@app.middleware("http")
async def portfolio_only(request: Request, call_next):
    if request.method not in {"GET", "HEAD"}:
        return JSONResponse({"detail":"Demonstração: operações externas e persistência no servidor estão desativadas."}, status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob: https://*.tile.openstreetmap.org; connect-src 'self'; frame-src 'none'; form-action 'none'; object-src 'none'; base-uri 'self'"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

@app.get("/healthz")
@app.get("/api/health")
def health():
    return {"ok":True,"demo_mode":True,"integrations_enabled":False}

@app.get("/api/auth/me")
def me():
    return {"user":USER}

@app.get("/api/system-state")
def state():
    return {"has_state":True,"data":json.loads((ROOT / "demo.json").read_text(encoding="utf-8")),"revision":1}

@app.api_route("/api/{path:path}", methods=["GET"])
def unavailable(path: str):
    return JSONResponse({"detail":"Recurso indisponível na versão de portfólio."},status_code=403)

@app.get("/login")
def login():
    return RedirectResponse("/workspace")

@app.get("/")
@app.get("/workspace")
@app.get("/rh/{section}")
@app.get("/finance/calendar")
@app.get("/fleet")
@app.get("/admin/users")
def workspace(request: Request):
    return templates.TemplateResponse(request=request,name="index.html",context={"demo_mode":True,"poll_interval_seconds":60,"history_max_range_days":31,"employee_portal_enabled":False,"system_state":state(),"current_user":USER})

@app.get("/{path:path}")
def other_pages(path: str):
    return RedirectResponse("/workspace")
