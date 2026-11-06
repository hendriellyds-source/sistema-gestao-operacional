from __future__ import annotations

from typing import Any

import httpx

from .settings import settings


class SelsynError(RuntimeError):
    pass


class SelsynClient:
    def __init__(self) -> None:
        self.base_url = settings.selsyn_base_url.rstrip("/")
        self.api_key = settings.selsyn_api_key.strip()
        self.timeout = settings.selsyn_timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        if not self.api_key:
            raise SelsynError("A chave SELSYN_API_KEY não foi configurada.")

        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise SelsynError("A Selsyn demorou mais que o limite configurado para responder.") from exc
        except httpx.RequestError as exc:
            raise SelsynError(f"Não foi possível conectar à Selsyn: {exc}") from exc

        if response.status_code in (401, 403):
            raise SelsynError(
                f"Acesso negado pela Selsyn ({response.status_code}). "
                "Verifique a API Key e se a permissão do endpoint está liberada."
            )
        if response.status_code == 429:
            raise SelsynError("A Selsyn recusou temporariamente as consultas por excesso de requisições.")
        if response.is_error:
            raise SelsynError(
                f"Erro recebido da Selsyn: HTTP {response.status_code}. "
                f"Resposta: {response.text[:700]}"
            )
        if response.status_code == 204 or not response.content:
            return {"ok": True, "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            return {"ok": True, "status_code": response.status_code, "text": response.text}

    async def live_positions(self) -> Any:
        return await self.request("GET", "/v1/integracao/gdr/posicao/aovivo")

    async def live_position_by_identifier(self, identifier: str) -> Any:
        return await self.request("GET", f"/v1/integracao/gdr/posicao/aovivo/{identifier.upper()}")

    async def rastreavel_by_identifier(self, identifier: str) -> Any:
        return await self.request("GET", f"/v1/integracao/gdr/rastreavel/{identifier.upper()}")

    async def export_positions(
        self,
        from_position_id: int,
        *,
        size: int = 1000,
        page: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"size": size, "page": page}
        if date_from:
            params["dataInicial"] = date_from
        if date_to:
            params["dataFinal"] = date_to
        return await self.request(
            "GET",
            f"/v1/integracao/gdr/posicao/exportar/{from_position_id}",
            params=params,
        )

    async def recent_alerts(self) -> Any:
        return await self.request("GET", "/v1/integracao/alerta")

    async def search_alerts(
        self,
        date_from: str,
        date_to: str,
        *,
        status: str | None = None,
        event_type: str | None = None,
        vehicle_id: int | None = None,
        driver_id: int | None = None,
        monitored: bool | None = None,
        page: int = 0,
        size: int = 500,
    ) -> Any:
        params: dict[str, Any] = {
            "dataInicial": date_from,
            "dataFinal": date_to,
            "page": page,
            "size": size,
        }
        if status:
            params["status"] = status
        if event_type:
            params["tipo"] = event_type
        if vehicle_id is not None:
            params["rastreavel"] = vehicle_id
        if driver_id is not None:
            params["motorista"] = driver_id
        if monitored is not None:
            params["monitoramento"] = str(monitored).lower()
        return await self.request("GET", "/v2/alerta", params=params)

    async def event_by_id(self, event_id: int) -> Any:
        return await self.request("GET", f"/v2/alerta/{event_id}")

    async def add_event_comment(self, event_id: int, comment: str) -> Any:
        return await self.request("PUT", f"/v2/alerta/{event_id}", json_body=comment)

    async def move_event(self, event_id: int, status: str) -> Any:
        return await self.request("PUT", f"/v2/alerta/mover/{status}/{event_id}")

    async def close_event(self, event_id: int, comment: str) -> Any:
        return await self.request("PUT", f"/v2/alerta/fechar/{event_id}", json_body=comment)

    async def report_claim(self, payload: dict[str, Any]) -> Any:
        return await self.request("POST", "/v2/alerta/reportar/SINISTRO", json_body=payload)

    async def dashboard_current(self, date_from: str, date_to: str) -> Any:
        return await self.request(
            "GET", "/dashboard/cliente",
            params={"dataInicial": date_from, "dataFinal": date_to},
        )

    async def dashboard_fuel(self, date_from: str, date_to: str, vehicle_id: int | None = None) -> Any:
        params: dict[str, Any] = {
            "dataInicial": date_from,
            "dataFinal": date_to,
            "categoria": "VEICULO",
            "arla": "false",
        }
        if vehicle_id is not None:
            params["rastreavel"] = vehicle_id
        return await self.request("GET", "/dashboard/cliente/abastecimento", params=params)

    async def dashboard_maintenance(self, date_from: str, date_to: str, vehicle_id: int | None = None) -> Any:
        params: dict[str, Any] = {"dataInicial": date_from, "dataFinal": date_to}
        if vehicle_id is not None:
            params["rastreavel"] = vehicle_id
        return await self.request("GET", "/dashboard/cliente/manutencao", params=params)

    async def dashboard_expenses(self, date_from: str, date_to: str, vehicle_id: int | None = None) -> Any:
        params: dict[str, Any] = {"dataInicial": date_from, "dataFinal": date_to}
        if vehicle_id is not None:
            params["rastreavel"] = vehicle_id
        return await self.request("GET", "/dashboard/cliente/gasto", params=params)

    async def dashboard_driving(self, date_from: str, date_to: str, entity: str = "rastreavel") -> Any:
        if entity not in {"rastreavel", "motorista"}:
            raise ValueError("A entidade deve ser rastreavel ou motorista.")
        return await self.request(
            "GET", f"/dashboard/cliente/avaliacao-conducao/{entity}",
            params={"dataInicial": date_from, "dataFinal": date_to},
        )
