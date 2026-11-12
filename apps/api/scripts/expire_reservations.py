"""Job: expira reservas vencidas de todas as empresas e libera os lotes.

Rodar periodicamente (ex.: a cada 5 min via cron/agendador) com o papel da aplicação:
  DATABASE_URL=... JWT_SECRET=... python -m scripts.expire_reservations
A expiração também acontece sob demanda (ao abrir mapa, lote e reservas); o job mantém
o estado correto mesmo sem acessos.
"""

from sqlalchemy import select

from app.core.db import get_sessionmaker, set_tenant
from app.models import Tenant
from app.services.reservations import expire_due


def main() -> None:
    sm = get_sessionmaker()
    with sm() as db:
        tenant_ids = list(db.scalars(select(Tenant.id).where(Tenant.is_active.is_(True))))
    total = 0
    for tid in tenant_ids:
        with sm() as db:
            set_tenant(db, tid)
            with db.begin():
                total += expire_due(db, tid)
    print(f"reservas expiradas: {total}")


if __name__ == "__main__":
    main()
