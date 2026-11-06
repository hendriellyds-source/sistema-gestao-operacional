# Ajustes operacionais — 03/08/2026

Branch: `feature-ajustes-operacionais-agendamentos-frota`

Esta entrega não foi mesclada nem publicada automaticamente.

## Escopo implementado

1. Cadastro tabular de empresas e obras, com prévia validada, valores brasileiros, prevenção de duplicidades e lançamento por linha ou em massa.
2. Relatório mensal calculado diretamente a partir dos agendamentos válidos de cada equipe. Inclui obras e levantamentos planejados ou confirmados; exclui cancelados, duplicados e obras pendentes de vinculação.
3. Saneamento seletivo da antiga frota fictícia no estado central, SQLite, monitoramento e calendário financeiro. O modo demonstração fica permanentemente desabilitado.
4. Fotos privadas de perfis, gerenciadas por administradores e diretores.
5. Favicon oficial instalado em `assets/favicon.png` e aplicado às quatro páginas HTML.
6. Áreas do perfil financeiro nomeadas como **RH** e **Financeiro**.
7. Cadastro de clínicas disponível no RH do perfil financeiro, com o mesmo formulário e ações dos perfis administrativo e diretor.
8. Histórico de veículo por um único dia local, da meia-noite até a meia-noite seguinte, sem sobreposição.
9. Validação de coordenadas, números finitos, duplicidades, saltos incompatíveis, velocidades impossíveis, lacunas e separação por veículo antes dos cálculos.
10. Viagens clicáveis, com isolamento do trecho no mapa e indicação de início, fim, parada seguinte e retomada.

## Migração e preparação manual

1. Faça backup do Supabase e dos arquivos SQLite persistidos no Render.
2. No Supabase, crie o bucket privado `user-profile-photos`. Não o torne público.
3. Aplique `migrations/20260803_operational_fixes.sql` depois de `migrations/20260803_first_deploy.sql`.
4. Confirme `DEMO_MODE=false`. O código também força esse modo a permanecer desativado.
5. Se desejar alterar o limite padrão das fotos, configure `PROFILE_PHOTO_FILE_MAX_BYTES=5242880`.
6. Reinicie o serviço somente depois da migração e da criação do bucket.

A migração adiciona colunas ao perfil, recria políticas com nomes estáveis e faz exclusão lógica apenas de compromissos manuais ligados às placas fictícias conhecidas ou marcados explicitamente como veículo demo/teste. Não há `DROP TABLE`, `TRUNCATE` ou recriação de tabelas produtivas.

No primeiro início, a limpeza local remove os identificadores fictícios conhecidos e marcadores explícitos de demonstração. O estado central anterior é mantido no histórico de revisões antes da gravação saneada.

## Verificação recomendada antes do merge

- Cole a amostra tabulada e confira as quinze linhas, inclusive as duas sem meia diária.
- Lance uma linha individual, depois as demais em massa, e confirme que repetir o texto não duplica cadastros.
- Compare o relatório mensal com a agenda de duas equipes, incluindo planejados, confirmados, levantamento, cancelado e duplicado.
- Confirme que nenhuma placa fictícia, revisão de teste ou evento correspondente aparece na frota e no calendário financeiro.
- Adicione, troque e remova uma foto; encerre e reabra a sessão para conferir o avatar.
- Entre como perfil financeiro e confira as áreas RH e Financeiro e o cadastro completo de clínicas.
- Consulte um veículo às 00:00 e às 23:59 do mesmo dia e confira que o dia seguinte não entra no resultado.
- Abra uma viagem, isole a rota e confira início, fim, parada e retomada; depois use **Mostrar o dia completo**.

## Testes automatizados

```powershell
& '..\.venv\Scripts\python.exe' -m unittest discover -s tests -v
node tests/frontend_operational.test.js
```

Também devem passar a compilação dos módulos Python, a análise sintática do JavaScript e `git diff --check`.

## Riscos conhecidos

- A limpeza SQLite é física e seletiva; por isso o backup continua obrigatório, embora os alvos sejam restritos às placas e aos marcadores fictícios conhecidos.
- Agendamentos **Planejados** agora entram no relatório mensal por decisão funcional. Se o fechamento contábil precisar considerar apenas executados, deve ser criada uma visão separada, sem voltar a misturar esse conceito com o relatório da agenda.
- Pontos que implicam mais de 220 km/h entre amostras ou informam velocidade superior a 240 km/h são descartados. Lacunas acima de 15 minutos aparecem como tempo desconhecido e não como parada.
- A exclusão de uma foto antiga no bucket é feita depois que a nova foto foi vinculada. Uma falha isolada nessa exclusão não perde a foto nova, mas pode deixar um objeto órfão para limpeza administrativa futura.
