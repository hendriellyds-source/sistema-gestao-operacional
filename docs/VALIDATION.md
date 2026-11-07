# Validação — 7 de novembro de 2026

## Falha identificada

Na execução do GitHub Actions vinculada ao commit `aa6d740`, os testes tentavam substituir `settings.integrations_enabled`, mas essa opção não estava declarada no modelo `Settings`. Isso causava `AttributeError` na preparação de vários testes.

Referência: https://github.com/hendriellyds-source/sistema-gestao-operacional/actions/runs/42318022343/job/128235395605

## Correção e verificações

O modelo agora declara a opção e aplica o isolamento de credenciais. O workflow instala também as dependências de desenvolvimento. Os testes operacionais usam configurações explícitas e os bancos são isolados em diretórios temporários.

Executado em um ambiente virtual novo, Python 3.12.0, Windows:

| Checagem | Resultado |
| --- | --- |
| Instalação de requirements-lock.txt e requirements-dev.txt | concluída |
| pytest | 120 testes e 8 subtestes passaram |
| Testes JavaScript | 5 suítes passaram |
| compileall | sem erros |
| pip check | sem conflitos |
| Chromium: login, agenda, financeiro e frota | sem erros JavaScript |

Há um aviso de depreciação do TestClient/Starlette sobre httpx; os testes passaram. As imagens são capturas da aplicação com dados sintéticos, não mockups.

O workflow está preparado para Ubuntu/Python 3.12/Node 22. Não foi publicado nem executado novamente no GitHub nesta preparação. O status verde só pode ser confirmado em uma nova execução após o envio dos arquivos. Não foi usado um runner Linux local.
