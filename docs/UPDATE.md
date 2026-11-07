# Aplicar esta atualização

O pacote completo usa como base o ZIP de portfólio `sistema-gestao-operacional-portfolio-9_5.zip`, com a falha diagnosticada no Actions e as correções validadas localmente.

1. Faça um backup da cópia atual.
2. Extraia o pacote e substitua o conteúdo do projeto, preservando `.git` na cópia local.
3. Remova os arquivos antigos que não existem no pacote, para não misturar testes de versões diferentes. O workflow atual é `.github/workflows/ci.yml`; o antigo `tests.yml` não faz parte desta versão.
4. Confira que `app/settings.py`, `tests/`, `requirements-dev.txt`, `requirements-lock.txt`, `.github/workflows/ci.yml`, README e `docs/screenshots/` foram atualizados juntos.
5. Execute os comandos de teste do README e envie um novo commit.
6. Confira a nova execução na aba Actions. Reexecutar o commit antigo não usa os arquivos corrigidos.

Credenciais demo: `demo@portfolio.local` / `Demo@12345`.

O pacote não contém bancos, `.env` real ou segredos. Os dados demo são criados em execução. Preferências antigas no navegador podem ser removidas limpando o armazenamento local da aplicação.
