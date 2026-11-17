"""Envio de e-mail da API (SMTP). Usado para redefinir senha e convidar membros.

- Mesmas variáveis SMTP_* do Documenso. Sem configuração, `enabled()` é falso e nada é enviado.
- 587 = STARTTLS (obrigatório); 465 = SSL direto. Nunca envia em texto puro.
- As rotas chamam pelo BackgroundTasks: o envio acontece depois da resposta (e depois do COMMIT), sem
  travar a tela; falha só vai para o log (sem o conteúdo, que tem link de uso único).
- Testes: `TRANSPORT_OVERRIDE` recebe a mensagem em vez do servidor SMTP.
"""

import logging
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.core.config import get_settings

log = logging.getLogger("imobil.email")

TRANSPORT_OVERRIDE: Callable[[EmailMessage], None] | None = None


def enabled() -> bool:
    return TRANSPORT_OVERRIDE is not None or get_settings().email_enabled


def build(to: str, subject: str, text: str) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = formataddr((s.smtp_from_name, s.smtp_from_address or "nao-responda@localhost"))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(s.smtp_from_address or "localhost").split("@")[-1])
    msg.set_content(text)
    return msg


def send(to: str, subject: str, text: str) -> None:
    """Envia agora. Chamar via BackgroundTasks nas rotas. Nunca levanta (só registra no log)."""
    msg = build(to, subject, text)
    if TRANSPORT_OVERRIDE is not None:
        TRANSPORT_OVERRIDE(msg)
        return
    s = get_settings()
    if not s.email_enabled or not s.smtp_host:
        log.warning("e-mail não configurado; mensagem não enviada (%s)", subject)
        return
    ctx = ssl.create_default_context()
    try:
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=ctx
            ) as smtp:
                _login_and_send(smtp, msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds) as smtp:
                smtp.starttls(context=ctx)
                _login_and_send(smtp, msg)
    except (OSError, smtplib.SMTPException) as e:
        log.error("falha ao enviar e-mail (%s): %s", subject, type(e).__name__)


def _login_and_send(smtp: smtplib.SMTP, msg: EmailMessage) -> None:
    s = get_settings()
    if s.smtp_username and s.smtp_password:
        smtp.login(s.smtp_username, s.smtp_password)
    smtp.send_message(msg)
