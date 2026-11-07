# Arquitetura

A aplicação usa FastAPI como camada HTTP e Jinja2/JavaScript no frontend. Os domínios principais possuem módulos de serviço próprios, enquanto `app/main.py` ainda atua como composition root e concentra rotas legadas.

## Domínios
- **Autenticação e acesso:** `auth.py`, `access_control.py`
- **Operações/frota:** `operations.py`, `history.py`, `tracker_health.py`, `geocoding.py`
- **RH:** `employee_portal.py`, imports, certificados e calendário
- **Projetos/processos:** `aline_projects.py`, `aline_email.py`
- **Estado e documentos:** `system_state.py`, `document_storage.py`, `private_storage.py`
- **Integrações:** clientes de rastreamento, WhatsApp, Cora e NFS-e

## Modo demo
O modo demo usa autenticação local temporária e dados fictícios, evitando dependências externas. Isso torna o case reproduzível para avaliação técnica.

## Evolução arquitetural
A próxima refatoração recomendada é extrair grupos de endpoints legados de `main.py` para routers por domínio, mantendo uma camada de composição pequena.
