# Segurança: autenticação, multi-tenancy e RBAC (Fase 0)

## Autenticação
- `POST /api/v1/auth/login` com `tenant_slug`, `email`, `password`. Senhas com **Argon2id**. Usuário inexistente também executa uma verificação de hash (tempo de resposta não revela e-mails) e a resposta de erro é sempre a mesma.
- Token de acesso **JWT HS256** com `sub` (usuário), `tid` (tenant), `mid` (vínculo), `iss`, `iat`, `exp`, `jti`. Algoritmo fixo (rejeita `none`), segredo ≥ 32 caracteres vindo do ambiente.
- Limite de tentativas: 5 falhas por (empresa, e-mail) em 15 min → HTTP 429. **Limitação:** em memória do processo; com várias réplicas, mover para Redis.
- `POST /api/v1/auth/switch-tenant` troca de empresa só se houver vínculo ativo na empresa de destino.
- Web (Next.js) guarda o token em cookie **httpOnly**, `SameSite=Lax`, `Secure` em produção; o navegador nunca lê o token. Chamadas à API são feitas pelo servidor Next (BFF). Server Actions do Next verificam a origem (proteção CSRF).

## Derivação do tenant
O tenant **nunca** vem do navegador. A cada requisição (`app/auth/deps.py`):
1. valida assinatura/expiração do token;
2. define o contexto do tenant na transação (`set_config('app.tenant_id', tid, true)`);
3. carrega o vínculo `mid` exigindo `user_id = sub`, `tenant_id = tid` e `is_active`; usuário e empresa ativos.

Revogar um vínculo invalida tokens já emitidos na próxima requisição. Cabeçalhos/campos como `X-Tenant-Id` ou `tenant_id` no corpo são ignorados (há testes para isso).

## PostgreSQL Row-Level Security
- Tabelas com `tenant_id` têm `ENABLE` + `FORCE ROW LEVEL SECURITY` e a política `tenant_isolation`:
  `USING / WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)`.
- **Sem contexto, nenhuma linha** é visível (falha fechada).
- A aplicação conecta com um papel **sem SUPERUSER e sem BYPASSRLS** (`imobil_app`). Superusuários ignoram RLS mesmo com FORCE — por isso migrations usam outro papel (`imobil_owner`) e a API nunca deve usá-lo.
- **Contexto por transação:** o valor é definido com `is_local = true` em cada início de transação (evento `after_begin` da sessão SQLAlchemy). Ao fazer COMMIT/ROLLBACK ele some, então uma conexão devolvida ao pool não carrega o tenant de outra requisição. Não use `SET` (sem LOCAL) nem PgBouncer em modo *statement*; modo *transaction* é compatível.
- Tabelas globais sem RLS: `tenants` (apenas SELECT para o papel da aplicação) e `users` (um usuário pode pertencer a várias empresas; nunca expor listagem global).
- Toda nova tabela de negócio deve: ter `tenant_id NOT NULL`, receber a mesma política na migration e ganhar teste de isolamento.

## Sessão de banco e COMMIT
A dependência `get_db` é declarada com `Depends(get_db, scope="function")` (`DB_DEPENDS`). No FastAPI ≥ 0.118 o código após o `yield` roda por padrão **depois** de a resposta ser enviada: o cliente receberia 201 antes do COMMIT (leitura desatualizada logo em seguida e "sucesso" mesmo se o COMMIT falhar). `tests/test_db_dependency.py` impede regressão.

## Integridade entre tenants
Além do RLS, as FKs entre tabelas de negócio são compostas `(tenant_id, id)`, porque checagens de FK não passam pelo RLS. Rotas validam o pai (empreendimento, quadra, proprietário) com filtro de tenant e devolvem 404 quando ele é de outra empresa.

## Codificação do banco
O banco deve ser **UTF-8** (`/ready` responde 503 caso contrário): o driver decodifica JSONB como UTF-8 e um banco WIN1252 (padrão do `initdb` no Windows) quebra a leitura da auditoria com acentos. Testes e `tools/pg-local` criam bancos em UTF-8.

## RBAC
Perfis por vínculo: `owner`, `admin`, `financeiro`, `comercial`, `corretor`, `leitura`. A matriz fica em `app/auth/rbac.py`; rotas exigem **permissões** (`require(Perm.X)`), nunca perfis. Só `owner` cria/altera outro `owner`; ninguém altera o próprio vínculo.

**Escopo por corretor** (Fase 2c): além da permissão, o perfil `corretor` passa por um filtro de linha em `app/auth/scope.py` — só registros com o `broker_id` ligado ao seu usuário (`brokers.user_id`, corretor ativo). Fora do escopo a API responde 404 (não revela existência) e o corretor nunca escolhe outro corretor. Sem vínculo ativo, 403 com mensagem clara. O filtro é da aplicação (o RLS do banco continua isolando por empresa).

## Auditoria
`audit_events` registra ator, data/hora, entidade, ação, motivo e dados antes/depois. É **append-only**: o papel da aplicação só tem `SELECT, INSERT` (UPDATE/DELETE negados pelo banco — testado). Login, troca de empresa e alterações de vínculo são auditados.

## Webhooks de pagamento (Fase 4a)
- Rota pública `POST /api/v1/webhooks/payments/{provedor}/{empresa}` — sem token de usuário; a autenticação é a **assinatura do provedor**, verificada **antes** de ler o conteúdo ou tocar no banco.
- Sandbox: `X-Sandbox-Signature: t=<unix>,v1=<HMAC-SHA256(segredo, "<t>.<corpo>")>`, comparação em tempo constante, janela de `PAYMENTS_WEBHOOK_TOLERANCE_SECONDS` (padrão 300 s) contra replay, corpo máximo de 64 KB. Assinatura ausente/errada/antiga → 401; corpo malformado → 400.
- A empresa vem da URL (cada empresa cadastra a própria URL no PSP). Depois da assinatura, o contexto RLS é definido para ela e a cobrança é procurada **só dentro dela**: um evento válido com a URL de outra empresa não encontra nada (testado).
- **Deduplicação**: `webhook_events` com índice único (empresa, provedor, id do evento) — reenvio responde `duplicado` sem efeito. **Idempotência**: cobrança já paga ignora novos pagamentos. Entregas simultâneas: trava da cobrança (`FOR UPDATE`) + índice único (testado com duas transações em paralelo).
- Resposta 2xx sempre que o evento foi aceito (inclusive ignorado/duplicado/divergente) para o PSP não reenviar; erro inesperado → 5xx e rollback, e o PSP reenvia.
- Segredo: `PAYMENTS_SANDBOX_SECRET` (≥ 32 caracteres, fora do Git). Sem ele, o sandbox gera um segredo aleatório por processo (serve para o simulador; webhooks externos exigem o fixo). O sandbox fica **desligado em produção** (`PAYMENTS_SANDBOX_ENABLED=true` força).
- **PSP real (4c)**: credenciais por empresa em cofre/variáveis de ambiente (nunca em texto puro no banco), segredo de webhook por empresa e, se o PSP oferecer, mTLS/lista de IPs. Hoje o segredo do sandbox é único da plataforma.

## Pendências conhecidas (antes de produção)
- Refresh token/rotação e revogação por `jti`; MFA para perfis críticos.
- Rate limit compartilhado (Redis) e também por IP; proteção contra abuso no login.
- Convite de usuário por e-mail (hoje a senha inicial é definida por quem cria o vínculo) e redefinição de senha.
- Rate limit na rota pública de webhooks.
- Onboarding de empresas (hoje via seed/SQL administrativo).
- CSP com nonce no web, logs estruturados sem dados pessoais, monitoramento e alertas.
