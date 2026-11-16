# Histórico do projeto — SysOrdem Imobil

Registro do que foi feito, decidido e deixado pendente, para quem continuar o trabalho (pessoa ou agente).
Ordem cronológica. Os detalhes técnicos de cada parte estão nos documentos de `docs/` citados.

> Este repositório é **público**: nada de senhas, chaves ou dados pessoais aqui. Detalhes operacionais do
> servidor (acesso, caminhos de chaves) ficam fora do Git, no arquivo particular `HISTORICO_OPERACIONAL.md`, na
> pasta de backups local do responsável (`../imobil-backups/`).

## Como o trabalho é conduzido
- Mensagens ao responsável sempre em **português do Brasil**.
- Cada fase ou entrega tem uma branch e um PR. O PR é aberto ao fim da entrega, sem pedir, e **mesclado só com o "sim" do
  responsável**, sempre com merge commit.
- Fases grandes são divididas: 3a/3b/3c, 4a/4b/4c, 5a/5b/5c, 6a/6b.
- Dúvidas de regra de negócio viram hipótese documentada em [DECISOES_PENDENTES.md](DECISOES_PENDENTES.md).
- Produção só recebe deploy manual, com aprovação no environment `production` do GitHub. O staging recebe deploy
  automático a cada push na `main`.

## Linha do tempo

### 10–12/11/2026 — Fases 0 a 5 (PRs #1 a #12)
| PR | Entrega |
|---|---|
| — | Fase 0: base multiempresa (RLS por tenant, RBAC, auditoria), identidade visual SysOrdem |
| #1 | Fase 1: empreendimentos, quadras, lotes e mapa |
| #2 | 2a: compradores, corretores, reservas com expiração |
| #3 | 2b: contratos com condições versionadas; política de contratos por empresa, Price/SAC |
| #4 | 2c: leads, propostas, escopo do corretor |
| #5 | 3a: parcelas, contas a receber, recebimentos e estornos |
| #6 | 3b: comissões, repasses ao proprietário da área, contas a pagar |
| #7 | 3c: renegociação, distrato e relatórios |
| #8 | 4a: cobranças Pix/boleto por adaptador de pagamentos (simulador) e webhooks autenticados |
| #9 | 4b: conciliação bancária (extrato OFX/CSV) |
| #10 | 5a: site público e formulário de contato |
| #11 | 5b: portal do comprador |
| #12 | 5c: planos SaaS, cadastro self-service, onboarding, faturas da assinatura |

### 14–15/11/2026 — Assinatura eletrônica e Fase 6 (PRs #13 a #15)
- **#13** Assinatura eletrônica com **Documenso auto-hospedado**. O responsável escolheu esse provedor. Ver [ASSINATURA_ELETRONICA.md](ASSINATURA_ELETRONICA.md).
- **#14** Fase 6a:
  - cabeçalhos de segurança, CSP com nonce, limite de corpo e de taxa nos webhooks, logs JSON;
  - travas de configuração de produção;
  - CI com auditoria de dependências, segredos e imagens;
  - CD com staging, backup e rollback. Ver [CI_CD.md](CI_CD.md) e [SEGURANCA.md](SEGURANCA.md).
- **#15** Fase 6b:
  - restauração de backup conferida no CI (job `drill`);
  - teste de carga e de concorrência (corrida de reservas);
  - autorrevisão de segurança. Ver [REVISAO_SEGURANCA.md](REVISAO_SEGURANCA.md).

### 15/11/2026 — Colocação no ar (PRs #16 a #20)
- **#16** Domínios do Imobil. O deploy não falha enquanto o servidor não está cadastrado.
- **#17** O nginx do próprio servidor faz o HTTPS (certbot), e staging e produção ficam **no mesmo servidor**, por escolha do responsável.
- **#18** O servidor é uma VPS **compartilhada** com outros sistemas SysOrdem. Por isso:
  - portas próprias: produção 3710/8710, staging 3720/8720, só em 127.0.0.1;
  - limite de memória em todos os containers;
  - PDF do contrato determinístico.
- **#19** Rotação de logs dos containers (até 3 arquivos de 10 MB por container).
- **#20** `deploy/pull_backups.sh`: cópia dos backups criptografados e das chaves para o computador do responsável.
- Domínios: `imobil.` / `api.imobil.` (produção) e `staging.imobil.` / `api.staging.imobil.` (staging), todos em
  `sysordemsistemas.com.br`. O staging pede senha extra (basic auth) e não é indexado.
- Backups diários às 03:15 (produção) e 03:20 (staging), criptografados. A cópia fora do servidor é **manual** (`pull_backups.sh`).
- Disco do servidor: estava em 88%. A limpeza do cache de build do Docker (de todos os projetos) baixou para 72%.

### 15–16/11/2026 — Pagamentos reais com Asaas (PRs #21 e #22)
- Decisão do responsável: **Asaas** como PSP (decisão pendente 6, agora resolvida).
- **#21** Integração completa. Ver [PAGAMENTOS_ASAAS.md](PAGAMENTOS_ASAAS.md).
  - **Recebimentos de cada loteadora:** a empresa liga a própria conta Asaas em Configurações → Recebimentos. A chave de
    API fica no banco cifrada com AES-256-GCM, e o webhook é registrado sozinho na conta da empresa.
  - **Assinatura SaaS:** cobrada pela conta Asaas da plataforma (variáveis `BILLING_ASAAS_*`).
  - **Webhooks:** autenticados por token e sempre **confirmados na API do Asaas** antes de qualquer efeito.
  - **Botão "Atualizar situação no Asaas"** para quando um aviso se perde.
  - **Testes:** contra um Asaas simulado e contra o **sandbox real**. Boleto: emissão, pagamento simulado, baixa
    automática pelo webhook no staging, cancelamento.
- **#22** Documentação: no `.env` do servidor, a chave do Asaas vai **entre aspas simples**. Ela começa com `$`, e o
  docker compose a leria como variável.
- Situação ao fim de 16/11/2026:
  - **Staging:** Asaas ativo. A conta **sandbox** do responsável está ligada na empresa "demo", com o webhook testado.
  - **Produção:**
    - roda a `sha-1e868db`, com o cofre de credenciais e a URL pública configurados;
    - a assinatura SaaS cobra pela conta Asaas da SysOrdem (aprovada, então o Pix funciona);
    - webhook "SysOrdem Imobil" ativo.
    - A mesma conta Asaas tem o webhook do SysOrdem Saúde. Eventos do Saúde chegam ao Imobil e são ignorados.

## Estado atual
- `main` = `2583851`; sem PRs abertos.
- Produção em `sha-2583851` (16/11: estorno automático e cópia externa dos backups). O staging acompanha a `main`.
- Migrations até **0017**.
- Testes: 323 na API (pytest, com PostgreSQL real e RLS) e 28 na web (Vitest). CI: api, web, infra e drill.

## Pendências (em ordem sugerida)
1. **Loteadoras:** ligar a conta Asaas de produção de cada empresa (Configurações → Recebimentos) e cadastrar o CNPJ
   (exigido para gerar a fatura da assinatura).
2. **SysOrdem Saúde:** confirmar que ele ignora os pagamentos do Imobil que chegam ao webhook dele (a conta Asaas é a mesma).
3. **Revisões externas:** pentest profissional e revisão jurídica, incluindo contrato, distrato, juros/multa, LGPD e termos de uso.
4. **Contador:** tarifas do Asaas na conciliação (o Asaas credita o valor líquido) e retenções de impostos.
5. ~~**Estornos:** estorno automático~~ **Feito**: o estorno total no Asaas estorna o recebimento e o parcial é sinalizado (ver PAGAMENTOS_ASAAS.md). Para receber o aviso de estorno parcial, as contas já ligadas precisam ser religadas.
6. **Backups:** a cópia automática fora do servidor está pronta no código (`deploy/offsite_backup.sh`, com conferência, sem apagar no destino e com aviso ao monitor). **Falta configurar no servidor:** bucket, credencial sem permissão de apagar, rclone e `.env` (passo a passo em CI_CD.md → Backups). Até lá a cópia segue manual (`deploy/pull_backups.sh`).
7. **Disco da VPS:** acompanhar. É ocupado principalmente por imagens de outros sistemas.
8. **Demais decisões abertas:** lista completa em [DECISOES_PENDENTES.md](DECISOES_PENDENTES.md).
