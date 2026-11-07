# Sistema de Gestão Operacional — Demo

> Case técnico de uma aplicação web criada para centralizar processos operacionais que antes dependiam de controles dispersos. Esta versão pública usa **dados 100% fictícios** e mantém integrações externas desabilitadas por padrão.

## Visão geral

O sistema integra, em uma única aplicação, rotinas de **operações, agenda, RH, frota, financeiro, documentos, permissões e indicadores**. O objetivo do case é demonstrar modelagem de regras de negócio, backend em Python, interface web, persistência local, integrações e preocupação com segurança.

### O que este projeto demonstra

- Backend com **FastAPI** e rotas organizadas por domínio
- Interface web responsiva em **HTML, CSS e JavaScript**
- Controle de acesso por perfil (RBAC)
- Gestão de equipes, agenda e atividades operacionais
- RH: colaboradores, exames, férias e documentos
- Frota: veículos, manutenção, custos, alertas e histórico
- Financeiro e calendário de compromissos
- Persistência local com SQLite e suporte a Supabase no modo integrado
- Integrações desacopladas (WhatsApp, rastreamento, cobrança e NFS-e)
- Testes automatizados de fluxos críticos
- **Modo demo isolado**, sem dependência de serviços externos

## Stack

`Python` · `FastAPI` · `Pydantic` · `Jinja2` · `JavaScript` · `HTML/CSS` · `SQLite` · `Supabase` · `Pytest`

## Executar localmente

### 1. Criar ambiente virtual

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Instalar dependências

```bash
python -m pip install -r requirements.txt
```

### 3. Criar configuração local

```powershell
Copy-Item .env.example .env
```

Linux/macOS:

```bash
cp .env.example .env
```

O arquivo de exemplo já vem configurado com `DEMO_MODE=true`.

### 4. Iniciar

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abra **http://127.0.0.1:8000**.

## Credenciais de demonstração

| Campo | Valor |
|---|---|
| E-mail | `demo@portfolio.local` |
| Senha | `demo1234` |

O login demo é processado localmente. **Supabase não é necessário** neste modo.

## Arquitetura

```text
Browser
  │
  ▼
FastAPI / Jinja2
  ├── Autenticação e acesso
  ├── Operações e agenda
  ├── RH e documentos
  ├── Frota e telemetria
  ├── Financeiro
  └── Integrações opcionais
          │
          ├── Supabase
          ├── WhatsApp Cloud API
          ├── Rastreamento
          ├── Cobrança
          └── NFS-e

Modo Demo → SQLite + dados fictícios locais
```

Mais detalhes em [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Segurança e sanitização

A versão de portfólio foi preparada especificamente para exposição técnica:

- dados reais de colaboradores foram substituídos por registros fictícios;
- credenciais e arquivos `.env` não são versionados;
- artefatos de banco e arquivos temporários são ignorados;
- integrações externas ficam `false` por padrão;
- contatos e endpoints empresariais foram removidos da configuração de exemplo.

Veja [`SECURITY.md`](SECURITY.md).

## Testes

```bash
python -m pytest -q
```

Há também testes JavaScript para regras específicas da agenda e interface.

## Decisões técnicas

Este projeto nasceu de uma necessidade operacional real e evoluiu de forma incremental. Para a versão de portfólio, a prioridade foi **preservar a riqueza das regras de negócio sem expor informações privadas da operação original**. Por isso, integrações permanecem representadas na arquitetura, mas o caminho padrão é um ambiente demo autocontido.

## Próximas evoluções

- Modularização progressiva de rotas ainda concentradas no `main.py`
- Cobertura de testes ampliada por domínio
- Containers Docker para onboarding em um comando
- Dashboard de observabilidade e métricas

## Autoria

Desenvolvido por **Hendrielly de Souza** como solução aplicada a processos empresariais e adaptado para apresentação técnica em portfólio.
