# Primeiro deploy — RH, certificados e calendário financeiro

Nenhuma migração deste pacote apaga ou recria bancos, tabelas ou dados. Não altere o Start Command do Render.

## Mapa de persistência

- **Supabase:** autenticação, `profiles`, `web_sessions`, os novos metadados de certificados e compromissos manuais, além dos dois buckets privados.
- **`system_state.db`:** funcionários, exames, férias, multas e cadastros financeiros já existentes no estado central JSON.
- **`history.db`:** histórico e operação da frota, incluindo manutenções, documentos e débitos operacionais.
- **Demais SQLite:** auditoria (`audit.db`), saúde dos rastreadores (`tracker_health.db`), geocodificação (`geocoding.db`), portal do funcionário, documentos e dados da Projetos.

`employee_id` e `vehicle_id` nas novas tabelas são vínculos lógicos: os cadastros de origem permanecem nos SQLite existentes, portanto não é possível criar uma chave estrangeira PostgreSQL sem duplicar ou migrar dados. `organization_id` e os usuários responsáveis possuem chaves estrangeiras no Supabase.

## 1. Backup obrigatório antes da migração

Abra o Shell do serviço no Render e execute, antes de reiniciar a aplicação:

```sh
backup_stamp="$(date +%Y%m%d_%H%M%S)"
mkdir -p "/var/data/backups/$backup_stamp"
sqlite3 /var/data/audit.db ".backup '/var/data/backups/$backup_stamp/audit.db'"
sqlite3 /var/data/history.db ".backup '/var/data/backups/$backup_stamp/history.db'"
sqlite3 /var/data/tracker_health.db ".backup '/var/data/backups/$backup_stamp/tracker_health.db'"
sqlite3 /var/data/geocoding.db ".backup '/var/data/backups/$backup_stamp/geocoding.db'"
```

Como o sistema também usa outros SQLite persistentes, é recomendado copiar no mesmo diretório, se existirem:

```sh
for db_file in system_state.db employee_portal.db documents.db projects_projects.db; do
  if [ -f "/var/data/$db_file" ]; then
    sqlite3 "/var/data/$db_file" ".backup '/var/data/backups/$backup_stamp/$db_file'"
  fi
done
```

O comando `.backup` cria uma cópia consistente mesmo se o banco estiver em modo WAL.

Valide sem modificar os originais:

```sh
ls -lh "/var/data/backups/$backup_stamp"
sqlite3 "/var/data/backups/$backup_stamp/audit.db" 'PRAGMA integrity_check;'
sqlite3 "/var/data/backups/$backup_stamp/history.db" 'PRAGMA integrity_check;'
sqlite3 "/var/data/backups/$backup_stamp/tracker_health.db" 'PRAGMA integrity_check;'
sqlite3 "/var/data/backups/$backup_stamp/geocoding.db" 'PRAGMA integrity_check;'
```

## 2. Ação manual no Supabase

No projeto correto, abra **Storage** e crie manualmente os dois buckets abaixo, usando exatamente estes nomes:

1. `employee-certificates`
2. `financial-calendar-attachments`

Em ambos os buckets:

- mantenha **Public** desativado;
- configure o limite máximo de arquivo como 10 MB;
- permita somente `application/pdf`, `image/jpeg` e `image/png`.

Depois, abra **SQL Editor**, revise e execute [`migrations/20260803_first_deploy.sql`](../migrations/20260803_first_deploy.sql). O script é idempotente e:

- cria `employee_certificates` e `financial_calendar_entries`;
- habilita RLS e restringe acesso a `admin`, `director` e `financial` por `profiles.auth_user_id`, `profiles.organization_id` e `profiles.role_code`;
- cria políticas privadas de Storage vinculadas à organização.

A migração não executa `INSERT`, `UPDATE` ou `DELETE` em `storage.buckets`; a criação e a configuração dos buckets são exclusivamente manuais. Ao terminar, confirme novamente em **Storage** que a coluna **Public** está desativada nos dois buckets. Não crie URL pública e não coloque a `SUPABASE_SECRET_KEY` no navegador.

## 3. Variáveis de ambiente

Os nomes dos buckets são fixos e devem permanecer `employee-certificates` e `financial-calendar-attachments`, em conformidade com as políticas e restrições da migração. As novas variáveis abaixo são opcionais porque possuem padrões seguros:

```text
EMPLOYEE_CERTIFICATE_FILE_MAX_BYTES=10485760
FINANCIAL_ATTACHMENT_FILE_MAX_BYTES=10485760
PRIVATE_DOWNLOAD_URL_SECONDS=300
```

Os limites de arquivo podem ser reduzidos. O teto permanece em 10 MB, também aplicado pelas tabelas e pelos buckets. Links temporários aceitam duração entre 30 e 3.600 segundos.

Os caminhos de todos os SQLite devem continuar apontando para `/var/data` no Render. Não altere o Start Command.

## 4. Recorrência

Uma recorrência exige quantidade explícita entre 2 e 36, frequência semanal, mensal ou anual e intervalo entre 1 e 12. O backend materializa apenas essa quantidade finita. O anexo da série fica no primeiro compromisso para evitar multiplicar um arquivo privado até 36 vezes.

## 5. Rollback operacional

Não use `DROP TABLE`. Em caso de problema, reverta a versão da aplicação no Render e preserve as tabelas/buckets novos. Os SQLite podem ser restaurados a partir da pasta de backup somente durante uma janela controlada, com o serviço parado e após nova cópia do estado atual.
