from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ..auth import COOKIE_NAME, user_from_session
from ..settings import settings

router = APIRouter(tags=["Páginas"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if user_from_session(request.cookies.get(COOKIE_NAME)):
        destination = "/agenda-tablet" if request.query_params.get("next") == "/agenda-tablet" else "/workspace"
        return RedirectResponse(destination, status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"tablet_login": request.query_params.get("next") == "/agenda-tablet", "demo_mode": settings.demo_mode, "demo_email": settings.demo_email, "demo_password": settings.demo_password},
    )


@router.get("/agenda-tablet", response_class=HTMLResponse)
async def tablet_agenda_page(request: Request):
    return templates.TemplateResponse(request=request, name="tablet_agenda.html", context={})


@router.get("/agenda-tablet/sw.js")
async def tablet_agenda_service_worker():
    from fastapi.responses import FileResponse
    return FileResponse("assets/tablet_agenda_sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/", response_class=HTMLResponse)
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


@router.get("/privacy", response_class=HTMLResponse)
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


@router.get("/terms", response_class=HTMLResponse)
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


@router.get("/data-deletion", response_class=HTMLResponse)
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


