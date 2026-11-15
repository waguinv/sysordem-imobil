# Assinatura eletrônica de contratos (Documenso)

Provedor escolhido: **Documenso Community Edition** (código aberto, AGPL-3.0), auto-hospedado — gratuito, com API e
webhooks. A integração passa por um adaptador (`apps/api/app/services/esign/`); trocar de provedor é escrever outro
adaptador, sem mexer em contratos.

> Validade jurídica: assinatura eletrônica avançada é usada em contratos particulares, mas o tipo exigido (avançada ×
> qualificada/ICP-Brasil) para o registro dos contratos de cada empresa deve ser confirmado com o jurídico
> (DECISOES_PENDENTES item 18). O simulador não tem validade jurídica.

## Como funciona
1. Na tela do contrato (rascunho), proprietário/administrador clica **Enviar versão N para assinatura**.
   Enviar exige a permissão `contracts:sign`, porque a conclusão assina o contrato.
2. A API gera o PDF do **quadro-resumo** da versão vigente (partes, imóvel, condições), registra o **SHA-256** do arquivo e
   cria o envelope no Documenso com dois signatários: o **comprador** (e-mail do cadastro) e o **representante da empresa**
   (usuário que enviou). O Documenso manda os links por e-mail.
3. Enquanto o envio está em andamento o contrato fica **congelado**: não muda de versão, não é cancelado nem assinado
   manualmente (cancele o envio antes).
4. O Documenso avisa por **webhook**. A API autentica (segredo), deduplica o evento e, para "concluído" ou "recusado",
   **confirma a situação direto na API do Documenso** antes de qualquer efeito — o webhook sozinho não assina nada.
5. Concluído e confirmado → o contrato é assinado (mesmo efeito do registro manual: lote vendido, parcelas, comissões),
   desde que a versão ainda seja a do envio. Recusado → o contrato segue em rascunho e pode ser reenviado.

Sem Documenso configurado, fora de produção fica disponível o **simulador** (botões "Simular: todos assinam/recusa"),
que passa pelo mesmo caminho de webhook assinado e confirmação.

## Subir o Documenso (Docker Compose)
1. Gere o certificado que sela os PDFs (autoassinado serve para testes; em produção use um certificado adequado):
   ```bash
   mkdir -p docker/documenso
   openssl req -x509 -newkey rsa:2048 -keyout /tmp/doc.key -out /tmp/doc.crt -days 3650 -nodes -subj "/CN=SysOrdem Imobil"
   openssl pkcs12 -export -out docker/documenso/cert.p12 -inkey /tmp/doc.key -in /tmp/doc.crt -passout pass:SUA_SENHA
   ```
   `*.p12` está no `.gitignore` — nunca comite o certificado.
2. No `.env`: `DOCUMENSO_DB_PASSWORD`, `DOCUMENSO_NEXTAUTH_SECRET`, `DOCUMENSO_ENCRYPTION_KEY`,
   `DOCUMENSO_ENCRYPTION_SECONDARY_KEY` (32+ caracteres cada; `openssl rand -base64 32`), `DOCUMENSO_SIGNING_PASSPHRASE`
   (a senha do .p12), `DOCUMENSO_PUBLIC_URL` (endereço público, ex.: `https://assinar.suaempresa.com.br`) e o SMTP.
3. `docker compose --profile assinatura up -d` — o Documenso fica em `DOCUMENSO_PUBLIC_URL` (padrão `http://localhost:3005`).
4. No Documenso: crie a conta da plataforma, gere um **token de API** e cadastre um **webhook**:
   - URL: `http://api:8000/api/v1/webhooks/esign/documenso` (rede interna do Compose) ou o endereço público da API;
   - eventos: `DOCUMENT_COMPLETED`, `DOCUMENT_SIGNED`, `DOCUMENT_RECIPIENT_COMPLETED`, `DOCUMENT_REJECTED`,
     `DOCUMENT_CANCELLED`;
   - segredo: um valor aleatório de 32+ caracteres.
5. No `.env` da API: `DOCUMENSO_URL` (ex.: `http://documenso:3000`), `DOCUMENSO_API_TOKEN` e `DOCUMENSO_WEBHOOK_SECRET`
   (o mesmo do passo 4). Reinicie a API. Com os três preenchidos o provedor real aparece; sem segredo de webhook ele
   não é habilitado (nunca aceitamos webhook sem autenticação).

## Segurança
- Webhook: segredo comparado em tempo constante; deduplicação por evento; a empresa vem da referência gravada no
  envelope (`<empresa>:<envio>`) e o envio é buscado só dentro dela (RLS) e precisa casar com o id do envelope.
- Conclusão/recusa confirmadas na API do provedor (o segredo do Documenso é estático, sem carimbo de tempo).
- Um envio em andamento por contrato (índice único parcial); envio vale para UMA versão (SHA-256 do PDF registrado).
- Token e segredo só no ambiente/cofre; nunca no banco nem no Git.

## API
| Rota | Permissão | Uso |
|---|---|---|
| `GET /esign/providers` | contracts:view | provedores habilitados neste ambiente |
| `GET /contracts/{id}/signature-requests` | contracts:view | histórico de envios |
| `GET /contracts/{id}/document.pdf` | contracts:view | prévia do PDF (cabeçalho `X-Content-SHA256`) |
| `POST /contracts/{id}/signature-requests` | contracts:sign | enviar (`expected_version`) |
| `POST /signature-requests/{id}/cancel` | contracts:sign | cancelar envio (cancela no provedor) |
| `POST /signature-requests/{id}/sandbox/simulate` | contracts:sign | simulador (fora de produção) |
| `POST /webhooks/esign/{provider}` | — (autenticado pelo provedor) | notificações |
