# Sistema de Gestão — Portfólio

Versão demonstrativa do projeto enviado. A interface de RH, agenda, projetos, financeiro e frota foi preservada para apresentar a organização do sistema. Os registros de exemplo são sintéticos.

## Executar

Requer Python 3.10 ou superior. No terminal, dentro desta pasta:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abra http://127.0.0.1:8000. Não existe login ou senha. Não é necessário arquivo .env, Supabase ou conta em outro serviço.

## O que foi retirado

- Cadastros reais de funcionários, documentos pessoais, familiares, salários e históricos importados.
- Nomes de clientes, obras, equipes e históricos financeiros reais.
- Contatos e identidade visual da empresa, fotografias, vídeo e logos de clientes.
- Bancos de dados e seus arquivos auxiliares; scripts e documentos de operação e implantação.
- Backend operacional de autenticação, armazenamento e integrações com bancos, emissão fiscal, e-mail, WhatsApp, rastreamento e IA.
- Senhas padrão e formulários de senha e envio de arquivos ativos.

## Comportamento da demonstração

A página inicial abre diretamente o painel como visitante. Os módulos que dependiam do backend operacional exibem uma mensagem de indisponibilidade. Não há emissão fiscal, envio de mensagens, importação de documentos, contas reais ou rastreamento ao vivo. O frontend possui rotinas desses módulos para apresentar a estrutura da interface, mas não há rotas operacionais correspondentes no servidor.

Edições de exemplo usam o armazenamento do navegador; não são gravadas em banco ou no servidor. Use o botão “Restaurar demonstração” para voltar aos exemplos iniciais. Não insira dados pessoais. Alterações não são compartilhadas entre visitantes.

O mapa pode carregar recursos públicos do OpenStreetMap. Bibliotecas visuais podem depender de CDN e internet. A política de conteúdo bloqueia consultas da aplicação a APIs externas.

O ZIP original não foi modificado. Esta versão contém apenas os arquivos necessários para a demonstração. Os campos de CPF/CNPJ existem como parte da interface, mas os cadastros de exemplo não possuem documentos reais.
