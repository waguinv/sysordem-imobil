"""Data de negócio: "hoje" no fuso da empresa (padrão America/Sao_Paulo), não em UTC.

Vencimentos, validade de propostas e dias de atraso são datas civis; usar UTC faria o sistema
virar o dia às 21h no Brasil e contar um dia de atraso a mais.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def business_today() -> date:
    return datetime.now(ZoneInfo(get_settings().business_timezone)).date()
