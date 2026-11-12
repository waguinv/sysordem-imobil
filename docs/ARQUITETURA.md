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
