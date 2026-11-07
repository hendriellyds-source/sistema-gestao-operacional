# Changelog

## 2026-11-07 — CI e demonstração visual

- Corrigido o contrato de configuração: `Settings.integrations_enabled` agora existe e pode ser configurado ou substituído nos testes.
- Integradas as opções de isolamento: credenciais externas são desconsideradas na demo e os arquivos ficam em `data/demo/`.
- Credenciais e documentação alinhadas: `demo@portfolio.local` / `Demo@12345`.
- Workflow de CI instala runtime fixado e dependências de teste, valida Python e JavaScript e preserva relatório JUnit.
- Fixtures isolam os bancos e ativam explicitamente o caminho operacional nos testes com adaptadores simulados.
- Adicionados testes do fluxo demo e de sintaxe de todos os scripts estáticos e renderizados.
- Corrigida a frota que tratava a demonstração como backend indisponível.
- Centralizados os dados sintéticos; removidos catálogos e históricos antigos do seed de interface.
- Adicionadas capturas reais de Agenda, Frota e Financeiro ao README.
