from app.nfse_ipm_client import IPMAPIError, IPMConfigurationError
from app.nfse_ipm_client import nfse_ipm_client


TEST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<nfse>
    <nfse_teste>1</nfse_teste>
</nfse>
"""


def main() -> None:
    print("Endereço:", nfse_ipm_client.base_url)
    print("Modo: TESTE — nenhuma NFS-e será emitida")

    try:
        response = nfse_ipm_client.send_xml(TEST_XML)
    except IPMConfigurationError as exc:
        print("ERRO DE CONFIGURAÇÃO:")
        print(exc)
        return
    except IPMAPIError as exc:
        print("RESPOSTA DO WEBSERVICE:")
        print(exc)
        return

    print("Status HTTP:", response.status_code)
    print("Resposta:")
    print(response.raw_xml)


if __name__ == "__main__":
    main()