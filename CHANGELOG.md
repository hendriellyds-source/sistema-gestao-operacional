# Changelog

## 1.0.0-portfolio — 2026-11-06

- Identidade visível alterada para Sistema de Gestão Operacional — Demo.
- Removidos cadastros pessoais, históricos de clientes, mídia corporativa e resíduos de bancos.
- Módulos e perfis com nomes pessoais passaram a usar nomes funcionais; referências internas atualizadas em conjunto.
- DEMO_MODE configurável e habilitado por padrão, com dados sintéticos e login independente de Supabase.
- Sessões locais com expiração, logout e tokens persistidos por hash.
- Dados demo separados em data/demo; seed central idempotente.
- Integrações preservadas, desabilitadas por padrão e bloqueadas na demonstração.
- Extraídas rotas de páginas para app/routes/pages.py; removidos imports duplicados e arquivo duplicado com extensão incorreta.
- Nova página de apresentação, marca neutra, textos de privacidade e documentação da demonstração.
- Atualizados testes afetados pela identidade e configuração; adicionados testes de execução demo sem conexões externas.
- README, configuração de exemplo, instruções de teste e registro de limitações.
