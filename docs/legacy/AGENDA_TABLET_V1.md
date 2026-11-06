# Agenda Gestão Operacional no tablet — versão 1

## Uso

Abra `/agenda-tablet` no tablet Samsung, entre com a conta do sistema e escolha **Manter acesso neste tablet**. Pelo menu do navegador, instale **Agenda Gestão Operacional** na tela inicial. O ícone abre a página do dia em tela cheia.

Escreva com a S Pen, uma obra por linha. Equipe, empresa, obra e horário podem aparecer em qualquer ordem. O traço original é guardado imediatamente no armazenamento local do tablet. **Conferir anotações** mostra a escrita de cada linha, tenta ler o texto e sugere os cadastros correspondentes. Confira os campos antes de usar **Confirmar agendamento**. Um agendamento confirmado entra no mesmo `workScheduleEntries` do sistema atual, com status **Planejado** e referência ao texto original.

Se a leitura automática não reconhecer a letra, digite ou corrija o texto na revisão. Para ensinar uma sigla, selecione o cadastro e use **Guardar apelido**. O reconhecimento de letra manuscrita usa Tesseract.js no navegador quando há internet; a escrita e a revisão manual continuam disponíveis offline. A qualidade da leitura precisa ser avaliada com páginas reais do Responsável Demo.

## Queda do Render

O aplicativo instalável guarda seus arquivos básicos para reabrir mesmo sem o servidor. Cada traço e revisão é persistido em IndexedDB antes de entrar na fila de envio. A tela mostra quando existem itens pendentes. O envio é repetido ao reconectar, ao reabrir ou periodicamente enquanto o aplicativo está aberto. Cada traço e cada linha possuem identificador único; repetir uma solicitação não cria outro traço ou serviço. A confirmação de um agendamento exige conexão e cadastros disponíveis no servidor.

O aplicativo não promete sincronização enquanto o Android o mantém fechado: o sistema pode suspender processos em segundo plano. Ao reabrir com rede, a fila é enviada. **Não limpar os dados do navegador/tablet enquanto houver itens pendentes.** O bloqueio de tela do Android deve estar ativo, pois os rascunhos ficam guardados no aparelho.

## Persistência e implantação

As novas tabelas `tablet_ink`, `tablet_review` e `tablet_alias` são criadas no mesmo arquivo configurado por `SYSTEM_STATE_DB_PATH`. No Render, esse caminho **deve apontar para o disco persistente**, junto com o estado central existente; confirme, por exemplo, que `SYSTEM_STATE_DB_PATH=/var/data/system_state.db` corresponde à configuração efetiva do serviço. Faça backup desse arquivo antes do deploy. Sem disco persistente, uma reinicialização pode perder o histórico já sincronizado ao servidor.

A instalação exige HTTPS no acesso real. A sessão opcional do tablet dura até 30 dias e pode ser revogada pelo mecanismo de logout do sistema. A página da agenda tem acesso apenas para perfis que já acessam o estado central.

## Validação feita

- Teste de API para traços repetidos, confirmação repetida, interpretação de apelidos e preservação de lançamento diante de uma aba antiga.
- Testes de acesso, login e histórico do estado central.
- Simulação em navegador: escrever sem conexão, recarregar offline, reconectar, sincronizar uma vez e abrir a revisão.

Antes do uso diário, validar no tablet real: instalação, S Pen e rejeição da palma da mão, leitura de amostras reais da escrita, desligar/ligar Wi-Fi, queda do Render e acesso após expiração da sessão.
