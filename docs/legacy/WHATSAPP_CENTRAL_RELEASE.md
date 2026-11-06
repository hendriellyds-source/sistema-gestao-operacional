# Central de WhatsApp — liberação segura

Antes do deploy, confirme no Render que `WHATSAPP_DB_PATH` aponta para o disco persistente (por exemplo, `/var/data/whatsapp.db`). O áudio fica ao lado desse banco, em `whatsapp_media`, salvo se `WHATSAPP_MEDIA_ROOT_PATH` definir outro diretório persistente. Não mova um banco de produção existente sem uma cópia e uma migração planejada.

Confirme também que o serviço já possui credencial operacional válida e assinatura dos webhooks para `messages`, `smb_message_echoes`, `history` e `smb_app_state_sync`. A aprovação das permissões na Meta não instala essas inscrições sozinha. Não desvincule nem remova o número do WhatsApp Business para testar.

Teste em uma conversa de teste:

1. Receba texto e áudio; confirme que o texto aparece e o áudio toca na central.
2. Grave um áudio no Chrome, envie pela central e confirme o recebimento no celular. Repita com um MP3/M4A anexado.
3. Responda no WhatsApp Business do celular; confirme que a resposta aparece na central como enviada pelo celular e que surge o aviso de resposta externa.
4. Atribua a conversa à Projetista. A pré-visualização por um administrador não deve remover o aviso de pendência para ela. Quando Projetista abrir, a pendência local deve desaparecer; confirme o comportamento de leitura no WhatsApp real.
5. Crie uma etiqueta, atribua-a a um contato, confira a cor do avatar e filtre por ela.
6. Teste Enter para enviar e Shift+Enter para quebrar a linha.
7. Teste os perfis administrativo/supervisor e atendente em sessões separadas. O atendente não deve abrir conversa de outra pessoa.
8. Solicite a sincronização do histórico apenas se a Meta ainda permitir no vínculo atual; valide a quantidade e a ordem antes de confiar no resultado. A disponibilidade do histórico antigo depende da Meta e da autorização dada no cadastro.

Se a credencial estiver ausente ou vencida, a central sinaliza isso. Reconectar pelo cadastro incorporado pode afetar temporariamente sessões do WhatsApp Web; combine essa etapa com quem utiliza o número e não execute migração ou exclusão do telefone.
