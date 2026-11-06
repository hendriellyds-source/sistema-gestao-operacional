# Validação da versão de portfólio

Executada em 6 de novembro de 2026, Windows, Python 3.12.0 e Node.js 24.22.1.

| Verificação | Resultado |
| --- | --- |
| `python -m pytest -q` | 115 testes e 8 subtestes passaram |
| `node --test tests/*.test.js` | 5 suítes passaram |
| `python -m compileall -q app` | sem erros |
| `python -m pip check` | sem conflitos de dependências |
| Startup Uvicorn em loopback | concluído |
| Navegação Chromium | apresentação, login, dashboard, pessoas, frota e agenda sem erros JavaScript |
| Portal das equipes | login fictício retornou HTTP 200 |

Os testes demo verificam login inválido e válido, cookie HttpOnly, autorização, logout, dados relacionados, seed idempotente, persistência e bloqueio das rotas externas. Tentativas de conexão de rede fora do loopback fazem o teste falhar. Os demais testes cobrem permissões, agenda, migrações, arquivos, histórico, webhooks, WhatsApp e regras operacionais com adaptadores simulados.

Há um aviso de depreciação emitido pelo TestClient da versão instalada de Starlette sobre seu transporte httpx. Não houve falha associada. O ambiente exato de runtime está registrado em `requirements-lock.txt`.

As capturas em `docs/screenshots/` foram geradas da aplicação executando com dados fictícios. Não são mockups. Integrações com serviços reais e a execução de migrações em PostgreSQL não foram exercitadas. O workflow de CI foi incluído, mas não foi executado no GitHub nesta preparação.
