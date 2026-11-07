# Arquitetura — visão de portfólio

## Objetivo

Demonstrar como uma aplicação operacional conecta múltiplos domínios — pessoas, agenda, obras, frota e financeiro — mantendo autenticação, persistência e integrações isoladas.

## Camadas

1. **HTTP e apresentação** — FastAPI + Jinja2 entregam páginas e endpoints.
2. **Serviços de domínio** — regras relacionadas a agenda, operações, estado e processos internos.
3. **Persistência** — SQLite no modo demo; adaptadores externos permanecem isolados.
4. **Integrações** — clientes específicos encapsulam comunicação com serviços externos.
5. **Testes** — validam domínio, segurança, persistência e isolamento da demonstração.

## Estratégia de evolução

A aplicação nasceu de um contexto real e cresceu por demanda. Para o portfólio, foi escolhida uma refatoração incremental em vez de uma reescrita completa. Isso preserva comportamento já validado e permite melhorar arquitetura com menor risco.

O principal ponto de evolução técnica é continuar decompondo responsabilidades concentradas em `main.py` em routers e serviços de domínio menores.
