from __future__ import annotations

from supabase import Client, create_client
from supabase.client import ClientOptions

from .settings import settings


def _options() -> ClientOptions:
    return ClientOptions(
        postgrest_client_timeout=settings.supabase_request_timeout_seconds,
        storage_client_timeout=settings.supabase_request_timeout_seconds,
        auto_refresh_token=False,
        persist_session=False,
        schema="public",
    )


def create_public_supabase() -> Client:
    if settings.demo_mode or not settings.integrations_enabled:
        raise RuntimeError("Supabase desabilitado: use uma instalação operacional configurada.")
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL não configurada.")

    if not settings.supabase_publishable_key:
        raise RuntimeError("SUPABASE_PUBLISHABLE_KEY não configurada.")

    return create_client(
        settings.supabase_url,
        settings.supabase_publishable_key,
        options=_options(),
    )


def create_admin_supabase() -> Client:
    if settings.demo_mode or not settings.integrations_enabled:
        raise RuntimeError("Supabase desabilitado: use uma instalação operacional configurada.")
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL não configurada.")

    if not settings.supabase_secret_key:
        raise RuntimeError("SUPABASE_SECRET_KEY não configurada.")

    return create_client(
        settings.supabase_url,
        settings.supabase_secret_key,
        options=_options(),
    )