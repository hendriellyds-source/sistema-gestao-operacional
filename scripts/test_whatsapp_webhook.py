from __future__ import annotations

import hashlib
import hmac
import json
import sys

import requests

from app.settings import settings

WEBHOOK_URL = "http://127.0.0.1:8000/api/whatsapp/webhook"


def main() -> int:
    app_secret = settings.whatsapp_app_secret.strip()

    if not app_secret:
        print(
            "Erro: WHATSAPP_APP_SECRET não está configurado no ambiente."
        )
        return 1

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "conta_teste",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "5548999999999",
                                    "id": "wamid.teste.local.001",
                                    "timestamp": "1786030000",
                                    "type": "text",
                                    "text": {
                                        "body": "Mensagem de teste da Gestão Operacional"
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    raw_body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    signature = (
        "sha256="
        + hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )

    try:
        response = requests.post(
            WEBHOOK_URL,
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"Erro ao acessar o webhook: {exc}")
        return 1

    print(f"Status: {response.status_code}")
    print(f"Resposta: {response.text}")

    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())