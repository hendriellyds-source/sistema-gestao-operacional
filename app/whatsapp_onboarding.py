from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

import requests

from app.settings import settings


class WhatsAppOnboardingError(RuntimeError):
    """Raised when Meta cannot complete an Embedded Signup operation."""


@dataclass(frozen=True)
class WhatsAppBusinessConnection:
    business_id: str | None
    waba_id: str
    phone_number_id: str | None
    business_name: str | None
    display_phone_number: str | None
    verified_name: str | None
    webhook_subscribed: bool
    access_token: str = field(repr=False)


def embedded_signup_ready() -> bool:
    return all(
        (
            settings.whatsapp_meta_app_id.strip(),
            settings.whatsapp_app_secret.strip(),
            settings.whatsapp_embedded_signup_config_id.strip(),
        )
    )


def _graph_url(path: str) -> str:
    version = settings.whatsapp_api_version.strip() or "v26.0"
    return f"https://graph.facebook.com/{version}/{path.lstrip('/')}"


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"A Meta retornou o status {response.status_code}."
    message = str((payload.get("error") or {}).get("message") or "").strip()
    return message or f"A Meta retornou o status {response.status_code}."


def exchange_embedded_signup_code(code: str) -> str:
    if not embedded_signup_ready():
        raise WhatsAppOnboardingError(
            "O Cadastro Incorporado ainda não está configurado no servidor."
        )

    try:
        response = requests.get(
            _graph_url("oauth/access_token"),
            params={
                "client_id": settings.whatsapp_meta_app_id.strip(),
                "client_secret": settings.whatsapp_app_secret.strip(),
                "code": code,
            },
            timeout=settings.whatsapp_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise WhatsAppOnboardingError(
            "Não foi possível acessar a Meta para concluir a autorização."
        ) from exc

    if not response.ok:
        raise WhatsAppOnboardingError(_error_message(response))
    token = str(response.json().get("access_token") or "").strip()
    if not token:
        raise WhatsAppOnboardingError(
            "A Meta não retornou uma credencial de autorização válida."
        )
    return token


def _graph_get(path: str, *, access_token: str, fields: str) -> dict[str, Any]:
    app_secret = settings.whatsapp_app_secret.strip()
    appsecret_proof = hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    try:
        response = requests.get(
            _graph_url(path),
            params={
                "fields": fields,
                "access_token": access_token,
                "appsecret_proof": appsecret_proof,
            },
            timeout=settings.whatsapp_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise WhatsAppOnboardingError(
            "Não foi possível consultar os ativos autorizados na Meta."
        ) from exc
    if not response.ok:
        raise WhatsAppOnboardingError(_error_message(response))
    return response.json()


def _graph_post(path: str, *, access_token: str) -> dict[str, Any]:
    """Call a WhatsApp Graph mutation without exposing the access token."""
    app_secret = settings.whatsapp_app_secret.strip()
    appsecret_proof = hmac.new(
        app_secret.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    try:
        response = requests.post(
            _graph_url(path),
            headers={"Authorization": f"Bearer {access_token}"},
            params={"appsecret_proof": appsecret_proof},
            timeout=settings.whatsapp_timeout_seconds,
        )
    except requests.RequestException as exc:
        raise WhatsAppOnboardingError(
            "Não foi possível ativar o recebimento de mensagens na Meta."
        ) from exc
    if not response.ok:
        raise WhatsAppOnboardingError(_error_message(response))
    return response.json()


def subscribe_app_to_waba(*, waba_id: str, access_token: str) -> None:
    """Subscribe the app once so Meta sends this WABA's events to our webhook."""
    payload = _graph_post(
        f"{waba_id}/subscribed_apps",
        access_token=access_token,
    )
    if payload.get("success") is not True:
        raise WhatsAppOnboardingError(
            "A Meta autorizou a conta, mas não confirmou o recebimento de mensagens."
        )


def inspect_authorized_business(
    *,
    code: str,
    waba_id: str,
    phone_number_id: str | None,
    business_id: str | None,
) -> WhatsAppBusinessConnection:
    """Exchange the code, inspect assets, and prepare the authorized connection."""
    access_token = exchange_embedded_signup_code(code)
    business_account = _graph_get(
        waba_id,
        access_token=access_token,
        fields="id,name",
    )

    resolved_phone_number_id = (phone_number_id or "").strip() or None
    phone: dict[str, Any] = {}
    if resolved_phone_number_id:
        phone = _graph_get(
            resolved_phone_number_id,
            access_token=access_token,
            fields="id,display_phone_number,verified_name,quality_rating",
        )
    else:
        phone_numbers = _graph_get(
            f"{waba_id}/phone_numbers",
            access_token=access_token,
            fields="id,display_phone_number,verified_name,quality_rating",
        )
        rows = phone_numbers.get("data") or []
        if rows:
            phone = rows[0]
            resolved_phone_number_id = str(phone.get("id") or "").strip() or None

    subscribe_app_to_waba(waba_id=waba_id, access_token=access_token)

    return WhatsAppBusinessConnection(
        business_id=(business_id or "").strip() or None,
        waba_id=str(business_account.get("id") or waba_id),
        phone_number_id=resolved_phone_number_id,
        business_name=str(business_account.get("name") or "").strip() or None,
        display_phone_number=(
            str(phone.get("display_phone_number") or "").strip() or None
        ),
        verified_name=str(phone.get("verified_name") or "").strip() or None,
        webhook_subscribed=True,
        access_token=access_token,
    )
