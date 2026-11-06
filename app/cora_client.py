from __future__ import annotations

import asyncio
import ssl
import time
from pathlib import Path
from typing import Any

import httpx

from .settings import settings


class CoraError(RuntimeError):
    """Erro seguro e apresentável da integração com a Cora."""


class CoraClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        environment = settings.cora_environment.strip().lower()
        if environment in {"stage", "staging", "test", "sandbox"}:
            return "https://matls-clients.api.stage.cora.com.br"
        if environment != "production":
            raise CoraError("Ambiente da Cora inválido.")
        return "https://matls-clients.api.cora.com.br"

    def _ssl_context(self) -> ssl.SSLContext:
        certificate = Path(settings.cora_certificate_path)
        private_key = Path(settings.cora_private_key_path)
        if not settings.cora_enabled:
            raise CoraError("A integração com a Cora está desativada.")
        if not settings.cora_client_id.strip():
            raise CoraError("O client ID da Cora não está configurado.")
        if not certificate.is_file() or not private_key.is_file():
            raise CoraError("O certificado ou a chave privada da Cora não está disponível.")

        context = ssl.create_default_context()
        try:
            context.load_cert_chain(
                certfile=str(certificate),
                keyfile=str(private_key),
            )
        except (OSError, ssl.SSLError) as exc:
            raise CoraError("Não foi possível carregar as credenciais da Cora.") from exc
        return context

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            try:
                async with httpx.AsyncClient(
                    verify=self._ssl_context(),
                    timeout=settings.cora_timeout_seconds,
                ) as client:
                    response = await client.post(
                        f"{self.base_url}/token",
                        data={
                            "grant_type": "client_credentials",
                            "client_id": settings.cora_client_id.strip(),
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise CoraError("Falha ao autenticar na Cora.") from exc

            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise CoraError("A Cora não retornou um token de acesso.")
            expires_in = max(60, int(payload.get("expires_in") or 3600))
            self._token = token
            self._token_expires_at = time.monotonic() + expires_in - min(60, expires_in // 4)
            return token

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        token = await self._access_token()
        request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        request_headers.update(headers or {})
        try:
            async with httpx.AsyncClient(
                verify=self._ssl_context(),
                timeout=settings.cora_timeout_seconds,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json,
                    headers=request_headers,
                )
                response.raise_for_status()
                if not response.content:
                    return {}
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self._token = None
                self._token_expires_at = 0.0
            raise CoraError(
                f"A Cora recusou a operação (HTTP {exc.response.status_code})."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CoraError("Falha de comunicação com a Cora.") from exc
        if not isinstance(payload, dict):
            raise CoraError("A Cora retornou uma resposta inesperada.")
        return payload

    async def invoice(self, invoice_id: str) -> dict[str, Any]:
        if not invoice_id.startswith("inv_") or len(invoice_id) > 120:
            raise CoraError("Identificador de boleto inválido.")
        return await self.request("GET", f"/v2/invoices/{invoice_id}")

    async def create_endpoint(
        self,
        *,
        url: str,
        resource: str,
        trigger: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/endpoints/",
            json={"url": url, "resource": resource, "trigger": trigger},
            headers={"Idempotency-Key": idempotency_key},
        )


cora_client = CoraClient()
