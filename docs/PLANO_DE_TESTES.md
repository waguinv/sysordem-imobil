# Plano de testes

## Implementado (Fase 0)
- `apps/api/tests/test_rls.py`: papel da aplicação sem superuser/BYPASSRLS; sem contexto nada é visível; consulta sem filtro só vê o tenant do contexto; INSERT/UPDATE para outro tenant rejeitados; contexto não vaza entre transações na mesma conexão; auditoria append-only.
- `apps/api/tests/test_tenant_isolation.py`: tenant A não lista/edita dados de B; `X-Tenant-Id`/`tenant_id` forjados ignorados; troca de empresa exige vínculo; vínculo revogado invalida token; token com tenant e vínculo misturados rejeitado; RBAC negativo (leitura, admin × owner); auditoria com ator e motivo; limite de tentativas de login.
- `apps/api/tests/test_unit_security.py`: Argon2id, JWT (assinatura, expiração, `alg=none`), matriz RBAC.
- `apps/web/tests/nav.test.ts`: menu filtrado por permissão.

## Planejado

- Unitários: cálculos de parcelas/arredondamento; comissões; estados e expiração de reservas; duplicidade de conciliação.
- Integração: migrations em banco limpo; contrato e geração de parcelas; reserva concorrente; webhook repetido; importação de extrato.
- Segurança: tenant A não acessa/edita tenant B; comprador não acessa contrato alheio; perfil sem permissão não baixa pagamento; DTO público sem dados privados.
- Operação: backup/restauração; readiness; logs sem segredos; falha do PSP sem corromper o estado financeiro.
