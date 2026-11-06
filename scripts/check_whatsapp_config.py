from __future__ import annotations

from app.settings import settings


def masked(value: str) -> str:
    value = value.strip()

    if not value:
        return "NÃO CONFIGURADO"

    if len(value) <= 6:
        return "***"

    return f"{value[:3]}***{value[-3:]}"


def main() -> None:
    print("WhatsApp habilitado:", settings.whatsapp_enabled)
    print("Versão da API:", settings.whatsapp_api_version)
    print(
        "Phone Number ID:",
        masked(settings.whatsapp_phone_number_id),
    )
    print(
        "WABA ID:",
        masked(settings.whatsapp_business_account_id),
    )
    print(
        "Access Token:",
        "CONFIGURADO"
        if settings.whatsapp_access_token.strip()
        else "NÃO CONFIGURADO",
    )
    print(
        "Verify Token:",
        "CONFIGURADO"
        if settings.whatsapp_verify_token.strip()
        else "NÃO CONFIGURADO",
    )
    print(
        "App Secret:",
        "CONFIGURADO"
        if settings.whatsapp_app_secret.strip()
        else "NÃO CONFIGURADO",
    )


if __name__ == "__main__":
    main()