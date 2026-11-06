from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

import requests
from requests.auth import HTTPBasicAuth

from app.settings import settings


class IPMConfigurationError(RuntimeError):
    """Configuração obrigatória da integração IPM ausente ou inválida."""


class IPMAPIError(RuntimeError):
    """Erro durante a comunicação com o WebService da IPM."""


@dataclass
class IPMResponse:
    status_code: int
    raw_xml: str
    parsed: dict[str, Any]


class IPMClient:
    @property
    def base_url(self) -> str:
        city = settings.nfse_ipm_city.strip().lower()

        if not city:
            raise IPMConfigurationError(
                "NFSE_IPM_CITY não foi configurado."
            )

        return (
            "https://cidade_demo.atende.net/"
            "?pg=rest&service=WNERestServiceNFSe"
        )

    def validate_configuration(self) -> None:
        if not settings.nfse_ipm_enabled:
            raise IPMConfigurationError(
                "A integração de NFS-e da IPM está desativada."
            )

        if not settings.nfse_ipm_username.strip():
            raise IPMConfigurationError(
                "NFSE_IPM_USERNAME não foi configurado."
            )

        if not settings.nfse_ipm_password:
            raise IPMConfigurationError(
                "NFSE_IPM_PASSWORD não foi configurado."
            )

        _ = self.base_url

    def send_xml(self, xml_content: str) -> IPMResponse:
        self.validate_configuration()

        xml_content = str(xml_content or "").strip()

        if not xml_content:
            raise ValueError("O XML da NFS-e não pode estar vazio.")

        try:
            ElementTree.fromstring(xml_content)
        except ElementTree.ParseError as exc:
            raise ValueError(
                f"O XML da NFS-e é inválido: {exc}"
            ) from exc

        try:
            response = requests.post(
                self.base_url,
                files={
                    "File": (
                        "nfse_teste.xml",
                        xml_content.encode("utf-8"),
                        "application/xml",
                     ),
                },
                headers={
                    "Accept": "application/xml, text/xml, application/json",
                },
                auth=HTTPBasicAuth(
                    settings.nfse_ipm_username.strip(),
                    settings.nfse_ipm_password,
                ),
                timeout=settings.nfse_ipm_timeout_seconds,
            )

        except requests.RequestException as exc:
            raise IPMAPIError(
                f"Não foi possível conectar ao WebService da IPM: {exc}"
            ) from exc

        response_text = response.text.strip()

        if response.status_code not in {200, 201}:
            raise IPMAPIError(
                f"O WebService da IPM retornou status "
                f"{response.status_code}: {response_text}"
            )

        return IPMResponse(
            status_code=response.status_code,
            raw_xml=response_text,
            parsed=self._parse_response(response_text),
        )

    @staticmethod
    def _parse_response(xml_content: str) -> dict[str, Any]:
        if not xml_content:
            return {}

        try:
            root = ElementTree.fromstring(xml_content)
        except ElementTree.ParseError:
            return {
                "raw_response": xml_content,
            }

        result: dict[str, Any] = {}

        for element in root.iter():
            tag = element.tag.split("}")[-1]
            value = (element.text or "").strip()

            if value:
                if tag in result:
                    current = result[tag]

                    if not isinstance(current, list):
                        result[tag] = [current]

                    result[tag].append(value)
                else:
                    result[tag] = value

        return result


nfse_ipm_client = IPMClient()