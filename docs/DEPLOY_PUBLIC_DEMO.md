# Preparação para publicação da demo

## Arquitetura recomendada

- **Render (Docker Web Service)** executa o FastAPI e renderiza a interface Jinja2.
- **Disco persistente** em `/app/data` armazena os bancos SQLite da demonstração.
- **Netlify** continua hospedando apenas a landing page Drillyx ONE, que terá um link para a URL pública do Render.
- O Dockerfile já inicia o servidor em `0.0.0.0:8000` e fornece `/healthz`.

## Segurança e isolamento antes da publicação

**Não publicar sem validar os seguintes pontos:**

1. Rodar testes e fazer smoke test em instância privada; garantir que `DEMO_MODE=true`, `INTEGRATIONS_ENABLED=false` e `AUTH_COOKIE_SECURE=true`.
2. Confirmar que as rotas e dados são totalmente fictícios, e que webhooks, e-mail, WhatsApp, rastreamento, NFS-e e integrações externas não fazem chamadas de produção.
3. A conta de demonstração é compartilhada. Este Blueprint ativa **somente leitura** (`PUBLIC_DEMO_READ_ONLY=true`): somente login/logout aceitam alterações de estado por HTTP; outras operações de escrita retornam 403. Fluxos de criação e edição não ficam disponíveis na demo pública. Para permitir interações de escrita futuramente, implementar **isolamento por visitante** antes de desativar esse bloqueio.
4. Proteger uploads e endpoints administrativos, revisar autorização de rotas e limitar tráfego. O modo demo e a sanitização existentes são uma base, não substituem teste de segurança.
5. Definir `DEMO_PASSWORD` no provedor (não versionar a senha); a tela de login atual pode exibir credenciais de demo e deverá ser revisada conforme a experiência escolhida.
6. Validar custo do plano e disco persistente do provedor, backup/reinicialização e política de retenção antes de ativar o serviço.

## Etapas de publicação após revisão

1. Conectar o repositório privado no painel do Render.
2. Criar um Blueprint usando `render.yaml`.
3. Revisar o plano de serviço (o exemplo usa plano **Starter**, sujeito a cobrança) e configurar as variáveis solicitadas.
4. Publicar a aplicação, confirmar `/healthz` e efetuar login com a conta demo.
5. Testar RH, agenda, frota, financeiro e logout, inclusive em celular; verificar a ausência de dados reais.
6. Copiar a **URL verdadeira** do deploy para o card de Gestão Operacional na landing page hospedada pela Netlify.
7. Na mesma atualização da landing page, corrigir o favicon a partir do símbolo oficial em PNG transparente (não usar a imagem com fundo removido de forma aproximada).

**Estado atual:** configuração pronta para avaliação; deployment ainda não criado e nenhuma URL de demo pública foi confirmada.
