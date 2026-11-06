from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.settings import settings
from app.whatsapp_store import (
    get_active_whatsapp_credentials,
    list_whatsapp_business_connections,
)


class WhatsAppConfigurationError(RuntimeError):
    pass


class WhatsAppAPIError(RuntimeError):
    pass


@dataclass
class WhatsAppSendResponse:
    status_code: int
    payload: dict[str, Any]


class WhatsAppClient:
    @staticmethod
    def _error_message(response: requests.Response, payload: dict[str, Any]) -> str:
        error = payload.get("error") or {}
        message = str(error.get("message") or "A Meta recusou a solicitação.")
        code = error.get("code")
        return f"{message}{f' (código {code})' if code else ''}"

    def _operational_credentials(self) -> tuple[str, str]:
        if not settings.whatsapp_enabled:
            raise WhatsAppConfigurationError(
                "O envio do WhatsApp está desativado."
            )

        if not settings.whatsapp_api_version.strip():
            raise WhatsAppConfigurationError(
                "WHATSAPP_API_VERSION não configurado."
            )

        try:
            active_credentials = get_active_whatsapp_credentials()
        except RuntimeError as exc:
            raise WhatsAppConfigurationError(str(exc)) from exc

        if active_credentials is None and list_whatsapp_business_connections(limit=1):
            raise WhatsAppConfigurationError(
                "Há uma conta WhatsApp cadastrada, mas sem credencial operacional. "
                "Autorize novamente a conexão oficial antes de enviar mensagens."
            )

        phone_number_id = (
            active_credentials[0]
            if active_credentials
            else settings.whatsapp_phone_number_id.strip()
        )
        access_token = (
            active_credentials[1]
            if active_credentials
            else settings.whatsapp_access_token.strip()
        )

        if not phone_number_id:
            raise WhatsAppConfigurationError(
                "WHATSAPP_PHONE_NUMBER_ID não configurado."
            )

        if not access_token:
            raise WhatsAppConfigurationError(
                "WHATSAPP_ACCESS_TOKEN não configurado."
            )
        return phone_number_id, access_token

    def validate_configuration(self) -> None:
        self._operational_credentials()

    @property
    def messages_url(self) -> str:
        version = settings.whatsapp_api_version.strip()
        phone_number_id, _ = self._operational_credentials()

        return (
            f"https://graph.facebook.com/"
            f"{version}/{phone_number_id}/messages"
        )

    @property
    def phone_number_url(self) -> str:
        version = settings.whatsapp_api_version.strip()
        phone_number_id, _ = self._operational_credentials()
        return f"https://graph.facebook.com/{version}/{phone_number_id}"

    def get_phone_number_status(self) -> dict[str, Any]:
        """Validate the token and return only non-secret phone metadata."""
        phone_number_id, access_token = self._operational_credentials()
        version = settings.whatsapp_api_version.strip()
        phone_number_url = (
            f"https://graph.facebook.com/{version}/{phone_number_id}"
        )
        try:
            response = requests.get(
                phone_number_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
                params={
                    "fields": (
                        "display_phone_number,verified_name,"
                        "quality_rating,status"
                    )
                },
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(
                f"Falha ao acessar a API do WhatsApp: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}

        if response.status_code != 200:
            error = payload.get("error") or {}
            code = error.get("code")
            message = error.get("message") or "credencial recusada pela Meta"
            suffix = f" (código {code})" if code else ""
            raise WhatsAppAPIError(f"{message}{suffix}")
        return payload

    def send_text(
        self,
        *,
        recipient: str,
        message: str,
        preview_url: bool = False,
    ) -> WhatsAppSendResponse:
        phone_number_id, access_token = self._operational_credentials()
        version = settings.whatsapp_api_version.strip()
        messages_url = (
            f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
        )

        recipient = "".join(
            character
            for character in recipient
            if character.isdigit()
        )

        if not recipient:
            raise ValueError(
                "O telefone do destinatário não foi informado."
            )

        message = message.strip()

        if not message:
            raise ValueError(
                "A mensagem não pode estar vazia."
            )

        try:
            response = requests.post(
                messages_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": "text",
                    "text": {
                        "preview_url": preview_url,
                        "body": message,
                    },
                },
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(
                f"Falha ao acessar a API do WhatsApp: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {
                "raw_response": response.text,
            }

        if response.status_code not in {200, 201}:
            raise WhatsAppAPIError(self._error_message(response, payload))

        return WhatsAppSendResponse(
            status_code=response.status_code,
            payload=payload,
        )

    def mark_message_read(self, message_id: str) -> None:
        message_id = str(message_id or "").strip()
        if not message_id:
            return
        _, access_token = self._operational_credentials()
        try:
            response = requests.put(
                self.messages_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                },
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(
                f"Falha ao confirmar a leitura no WhatsApp: {exc}"
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}
        if response.status_code not in {200, 201}:
            raise WhatsAppAPIError(self._error_message(response, payload))

    def request_app_data_sync(self, sync_type: str) -> dict[str, Any]:
        if sync_type not in {"history", "smb_app_state_sync"}:
            raise ValueError("Tipo de sincronização inválido.")
        phone_number_id, access_token = self._operational_credentials()
        version = settings.whatsapp_api_version.strip()
        url = f"https://graph.facebook.com/{version}/{phone_number_id}/smb_app_data"
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"messaging_product": "whatsapp", "sync_type": sync_type},
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(
                f"Falha ao solicitar a sincronização do WhatsApp: {exc}"
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}
        if response.status_code not in {200, 201}:
            raise WhatsAppAPIError(self._error_message(response, payload))
        return payload

    def download_media(self, media_id: str) -> tuple[bytes, str | None]:
        media_id = str(media_id or "").strip()
        if not media_id:
            raise ValueError("A mídia recebida não possui identificação.")
        _, access_token = self._operational_credentials()
        version = settings.whatsapp_api_version.strip()
        try:
            metadata_response = requests.get(
                f"https://graph.facebook.com/{version}/{media_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=settings.whatsapp_timeout_seconds,
            )
            metadata = metadata_response.json()
        except (requests.RequestException, ValueError) as exc:
            raise WhatsAppAPIError(
                f"Falha ao localizar a mídia recebida: {exc}"
            ) from exc
        if metadata_response.status_code != 200 or not metadata.get("url"):
            raise WhatsAppAPIError(self._error_message(metadata_response, metadata))
        try:
            media_response = requests.get(
                str(metadata["url"]),
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=settings.whatsapp_timeout_seconds,
                stream=True,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f"Falha ao baixar a mídia recebida: {exc}") from exc
        try:
            if media_response.status_code != 200:
                raise WhatsAppAPIError("A Meta não liberou o arquivo recebido.")
            limit = 20 * 1024 * 1024
            declared_size = media_response.headers.get("content-length")
            if declared_size and int(declared_size) > limit:
                raise WhatsAppAPIError("O arquivo recebido ultrapassa o limite de 20 MB.")
            chunks = []
            total = 0
            for chunk in media_response.iter_content(chunk_size=64 * 1024):
                total += len(chunk)
                if total > limit:
                    raise WhatsAppAPIError("O arquivo recebido ultrapassa o limite de 20 MB.")
                chunks.append(chunk)
            return b"".join(chunks), str(
                metadata.get("mime_type") or media_response.headers.get("content-type") or ""
            ).split(";", 1)[0] or None
        except (ValueError, requests.RequestException) as exc:
            raise WhatsAppAPIError(f"Falha ao baixar a mídia recebida: {exc}") from exc
        finally:
            media_response.close()

    def upload_audio(
        self, *, content: bytes, filename: str, mime_type: str
    ) -> str:
        phone_number_id, access_token = self._operational_credentials()
        version = settings.whatsapp_api_version.strip()
        url = f"https://graph.facebook.com/{version}/{phone_number_id}/media"
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (filename, content, mime_type)},
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f"Falha ao enviar o áudio para a Meta: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}
        media_id = str(payload.get("id") or "")
        if response.status_code not in {200, 201} or not media_id:
            raise WhatsAppAPIError(self._error_message(response, payload))
        return media_id

    def upload_file(self, *, content: bytes, filename: str, mime_type: str) -> str:
        phone_number_id, access_token = self._operational_credentials()
        version = settings.whatsapp_api_version.strip()
        url = f"https://graph.facebook.com/{version}/{phone_number_id}/media"
        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (filename, content, mime_type)},
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f"Falha ao enviar o arquivo para a Meta: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}
        media_id = str(payload.get("id") or "")
        if response.status_code not in {200, 201} or not media_id:
            raise WhatsAppAPIError(self._error_message(response, payload))
        return media_id

    def send_file(
        self, *, recipient: str, media_id: str, message_type: str, filename: str
    ) -> WhatsAppSendResponse:
        if message_type not in {"document", "image"}:
            raise ValueError("Tipo de anexo inválido.")
        recipient = "".join(character for character in recipient if character.isdigit())
        if not recipient or not media_id:
            raise ValueError("Destinatário ou arquivo não informado.")
        _, access_token = self._operational_credentials()
        media = {"id": media_id}
        if message_type == "document":
            media["filename"] = filename
        try:
            response = requests.post(
                self.messages_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": message_type,
                    message_type: media,
                },
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f"Falha ao enviar o arquivo pelo WhatsApp: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}
        if response.status_code not in {200, 201}:
            raise WhatsAppAPIError(self._error_message(response, payload))
        return WhatsAppSendResponse(response.status_code, payload)

    def send_audio(self, *, recipient: str, media_id: str) -> WhatsAppSendResponse:
        _, access_token = self._operational_credentials()
        recipient = "".join(character for character in recipient if character.isdigit())
        if not recipient or not media_id:
            raise ValueError("Destinatário ou áudio não informado.")
        try:
            response = requests.post(
                self.messages_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": "audio",
                    "audio": {"id": media_id},
                },
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(f"Falha ao enviar o áudio pelo WhatsApp: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}
        if response.status_code not in {200, 201}:
            raise WhatsAppAPIError(self._error_message(response, payload))
        return WhatsAppSendResponse(response.status_code, payload)

    def send_template(
        self,
        *,
        recipient: str,
        template_name: str,
        language_code: str,
        components: list[dict[str, Any]] | None = None,
    ) -> WhatsAppSendResponse:
        """Start a conversation using an approved WhatsApp template."""
        phone_number_id, access_token = self._operational_credentials()
        version = settings.whatsapp_api_version.strip()
        messages_url = (
            f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
        )
        recipient = "".join(character for character in recipient if character.isdigit())
        template_name = template_name.strip()
        language_code = language_code.strip()

        if not recipient:
            raise ValueError("O telefone do destinatário não foi informado.")
        if not template_name or not language_code:
            raise WhatsAppConfigurationError(
                "O modelo inicial do WhatsApp não está configurado."
            )

        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template["components"] = components

        try:
            response = requests.post(
                messages_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": "template",
                    "template": template,
                },
                timeout=settings.whatsapp_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise WhatsAppAPIError(
                f"Falha ao acessar a API do WhatsApp: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_response": response.text}

        if response.status_code not in {200, 201}:
            raise WhatsAppAPIError(self._error_message(response, payload))

        return WhatsAppSendResponse(
            status_code=response.status_code,
            payload=payload,
        )


whatsapp_client = WhatsAppClient()
