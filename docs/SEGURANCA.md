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
- Segredo: `PAYMENTS_SANDBOX_SECRET` (≥ 32 caracteres, fora do Git). Sem ele, o sandbox gera um segredo aleatório por processo (serve para o simulador; webhooks externos exigem o fixo). O sandbox fica **desligado em produção** (desde a Fase 6a, `PAYMENTS_SANDBOX_ENABLED=true` em produção impede a API de subir).
- **PSP real — Asaas** (ver PAGAMENTOS_ASAAS.md): a chave de API de cada empresa fica no banco **cifrada** com AES-256-GCM (`PAYMENTS_CREDENTIALS_KEY`, só no ambiente do servidor; o texto cifrado é amarrado à empresa e ao uso, então copiá-lo para outra empresa não decifra). Cada empresa tem um token de webhook próprio, gerado pelo sistema e guardado só como SHA-256. Como o Asaas autentica só por token (sem assinatura do corpo nem carimbo de tempo), todo evento de pagamento é **confirmado na API do Asaas** antes de qualquer efeito, valendo a situação e o valor de lá. A chave nunca volta para a tela, a auditoria ou os logs. A conta da plataforma (assinatura SaaS) só existe em variáveis de ambiente, e em produção só a conta de produção do Asaas é aceita.

## Site público (Fase 5a)
- Rotas `/api/v1/public/...` não exigem token. A empresa vem do `slug` da URL; o contexto RLS só é definido depois de confirmar empresa ativa **e** site publicado. Tudo o que sai passa por DTOs públicos de lista fechada.
- O formulário de contato grava lead com consentimento LGPD; tem campo-isca e limite por empresa/contato. Como o web chama a API pelo servidor (BFF), a API não vê o IP do visitante: o limite por IP fica para quando houver proxy confiável repassando `X-Forwarded-For` (ou Redis + limite no próprio web).

## Portal do comprador (Fase 5b)
- Identidade separada da equipe: `portal_accounts` (por empresa) e JWT com `aud=portal-comprador` + `cid`; o decodificador da equipe rejeita tokens com `aud`, e o do portal exige `aud` — testado nos dois sentidos. Cada requisição revalida empresa ativa, acesso ativo e ativado e o comprador.
- Convites: 256 bits aleatórios (`secrets.token_urlsafe(32)`), guardados só como sha256, uso único, expiração; o link aparece uma vez para quem gerou e nunca vai para a auditoria.
- Senha Argon2id (mín. 8 caracteres); login com limite de tentativas por empresa+e-mail e o mesmo tempo/resposta para conta inexistente.
- Todos os dados do portal saem por DTOs próprios com lista fechada de campos; consultas sempre filtradas pelo comprador do token (além do RLS por empresa).

## Assinatura e cadastro (Fase 5c)
- O cadastro público é a única rota que cria empresa: o papel da aplicação só pode inserir `(id, slug, name)` em `tenants` (sem UPDATE/DELETE, sem mexer em `is_active`) — testado. E-mail já cadastrado é recusado (nunca sobrescreve senha de conta existente). Campo-isca e limite por hora contra robôs (mover para Redis/captcha antes de produção).
- Faturas da assinatura só são quitadas por webhook com assinatura do PSP, deduplicado por evento (`billing_events`); redirect ou "simular" fora do sandbox não existem.
- Suspensão bloqueia escrita no servidor (402) para qualquer rota, não só na interface.

## Assinatura eletrônica
- Enviar para assinatura exige `contracts:sign` (a conclusão assina o contrato).
- Webhook do Documenso: segredo em tempo constante, deduplicação, empresa pela referência do envelope + RLS, e a
  conclusão/recusa é confirmada na API do provedor antes de qualquer efeito. Sem segredo configurado o provedor fica
  desabilitado. Simulador desligado em produção. Ver [ASSINATURA_ELETRONICA.md](ASSINATURA_ELETRONICA.md).

## Camada HTTP e configuração (Fase 6a)
- Middleware ASGI (`app/core/http.py`) em todas as respostas da API, inclusive erros: `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `CSP default-src 'none'` nas rotas da API,
  `Cache-Control: no-store` em `/api/*` e HSTS em produção.
- **Id de requisição** (`X-Request-ID`): aceita o recebido só se tiver 8–64 caracteres seguros (senão gera outro —
  evita injeção de cabeçalho/log) e devolve na resposta; vai em toda linha de log.
- **Limite de corpo** global (`MAX_BODY_BYTES`, 5 MB): por `Content-Length` e também contando o fluxo real (corpo
  chunked) → 413.
- **Limite de taxa nos webhooks** (`WEBHOOK_RATE_PER_MINUTE`, 600/min por IP) → 429 com `Retry-After`. As demais
  rotas chegam pelo BFF (mesmo IP) e têm limites próprios por empresa/e-mail. Em memória do processo.
- **Logs estruturados** (JSON em staging/produção): método, caminho (sem query string), status, duração e id da
  requisição. Sem corpo, tokens ou dados pessoais; o log de acesso do uvicorn fica desligado.
- Banco fora do ar falha em ~5 s (`connect_timeout`) — `/ready` responde 503 em vez de pendurar.
- **Travas de produção** (`ENVIRONMENT=production`): a API não sobe se `DATABASE_URL` usar senha de exemplo ou o
  mesmo papel das migrations, se `CORS_ORIGINS` não for https, se algum simulador (PSP/assinatura/cobrança) for
  ligado ou se `JWT_SECRET` tiver menos de 48 caracteres.
- Web: **CSP com nonce por requisição** (`src/proxy.ts`): scripts só do próprio Next (`'strict-dynamic'`), sem
  `unsafe-eval` em produção, `frame-ancestors 'none'`, `form-action 'self'`; estilos com `'unsafe-inline'` (atributos
  `style` do mapa). Todas as páginas passam a ser dinâmicas (o nonce exige).
- Containers da API e do web: usuário sem privilégio, `no-new-privileges`, `cap_drop: ALL`, healthcheck.
- CI: pip-audit, npm audit, gitleaks, Trivy e shellcheck — ver [CI_CD.md](CI_CD.md).

Revisão consolidada, riscos em aberto e o que falta da revisão profissional: [REVISAO_SEGURANCA.md](REVISAO_SEGURANCA.md).

## Verificação em duas etapas (TOTP)
- Opcional por usuário e **recomendada** (aviso no topo e destaque na tela) para proprietário, administrador e
  financeiro. Em Equipe e acessos aparece quem entra "só com senha". Vale em todas as empresas do usuário.
- Aplicativo autenticador padrão (RFC 6238: HMAC-SHA1, 30 s, 6 dígitos, tolerância de ±1 intervalo). O cálculo é feito
  só com a biblioteca padrão (`app/core/totp.py`, testado com os vetores da RFC).
- **Segredo cifrado** no banco (`user_mfa`) com o cofre de credenciais (AES-256-GCM, chave `PAYMENTS_CREDENTIALS_KEY`,
  finalidade "mfa-totp" amarrada ao id do usuário). Sem a chave no servidor, a ativação fica indisponível.
- Login em duas etapas: com MFA ligado, a senha certa devolve só um **desafio** (JWT de 5 min com `aud` próprio e sem
  `mid`, que não serve como token de acesso). O token de acesso só sai em `POST /auth/mfa/login` com o código.
  O mesmo código não vale duas vezes (`last_step`, com trava da linha). Há limite de 5 erros em 15 min por usuário,
  separado do limite da senha.
- **10 códigos de recuperação** de uso único, guardados só como hash Argon2id e mostrados uma vez. Gerar novos invalida
  os anteriores.
- Ativar exige a senha atual, para que uma sessão roubada não ative um MFA do atacante e tranque o dono fora. Desligar
  e gerar novos códigos exigem senha **e** código. Tudo auditado: `auth.mfa_enable`, `auth.mfa_disable`,
  `auth.mfa_recovery_codes` e `auth.login` com o método usado.
- Não existe "desligar o MFA de outro membro": como o MFA vale em todas as empresas do usuário, o administrador de uma
  empresa poderia tirar a proteção do dono de outra. Quem perder o celular **e** os códigos precisa do suporte
  (decisão 42).

## Senha: troca, redefinição por e-mail e convite
- **Trocar** (logado, em Segurança da conta): exige a senha atual e devolve uma sessão nova.
- **Redefinir** ("Esqueci minha senha"): a resposta é sempre a mesma, exista ou não a conta, para não revelar
  e-mails cadastrados. O link vale **30 min** e uma vez. Há limite de 3 pedidos por e-mail e 100 no total por hora
  (em memória do processo).
- **Convite**: ao adicionar uma pessoa nova sem senha inicial, ela recebe um link para criar a própria senha (vale
  **7 dias** e uma vez). Quem já usa o sistema em outra empresa só recebe o aviso do novo acesso. Sem e-mail
  configurado, a senha inicial continua obrigatória.
- Links: token aleatório de 256 bits. No banco (`password_tokens`) só fica o **SHA-256** do token. Pedir outro link
  invalida o anterior do mesmo tipo. O papel da aplicação não apaga nem altera os tokens, só marca o uso.
- **Trocar ou redefinir a senha encerra as sessões abertas.** Cada token carrega a versão da senha (`pwv`, o instante
  da última troca em microssegundos), e o token só vale se a versão bater: comparação exata, sem depender do horário.
  Vale também para o desafio pendente do MFA. A verificação em duas etapas **continua valendo** depois da redefinição.
- Regras da senha nova: pelo menos 12 caracteres, diferente do e-mail, recusa senhas óbvias e repetições.
- E-mail: SMTP com STARTTLS (587) ou SSL (465), enviado **depois** da resposta (BackgroundTasks). Uma falha vai só para
  o log, sem o conteúdo. Configuração: `SMTP_*` (as mesmas do Documenso) e `PUBLIC_WEB_URL` (endereço do sistema,
  para os links). Sem elas, o envio fica desligado e as telas avisam.

## Pendências conhecidas (antes de produção)
- Refresh token/rotação e revogação por `jti`. MFA: feito (opcional; tornar obrigatório é a decisão 43).
- Rate limit compartilhado (Redis) e também por IP; proteção contra abuso no login.
- Convite e redefinição de senha por e-mail: feitos (ver acima). Falta configurar SMTP e `PUBLIC_WEB_URL` em produção.
- Limite por IP no formulário do site (exige proxy confiável repassando o IP do visitante ao web).
- Onboarding de empresas (hoje via seed/SQL administrativo).
- Monitoramento e alertas: feito (`/ops/status` + `deploy/monitor.sh` + healthchecks.io, ver CI_CD.md). Falta configurar no servidor e, depois, métricas de negócio (avisos do Asaas parados, cobranças divergentes).
