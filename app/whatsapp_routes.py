from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.access_control import ADMIN_ROLES, WHATSAPP_ROLES, require_roles
from app.settings import settings
from app.supabase_client import create_admin_supabase
from app.whatsapp_client import WhatsAppAPIError, WhatsAppConfigurationError, whatsapp_client
from app.whatsapp_onboarding import (
    WhatsAppOnboardingError,
    embedded_signup_ready,
    inspect_authorized_business,
)
from app.whatsapp_service import (
    QUEUE_BY_KEY,
    extract_message_text,
    queue_payloads,
    route_incoming_message,
    send_and_record,
)
from app.whatsapp_store import (
    disconnect_whatsapp_business_connection,
    get_conversation,
    get_contact_labels,
    get_message,
    get_message_by_provider_id,
    get_active_whatsapp_business_connection,
    get_or_create_outbound_conversation,
    list_whatsapp_business_connections,
    list_conversations,
    list_messages,
    list_labels,
    create_label,
    mark_conversation_read_for_user,
    mark_message_meta_read,
    record_incoming_message,
    record_outbound_message,
    save_whatsapp_event,
    save_whatsapp_business_connection,
    set_contact_labels,
    upsert_whatsapp_contact,
    update_conversation,
    update_message_media,
    update_message_status,
    whatsapp_sync_diagnostics,
)
from app.whatsapp_media import (
    decode_audio_upload,
    decode_file_upload,
    download_and_store_audio,
    download_and_store_file,
    resolve_audio_file,
    resolve_media_file,
    save_audio_file,
    save_media_file,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["WhatsApp"])


class SendMessagePayload(BaseModel):
    message: str = Field(min_length=1, max_length=4096)


class SendAudioPayload(BaseModel):
    content_base64: str = Field(min_length=4, max_length=24_000_000)
    filename: str = Field(default="audio.m4a", min_length=1, max_length=160)
    mime_type: str | None = Field(default=None, max_length=120)


class SendFilePayload(BaseModel):
    content_base64: str = Field(min_length=4, max_length=28_000_000)
    filename: str = Field(min_length=1, max_length=160)
    mime_type: str | None = Field(default=None, max_length=120)


class ReadConversationPayload(BaseModel):
    propagate_to_whatsapp: bool = True


class LabelPayload(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class ContactLabelsPayload(BaseModel):
    label_ids: list[int] = Field(default_factory=list, max_length=20)
    primary_label_id: int | None = None


class StartConversationPayload(BaseModel):
    phone: str = Field(min_length=8, max_length=24)
    profile_name: str | None = Field(default=None, max_length=120)


class ConversationUpdatePayload(BaseModel):
    queue_key: str | None = None
    assignee: str | None = Field(default=None, max_length=120)
    assignee_profile_id: UUID | None = None
    status: Literal["awaiting_menu", "open", "pending", "closed"] | None = None
    mark_read: bool = False


class EmbeddedSignupPayload(BaseModel):
    code: str = Field(min_length=8, max_length=4096)
    waba_id: str = Field(min_length=3, max_length=64, pattern=r"^[0-9]+$")
    phone_number_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[0-9]+$",
    )
    business_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[0-9]+$",
    )


class DisconnectConnectionPayload(BaseModel):
    waba_id: str = Field(min_length=3, max_length=64, pattern=r"^[0-9]+$")
    confirmation: Literal["DESCONECTAR"]


def _validate_webhook_signature(
    *, raw_body: bytes, received_signature: str | None
) -> bool:
    """Confere se o evento POST realmente foi enviado pela Meta."""
    app_secret = settings.whatsapp_app_secret.strip()
    if not app_secret:
        logger.warning(
            "WHATSAPP_APP_SECRET não configurado. Webhook POST bloqueado por segurança."
        )
        return False
    if not received_signature:
        return False

    expected_signature = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, received_signature)


def _contacts_by_phone(value: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for contact in value.get("contacts") or []:
        phone = str(contact.get("wa_id") or "").strip()
        name = str(contact.get("profile", {}).get("name") or "").strip()
        if phone and name:
            result[phone] = name
    return result


def _require_same_origin(request: Request) -> None:
    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        return
    parsed = urlparse(origin)
    request_host = str(request.headers.get("host") or "").split(":", 1)[0].lower()
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != request_host:
        raise HTTPException(status_code=403, detail="Origem da solicitação inválida.")


def _user_key(user: dict[str, Any]) -> str:
    return str(
        user.get("profile_id")
        or user.get("id")
        or user.get("email")
        or user.get("display_name")
        or "unknown"
    )


def _attendant_name(user: dict[str, Any]) -> str:
    display_name = " ".join(str(user.get("display_name") or "Atendente").split())
    return display_name.split(" ", 1)[0] or "Atendente"


def _assignee_terms(user: dict[str, Any]) -> list[str]:
    display_name = " ".join(str(user.get("display_name") or "").split())
    role = str(user.get("role_code") or "").strip().lower()
    terms = [display_name, display_name.split(" ", 1)[0] if display_name else ""]
    role_aliases = {
        "financial": ["Financeiro"],
        "projectist": ["Projetista"],
        "projects": ["Projetos"],
    }
    terms.extend(role_aliases.get(role, []))
    return list(dict.fromkeys(term for term in terms if term))


def _assignable_profiles(user: dict[str, Any]) -> list[dict[str, str]]:
    try:
        response = (
            create_admin_supabase()
            .table("profiles")
            .select("id,auth_user_id,full_name,email,role_code,status")
            .eq("organization_id", str(user["organization_id"]))
            .eq("status", "active")
            .order("full_name", desc=False)
            .execute()
        )
    except Exception as exc:
        logger.exception("Não foi possível listar perfis para atribuição do WhatsApp")
        raise HTTPException(
            status_code=503, detail="Não foi possível carregar os responsáveis."
        ) from exc
    return [
        {
            "id": str(profile["id"]),
            "name": str(profile.get("full_name") or profile.get("email") or "Usuário"),
            "email": str(profile.get("email") or ""),
            "role_code": str(profile.get("role_code") or ""),
        }
        for profile in (response.data or [])
        if isinstance(profile, dict)
        and profile.get("id")
        and profile.get("auth_user_id")
        and str(profile.get("role_code") or "").lower() in WHATSAPP_ROLES
    ]


def _assignable_profile(user: dict[str, Any], profile_id: UUID) -> dict[str, str]:
    for profile in _assignable_profiles(user):
        if profile["id"] == str(profile_id):
            return profile
    raise HTTPException(
        status_code=422,
        detail="Selecione um perfil ativo com acesso ao atendimento WhatsApp.",
    )


def _can_access_conversation(
    user: dict[str, Any], conversation: dict[str, Any]
) -> bool:
    if str(user.get("role_code") or "").lower() in ADMIN_ROLES:
        return True
    assigned_profile_id = str(conversation.get("assignee_profile_id") or "")
    if assigned_profile_id:
        return assigned_profile_id == str(user.get("profile_id") or "")
    assignee = str(conversation.get("assignee") or "").strip().lower()
    return bool(assignee and assignee in {term.lower() for term in _assignee_terms(user)})


def _require_conversation_access(
    user: dict[str, Any], conversation_id: int
) -> dict[str, Any]:
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    if not _can_access_conversation(user, conversation):
        raise HTTPException(
            status_code=403,
            detail="Esta conversa está atribuída a outro atendimento.",
        )
    return conversation


def _message_media(message: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    message_type = str(message.get("type") or "unknown")
    media = message.get(message_type) if isinstance(message.get(message_type), dict) else {}
    media_id = str(media.get("id") or "").strip() or None
    mime_type = str(media.get("mime_type") or "").strip() or None
    filename = str(media.get("filename") or "").strip() or None
    return media_id, mime_type, filename


def _timestamp_text(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(
            int(str(value)), timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return None


@router.get("/api/whatsapp/webhook", response_class=Response)
async def verify_whatsapp_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    configured_token = settings.whatsapp_verify_token.strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token de verificação do WhatsApp não configurado.",
        )
    if (
        hub_mode == "subscribe"
        and hub_verify_token == configured_token
        and hub_challenge
    ):
        return Response(
            content=hub_challenge,
            media_type="text/plain",
            status_code=status.HTTP_200_OK,
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Falha na verificação do webhook do WhatsApp.",
    )


@router.post("/api/whatsapp/webhook")
async def receive_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not _validate_webhook_signature(
        raw_body=raw_body, received_signature=signature
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Assinatura do webhook inválida.",
        )

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Corpo JSON inválido.",
        ) from exc

    active_connection = get_active_whatsapp_business_connection()
    active_waba_id = str((active_connection or {}).get("waba_id") or "")
    if not active_waba_id and list_whatsapp_business_connections(limit=1):
        logger.info("Evento de conta do WhatsApp desconectada ignorado.")
        return {"status": "ignored", "processed": False, "messages": 0, "duplicates": 0}
    if active_waba_id:
        matching_entries = [
            entry for entry in payload.get("entry") or []
            if str(entry.get("id") or "") == active_waba_id
        ]
        if not matching_entries:
            logger.info("Evento de conta do WhatsApp inativa ignorado.")
            return {"status": "ignored", "processed": False, "messages": 0, "duplicates": 0}
        payload = {**payload, "entry": matching_entries}

    processed_messages = 0
    duplicate_messages = 0
    event_type: str | None = None
    first_sender: str | None = None
    first_message_id: str | None = None
    first_message_text: str | None = None

    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            field = str(change.get("field") or "")
            event_type = field or event_type
            value = change.get("value") or {}

            if field == "smb_app_state_sync":
                for state_item in value.get("state_sync") or []:
                    contact = state_item.get("contact") or {}
                    phone = "".join(
                        character
                        for character in str(contact.get("phone_number") or "")
                        if character.isdigit()
                    )
                    profile_name = str(
                        contact.get("full_name") or contact.get("first_name") or ""
                    ).strip() or None
                    upsert_whatsapp_contact(phone=phone, profile_name=profile_name)
                continue

            if field == "history":
                for history_batch in value.get("history") or []:
                    for thread in history_batch.get("threads") or []:
                        contact_phone = "".join(
                            character
                            for character in str(thread.get("id") or "")
                            if character.isdigit()
                        )
                        if not contact_phone:
                            continue
                        for message in thread.get("messages") or []:
                            message_id = str(message.get("id") or "").strip()
                            if not message_id:
                                continue
                            message_type = str(message.get("type") or "unknown")
                            message_text, _ = extract_message_text(message)
                            media_id, media_mime_type, media_filename = _message_media(message)
                            sender_phone = "".join(
                                character
                                for character in str(message.get("from") or "")
                                if character.isdigit()
                            )
                            created_at = _timestamp_text(message.get("timestamp"))
                            message_status = str(
                                (message.get("history_context") or {}).get("status")
                                or "sent"
                            )
                            if sender_phone == contact_phone:
                                _, inserted, _ = record_incoming_message(
                                    payload=message,
                                    sender_phone=contact_phone,
                                    message_id=message_id,
                                    message_text=message_text,
                                    message_type=message_type,
                                    source="history",
                                    media_id=media_id,
                                    media_mime_type=media_mime_type,
                                    media_filename=media_filename,
                                    created_at=created_at,
                                )
                            else:
                                conversation = get_or_create_outbound_conversation(
                                    contact_phone=contact_phone
                                )
                                existed = get_message_by_provider_id(message_id) is not None
                                record_outbound_message(
                                    conversation_id=int(conversation["id"]),
                                    message_id=message_id,
                                    body=message_text or "",
                                    status=message_status,
                                    payload=message,
                                    source="history",
                                    message_type=message_type,
                                    media_id=media_id,
                                    media_mime_type=media_mime_type,
                                    media_filename=media_filename,
                                    created_at=created_at,
                                )
                                inserted = not existed
                            processed_messages += int(inserted)
                            duplicate_messages += int(not inserted)
                continue

            if field == "smb_message_echoes":
                for message in value.get("message_echoes") or []:
                    contact_phone = "".join(
                        character
                        for character in str(message.get("to") or "")
                        if character.isdigit()
                    )
                    message_id = str(message.get("id") or "").strip()
                    if not contact_phone or not message_id:
                        continue
                    conversation = get_or_create_outbound_conversation(
                        contact_phone=contact_phone
                    )
                    existed = get_message_by_provider_id(message_id) is not None
                    message_type = str(message.get("type") or "unknown")
                    message_text, _ = extract_message_text(message)
                    media_id, media_mime_type, media_filename = _message_media(message)
                    record_outbound_message(
                        conversation_id=int(conversation["id"]),
                        message_id=message_id,
                        body=message_text or "",
                        status="sent",
                        payload=message,
                        actor_name=settings.whatsapp_mobile_sender_name.strip() or "Celular",
                        source="business_app",
                        message_type=message_type,
                        media_id=media_id,
                        media_mime_type=media_mime_type,
                        media_filename=media_filename,
                        created_at=_timestamp_text(message.get("timestamp")),
                    )
                    inserted = not existed
                    processed_messages += int(inserted)
                    duplicate_messages += int(not inserted)
                    if inserted and media_id:
                        if message_type == "audio":
                            background_tasks.add_task(
                                download_and_store_audio,
                                message_id=message_id, media_id=media_id,
                                declared_mime=media_mime_type,
                            )
                        elif message_type in {"document", "image"}:
                            background_tasks.add_task(
                                download_and_store_file,
                                message_id=message_id, media_id=media_id,
                                message_type=message_type, filename=media_filename,
                                declared_mime=media_mime_type,
                            )
                continue

            for delivery_status in value.get("statuses") or []:
                update_message_status(
                    str(delivery_status.get("id") or ""),
                    str(delivery_status.get("status") or "unknown"),
                )

            names = _contacts_by_phone(value)
            for message in value.get("messages") or []:
                sender_phone = str(message.get("from") or "").strip()
                message_id = str(message.get("id") or "").strip()
                message_type = str(message.get("type") or "unknown").strip()
                message_text, interactive_id = extract_message_text(message)
                media_id, media_mime_type, media_filename = _message_media(message)
                if not sender_phone or not message_id:
                    logger.warning("Mensagem recebida sem remetente ou identificação.")
                    continue

                conversation, inserted, is_first_contact_message = record_incoming_message(
                    payload=message,
                    sender_phone=sender_phone,
                    message_id=message_id,
                    message_text=message_text,
                    message_type=message_type,
                    profile_name=names.get(sender_phone),
                    media_id=media_id,
                    media_mime_type=media_mime_type,
                    media_filename=media_filename,
                    created_at=_timestamp_text(message.get("timestamp")),
                )
                first_sender = first_sender or sender_phone
                first_message_id = first_message_id or message_id
                first_message_text = first_message_text or message_text

                if not inserted:
                    duplicate_messages += 1
                    continue

                processed_messages += 1
                if media_id:
                    if message_type == "audio":
                        background_tasks.add_task(
                            download_and_store_audio,
                            message_id=message_id, media_id=media_id,
                            declared_mime=media_mime_type,
                        )
                    elif message_type in {"document", "image"}:
                        background_tasks.add_task(
                            download_and_store_file,
                            message_id=message_id, media_id=media_id,
                            message_type=message_type, filename=media_filename,
                            declared_mime=media_mime_type,
                        )
                if settings.whatsapp_auto_menu_enabled:
                    automatic_reply = route_incoming_message(
                        conversation=conversation,
                        message_text=message_text,
                        interactive_id=interactive_id,
                        is_first_contact_message=is_first_contact_message,
                    )
                    if automatic_reply:
                        background_tasks.add_task(
                            send_and_record,
                            conversation_id=int(conversation["id"]),
                            recipient=sender_phone,
                            message=automatic_reply,
                        )

    save_whatsapp_event(
        payload=payload,
        event_type=event_type,
        sender_phone=first_sender,
        message_id=first_message_id,
        message_text=first_message_text,
    )
    logger.info(
        "Evento do WhatsApp processado. mensagens=%s duplicadas=%s",
        processed_messages,
        duplicate_messages,
    )
    return {
        "status": "received",
        "processed": True,
        "messages": processed_messages,
        "duplicates": duplicate_messages,
    }


@router.get("/api/whatsapp/onboarding/status")
async def whatsapp_onboarding_status(request: Request) -> dict[str, Any]:
    require_roles(request, ADMIN_ROLES)
    return {
        "ready": embedded_signup_ready(),
        "app_id": bool(settings.whatsapp_meta_app_id.strip()),
        "app_secret": bool(settings.whatsapp_app_secret.strip()),
        "configuration_id": bool(
            settings.whatsapp_embedded_signup_config_id.strip()
        ),
        "connections": list_whatsapp_business_connections(),
    }


@router.post("/api/whatsapp/onboarding/complete")
async def complete_whatsapp_onboarding(
    payload: EmbeddedSignupPayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, ADMIN_ROLES)
    _require_same_origin(request)
    try:
        inspected = await run_in_threadpool(
            inspect_authorized_business,
            code=payload.code,
            waba_id=payload.waba_id,
            phone_number_id=payload.phone_number_id,
            business_id=payload.business_id,
        )
    except WhatsAppOnboardingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    connection = save_whatsapp_business_connection(
        business_id=inspected.business_id,
        waba_id=inspected.waba_id,
        phone_number_id=inspected.phone_number_id,
        business_name=inspected.business_name,
        display_phone_number=inspected.display_phone_number,
        verified_name=inspected.verified_name,
        connected_by=str(user.get("display_name") or user.get("email") or ""),
        access_token=inspected.access_token,
        activate=True,
    )
    sync_requests: dict[str, Any] = {}
    sync_errors: list[str] = []
    for sync_type in ("smb_app_state_sync", "history"):
        try:
            sync_requests[sync_type] = await run_in_threadpool(
                whatsapp_client.request_app_data_sync, sync_type
            )
        except (WhatsAppConfigurationError, WhatsAppAPIError, ValueError) as exc:
            logger.warning("Sincronização inicial %s não solicitada: %s", sync_type, exc)
            sync_errors.append(str(exc))
    return {
        "ok": True,
        "connection": connection,
        "operational_configuration_changed": True,
        "sync_requests": sync_requests,
        "sync_errors": sync_errors,
    }


@router.post("/api/whatsapp/onboarding/connections/{connection_id}/disconnect")
async def disconnect_whatsapp_connection(
    connection_id: int, payload: DisconnectConnectionPayload, request: Request,
) -> dict[str, Any]:
    user = require_roles(request, ADMIN_ROLES)
    _require_same_origin(request)
    try:
        disconnected = await run_in_threadpool(
            disconnect_whatsapp_business_connection,
            connection_id,
            waba_id=payload.waba_id,
            disconnected_by=str(user.get("display_name") or user.get("email") or "Administrador"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "connection": disconnected}


@router.get("/api/whatsapp/sync-diagnostics")
async def whatsapp_sync_diagnostics_view(request: Request) -> dict[str, Any]:
    require_roles(request, ADMIN_ROLES)
    return await run_in_threadpool(whatsapp_sync_diagnostics)


@router.get("/api/whatsapp/status")
async def whatsapp_status(request: Request) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    active_connection = get_active_whatsapp_business_connection()
    has_saved_connection = bool(list_whatsapp_business_connections(limit=1))
    configured = {
        "phone_number_id": bool(
            (active_connection or {}).get("phone_number_id")
            or (settings.whatsapp_phone_number_id.strip() if not has_saved_connection else "")
        ),
        "business_account_id": bool(
            (active_connection or {}).get("waba_id")
            or (settings.whatsapp_business_account_id.strip() if not has_saved_connection else "")
        ),
        "access_token": bool(
            (active_connection or {}).get("has_access_token")
            or (settings.whatsapp_access_token.strip() if not has_saved_connection else "")
        ),
        "verify_token": bool(settings.whatsapp_verify_token.strip()),
        "app_secret": bool(settings.whatsapp_app_secret.strip()),
    }
    provider: dict[str, Any] | None = None
    provider_error: str | None = (
        "Nenhuma conta está ativa nesta central. Reconecte pela página de integração."
        if has_saved_connection and active_connection is None else None
    )
    locally_ready = settings.whatsapp_enabled and all(configured.values())
    if locally_ready:
        try:
            phone = await run_in_threadpool(whatsapp_client.get_phone_number_status)
            provider = {
                "display_phone_number": phone.get("display_phone_number"),
                "verified_name": phone.get("verified_name"),
                "quality_rating": phone.get("quality_rating"),
                "status": phone.get("status"),
            }
        except (WhatsAppConfigurationError, WhatsAppAPIError) as exc:
            provider_error = str(exc)

    return {
        "enabled": settings.whatsapp_enabled,
        "ready": locally_ready and provider is not None,
        "api_version": settings.whatsapp_api_version,
        "configured": configured,
        "provider": provider,
        "provider_error": provider_error,
        "automatic_menu": settings.whatsapp_auto_menu_enabled,
        "queues": queue_payloads(),
        "viewer": {
            "name": str(user.get("display_name") or ""),
            "first_name": _attendant_name(user),
            "role": str(user.get("role_code") or ""),
            "can_view_all": str(user.get("role_code") or "").lower() in ADMIN_ROLES,
            "auto_marks_read": str(user.get("role_code") or "").lower() not in ADMIN_ROLES,
        },
    }


@router.get("/api/whatsapp/conversations")
async def conversations(
    request: Request,
    queue_key: str | None = None,
    conversation_status: str | None = Query(default=None, alias="status"),
    label_id: int | None = None,
    view: Literal["all", "mine"] = "all",
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    can_view_all = str(user.get("role_code") or "").lower() in ADMIN_ROLES
    assignee_terms = None if can_view_all and view == "all" else _assignee_terms(user)
    return {
        "items": list_conversations(
            queue_key=queue_key,
            status=conversation_status,
            label_id=label_id,
            assignee_terms=assignee_terms,
            assignee_profile_id=(
                None if can_view_all and view == "all" else str(user.get("profile_id") or "") or None
            ),
            viewer_key=_user_key(user),
            limit=limit,
        )
    }


@router.get("/api/whatsapp/assignees")
async def whatsapp_assignees(request: Request) -> dict[str, Any]:
    user = require_roles(request, ADMIN_ROLES)
    return {"items": await run_in_threadpool(_assignable_profiles, user)}


@router.post("/api/whatsapp/conversations")
async def start_conversation(
    payload: StartConversationPayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    recipient = "".join(character for character in payload.phone if character.isdigit())
    if not 10 <= len(recipient) <= 15:
        raise HTTPException(
            status_code=422,
            detail="Informe o telefone com DDI e DDD, usando de 10 a 15 dígitos.",
        )

    profile_name = (payload.profile_name or "").strip() or None
    reference = f"TESTE-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": profile_name or "Cliente"},
                {"type": "text", "text": reference},
                {"type": "text", "text": datetime.now().strftime("%b %d, %Y")},
            ],
        }
    ]

    try:
        response = await run_in_threadpool(
            whatsapp_client.send_template,
            recipient=recipient,
            template_name=settings.whatsapp_initial_template_name,
            language_code=settings.whatsapp_initial_template_language,
            components=components,
        )
    except (WhatsAppConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WhatsAppAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    conversation = get_or_create_outbound_conversation(
        contact_phone=recipient,
        profile_name=profile_name,
    )
    messages = response.payload.get("messages") or []
    message_id = str(messages[0].get("id") or "") if messages else None
    body = f"Confirmação de teste enviada ({reference})."
    saved = record_outbound_message(
        conversation_id=int(conversation["id"]),
        message_id=message_id or None,
        body=body,
        status="sent",
        payload=response.payload,
        actor_name=_attendant_name(user),
        source="api",
    )
    conversation = update_conversation(
        int(conversation["id"]),
        assignee=str(user.get("display_name") or ""),
        assignee_profile_id=str(user.get("profile_id") or "") or None,
        status="open",
        mark_read=True,
    )
    return {
        "conversation": conversation,
        "message": saved,
        "provider": response.payload,
    }


@router.get("/api/whatsapp/conversations/{conversation_id}")
async def conversation_detail(
    conversation_id: int,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    conversation = _require_conversation_access(user, conversation_id)
    return {
        "conversation": conversation,
        "messages": list_messages(conversation_id),
        "labels": get_contact_labels(str(conversation["contact_phone"])),
    }


@router.patch("/api/whatsapp/conversations/{conversation_id}")
async def change_conversation(
    conversation_id: int,
    payload: ConversationUpdatePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    _require_conversation_access(user, conversation_id)
    is_admin = str(user.get("role_code") or "").lower() in ADMIN_ROLES
    profile_id_provided = "assignee_profile_id" in payload.model_fields_set
    if not is_admin and (
        payload.queue_key is not None
        or payload.assignee is not None
        or profile_id_provided
    ):
        raise HTTPException(
            status_code=403,
            detail="Somente o administrativo pode transferir atendimentos.",
        )
    queue = None
    if payload.queue_key:
        queue = QUEUE_BY_KEY.get(payload.queue_key)
        if queue is None:
            raise HTTPException(status_code=422, detail="Fila inválida.")

    assignment_kwargs: dict[str, Any] = {}
    if profile_id_provided:
        if payload.assignee_profile_id is None:
            assignment_kwargs = {"assignee": "", "assignee_profile_id": None}
        else:
            profile = await run_in_threadpool(
                _assignable_profile, user, payload.assignee_profile_id
            )
            assignment_kwargs = {
                "assignee": profile["name"],
                "assignee_profile_id": profile["id"],
            }
    elif payload.assignee is not None:
        assignment_kwargs = {"assignee": payload.assignee}
    elif queue:
        assignment_kwargs = {"assignee": queue.default_assignee}

    conversation = update_conversation(
        conversation_id,
        queue_key=queue.key if queue else payload.queue_key,
        queue_label=queue.label if queue else None,
        status=payload.status,
        mark_read=False,
        **assignment_kwargs,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    return {"conversation": conversation}


@router.post("/api/whatsapp/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    payload: SendMessagePayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    conversation = _require_conversation_access(user, conversation_id)
    actor_name = _attendant_name(user)
    original_message = payload.message.strip()
    wire_message = f"{actor_name}: {original_message}"

    try:
        response = await run_in_threadpool(
            whatsapp_client.send_text,
            recipient=str(conversation["contact_phone"]),
            message=wire_message,
        )
    except WhatsAppConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WhatsAppAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    messages = response.payload.get("messages") or []
    message_id = str(messages[0].get("id") or "") if messages else None
    saved = record_outbound_message(
        conversation_id=conversation_id,
        message_id=message_id or None,
        body=original_message,
        status="sent",
        payload=response.payload,
        actor_name=actor_name,
        source="api",
    )
    update_conversation(conversation_id, status="open", mark_read=False)
    return {"message": saved, "provider": response.payload}


@router.post("/api/whatsapp/conversations/{conversation_id}/read")
async def read_conversation(
    conversation_id: int,
    payload: ReadConversationPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    conversation = _require_conversation_access(user, conversation_id)
    message_ids = mark_conversation_read_for_user(
        conversation_id, user_key=_user_key(user)
    )
    assignee = str(conversation.get("assignee") or "").strip().lower()
    is_admin = str(user.get("role_code") or "").lower() in ADMIN_ROLES
    assigned_profile_id = str(conversation.get("assignee_profile_id") or "")
    owns_conversation = (
        assigned_profile_id == str(user.get("profile_id") or "")
        if assigned_profile_id
        else assignee in {term.lower() for term in _assignee_terms(user)}
    )
    should_propagate = payload.propagate_to_whatsapp and (
        owns_conversation or not is_admin
    )
    if should_propagate and message_ids:
        background_tasks.add_task(_propagate_read_receipts, message_ids)
    return {
        "ok": True,
        "read_messages": len(message_ids),
        "propagated_to_whatsapp": False,
        "queued_for_whatsapp": bool(should_propagate and message_ids),
    }


def _propagate_read_receipts(message_ids: list[str]) -> None:
    for message_id in message_ids:
        try:
            whatsapp_client.mark_message_read(message_id)
            mark_message_meta_read(message_id)
        except (WhatsAppConfigurationError, WhatsAppAPIError) as exc:
            logger.warning("Leitura local salva, mas a Meta recusou a confirmação: %s", exc)


@router.post("/api/whatsapp/conversations/{conversation_id}/assume")
async def assume_conversation(
    conversation_id: int, request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    _require_conversation_access(user, conversation_id)
    updated = update_conversation(
        conversation_id,
        assignee=str(user.get("display_name") or _attendant_name(user)),
        assignee_profile_id=str(user.get("profile_id") or "") or None,
        status="open",
        mark_read=False,
    )
    message_ids = mark_conversation_read_for_user(
        conversation_id, user_key=_user_key(user)
    )
    if message_ids:
        background_tasks.add_task(_propagate_read_receipts, message_ids)
    return {"conversation": updated, "queued_for_whatsapp": bool(message_ids)}


@router.post("/api/whatsapp/conversations/{conversation_id}/messages/audio")
async def send_audio_message(
    conversation_id: int,
    payload: SendAudioPayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    conversation = _require_conversation_access(user, conversation_id)
    _require_same_origin(request)
    try:
        content, filename, mime_type = decode_audio_upload(
            content_base64=payload.content_base64,
            filename=payload.filename,
            declared_mime=payload.mime_type,
        )
        extension = Path(filename).suffix
        media_path = save_audio_file(content=content, extension=extension)
        media_id = await run_in_threadpool(
            whatsapp_client.upload_audio,
            content=content, filename=filename, mime_type=mime_type
        )
        response = await run_in_threadpool(
            whatsapp_client.send_audio,
            recipient=str(conversation["contact_phone"]), media_id=media_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WhatsAppConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WhatsAppAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    messages = response.payload.get("messages") or []
    message_id = str(messages[0].get("id") or "") if messages else None
    saved = record_outbound_message(
        conversation_id=conversation_id,
        message_id=message_id or None,
        body="",
        status="sent",
        payload=response.payload,
        actor_name=_attendant_name(user),
        source="api",
        message_type="audio",
        media_id=media_id,
        media_mime_type=mime_type,
        media_filename=filename,
        media_path=media_path,
    )
    update_conversation(conversation_id, status="open", mark_read=False)
    return {"message": saved, "provider": response.payload}


@router.get("/api/whatsapp/messages/{message_row_id}/media")
async def whatsapp_message_media(
    message_row_id: int, request: Request,
    download: bool = False,
) -> FileResponse:
    user = require_roles(request, WHATSAPP_ROLES)
    message = get_message(message_row_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    _require_conversation_access(user, int(message["conversation_id"]))
    media_path = str(message.get("media_path") or "")
    message_type = str(message.get("message_type") or "")
    if not media_path and message_type in {"document", "image"} and message.get("media_id"):
        await run_in_threadpool(
            download_and_store_file,
            message_id=str(message.get("message_id") or ""),
            media_id=str(message["media_id"]),
            message_type=message_type,
            filename=str(message.get("media_filename") or "") or None,
            declared_mime=str(message.get("media_mime_type") or "") or None,
        )
        message = get_message(message_row_id) or message
        media_path = str(message.get("media_path") or "")
    if not media_path:
        raise HTTPException(
            status_code=404,
            detail="O arquivo ainda não está disponível. Tente novamente em instantes.",
        )
    try:
        path = resolve_media_file(media_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=str(message.get("media_mime_type") or "application/octet-stream"),
        filename=str(message.get("media_filename") or path.name),
        content_disposition_type=("attachment" if download or message_type == "document" else "inline"),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/whatsapp/conversations/{conversation_id}/messages/file")
async def send_file_message(
    conversation_id: int, payload: SendFilePayload, request: Request,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    conversation = _require_conversation_access(user, conversation_id)
    _require_same_origin(request)
    try:
        content, filename, mime_type, message_type = decode_file_upload(
            content_base64=payload.content_base64,
            filename=payload.filename,
            declared_mime=payload.mime_type,
        )
        media_path = save_media_file(content=content, extension=Path(filename).suffix)
        media_id = await run_in_threadpool(
            whatsapp_client.upload_file,
            content=content, filename=filename, mime_type=mime_type,
        )
        response = await run_in_threadpool(
            whatsapp_client.send_file,
            recipient=str(conversation["contact_phone"]),
            media_id=media_id, message_type=message_type, filename=filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except WhatsAppConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WhatsAppAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Não foi possível guardar o arquivo enviado.") from exc
    messages = response.payload.get("messages") or []
    message_id = str(messages[0].get("id") or "") if messages else None
    saved = record_outbound_message(
        conversation_id=conversation_id,
        message_id=message_id or None,
        body="",
        status="sent",
        payload=response.payload,
        actor_name=_attendant_name(user),
        source="api",
        message_type=message_type,
        media_id=media_id,
        media_mime_type=mime_type,
        media_filename=filename,
        media_path=media_path,
    )
    update_conversation(conversation_id, status="open", mark_read=False)
    return {"message": saved, "provider": response.payload}


@router.get("/api/whatsapp/labels")
async def whatsapp_labels(request: Request) -> dict[str, Any]:
    require_roles(request, WHATSAPP_ROLES)
    return {"items": list_labels()}


@router.post("/api/whatsapp/labels")
async def add_whatsapp_label(
    payload: LabelPayload, request: Request
) -> dict[str, Any]:
    require_roles(request, WHATSAPP_ROLES)
    _require_same_origin(request)
    try:
        label = create_label(name=payload.name, color=payload.color)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"label": label}


@router.put("/api/whatsapp/conversations/{conversation_id}/labels")
async def change_conversation_labels(
    conversation_id: int,
    payload: ContactLabelsPayload,
    request: Request,
) -> dict[str, Any]:
    user = require_roles(request, WHATSAPP_ROLES)
    conversation = _require_conversation_access(user, conversation_id)
    _require_same_origin(request)
    try:
        labels = set_contact_labels(
            contact_phone=str(conversation["contact_phone"]),
            label_ids=payload.label_ids,
            primary_label_id=payload.primary_label_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"labels": labels}


@router.post("/api/whatsapp/sync/{sync_type}")
async def request_whatsapp_sync(
    sync_type: Literal["history", "smb_app_state_sync"], request: Request
) -> dict[str, Any]:
    require_roles(request, ADMIN_ROLES)
    _require_same_origin(request)
    try:
        provider = await run_in_threadpool(whatsapp_client.request_app_data_sync, sync_type)
    except (WhatsAppConfigurationError, WhatsAppAPIError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "provider": provider}
