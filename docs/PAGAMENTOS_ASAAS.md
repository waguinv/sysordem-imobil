# Pagamentos reais — Asaas

Decisão (16/11/2026): **Asaas** é o PSP do SysOrdem Imobil, para Pix e boleto (decisão pendente 6).
O simulador (sandbox) continua para desenvolvimento e fica desligado em produção.

## Duas contas, dois papéis

| Quem cobra | De quem | Conta Asaas | Onde se configura |
|---|---|---|---|
| A **empresa** (loteadora) | compradores (entrada e parcelas) | a da **própria empresa**: o dinheiro cai direto nela | Configurações → Recebimentos (owner/admin) |
| A **SysOrdem** | empresas (assinatura SaaS) | a da plataforma | variáveis `BILLING_ASAAS_*` do servidor |

### Conta da empresa
1. No Asaas da empresa: Integrações → Chave de API (sandbox para testar, produção para valer).
2. No sistema: Configurações → Recebimentos → escolher o ambiente e colar a chave.
3. O sistema confere a chave na API, **registra o webhook na conta da empresa**
   (`PUBLIC_API_URL/api/v1/webhooks/payments/asaas/<empresa>`, eventos de pagamento, envio sequencial) com um
   token aleatório e guarda a chave **cifrada** (AES-256-GCM, `PAYMENTS_CREDENTIALS_KEY`). O token do webhook
   fica só como SHA-256. A chave nunca volta para a tela, para a auditoria ou para os logs (só os 4 últimos caracteres).
4. A partir daí, novas cobranças da empresa saem pelo Asaas. Cobranças antigas seguem no provedor em que foram emitidas.

Trocar a chave refaz o webhook (o antigo é apagado no Asaas). Desligar exige zero cobranças pendentes no Asaas.
Em produção, só a conta de produção do Asaas é aceita.

### Conta da plataforma (assinatura)
`BILLING_ASAAS_API_KEY`, `BILLING_ASAAS_ENVIRONMENT` (`production` obrigatório em produção) e
`BILLING_ASAAS_WEBHOOK_TOKEN` (32+ caracteres). **A chave começa com `$`: no `.env` do servidor, escreva-a entre aspas simples**
(`BILLING_ASAAS_API_KEY='$aact_prod_...'`). Sem as aspas, o docker compose trata `$aact_...` como variável e a API
recebe a chave vazia.

Situação em 16/11/2026: produção configurada com a conta da SysOrdem (ESMERALDA SERVICOS LTDA, aprovada) e webhook
"SysOrdem Imobil" ativo. A mesma conta já tinha o webhook do SysOrdem Saúde. O Asaas manda todos os eventos da conta para os
dois webhooks: os eventos do Saúde chegam aqui e são ignorados, porque não trazem a referência `<empresa>:<fatura>`. Na conta Asaas da SysOrdem, cadastre **um** webhook para
`https://api.imobil.sysordemsistemas.com.br/api/v1/webhooks/billing/asaas` com esse token e os eventos
`PAYMENT_RECEIVED`, `PAYMENT_CONFIRMED`, `PAYMENT_DELETED` e `PAYMENT_REFUNDED`. A fatura leva a referência
`<empresa>:<fatura>`, e é assim que o webhook único descobre a empresa. A empresa precisa informar o CNPJ
(Configurações → Recebimentos) antes de gerar a fatura.

## Como uma cobrança funciona
- **Emissão**: o cadastro do comprador é reaproveitado no Asaas (`externalReference` = id do comprador; CPF/CNPJ é
  obrigatório). Depois a cobrança `PIX`/`BOLETO` é criada e o sistema busca o copia e cola ou a linha digitável. Se a
  busca falhar, a cobrança é apagada no Asaas. A página do Asaas com o QR Code e o boleto em PDF (`invoiceUrl`) aparece
  para a equipe e no portal do comprador.
- **Encargos**: se a cobrança vence junto com a parcela ainda em dia, o Asaas recebe a multa e os juros do contrato
  e os aplica a quem pagar atrasado. Para parcela já vencida, o valor do dia já inclui multa e mora, e o Asaas não
  acrescenta nada. Os encargos vão sempre explícitos, para a configuração global da conta Asaas não somar nada à revelia.
- **Baixa**: o webhook é autenticado pelo token (`asaas-access-token`) e deduplicado pelo id do evento. Antes de
  qualquer efeito, o sistema **consulta o pagamento na API do Asaas** e usa a situação e o valor de lá, nunca os do
  corpo do aviso. Se o Asaas estiver fora do ar nessa consulta, a resposta é 503 e o Asaas reenvia depois. Pagamento
  confirmado vira um recebimento normal, com multa, mora, comissão e repasse calculados como em qualquer recebimento.
  Se não puder ser aplicado, a cobrança fica "divergente".
- **Atualizar situação**: botão na cobrança que consulta o Asaas e aplica o que mudou, pelo mesmo caminho do webhook.
  É a rede de segurança para aviso perdido e o único caminho em ambiente sem `PUBLIC_API_URL`.
- **Cancelamento**: cancelar aqui apaga a cobrança no Asaas. Cobrança apagada no painel do Asaas vira "cancelada"
  aqui, pelo webhook ou pela consulta.

## Servidor (staging/produção)
```bash
# uma vez por ambiente, no .env do servidor
PAYMENTS_CREDENTIALS_KEY=$(openssl rand -base64 32)
PUBLIC_API_URL=https://api.imobil.sysordemsistemas.com.br        # staging: https://api.staging.imobil...
```
Guarde `PAYMENTS_CREDENTIALS_KEY` fora do servidor, junto com as chaves de backup (`deploy/pull_backups.sh`).
Sem ela, as chaves cifradas não abrem e cada empresa precisa religar a conta Asaas.
O nginx já expõe só `/api/v1/webhooks/` e `/health` na API pública.

## Teste contra o sandbox real
```bash
cd apps/api && python -m scripts.asaas_smoke   # lê ASAAS_SANDBOX_API_KEY do ambiente ou do .env da raiz
```
O script cria um Pix de R$ 12,34 (ou um boleto, se o Pix da conta ainda não estiver liberado), confirma o
pagamento pelo endpoint de simulação do sandbox, confere a consulta, emite e cancela um boleto.

**Resultado em 16/11/2026, contra o sandbox real**: chave validada; boleto emitido com linha digitável e link da
fatura; o pagamento simulado virou `charge.paid` com o valor certo; o cancelamento virou `charge.cancelled`; o
Asaas recusou cancelar uma cobrança já paga. Também passou o fluxo completo pela API local: conta ligada, boleto
da parcela, pagamento simulado, "Atualizar situação", cobrança paga e parcela quitada.
**Pix**: o Asaas só libera Pix para conta **aprovada** (com chave Pix cadastrada). Antes disso, a emissão Pix
devolve a mensagem do Asaas ("O Pix não está disponível no momento…") e o boleto funciona normalmente. Ele recusa chave de produção. Os testes automatizados (`tests/test_asaas.py`)
usam um Asaas falso que segue a API v3.

## Estornos (devolução ao pagador)
- Eventos `PAYMENT_REFUNDED` e `PAYMENT_PARTIALLY_REFUNDED`. Como nos pagamentos, o efeito vem da **consulta** da
  cobrança no Asaas, não do corpo do aviso. Só conta o que o Asaas já devolveu: os itens de `refunds` com
  `status: DONE` (a documentação avisa que um item no array não quer dizer dinheiro devolvido). Status `REFUNDED`
  significa estorno total.
- **Total**, de cobrança paga: o recebimento da parcela é estornado pelo mesmo caminho do estorno manual (desfaz
  comissão, repasse e conciliação, e a parcela volta a "aberta"). A cobrança fica "estornada" e a parcela pode ser
  cobrada de novo. Se o financeiro já tinha estornado à mão, só a cobrança é marcada.
- **Parcial**: não existe meio estorno de recebimento. A cobrança continua paga, com o aviso em destaque, e o
  financeiro estorna e registra o valor que ficou (decisão 41).
- Parcela renegociada ou cancelada: o estorno não é aplicado sozinho e fica sinalizado na cobrança.
- Cobrança **divergente** devolvida por inteiro: a divergência fica resolvida automaticamente.
- Webhook perdido: o botão "Atualizar situação no Asaas" também consulta cobranças pagas e aplica o estorno.
- Contas ligadas antes desta versão: o webhook delas foi cadastrado sem `PAYMENT_PARTIALLY_REFUNDED`. Para recebê-lo,
  desligue e ligue de novo a conta em Configurações → Recebimentos. O estorno total chega de qualquer forma.
- Assinatura SaaS: estorno de fatura continua fora do automático. É decisão do operador (decisão 38).

## Pendências
- Conferir com o contador: o Asaas credita o valor **líquido** de tarifas (`netValue`); a baixa usa o valor pago pelo
  comprador (`value`). A tarifa fica fora do recebimento (decisão pendente 31).
- Cartão de crédito e Pix automático não fazem parte do escopo.
