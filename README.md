# Sistema de Gestão Operacional — Demo

> Plataforma web desenvolvida para centralizar processos operacionais de uma empresa de serviços de campo, conectando pessoas, equipes, obras, agenda, frota, financeiro e indicadores em um único ambiente.

Este repositório é uma **versão de portfólio anonimizada** de um sistema que desenvolvi por iniciativa própria para resolver necessidades reais de operação. A edição pública/demo preserva arquitetura, regras de negócio e integrações relevantes, mas utiliza dados fictícios e mantém serviços externos desabilitados por padrão.

## Visão rápida

- **Backend:** Python, FastAPI, Pydantic, Uvicorn
- **Frontend:** Jinja2, HTML, CSS, JavaScript, Leaflet
- **Persistência demo:** SQLite
- **Integrações preservadas:** Supabase, WhatsApp Cloud API, Cora, NFS-e/IPM, OpenAI, IMAP e serviços de rastreamento
- **Qualidade:** testes Python, testes JavaScript e workflow de CI

### O problema

A operação original dependia de informações distribuídas entre agendas, cadastros, equipes, veículos, documentos e controles financeiros. Isso aumentava retrabalho, dificultava a rastreabilidade e tornava a gestão dependente de conferências manuais.

### A solução

O sistema centraliza os principais fluxos operacionais e relaciona informações que antes ficavam separadas: uma obra pode ser conectada ao cliente, agenda, equipe, veículo, produção, documentos e financeiro.

## Demonstração

A aplicação possui um **modo demo totalmente local**, com dados fictícios e sem dependência de contas externas.

### Credenciais

| Acesso | Valor |
|---|---|
| E-mail | `demo@portfolio.local` |
| Senha | `Demo@12345` |
| Perfil | Administrador Demo |
| Portal das equipes | `pessoa.demo1` / `DemoPortal123` |

> As credenciais acima existem exclusivamente para a demonstração local.

## Como executar

### Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Depois acesse `http://127.0.0.1:8000/`.

## Principais módulos

| Área | O que demonstra |
|---|---|
| Dashboard | visão consolidada da operação e indicadores |
| Pessoas / RH | colaboradores, exames, férias e afastamentos |
| Obras e empresas | cadastros, relacionamentos e histórico operacional |
| Agenda | alocação de equipes, conflitos e acompanhamento |
| Agenda em tablet | fluxo de campo, desenho, revisão e confirmação idempotente |
| Frota | veículos, posições, trajetos, alertas, geofences, custos e manutenção |
| Financeiro | lançamentos, cobranças e vínculos com serviços e diárias |
| Projetos | clientes, orçamentos, documentos e acompanhamento |
| Acesso | autenticação, sessão, perfis e autorização |

## Arquitetura

```text
app/
  main.py               composição da aplicação e rotas operacionais
  routes/pages.py       páginas públicas, login e agenda em tablet
  auth.py               autenticação e autorização
  demo_auth.py          autenticação local do modo demo
  demo_seed.py          cenário sintético e dados fictícios
  settings.py           configuração e isolamento de integrações
  system_state.py       estado central, revisão e histórico
  operations.py         regras operacionais e frota
  calendar_service.py   consolidação de eventos
  *_client.py           adaptadores de serviços externos
  templates/            interface renderizada no servidor
assets/                  recursos do navegador
migrations/              migrações incrementais
tests/                   testes de domínio, API, segurança e interface
docs/                    arquitetura, validação e material de demonstração
data/                    dados locais de execução (não versionados)
```

A estratégia adotada foi de **refatoração incremental**: preservar regras de negócio já validadas e reduzir acoplamento progressivamente, sem reescrever o sistema apenas para fins de portfólio.

## Modo demo e segurança

`DEMO_MODE=true` é o padrão da versão de portfólio. Nesse modo:

- os dados são gravados em `data/demo/`;
- integrações externas ficam desabilitadas;
- não são enviadas mensagens reais;
- não são geradas cobranças ou notas fiscais reais;
- credenciais externas não são necessárias;
- o cenário inicial é criado com dados sintéticos.

Para limpar a demonstração, pare o servidor, remova `data/demo/` e inicie novamente.

## Integrações reais

Os adaptadores foram mantidos no código para demonstrar a arquitetura utilizada em produção, mas ficam desabilitados por padrão.

```env
DEMO_MODE=true
INTEGRATIONS_ENABLED=false
```

Uma instalação operacional exige configuração própria, credenciais válidas e ativação explícita das integrações. Nenhum segredo de produção faz parte deste repositório.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app
node --test tests/*.test.js
```

Os testes cobrem regras de negócio, APIs, autenticação, persistência, fluxo demo e bloqueio de conexões externas.

## Decisões técnicas que eu destacaria em entrevista

**SQLite no modo demo.** Permite executar o projeto sem infraestrutura externa, mantendo persistência e transações reais.

**Adaptadores preservados.** As integrações continuam visíveis arquiteturalmente, porém isoladas e desabilitadas no portfólio.

**Dados sintéticos.** O seed demonstra relacionamentos reais entre entidades sem expor informações de clientes, funcionários ou operação.

**Refatoração incremental.** Em vez de reescrever uma aplicação funcional, priorizei isolamento, testabilidade e redução gradual de acoplamento.

**Estado com histórico.** A aplicação preserva versões e detecta alterações concorrentes em fluxos centrais.

## Minha participação

O sistema foi **concebido e desenvolvido por mim por iniciativa própria**, a partir da observação de necessidades reais de uma operação de topografia. Trabalhei desde o levantamento dos fluxos até a implementação da aplicação, regras de negócio, interface, banco de dados e integrações.

Para este portfólio, adaptei o projeto para um cenário demonstrativo: removi dados específicos da organização, isolei integrações externas e criei um ambiente reproduzível com informações fictícias.

## Próximas evoluções

- decomposição adicional de `main.py` em módulos de domínio;
- cobertura de testes ampliada por serviço;
- tipagem estática progressiva;
- observabilidade e métricas estruturadas;
- containerização para execução ainda mais rápida.

## Documentação

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — visão técnica resumida
- [`docs/DEMO.md`](docs/DEMO.md) — roteiro para avaliar o projeto
- [`SECURITY.md`](SECURITY.md) — política de segurança do repositório
- [`PORTFOLIO_NOTES.md`](PORTFOLIO_NOTES.md) — escopo da adaptação de portfólio
- [`CHANGELOG.md`](CHANGELOG.md) — histórico das principais mudanças

---

**Autora:** Hendrielly de Souza  
Projeto apresentado como case técnico de desenvolvimento de software e automação de processos.
