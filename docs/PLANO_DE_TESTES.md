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

## Implementado (Fase 2a) — `apps/api/tests/test_fase2a.py`
- Reserva: lote vai a "reservado", preço congelado (reajuste posterior não altera a reserva), validade padrão 72 h e limite máximo; corretor inativo recusado.
- Lote com reserva ativa não muda de status manualmente; lote bloqueado/vendido não é reservado; segunda reserva do mesmo lote → 409.
- Cancelar libera o lote e permite nova reserva; converter vende o lote; ações sobre reserva encerrada → 409.
- Expiração: ao ler o lote/reserva, ao reservar de novo e pelo job (todas as empresas); reserva expirada não pode ser convertida.
- **Concorrência**: duas reservas simultâneas do mesmo lote → só uma vence; índice único parcial barra inserção direta no banco.
- Isolamento: empresa B não vê/usa compradores, corretores, reservas nem lotes de A; mesmo CPF permitido em empresas diferentes; RLS nas 3 tabelas.
- RBAC: leitura/corretor sem acesso; financeiro só consulta; comercial cadastra comprador e reserva, mas não cadastra corretor.

## Implementado (Fase 2b) — `tests/test_fase2b.py` e `tests/test_finance.py`
- Gerar contrato a partir da reserva (preço congelado, parcela calculada, reserva convertida, lote preso ao contrato).
- Numeração sequencial por empresa; condições inválidas recusadas **sem efeitos colaterais** (reserva segue ativa, número não consumido).
- Tabela Price (valor de referência 100.000 a 1% em 12× = 8.884,88), venda à vista, arredondamento meio-para-cima.
- Versões: histórico preservado, versão esperada obrigatória; versões append-only no banco.
- Assinar vende o lote e congela o contrato; assinado não cancela (distrato na Fase 3); cancelar rascunho libera o lote para nova reserva.
- Reserva expirada não vira contrato; reserva não gera dois contratos — inclusive com duas transações simultâneas.
- Isolamento entre empresas, RLS nas 3 tabelas e RBAC (comercial gera, só owner/admin assinam, financeiro consulta).

## Implementado (política de contratos) — `tests/test_policy.py`
- Padrão do sistema sem configuração; salvar política (auditoria com antes/depois).
- Contrato usa os padrões da empresa (SAC, IPCA, juros) e calcula a 1ª parcela SAC; cliente pode escolher Price dentro da política.
- Recusa: juros acima do máximo, parcelas acima do máximo, entrada abaixo do mínimo, multa alterada sem permissão, sistema não permitido.
- Política inválida recusada; política é por empresa; só owner/admin alteram.
- `tests/test_finance.py`: 1ª parcela SAC.

## Implementado (Fase 2c) — `tests/test_fase2c.py`
- Leads: contato obrigatório; ligação move "novo" → "em contato"; perda exige motivo e reabertura; conversão cria comprador uma única vez (herda corretor) ou liga a existente; CPF duplicado recusado.
- Propostas: condições pela política (desconto calculado, parcela Price), limites recusados, validade (passado/máx. 90 dias), lote vendido/bloqueado recusado.
- Aceitar reserva o lote congelando o valor negociado, liga a reserva e é final; proposta concorrente de outro cliente recusada; reaproveita a reserva do próprio cliente uma única vez; proposta expirada não é aceita; histórico do lead registra proposta e aceite.
- Concorrência: duas aceitações simultâneas no mesmo lote — só uma vence.
- Escopo do corretor: precisa de vínculo ativo; vê/age só nos próprios leads, compradores, reservas e propostas (404 fora do escopo); atua sempre em nome próprio; sem contratos nem cadastro de corretores; corretor inativo perde o acesso.
- Isolamento (empresa B), RLS nas 3 tabelas, `lead_activities` append-only, RBAC (leitura sem acesso, financeiro só consulta propostas, comercial faz tudo).

## Implementado (Fase 3a) — `tests/test_fase3a.py` e `tests/test_finance.py`
- Cronograma: entrada + parcelas na assinatura; soma das amortizações = saldo financiado (Price, SAC, sem juros, à vista); rascunho sem parcelas; contrato antigo ganha cronograma na 1ª leitura (idempotente); meses curtos.
- Recebimento total e parcial; acima do devido, data futura e parcela quitada recusados.
- Atraso: multa 2% + mora 1% a.m. pro rata (30 dias → 177,70 + 88,85 sobre 8.884,88); parcial em atraso cobra encargos só da parte paga; cotação para data passada sem encargos.
- Estorno devolve o valor, só uma vez; recebido no mês exclui estornos; resumo (vencido, 30 dias, em aberto); filtros da lista.
- `payments` e `payment_reversals` append-only (UPDATE/DELETE negados pelo banco); dois recebimentos simultâneos da mesma parcela: só um passa.
- Isolamento (empresa B), RLS nas 3 tabelas, RBAC (leitura, comercial e corretor sem acesso; financeiro recebe e estorna).

## Implementado (Fase 3b) — `tests/test_fase3b.py`
- Regras: padrões do sistema, alteração só por quem tem `settings:manage`; repasse exige proprietário no empreendimento.
- Créditos sobre o recebido (comissão 4% e repasse 10% de entrada + parcial); multa/mora fora da base; base "assinatura" credita o total uma vez; corretor sem % usa o padrão; regras congeladas na assinatura; contrato sem corretor só gera repasse.
- Estorno de recebimento gera negativos; após comissão paga, o negativo abate o próximo acerto e saldo negativo não gera conta.
- Acerto gera conta a pagar e é idempotente; cancelar devolve os créditos; ciclo da conta manual (pagar, data futura recusada, estornar, cancelar), resumo e filtros.
- `participations` e `payable_events` protegidos no banco (UPDATE/DELETE negados; só `payable_id` editável); isolamento (empresa B), RLS nas 4 tabelas e RBAC.

## Planejado

- Unitários: cálculos de parcelas/arredondamento; comissões; estados e expiração de reservas; duplicidade de conciliação.
- Integração: migrations em banco limpo; contrato e geração de parcelas; reserva concorrente; webhook repetido; importação de extrato.
- Segurança: tenant A não acessa/edita tenant B; comprador não acessa contrato alheio; perfil sem permissão não baixa pagamento; DTO público sem dados privados.
- Operação: backup/restauração; readiness; logs sem segredos; falha do PSP sem corromper o estado financeiro.
