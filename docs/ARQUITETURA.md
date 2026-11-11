# Arquitetura

## Domínios
Identidade e acesso; tenants/memberships; empreendimentos e proprietários; quadras/lotes e histórico; CRM/leads/compradores/corretores; propostas/reservas/contratos; parcelas; contas a pagar/receber; pagamentos, alocações, estornos e conciliação; comissões; site público; portal do comprador; integrações; planos SaaS.

## Implementado na Fase 0
- `apps/api/app/core`: configuração, sessão com contexto de tenant por transação, hash de senha e JWT.
- `apps/api/app/auth`: dependência `get_auth` (tenant derivado do token e revalidado no banco) e RBAC (`require(Perm.X)`).
- Tabelas: `tenants`, `users`, `memberships` (RLS), `audit_events` (RLS, append-only). Migration `0001`.
- Rotas: `/health`, `/ready`, `/api/v1/auth/login`, `/api/v1/auth/switch-tenant`, `/api/v1/me`, `/api/v1/tenant`, `/api/v1/members` (GET/POST/PATCH), `/api/v1/audit-events`.
- Web: BFF Next.js (token em cookie httpOnly; chamadas à API pelo servidor), login, shell com menu por permissão, painel e equipe.
- Detalhes de segurança em [SEGURANCA.md](SEGURANCA.md).

## Implementado na Fase 1
- Tabelas (todas com `tenant_id` + RLS): `land_owners`, `developments`, `blocks` (quadras), `lots`, `lot_status_history` (append-only). Migration `0002`.
- **FKs compostas `(tenant_id, id)`**: checagens de chave estrangeira ignoram RLS; sem a FK composta, um registro do tenant A poderia apontar para uma linha do tenant B cujo id fosse conhecido.
- Dinheiro e área em `NUMERIC` (`price` 14,2; `area_m2` 12,2) e `Decimal`; o JSON trafega strings decimais e o web só converte para exibir.
- Status de lote: `disponivel`, `reservado`, `vendido`, `bloqueado`. Transições (Fase 1, manuais): disponível → reservado/vendido/bloqueado; reservado → disponível/vendido; bloqueado → disponível; vendido → (só distrato, Fase 3). Preço de lote vendido é imutável.
- **Concorrência**: mudança de status e edição usam `SELECT … FOR UPDATE` + versão esperada (`expected_version`). A segunda requisição simultânea recebe 409 — testado com duas transações reais em paralelo.
- Rotas: `/land-owners`, `/developments` (+ `/{id}`, `/{id}/map`, `/{id}/blocks`, `/{id}/lots`, `/{id}/lots/batch`), `/lots/{id}` (+ `/status`, `/history`).
- Permissões novas: `developments:view` (todos), `developments:manage` (owner/admin), `lots:status` (owner/admin/comercial), `owners:view` (owner/admin/financeiro), `owners:manage` (owner/admin).
- Web: lista de empreendimentos, cadastro, mapa esquemático por quadra (grade responsiva, cores de status SysOrdem), detalhe do lote com mudança de status, edição e histórico, proprietários (documento mascarado na tela).
- Mapa: esquemático por quadra/número. O campo `lots.geometry` (GeoJSON) fica reservado para a planta georreferenciada quando o formato da planta for definido (ver DECISOES_PENDENTES.md).

## Implementado na Fase 2a
- Tabelas (RLS + FKs compostas): `customers` (CPF/CNPJ único por empresa), `brokers` (ativo/inativo, `user_id` opcional), `reservations` (sem DELETE; encerra por status). Migration `0003`.
- **Reserva** (`app/services/reservations.py`): trava o lote (`FOR UPDATE`), exige "disponível", grava o **preço do momento** e a validade (padrão 72 h, máx. 720 h — `RESERVATION_DEFAULT_HOURS`/`RESERVATION_MAX_HOURS`). Status: `ativa` → `convertida` | `cancelada` | `expirada`.
- **Barreira no banco** contra reserva dupla: índice único parcial `reservations(lot_id) WHERE status='ativa'`.
- "Reservado" só nasce de uma reserva; lote com reserva ativa não muda de status manualmente (sai cancelando, convertendo ou expirando).
- **Expiração**: sob demanda (ao abrir mapa, lote, reserva e lista) e pelo job `python -m scripts.expire_reservations` (agendar a cada ~5 min). Expiração libera o lote com histórico "reserva expirada" (ator: sistema).
- "Converter em venda" marca o lote como vendido; na Fase 2b passará a gerar o contrato.
- Rotas: `/customers`, `/brokers`, `/reservations` (+ `/{id}`, `/{id}/cancel`, `/{id}/convert`).
- Permissões: `customers:view|manage`, `brokers:view|manage`, `reservations:view|manage`. Comercial reserva e cadastra compradores; financeiro só consulta; leitura sem acesso; corretor com escopo próprio desde a Fase 2c (ver abaixo).
- Web: Compradores (busca, documento mascarado), Corretores (ativar/desativar), Reservas (filtros, prazo restante) e painel de reserva na tela do lote.

## Implementado na Fase 2b
- Tabelas (RLS + FKs compostas): `sales_contracts`, `contract_versions` (append-only), `tenant_counters` (numeração por empresa, `INSERT … ON CONFLICT … RETURNING`). Migration `0004`.
- Fluxo: reserva ativa → **gerar contrato** (rascunho; reserva vira "convertida"; lote segue "reservado") → novas versões de condições → **assinar** (lote "vendido", condições congeladas) ou **cancelar** (lote "disponível"). Contrato assinado só se desfaz por distrato (Fase 3). O antigo "converter reserva" foi substituído pelo contrato.
- Garantias: um contrato não cancelado por lote (índice único parcial); uma reserva gera no máximo um contrato; versão esperada (`expected_version`) ao alterar e ao assinar, para ninguém assinar condições que mudaram.
- Condições: valor total (padrão: preço congelado na reserva), entrada, nº de parcelas (0 = à vista), juros % a.m., 1º vencimento, índice de correção (nenhum/IPCA/IGP-M/INCC), multa e juros de mora. Parcela calculada no servidor em `Decimal` (`app/services/finance.py`: divisão simples ou Tabela Price). O cronograma de parcelas é gerado na Fase 3.
- Numeração `AAAA-NNNN` por empresa; número só é consumido se o contrato for criado.
- Permissões: `contracts:view` (owner/admin/comercial/financeiro), `contracts:manage` (owner/admin/comercial), `contracts:sign` (owner/admin).
- Web: lista de contratos com filtros, tela do contrato (condições, versões, assinar, cancelar, nova versão) e "Gerar contrato" na reserva ativa do lote.

## Política de contratos por empresa (Fase 2b)
- Tabela `contract_policies` (RLS, uma linha por empresa; migration `0005`). Sem linha salva, valem os padrões do sistema (`app/services/policy.py`).
- A empresa define **padrões** (juros do financiamento, sistema de amortização, índice de correção, multa, juros de mora) e **limites** (juros máximo, sistemas permitidos — Price e/ou SAC —, nº máximo de parcelas, entrada mínima) e se multa/mora podem ser negociadas por contrato.
- Em cada contrato, **empresa e cliente escolhem** entrada, parcelas, juros e Price/SAC dentro dos limites; campos omitidos usam o padrão da empresa. Fora da política → 409 com a regra violada.
- `contract_versions.amortization` registra o sistema escolhido; `installment_amount` é a parcela (Price) ou a 1ª parcela (SAC: P/n + P·i, decrescentes).
- `GET /settings/contract-policy` (quem vê contratos) e `PUT` (`settings:manage`: owner/admin), auditado com antes/depois e motivo. Alterar a política não muda contratos existentes.
- Web: Configurações → Política de contratos; o formulário do contrato mostra os padrões e limites da empresa.

## Implementado na Fase 2c — CRM, propostas e escopo do corretor
- Tabelas (RLS + FKs compostas; migration `0006`): `leads`, `lead_activities` (append-only: o papel da aplicação só tem `SELECT, INSERT`), `proposals`; novas colunas `customers.broker_id` (corretor responsável) e índice único `brokers(tenant_id, user_id)`.
- **Leads**: nome + telefone ou e-mail, origem, empreendimento de interesse, corretor. Situação `novo` → `em_contato` (automático ao registrar ligação/WhatsApp/e-mail/visita) → `qualificado`; `perdido` exige motivo e pode ser reaberto. **Converter** cria o comprador com os dados do lead (herda o corretor) ou liga a um comprador existente; lead convertido é final. Todas as mudanças ficam no histórico do lead.
- **Propostas**: lote + comprador + condições (valor, entrada, parcelas, juros, Price/SAC, 1º vencimento) validadas pela **política de contratos** já na criação; validade padrão 7 dias (máx. 90). Proposta aberta não segura o lote — várias podem concorrer. **Aceitar** trava o lote e reserva para o cliente (ou usa a reserva ativa dele no lote) congelando o **valor negociado**; as condições aceitas pré-preenchem o contrato. `aberta` → `aceita` | `recusada` | `cancelada` | `expirada` (vencimento verificado ao listar/abrir/aceitar).
- **Escopo do corretor** (`app/auth/scope.py`, hipótese da decisão 15): o perfil `corretor` precisa estar ligado a um cadastro de corretor ativo (`brokers.user_id`); vê e opera só os próprios leads, propostas e reservas e os compradores pelos quais responde (ou com quem tem lead/proposta/reserva). Atua sempre em nome próprio; fora do escopo a API responde 404. Sem acesso a contratos nem ao cadastro de corretores.
- Permissões: `leads:view|manage` (owner/admin/comercial/corretor), `proposals:view` (+ financeiro), `proposals:manage` (owner/admin/comercial/corretor). Aceitar exige também `reservations:manage`; converter exige `customers:manage`.
- Web: Leads (lista, filtros, cadastro, histórico, conversão), Propostas (lista e decisão), painel de propostas na tela do lote, contrato pré-preenchido pela proposta aceita e vínculo corretor ↔ usuário em Corretores.

## Implementado na Fase 3a — parcelas, contas a receber e recebimentos
- Tabelas (RLS + FKs compostas; migration `0007`): `installments`, `payments` e `payment_reversals` — as duas últimas **append-only** (o papel da aplicação só tem `SELECT, INSERT`): lançamento nunca é editado nem apagado, corrige-se por estorno (um por recebimento, índice único).
- **Cronograma** gerado na **assinatura** a partir da versão assinada (`app/services/finance.py: build_schedule`): nº 0 = entrada (vence no dia da assinatura); parcelas mensais a partir do 1º vencimento (dia 31 → último dia do mês). Price: parcela fixa, juros sobre o saldo; SAC: amortização constante. A **última parcela absorve os centavos**, então a soma das amortizações é exatamente o saldo financiado. Contratos assinados antes da 3a ganham o cronograma na 1ª consulta.
- **Recebimento** de uma parcela, total ou **parcial**, com data (não futura) e forma (Pix, boleto, transferência…). Em atraso: multa (% única) e juros de mora pro rata die (mês de 30 dias), com as taxas da versão do contrato, incidindo **só sobre a parte da parcela quitada agora** — pagar em duas vezes custa o mesmo que de uma vez. Valor acima do devido é recusado. Parcela travada (`FOR UPDATE`) contra recebimentos simultâneos.
- **Estorno** com motivo devolve o valor à parcela (volta a "aberta"). Recebido no mês é líquido de estornos.
- **Datas de negócio** no fuso da empresa (`BUSINESS_TIMEZONE`, padrão `America/Sao_Paulo`, `app/core/clock.py`): vencimentos, atraso e validade de propostas não viram o dia às 21h.
- **Correção monetária (IPCA/IGP-M/INCC) ainda não é aplicada** — falta definir a fonte dos índices; o índice fica registrado no contrato.
- Permissões: `finance:view`, `finance:manage` (registrar), `finance:reverse` (estornar) — owner, admin e financeiro. Comercial e corretor não veem o financeiro.
- Web: Financeiro → Contas a receber (resumo: vencido, vence em 30 dias, em aberto, recebido no mês; filtros e busca), tela da parcela (valor para quitar hoje, recebimento, estorno) e parcelas na tela do contrato.

## Implementado na Fase 3b — comissões, repasses e contas a pagar
- Tabelas (RLS + FKs compostas; migration `0008`): `finance_settings`, `payables`, `payable_events` (append-only) e `participations` (append-only, exceto a coluna `payable_id`, única com `GRANT UPDATE`). Colunas novas: `brokers.commission_pct`, `developments.owner_share_pct` e, em `sales_contracts`, o snapshot `commission_pct`, `commission_basis`, `owner_share_pct`, `land_owner_id`.
- **Comissão** (hipótese, decisão 4): % do corretor ou o padrão da empresa (5%). Base/momento configurável: **sobre o recebido** (padrão: a cada valor de parcela recebido, entrada inclusa) ou **na assinatura** (sobre o total do contrato, de uma vez).
- **Repasse ao proprietário da área** (hipótese, decisão 3): % do empreendimento sobre cada valor de parcela recebido (permuta financeira). Exige o proprietário definido no empreendimento.
- Regras **congeladas no contrato na assinatura**; contratos assinados antes da 3b não geram comissão/repasse. A base é a parte da **parcela** recebida — multa e juros de mora ficam com a empresa.
- Estorno de recebimento lança o **negativo** de cada crédito (um estorno por crédito, índice único); se o original já foi pago, o negativo abate do próximo acerto. Saldo ≤ 0 não gera conta.
- **Acerto**: os créditos pendentes de um beneficiário viram uma conta a pagar (`/participations/settle`); cancelar a conta devolve os créditos para pendente.
- **Contas a pagar**: manuais (fornecedor, obra, imposto, marketing, outro) ou geradas pelo acerto (comissão, repasse). Pagamento (data não futura), estorno e cancelamento com motivo, sempre registrados em `payable_events`.
- Permissões: `finance:view|manage|reverse` (owner, admin, financeiro); regras de comissão exigem `settings:manage`; % do proprietário exige `owners:manage`.
- Web: Comissões e repasses (saldo a acertar, gerar conta, lançamentos), Financeiro → Contas a pagar (resumo, filtros, nova conta, tela da conta), Configurações → Regras de comissão, e % de comissão em Corretores.

## Padrões obrigatórios
- API versionada `/api/v1`; PostgreSQL como fonte transacional.
- `NUMERIC` no banco e `Decimal` na aplicação para dinheiro.
- `tenant_id` em entidades de negócio; autorização server-side. Não confiar em tenant recebido do navegador.
- Planejar PostgreSQL Row-Level Security (RLS) e testar o contexto por transação e conexões pooled.
- Transação e controle de concorrência para reservar/vender lotes.
- Lançamentos financeiros não são apagados: estorno/contra-lançamento com auditoria.
- Webhooks autenticados, idempotentes e deduplicados.
- DTOs públicos separados dos modelos privados; site nunca mostra dados financeiros ou pessoais.
- Segredos fora do Git; armazenamento privado para documentos; URLs temporárias.
- Backups criptografados e restauração testada; logs sem segredos.

## Entidades propostas
Tenant, User, Membership, Development, LandOwnerAgreement, DevelopmentDocument, Block, Lot, LotStatusHistory, Reservation, Lead, Customer, Broker, Activity, Proposal, SalesContract, ContractVersion, Installment, Payable, Receivable, Payment, PaymentAllocation, Reversal, CommissionRule, CommissionAccrual, CommissionPayment, ExternalCharge, WebhookEvent, ReconciliationItem, AuditEvent, SubscriptionPlan, TenantSubscription.
