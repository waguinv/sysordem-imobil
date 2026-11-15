# Revisão de segurança, carga e restauração (Fase 6b)

> **Autorrevisão técnica** feita junto com o desenvolvimento. **Não substitui** o pentest e a revisão por
> profissionais independentes exigidos antes de produção (ver o fim deste documento).

## 1. Teste de restauração de backup
Rodado **a cada PR** no job `drill` do CI (`.github/workflows/ci.yml`), com a pilha real do Docker Compose:
1. sobe banco, migrations e API; `seed_dev`;
2. gera dados pela API com o teste de carga (abaixo);
3. `deploy/backup.sh` → dump `-Fc` cifrado (AES-256, chave em arquivo) + sha256;
4. confere que o arquivo no disco **não** é um dump legível e tem permissão `600`;
5. `deploy/restore.sh` num banco novo (`imobil_restore_ci`), conferindo o sha256;
6. `scripts/verify_restore.py` compara original × restaurado: revisão das migrations, **quantidade e hash md5 de
   todas as linhas de cada tabela**, RLS ligado/forçado e o texto das políticas; e, com o papel da aplicação,
   confirma que sem empresa no contexto nada aparece e com o contexto aparecem exatamente as linhas da empresa.

O verificador foi validado localmente nos dois sentidos: cópia idêntica → ok (43 tabelas, 1.804 linhas); com uma
linha alterada, `NO FORCE ROW LEVEL SECURITY` numa tabela e uma política removida → as três divergências
apontadas e saída 1.

**Em produção:** agendar `deploy/backup.sh` diariamente, copiar para fora do servidor (`BACKUP_RCLONE_REMOTE`),
guardar a chave em dois lugares (sem ela o backup não abre) e **restaurar mensalmente** num banco de teste com
`deploy/restore.sh` + `verify_restore.py`, registrando o resultado. Objetivos sugeridos (a confirmar com o cliente):
RPO 24 h (backup diário; menor exige WAL/PITR, ex.: banco gerenciado), RTO 2 h.

## 2. Carga e concorrência
`scripts/load_test.py` (só HTTP; nunca contra produção):
- **corrida**: 25 requisições simultâneas tentam reservar o mesmo lote, 5 lotes → exatamente **1** reserva por
  lote e 409 nas demais (proteção contra lote vendido duas vezes);
- **carga mista** com N usuários: mapa (30%), lote, reservas, compradores, empreendimentos e 10% de cadastros.

Resultados locais (15/11/2026 — notebook Windows, **1 processo** uvicorn, PostgreSQL embarcado na mesma máquina;
valores indicativos, não de produção):

| Usuários simultâneos | req/s | p50 | p95 | p99 | Erros | Corrida |
|---|---|---|---|---|---|---|
| 5 | 106 | 44 ms | 75 ms | 144 ms | 0% | ok (5/5) |
| 20 (pool antigo 5+10) | 63 | 187 ms | 966 ms | 1.487 ms | 0% | ok |
| 20 (pool 20+10) | 81 | 138 ms | 757 ms | 1.170 ms | 0% | ok |

Conclusões:
- O pool de conexões padrão (5+10) era menor que o número de threads atendendo requisições → filas. Agora
  `DB_POOL_SIZE=20`, `DB_MAX_OVERFLOW=10` (configuráveis). A soma de todos os processos precisa caber em
  `max_connections` do PostgreSQL (padrão 100).
- Um processo satura perto de ~100 req/s (CPU/GIL). Para mais carga: `WEB_CONCURRENCY=<n>` (workers do uvicorn) ou
  réplicas. **Atenção:** os limites em memória (login, cadastro, webhooks) passam a valer por processo — mover para
  Redis antes de escalar horizontalmente.
- Nenhum erro e nenhuma reserva duplicada sob concorrência.
- Refazer a medição no servidor de staging (hardware real) e definir as metas com o cliente (ex.: p95 < 500 ms para
  o volume esperado de usuários por empresa).

## 3. Autorrevisão — o que está coberto
| Área | Controle | Evidência |
|---|---|---|
| Isolamento entre empresas | tenant só do token revalidado no banco; RLS `FORCE` com falha fechada; FKs compostas; papel sem BYPASSRLS | testes de isolamento em todas as fases; `check_migrations.py` (RLS em toda tabela com `tenant_id`); `verify_restore.py` |
| Autenticação | Argon2id, JWT HS256 com algoritmo fixo, tempo constante para usuário inexistente, limite de tentativas | `test_auth*` |
| Autorização | permissões por rota, escopo do corretor, comprador só vê o próprio contrato | testes RBAC por fase |
| Webhooks | HMAC/segredo em tempo constante, janela anti-replay (PSP), deduplicação, confirmação no provedor (assinatura), corpo máximo, limite de taxa | `test_fase4a`, `test_fase5c`, `test_esign`, `test_fase6a` |
| Dinheiro | Decimal/NUMERIC, travas `FOR UPDATE`, índices únicos parciais, histórico imutável | testes financeiros; corrida de reservas |
| Web | cookie httpOnly/SameSite/Secure, BFF, CSP com nonce, cabeçalhos | `csp.test.ts`; navegador com build de produção |
| Configuração | travas de produção, docs/openapi desligados, segredos fora do Git (gitleaks) | `test_fase6a` |
| Dados pessoais em logs | log de acesso sem query string; `hide_parameters` do SQLAlchemy fora do desenvolvimento | `test_fase6a` |
| Cadeia de suprimentos | pip-audit, npm audit, Trivy nas imagens, versões fixas no web | CI |
| Operação | backup cifrado + restauração conferida, deploy com rollback automático, migrations reversíveis | CI `drill` e `api` |

## 4. Riscos em aberto (por prioridade)
**Alta — resolver antes de produção**
1. Limites de tentativa/cadastro/webhook **em memória** do processo: com vários workers/réplicas viram N× maiores.
   → Redis compartilhado.
2. Sessão: token de 60 min **continua válido após "sair"** (só a desativação do vínculo o derruba); sem MFA.
   → lista de revogação por `jti` (Redis), refresh token rotativo e MFA para owner/admin/financeiro.
3. Sem "esqueci a senha" e convite de usuário por e-mail (depende do provedor de e-mail — decisão 10).
4. Monitoramento e alertas inexistentes (5xx, latência, webhooks rejeitados, backup que falhou, disco).
   → ex.: Uptime Kuma/Prometheus + alertas; centralizar logs JSON.

**Média**
5. Segredo único do PSP sandbox; no PSP real (4c), segredo e credenciais por empresa em cofre.
6. Formulário do site sem limite por IP (o web ainda não repassa o IP do visitante à API).
7. LGPD: exportação/exclusão de dados do comprador a pedido, política de retenção (auditoria, backups, logs) e
   registro de operações — definir com o jurídico.
8. Chave dos backups gerida à mão; RPO de 24 h (sem PITR).

**Baixa**
9. CSP de estilos com `'unsafe-inline'` (atributos `style` do mapa).
10. `FORWARDED_ALLOW_IPS="*"` no deploy depende de a API não ter porta pública (garantido pelo compose de deploy).

## 5. Revisão profissional (pendente — fora do alcance do código)
Antes de colocar dados reais:
- **Pentest externo** (caixa cinza) no staging, com foco em: isolamento entre empresas (troca de ids em todas as
  rotas, inclusive portal e site público), escalonamento de perfil, webhooks forjados/repetidos, upload de extrato,
  sessão/cookies/CSRF nas Server Actions, CSP e cabeçalhos, enumeração de usuários e força bruta.
- **Revisão do código** por outra pessoa sênior (segurança e regras financeiras).
- **Jurídico/contábil**: contratos, juros/multa, distrato, assinatura eletrônica (avançada × ICP-Brasil), LGPD,
  termos de uso e política de privacidade (DECISOES_PENDENTES 5, 18, 40).
- **Infraestrutura**: hardening do servidor (SSH só com chave, firewall 80/443, atualizações automáticas),
  backups fora do provedor principal e teste de restauração assistido.
Registrar as conclusões e as correções neste documento.
