from __future__ import annotations

from typing import Any

from .settings import settings


def _sdk():
    """Importa o SDK apenas quando uma integração Supabase é realmente usada.

    O modo demo não precisa do pacote em runtime, o que deixa o case mais simples
    de executar e testar localmente.
    """
    try:
        from supabase import create_client
        from supabase.client import ClientOptions
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "Supabase SDK indisponível. Instale as dependências do requirements.txt "
            "ou use DEMO_MODE=true."
        ) from exc
    return create_client, ClientOptions


def _options() -> Any:
    _, ClientOptions = _sdk()
    return ClientOptions(
        postgrest_client_timeout=settings.supabase_request_timeout_seconds,
        storage_client_timeout=settings.supabase_request_timeout_seconds,
        auto_refresh_token=False,
        persist_session=False,
        schema="public",
    )


def create_public_supabase() -> Any:
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL não configurada.")
    if not settings.supabase_publishable_key:
        raise RuntimeError("SUPABASE_PUBLISHABLE_KEY não configurada.")
    create_client, _ = _sdk()
    return create_client(
        settings.supabase_url,
        settings.supabase_publishable_key,
        options=_options(),
    )


def create_admin_supabase() -> Any:
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL não configurada.")
    if not settings.supabase_secret_key:
        raise RuntimeError("SUPABASE_SECRET_KEY não configurada.")
    create_client, _ = _sdk()
    return create_client(
        settings.supabase_url,
        settings.supabase_secret_key,
        options=_options(),
    )
