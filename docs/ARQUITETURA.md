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
