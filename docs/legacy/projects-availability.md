# Disponibilidade da área da Projetos — 14/10/2026

## Diagnóstico confirmado

O serviço Render `Sistema` (512 MB) registrou repetidas falhas por falta de
memória, inclusive às 11h19, 11h23 e 11h33 depois do redeploy manual das 11h14
(horário de Brasília). Portanto, refazer o último commit apenas reiniciava o
processo e liberava recursos temporariamente.

Na inspeção do processo, RSS cresceu de 189740 para 267444 e 313040 KiB.
As conexões abertas de history.db aumentaram de 51 para 68; tracker_health.db
de 48 para 64; geocoding.db de 42 para 56; system_state.db de 17 para 22.
O problema compartilhado derrubava também a área da Projetos.

As fábricas usavam `with sqlite3.Connection`, que confirma/desfaz a transação,
mas NÃO fecha a conexão. Os ciclos internos adiavam a liberação de descritores
e caches nativos até a coleta de lixo. A correção fecha cada conexão ao sair
da operação, mantendo commit/rollback, caminhos, esquema e dados existentes.
Aplicada à Projetos e às fábricas de bancos compartilhados. A validação em
produção também identificou a segunda fábrica de history.db em operations.py
e conexões de employee_portal.db; elas e a fábrica equivalente de documentos
receberam o mesmo fechamento determinístico. Nenhuma regra de
negócio desses módulos foi alterada.

## Render

- Manter o comando `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Configurar Settings > Health Checks > Health Check Path como `/healthz`.
  A rota é pública, leve e não consulta serviços externos nem inicializa bancos.
  O Render pode assim detectar um processo HTTP travado mesmo com a porta aberta.
- Não aumentar workers em uma instância de 512 MB.
- Depois do deploy, confirmar `GET /healthz` com 200 e `{"ok":true}`.
- Acompanhar Events e Metrics durante uso e múltiplos ciclos de sincronização:
  não deve haver crescimento contínuo de memória/conexões ou novos eventos
  `Ran out of memory`.
- Validar clientes, processos, calendário e abertura de documentos da Projetos.
  Criar/editar/anexar foi testado em banco temporário, sem cadastrar testes em produção.

## Preservação e observações

Banco, documentos e chave da Projetos já estão em `/var/data`. Antes do deploy,
foi feito backup SQLite consistente e verificado com `PRAGMA quick_check`:
`/var/data/backups/projects-before-availability-20261014T155545Z.db`.

A sincronização Terra registrava `AUTHENTICATIONFAILED`; é um problema de
credencial distinto do encerramento por memória. Não foram alteradas senhas.
O caminho de e-mails originais está em `data/projects/email_inbox`, fora do disco
persistente, e estava sem arquivos `.eml` na inspeção. Antes de reativar o fluxo
Terra, configurar `PROJECTS_EMAIL_ROOT_PATH` em `/var/data/projects/email_inbox` e
copiar qualquer arquivo que tenha surgido, sem sobrescrever arquivos existentes.

## Testes

`python -m unittest discover -s tests -p 'test_*.py' -q`

Os testes de ciclo de vida desativam a coleta de lixo e verificam o fechamento
após 800 operações nas oito fábricas; também verificam commit, rollback,
reabertura, isolamento da rota de saúde e fluxo HTTP da Projetos com cliente,
processo, edição, upload/download e consultas principais.

Referências: https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager
e https://render.com/docs/health-checks.
