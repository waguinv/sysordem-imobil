# Roteiro: ligar monitor, e-mail e cópia externa dos backups no servidor

Três recursos já estão no código em produção, mas só funcionam depois de configurados no servidor:

| Parte | O que liga | Tempo estimado |
|---|---|---|
| 1. Monitor | alerta (e-mail/WhatsApp) se a API, o site, o disco ou o backup tiverem problema, ou se o servidor cair | 15 min |
| 2. E-mail | "Esqueci minha senha" e convite de membros por e-mail | 20 min + DNS |
| 3. Cópia externa | backups copiados todo dia para fora do servidor, conferidos e protegidos contra apagamento | 25 min |

Faça na ordem: o monitor passa a vigiar o resto. Cada parte vale para **produção e staging**. Os comandos usam a
produção (`/opt/sysordem-imobil`); repita no staging (`/opt/sysordem-imobil-staging`) trocando o que estiver indicado.

**Regras para todo o roteiro**
- Segredos (tokens, senhas, chaves) só no `.env` do servidor e no seu gerenciador de senhas. Nunca no Git, no chat ou
  em print.
- Antes de editar o `.env`, faça uma cópia: `cp .env .env.bak-$(date +%Y%m%d)`.
- Valor com `$`, espaço ou `#` vai entre **aspas simples**: `SMTP_PASSWORD='a$b c'`.
- Para a API ler o `.env` novo, reimplante a mesma versão (não muda a versão nem perde dados):
  ```bash
  cd /opt/sysordem-imobil && deploy/deploy.sh "$(cat .deploy/current_tag)"
  ```
- Para desfazer qualquer parte: volte o `.env` da cópia (ou apague as linhas novas) e reimplante do mesmo jeito.

Entre no servidor como o usuário `deploy` (`ssh deploy@<servidor>`). Os detalhes de acesso ficam no
`HISTORICO_OPERACIONAL.md` particular, fora do Git.

---

## Parte 1: Monitor (healthchecks.io)

**No navegador**
1. Crie uma conta grátis em https://healthchecks.io.
2. Crie **dois checks**, `imobil-producao` e `imobil-staging`, cada um com *Period* **5 minutes** e *Grace* **10 minutes**.
3. Em *Integrations*, ligue o e-mail e, se quiser, WhatsApp, Telegram ou Slack.
4. Copie a **ping URL** de cada check (formato `https://hc-ping.com/<código>`).

**No servidor** (produção; repita no staging com a URL do check de staging)
```bash
cd /opt/sysordem-imobil
cp .env .env.bak-$(date +%Y%m%d)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # gera o OPS_TOKEN — copie a saída
nano .env
```
No `.env`, coloque (ou ajuste, se já houver as linhas):
```
OPS_TOKEN=<o valor gerado acima>
MONITOR_HEALTHCHECK_URL=https://hc-ping.com/<código do check deste ambiente>
```
Aplique e teste:
```bash
deploy/deploy.sh "$(cat .deploy/current_tag)"   # a API passa a conhecer o OPS_TOKEN
deploy/monitor.sh                               # deve imprimir "OK ..." e o check fica verde no painel
```
Se aparecer **PROBLEMA**, o relatório diz o quê. Os casos comuns:
- "nenhum backup": ainda não houve backup nessa pasta. Rode `deploy/backup.sh` uma vez.
- "OPS_TOKEN não confere": a API não foi reimplantada depois de editar o `.env`.

Agende a cada 5 minutos (`crontab -e`, como `deploy`) e mantenha a linha do backup que já existe:
```
*/5 * * * * cd /opt/sysordem-imobil && deploy/monitor.sh > /dev/null 2>&1
*/5 * * * * cd /opt/sysordem-imobil-staging && deploy/monitor.sh > /dev/null 2>&1
```
**Conferir:** depois de 10 minutos, os dois checks estão verdes no healthchecks.io. Para testar o alerta, pause o cron
(comente as linhas) e veja o aviso chegar depois da tolerância. Depois volte o cron.

---

## Parte 2: E-mail (SMTP)

**Escolha o provedor.** Se a SysOrdem já envia e-mail por algum serviço (por exemplo, o do SysOrdem Saúde), use o
mesmo. Senão, qualquer provedor com SMTP serve, como Brevo, Amazon SES, Zoho ou o e-mail do domínio. Prefira um
remetente do domínio da empresa, como `nao-responda@sysordemsistemas.com.br`.

**No provedor e no DNS**
1. Crie as credenciais SMTP: servidor, porta (587 com STARTTLS ou 465 com SSL), usuário e senha.
2. Cadastre e **verifique o domínio do remetente**. Isso significa criar no DNS os registros **SPF** e **DKIM** que o
   provedor indicar (e, se possível, **DMARC**). Sem eles, os e-mails caem no spam.

**No servidor** (produção; no staging use o endereço do staging)
```bash
cd /opt/sysordem-imobil
cp .env .env.bak-$(date +%Y%m%d)
nano .env
```
```
PUBLIC_WEB_URL=https://imobil.sysordemsistemas.com.br
SMTP_HOST=<servidor SMTP do provedor>
SMTP_PORT=587
SMTP_USERNAME=<usuário>
SMTP_PASSWORD='<senha>'
SMTP_FROM_ADDRESS=nao-responda@sysordemsistemas.com.br
SMTP_FROM_NAME=SysOrdem Imobil
```
No staging: `PUBLIC_WEB_URL=https://staging.imobil.sysordemsistemas.com.br`. Essas mesmas variáveis também servem ao
Documenso, se ele estiver ligado.
```bash
deploy/deploy.sh "$(cat .deploy/current_tag)"
```
**Conferir**
1. Abra `https://imobil.sysordemsistemas.com.br/esqueci-senha` e peça o link para o **seu** e-mail de usuário. A tela
   deve dizer "Se houver uma conta com este e-mail, enviamos um link…", e não mais "envio de e-mail não está ligado".
2. O e-mail chega (confira o spam). O link abre "Senha nova" e vale por 30 minutos.
3. Se não chegar, veja o motivo no log da API: `docker compose -f docker-compose.yml -f deploy/compose.deploy.yml logs
   --tail=50 api | grep -i e-mail`. Erros de login ou de TLS apontam usuário, senha ou porta.
4. Em Equipe e acessos, "Adicionar pessoa" passa a dizer que a pessoa recebe um e-mail para criar a senha.

---

## Parte 3: Cópia externa dos backups (Cloudflare R2)

**No navegador (Cloudflare)**
1. Crie uma conta grátis e ative o **R2**. O plano grátis cobre 10 GB, o que é suficiente para os dumps.
2. Crie um bucket **privado**: `sysordem-imobil-backups`.
3. Nas configurações do bucket:
   - em **Object lifecycle rules**, apague objetos com mais de **90 dias**;
   - em **Bucket lock rules**, crie uma retenção de **30 dias**: nada pode ser apagado antes disso, nem por quem tiver a
     credencial.
4. Em **R2 → Manage API tokens**, crie um token com permissão **Object Read & Write** só para esse bucket. Guarde o
   *Access Key ID*, a *Secret Access Key* e o endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. O segredo
   aparece uma única vez.

**No servidor** (uma vez, como `deploy`; o remoto `r2` serve aos dois ambientes)
```bash
sudo apt install -y rclone
# as chaves são digitadas sem aparecer na tela e não ficam no histórico do shell
read -rsp "Access Key ID: " R2_KEY; echo
read -rsp "Secret Access Key: " R2_SECRET; echo
rclone config create r2 s3 provider=Cloudflare region=auto acl=private no_check_bucket=true \
  endpoint=https://<ACCOUNT_ID>.r2.cloudflarestorage.com \
  access_key_id="$R2_KEY" secret_access_key="$R2_SECRET"
unset R2_KEY R2_SECRET
chmod 600 ~/.config/rclone/rclone.conf
rclone lsf r2:sysordem-imobil-backups   # deve responder sem erro (vazio)
```
No `.env` da produção:
```
BACKUP_RCLONE_REMOTE=r2:sysordem-imobil-backups/producao
```
No `.env` do staging, o mesmo terminando em `/staging`.
```bash
deploy/offsite_backup.sh   # envia os backups que já existem e confere — "N backup(s) conferido(s)"
deploy/monitor.sh          # o relatório deixa de reclamar da cópia externa
```
A partir daí, cada backup (o diário e o de antes de cada deploy) é enviado e conferido sozinho. Se a cópia falhar, o
backup local não é apagado e o monitor avisa.

**As chaves dos backups não vão para o bucket.** No seu computador, rode uma vez `deploy/pull_backups.sh`, que traz as
chaves `/etc/sysordem/backup-*.key` para `../imobil-backups/chaves/`, e guarde-as também no gerenciador de senhas. Sem
elas nenhum backup abre, nem o do R2.

**Conferir:** no painel do R2 aparecem os arquivos `imobil-*.dump.enc` e `.sha256` nas pastas `producao/` e `staging/`.
No dia seguinte ao das 03h15, o backup novo aparece lá também.

---

## Conferência final
- [ ] healthchecks.io: os dois checks verdes há pelo menos 15 min.
- [ ] Produção e staging: "Esqueci minha senha" entrega o e-mail e o link funciona.
- [ ] R2: arquivos de `producao/` e `staging/` presentes e `deploy/monitor.sh` sem reclamar da cópia externa.
- [ ] Chaves dos backups guardadas fora do servidor (gerenciador de senhas).
- [ ] `HISTORICO_OPERACIONAL.md` particular atualizado: contas criadas (healthchecks, SMTP, Cloudflare) e onde
  estão as credenciais, nunca os valores.

Referências: [CI_CD.md](CI_CD.md) (Backups, Monitoramento), [SEGURANCA.md](SEGURANCA.md) (senha e e-mail).
