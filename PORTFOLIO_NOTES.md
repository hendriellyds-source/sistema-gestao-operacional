# Notas para avaliação técnica

## O que esta versão demonstra

Fluxos operacionais conectados, validação de entrada, autorização por perfil, processamento de arquivos, estado com controle de revisão e adaptadores externos. O modo local permite avaliar sem provisionar contas ou acessar sistemas de terceiros.

## Limites deliberados

- A conta demo é administrativa e compartilhada; destina-se à avaliação local. Não oferece isolamento entre visitantes de uma instância pública.
- Fotos, certificados e alguns compromissos financeiros persistidos no Supabase não têm um backend fictício. Esses recursos requerem instalação operacional e falham com indisponibilidade explícita quando não configurados.
- Não foram executadas chamadas reais de emissão fiscal, pagamento, WhatsApp, rastreamento, visão ou IMAP. Testes desses adaptadores usam mocks.
- O frontend principal ainda é grande e usa estado JSON com cópia no navegador. Uma próxima etapa seria extrair módulos por domínio e reduzir a duplicação de fontes de dados.
- As migrações incluídas são incrementais: não provisionam, sozinhas, todo o schema legado de uma instalação real.
- A demonstração não é uma certificação de segurança, escalabilidade ou conformidade jurídica para produção.

## Principais decisões desta adaptação

Remoção de cadastros e históricos reais, mídia e contatos institucionais, linkage de provedor e resíduos SQLite. Identidade neutra, nomes funcionais de módulos e perfis, seed sintético, sessão local com expiração e token armazenado por hash, isolamento em `data/demo/` e bloqueio de integrações por padrão.

Não foi adicionada uma licença sem confirmação de titularidade. Antes de publicar, o responsável pelo repositório pode escolher uma licença compatível e acrescentar sua participação real no projeto, sem atribuições inventadas.

## Roteiro sugerido de demonstração

1. Entrar com a conta demo e mostrar indicadores e cadastros de pessoas.
2. Relacionar uma equipe à obra e consultar os agendamentos.
3. Mostrar posições simuladas e custos da frota.
4. Editar uma observação, recarregar e conferir a persistência.
5. Mostrar os testes de idempotência da agenda, controle de acesso e webhooks simulados.
6. Explicar o isolamento de serviços externos e os limites descritos acima.
