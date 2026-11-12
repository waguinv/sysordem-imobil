# Plano de testes

## Implementado (Fase 0)
- `apps/api/tests/test_rls.py`: papel da aplicação sem superuser/BYPASSRLS; sem contexto nada é visível; consulta sem filtro só vê o tenant do contexto; INSERT/UPDATE para outro tenant rejeitados; contexto não vaza entre transações na mesma conexão; auditoria append-only.
- `apps/api/tests/test_tenant_isolation.py`: tenant A não lista/edita dados de B; `X-Tenant-Id`/`tenant_id` forjados ignorados; troca de empresa exige vínculo; vínculo revogado invalida token; token com tenant e vínculo misturados rejeitado; RBAC negativo (leitura, admin × owner); auditoria com ator e motivo; limite de tentativas de login.
- `apps/api/tests/test_unit_security.py`: Argon2id, JWT (assinatura, expiração, `alg=none`), matriz RBAC.
- `apps/web/tests/nav.test.ts`: menu filtrado por permissão.

## Implementado (Fase 1) — `apps/api/tests/test_fase1.py`
- Cadastro de empreendimento, quadras (ordenação natural) e lotes em lote; mapa e resumo (estoque disponível em Decimal exato).
- Decimal: preço `0.30` exato; 3 casas decimais rejeitadas.
- Isolamento: tenant B não vê/edita empreendimento, mapa, lote, histórico de A; não anexa lote a quadra de A; FK composta bloqueia referência cruzada mesmo direto no banco; RLS esconde as 5 tabelas sem contexto; histórico append-only.
- RBAC: leitura/corretor só visualizam; comercial muda status mas não edita catálogo; dados de proprietários só para owner/admin/financeiro.
- Status: fluxo reservado → vendido com histórico (ator e motivo) e auditoria; saída de vendido negada; preço de vendido imutável; versão desatualizada → 409.
- **Concorrência**: duas transações simultâneas vendendo o mesmo lote → exatamente uma vence, um único registro "vendido" no histórico.
- `tests/test_db_dependency.py`: COMMIT antes da resposta (regressão).

## Planejado

- Unitários: cálculos de parcelas/arredondamento; comissões; estados e expiração de reservas; duplicidade de conciliação.
- Integração: migrations em banco limpo; contrato e geração de parcelas; reserva concorrente; webhook repetido; importação de extrato.
- Segurança: tenant A não acessa/edita tenant B; comprador não acessa contrato alheio; perfil sem permissão não baixa pagamento; DTO público sem dados privados.
- Operação: backup/restauração; readiness; logs sem segredos; falha do PSP sem corromper o estado financeiro.
