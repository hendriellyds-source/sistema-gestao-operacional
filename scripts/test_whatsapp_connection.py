from __future__ import annotations

import requests

from app.settings import settings


def main() -> None:
    phone_number_id = settings.whatsapp_phone_number_id.strip()
    access_token = settings.whatsapp_access_token.strip()
    api_version = settings.whatsapp_api_version.strip()

    if not phone_number_id:
        print("Phone Number ID não configurado.")
        return

    if not access_token:
        print("Access Token não configurado.")
        return

    url = (
        f"https://graph.facebook.com/"
        f"{api_version}/{phone_number_id}"
    )

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
            },
            timeout=settings.whatsapp_timeout_seconds,
        )
    except requests.RequestException as exc:
        print(f"Erro ao conectar com a Meta: {exc}")
        return

    print("Status:", response.status_code)

    try:
        payload = response.json()
    except ValueError:
        print("Resposta:", response.text)
        return

    if response.status_code == 200:
        print("CONEXÃO COM A META FUNCIONANDO.")
        print("ID retornado:", payload.get("id"))
    else:
        print("Resposta da Meta:", payload)


if __name__ == "__main__":
    main()