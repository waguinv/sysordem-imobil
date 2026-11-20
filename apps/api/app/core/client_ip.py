"""IP do visitante, repassado pelo web (BFF) com assinatura.

As rotas de login, senha, cadastro e contato só são acessadas pelo servidor do web (o nginx não expõe a API),
então a conexão que a API vê é sempre a do web. O web lê o `X-Real-IP` (gravado pelo nginx com o IP real) e
manda `X-Client-IP` + `X-Client-IP-Sig` = HMAC-SHA256(CLIENT_IP_SECRET, ip). Só um IP com assinatura válida é
usado; sem o segredo configurado, não há IP (e os limites por IP ficam desligados — se a API usasse o IP da
conexão, todos os visitantes cairiam no mesmo limite).

IPv6 vira o bloco /64: trocar de endereço dentro do mesmo bloco não escapa do limite.
"""

import hashlib
import hmac
import ipaddress

from fastapi import Request

from app.core.config import get_settings

HEADER_IP = "x-client-ip"
HEADER_SIG = "x-client-ip-sig"


def sign(ip: str, secret: str) -> str:
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()


def normalize(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def client_ip(request: Request) -> str | None:
    """Dependência FastAPI: o IP do visitante (ou bloco /64), ou None sem assinatura válida."""
    secret = get_settings().client_ip_secret
    if not secret:
        return None
    ip = request.headers.get(HEADER_IP)
    sig = request.headers.get(HEADER_SIG)
    if not ip or not sig or len(ip) > 64:
        return None
    if not hmac.compare_digest(sign(ip, secret), sig):
        return None
    return normalize(ip)
