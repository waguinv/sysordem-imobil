"""Dados FICTÍCIOS para desenvolvimento local. Nunca rode em produção.

Uso: MIGRATION_DATABASE_URL=... python -m scripts.seed_dev
Credenciais de desenvolvimento (fictícias) ficam documentadas em docs/DESENVOLVIMENTO.md.
"""

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Membership, Tenant, User

DEV_PASSWORD = "imobil-dev-123"  # fictícia, só para ambiente local

TENANTS = [("demo", "Loteadora Demonstração"), ("outra", "Outra Empresa Demo")]
USERS = [
    ("admin@demo.test", "Admin Demonstração", [("demo", "owner")]),
    ("leitura@demo.test", "Leitura Demonstração", [("demo", "leitura")]),
    ("corretor@demo.test", "Corretor Demonstração", [("demo", "corretor")]),
    ("admin@outra.test", "Admin Outra Empresa", [("outra", "owner")]),
]


def main() -> None:
    if os.environ.get("ENVIRONMENT") == "production":
        sys.exit("seed_dev recusado em produção")
    url = os.environ["MIGRATION_DATABASE_URL"]
    engine = create_engine(url)
    with Session(engine) as db, db.begin():
        tenants: dict[str, Tenant] = {}
        for slug, name in TENANTS:
            t = db.query(Tenant).filter_by(slug=slug).one_or_none() or Tenant(slug=slug, name=name)
            db.add(t)
            tenants[slug] = t
        db.flush()
        for email, full_name, links in USERS:
            u = db.query(User).filter_by(email=email).one_or_none()
            if u is None:
                u = User(email=email, full_name=full_name, password_hash=hash_password(DEV_PASSWORD))
                db.add(u)
                db.flush()
            for slug, role in links:
                tid = tenants[slug].id
                db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
                if not db.query(Membership).filter_by(tenant_id=tid, user_id=u.id).one_or_none():
                    db.add(Membership(tenant_id=tid, user_id=u.id, role=role))
                    db.flush()
    print("seed ok: empresas", ", ".join(s for s, _ in TENANTS))


if __name__ == "__main__":
    main()
