from __future__ import annotations

import sys

from app.whatsapp_client import (
    WhatsAppConfigurationError,
    whatsapp_client,
)


def main() -> int:
    try:
        whatsapp_client.validate_configuration()
    except WhatsAppConfigurationError as exc:
        print(f"ENVIO BLOQUEADO CORRETAMENTE: {exc}")
        return 0

    print(
        "ATENÇÃO: o envio está habilitado. "
        "Nenhuma mensagem foi enviada por este teste."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())