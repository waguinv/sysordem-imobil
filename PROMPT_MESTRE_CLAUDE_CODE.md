# PROMPT MESTRE — SysOrdem Imobil (codinome LoteaPro)

> Fases 0, 1 e 2 concluídas (2a: compradores, corretores, reservas; 2b: contratos; 2c: leads, propostas, escopo do corretor). Fase 3 concluída: 3a (parcelas, contas a receber, recebimentos, estornos), 3b (comissões, repasses ao proprietário, contas a pagar) e 3c (renegociação, distrato, relatórios). Fase 4a concluída (adaptador de pagamentos, PSP sandbox, cobranças Pix/boleto, webhooks) e 4b (conciliação bancária com extrato OFX/CSV). Próximo: 4c (PSP real) aguarda a escolha do cliente; sem ela, seguir para a Fase 5 — ver README.md e docs/SEGURANCA.md. Identidade visual: docs/IDENTIDADE_VISUAL.md (padrão SysOrdem).

Atue como engenheiro de software sênior e arquiteto SaaS multi-tenant. Desenvolva o SysOrdem Imobil (codinome LoteaPro), uma plataforma responsiva para gestão de loteamentos de terrenos e chácaras. Antes de alterar código, leia README.md e todos os documentos em docs/. Trabalhe incrementalmente, implemente testes e não declare algo pronto sem executar e relatar os testes.

## Produto
- Empresas/tenants isolados; usuários e permissões.
- Empreendimentos, proprietários da área, documentos e acordos financeiros.
- Quadras/lotes, planta/mapa, status disponível/reservado/vendido/bloqueado e histórico.
- CRM, compradores, leads, corretores, propostas e reservas com expiração.
- Contratos, entrada, parcelas, juros/multa/correção configuráveis, renegociação e distrato.
- Contas a pagar/receber, pagamentos parciais, estornos, conciliação e fluxo de caixa.
- Comissões de corretores e participação do proprietário da área.
- Site público sincronizado e portal do comprador.
- Integrações futuras com PSP/banco, Pix/boleto, e-mail/WhatsApp e assinatura eletrônica.
- Administração SaaS: planos, limites, onboarding e cobrança de assinatura.

## Stack
- Frontend Next.js + React + TypeScript, mobile-first/PWA.
- Backend FastAPI + Pydantic + SQLAlchemy 2 + Alembic.
- PostgreSQL; Docker Compose; pytest.
- API REST versionada em /api/v1.

## Regras obrigatórias
1. Toda entidade de negócio deve pertencer a tenant_id. O servidor deriva tenant da identidade autenticada; nunca confie no tenant_id enviado pelo browser.
2. Implementar RBAC e testes negativos: tenant A não acessa dados de tenant B. Planejar PostgreSQL RLS e documentar a configuração correta do contexto por transação.
3. Valores monetários usam Decimal e NUMERIC, nunca float.
4. Reserva/venda de lote precisa de transação e controle de concorrência para impedir dupla venda.
5. Não apagar lançamentos financeiros; corrigir por estorno/contra-lançamento auditável.
6. Webhooks autenticados, idempotentes e deduplicados. Redirect de browser não confirma liquidação.
7. Não guardar senhas bancárias; usar PSP/banco autorizado e segredos fora do repositório.
8. O site público nunca expõe compradores, CPF, contratos, parcelas ou inadimplência. Usar DTOs públicos separados.
9. Auditar alterações críticas com ator, data/hora, entidade, ação e motivo.
10. Aplicar minimização de dados e processos LGPD; não declarar conformidade legal de loteamento.
11. Nunca incluir segredos reais nem dados pessoais reais no Git.
12. Não publicar em produção antes de testes, revisão de segurança, HTTPS, backups restauráveis e monitoramento.

## Método
- Entregas verticais pequenas; cada uma inclui migrations, schemas, serviços, rotas, autorização, logs e testes.
- Documente hipóteses quando faltarem regras comerciais; deixe-as configuráveis.
- Não implemente todos os módulos de uma vez.
- Execute testes, lint e type-check disponíveis; informe comandos e resultados reais.

## Ordem
Fase 0: configuração, banco, migrations, health/readiness, testes, autenticação, membership de tenant, RBAC e testes de isolamento.
Fase 1: tenants, empreendimentos, proprietários, quadras, lotes, histórico e mapa responsivo.
Fase 2: CRM, compradores, corretores, propostas, reservas com expiração e contratos.
Fase 3: parcelas, contas a pagar/receber, comissões, recebimentos parciais, estornos, renegociação, distratos e relatórios.
Fase 4: adaptador de pagamentos sandbox, depois integração com um PSP escolhido pelo cliente; Pix/boleto se suportados, webhooks e conciliação.
Fase 5: site público, portal do comprador, planos SaaS, onboarding e cobrança.
Fase 6: segurança, carga, backups restaurados, staging, CI/CD, rollback e revisão profissional.

## Primeira tarefa
Inspecione o repositório. Faça apenas a Fase 0: corrija execução local, configure banco/migrations/testes, implemente autenticação e multi-tenancy corretamente ou documente precisamente o que falta, crie teste provando isolamento entre tenants e atualize documentação. Não avance enquanto a base não passar nos testes. Ao concluir, liste arquivos alterados, comandos executados, resultados reais e limitações.
