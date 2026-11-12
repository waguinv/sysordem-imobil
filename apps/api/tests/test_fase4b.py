"""Fase 4b: contas bancárias, importação de extratos, conciliação automática/manual, desfazer,
ignorar, estornos, isolamento, RBAC e append-only."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
D = Decimal


def _today():
    from app.core.clock import business_today

    return business_today()


def _br(d) -> str:
    return d.strftime("%d/%m/%Y")


def _csv(*rows: tuple) -> str:
    """rows: (data, descrição, valor-string)."""
    return "Data;Descrição;Valor\n" + "".join(f"{_br(d)};{desc};{v}\n" for d, desc, v in rows)


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Extrato"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "E"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 3, "area_m2": "300", "price": "120000.00"},
    ).json()
    customer = client.post(f"{API}/customers", headers=h, json={"name": "Correntista"}).json()

    def contract(lot_idx: int = 0):
        res = client.post(
            f"{API}/reservations",
            headers=h,
            json={"lot_id": lots[lot_idx]["id"], "customer_id": customer["id"]},
        ).json()
        c = client.post(
            f"{API}/contracts",
            headers=h,
            json={
                "reservation_id": res["id"],
                "down_payment": "20000.00",
                "installments_count": 12,
                "interest_monthly_pct": "1",
                "first_due_date": (_today() + timedelta(days=30)).isoformat(),
            },
        ).json()
        assert (
            client.post(
                f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1}
            ).status_code
            == 200
        )
        return client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()

    def pay(inst_id, amount, *, days_ago=0, method="pix"):
        r = client.post(
            f"{API}/installments/{inst_id}/payments",
            headers=h,
            json={
                "amount": amount,
                "method": method,
                "received_on": (_today() - timedelta(days=days_ago)).isoformat(),
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["payments"][0]

    def payable(amount, *, days_ago=0, method="transferencia"):
        p = client.post(
            f"{API}/payables",
            headers=h,
            json={
                "description": "Terraplanagem",
                "category": "obra",
                "payee_name": "Construtora X",
                "due_date": _today().isoformat(),
                "amount": amount,
            },
        ).json()
        r = client.post(
            f"{API}/payables/{p['id']}/pay",
            headers=h,
            json={"paid_on": (_today() - timedelta(days=days_ago)).isoformat(), "method": method},
        )
        assert r.status_code == 200, r.text
        return p

    acc = client.post(
        f"{API}/bank-accounts", headers=h, json={"name": "Banco Teste", "bank_name": "999"}
    ).json()
    return {"h": h, "contract": contract, "pay": pay, "payable": payable, "acc": acc}


def _import(client, h, acc_id, content, name="extrato.csv"):
    return client.post(
        f"{API}/bank-accounts/{acc_id}/statements", headers=h, json={"file_name": name, "content": content}
    )


def _lines(client, h, acc_id, **params):
    return client.get(f"{API}/statement-lines", headers=h, params={"account_id": acc_id, **params}).json()


def _by_desc(lines, desc):
    return next(ln for ln in lines if ln["description"] == desc)


# ─────────────────────────── contas ───────────────────────────


def test_bank_accounts(client, env):
    h = env["h"]
    assert client.post(f"{API}/bank-accounts", headers=h, json={"name": "Banco Teste"}).status_code == 409
    r = client.patch(f"{API}/bank-accounts/{env['acc']['id']}", headers=h, json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert _import(client, h, env["acc"]["id"], _csv((_today(), "x", "1,00"))).status_code == 409
    assert [a["name"] for a in client.get(f"{API}/bank-accounts", headers=h).json()] == ["Banco Teste"]


# ─────────────────────────── importação e automática ───────────────────────────


def test_import_auto_matches_unique_and_dedups(client, env):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    p0 = env["pay"](insts[0]["id"], "20000.00", days_ago=1)
    pb = env["payable"]("4500.00", days_ago=2)
    content = _csv(
        (_today(), "PIX MARIA", "20.000,00"),  # recebimento de ontem (1 dia de diferença)
        (_today(), "TED CONSTRUTORA", "-4.500,00"),  # pagamento da conta
        (_today(), "TARIFA", "-35,90"),
        (_today() - timedelta(days=10), "PIX SEM PAR", "777,00"),
    )
    r = _import(client, h, acc, content)
    assert r.status_code == 201, r.text
    st = r.json()
    assert (st["lines_total"], st["lines_new"], st["duplicates"], st["auto_matched"]) == (4, 4, 0, 2)
    assert st["format"] == "csv" and st["imported_by_name"] == "Alice"

    lines = _lines(client, h, acc)
    assert _by_desc(lines, "PIX MARIA")["status"] == "conciliada"
    assert _by_desc(lines, "TED CONSTRUTORA")["status"] == "conciliada"
    assert _by_desc(lines, "TARIFA")["status"] == "pendente"
    detail = client.get(f"{API}/statement-lines/{_by_desc(lines, 'PIX MARIA')['id']}", headers=h).json()
    assert [(m["kind"], m["target_id"], m["auto"]) for m in detail["matches"]] == [
        ("recebimento", p0["id"], True)
    ]
    assert (
        detail["targets"][0]["label"] == "Correntista" and detail["targets"][0]["link_id"] == insts[0]["id"]
    )
    ted = client.get(f"{API}/statement-lines/{_by_desc(lines, 'TED CONSTRUTORA')['id']}", headers=h).json()
    assert ted["targets"][0]["link_id"] == pb["id"]

    # mesmo arquivo de novo: recusado; arquivo com período sobreposto: só o novo entra
    assert "já foi importado" in _import(client, h, acc, content).json()["detail"]
    r2 = _import(client, h, acc, content + f"{_br(_today())};NOVO;10,00\n", "extrato2.csv").json()
    assert (r2["lines_total"], r2["lines_new"], r2["duplicates"]) == (5, 1, 4)
    assert len(_lines(client, h, acc)) == 5
    assert [
        s["file_name"] for s in client.get(f"{API}/bank-accounts/{acc}/statements", headers=h).json()
    ] == [
        "extrato2.csv",
        "extrato.csv",
    ]

    s = client.get(f"{API}/bank-accounts/{acc}/reconciliation-summary", headers=h).json()
    assert (s["pending_count"], s["reconciled_count"], s["ignored_count"]) == (3, 2, 0)
    assert D(s["pending_credits"]) == D("787.00") and D(s["pending_debits"]) == D("35.90")
    assert s["unmatched_payments_count"] == 0 and s["unmatched_payables_count"] == 0


def test_ofx_import(client, env):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    env["pay"](insts[0]["id"], "20000.00")
    d = _today().strftime("%Y%m%d")
    ofx = (
        "OFXHEADER:100\n<OFX><BANKTRANLIST>"
        f"<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>{d}<TRNAMT>20000.00<FITID>X1<MEMO>PIX</STMTTRN>"
        "</BANKTRANLIST></OFX>"
    )
    st = _import(client, h, acc, ofx, "extrato.ofx").json()
    assert st["format"] == "ofx" and st["auto_matched"] == 1
    bad = _import(client, h, acc, "Data;Descrição;Valor\n99/99/2026;x;1,00\n")
    assert bad.status_code == 422 and "Linha 2" in bad.json()["detail"]


def test_ambiguous_lines_stay_for_manual_with_candidates(client, env):
    h, acc = env["h"], env["acc"]["id"]
    a = env["contract"](0)
    b = env["contract"](1)
    pa = env["pay"](a[0]["id"], "20000.00")
    pb = env["pay"](b[0]["id"], "20000.00")
    st = _import(client, h, acc, _csv((_today(), "PIX 1", "20.000,00"))).json()
    assert st["auto_matched"] == 0  # dois recebimentos iguais: não adivinha
    line = _lines(client, h, acc)[0]
    cands = client.get(f"{API}/statement-lines/{line['id']}/candidates", headers=h).json()
    assert {c["id"] for c in cands if c["exact"]} == {pa["id"], pb["id"]}

    r = client.post(
        f"{API}/statement-lines/{line['id']}/reconcile", headers=h, json={"target_ids": [pb["id"]]}
    )
    assert r.status_code == 200 and r.json()["status"] == "conciliada"
    again = client.post(
        f"{API}/statement-lines/{line['id']}/reconcile", headers=h, json={"target_ids": [pa["id"]]}
    )
    assert again.status_code == 409 and "já conciliado" in again.json()["detail"]

    # o recebimento já usado não aparece para outra linha
    _import(client, h, acc, _csv((_today(), "PIX 2", "20.000,00")), "b.csv")
    other = _by_desc(_lines(client, h, acc), "PIX 2")
    assert other["status"] == "conciliada"  # sobrou um único candidato → automática
    assert (
        client.get(f"{API}/statement-lines/{other['id']}", headers=h).json()["matches"][0]["target_id"]
        == pa["id"]
    )


def test_manual_reconcile_groups_and_validates(client, env):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    p0 = env["pay"](insts[0]["id"], "20000.00")
    p1 = env["pay"](insts[1]["id"], insts[1]["amount"])
    pb = env["payable"]("300.00")
    total = D("20000.00") + D(insts[1]["amount"])
    _import(
        client,
        h,
        acc,
        _csv((_today(), "LOTE BOLETOS", f"{total}".replace(".", ",")), (_today(), "OUTRO", "-1,00")),
    )
    line = _by_desc(_lines(client, h, acc), "LOTE BOLETOS")
    debit = _by_desc(_lines(client, h, acc), "OUTRO")
    url = f"{API}/statement-lines/{line['id']}/reconcile"

    wrong = client.post(url, headers=h, json={"target_ids": [p0["id"]]})
    assert wrong.status_code == 409 and "difere" in wrong.json()["detail"]
    mixed = client.post(url, headers=h, json={"target_ids": [p0["id"], pb["id"]]})
    assert mixed.status_code == 409 and "só concilia com recebimentos" in mixed.json()["detail"]
    debit_with_payment = client.post(
        f"{API}/statement-lines/{debit['id']}/reconcile", headers=h, json={"target_ids": [p1["id"]]}
    )
    assert debit_with_payment.status_code == 409

    ok = client.post(url, headers=h, json={"target_ids": [p0["id"], p1["id"]]})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "conciliada" and body["matched_count"] == 2 and len(body["targets"]) == 2


def test_undo_ignore_and_unignore(client, env):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    p0 = env["pay"](insts[0]["id"], "20000.00")
    _import(client, h, acc, _csv((_today(), "PIX", "20.000,00"), (_today(), "TARIFA", "-9,90")))
    line = _by_desc(_lines(client, h, acc), "PIX")
    tarifa = _by_desc(_lines(client, h, acc), "TARIFA")

    dave = login(client, "dave")
    assert (
        client.post(
            f"{API}/statement-lines/{line['id']}/undo", headers=dave, json={"reason": "erro"}
        ).status_code
        == 403
    )
    fabi = login(client, "fabi")
    r = client.post(
        f"{API}/statement-lines/{line['id']}/undo", headers=fabi, json={"reason": "conciliei errado"}
    )
    assert r.status_code == 200 and r.json()["status"] == "pendente"
    m = r.json()["matches"][0]
    assert m["undone_at"] and m["undo_reason"] == "conciliei errado"  # histórico preservado
    assert (
        client.post(
            f"{API}/statement-lines/{line['id']}/undo", headers=fabi, json={"reason": "xxx"}
        ).status_code
        == 409
    )
    cands = client.get(f"{API}/statement-lines/{line['id']}/candidates", headers=h).json()
    assert [c["id"] for c in cands] == [p0["id"]]  # livre de novo

    ig = client.post(
        f"{API}/statement-lines/{tarifa['id']}/ignore", headers=h, json={"reason": "tarifa bancária"}
    )
    assert (
        ig.status_code == 200
        and ig.json()["status"] == "ignorada"
        and ig.json()["ignored_reason"] == "tarifa bancária"
    )
    assert (
        client.post(
            f"{API}/statement-lines/{tarifa['id']}/reconcile", headers=h, json={"target_ids": [p0["id"]]}
        ).status_code
        == 409
    )
    assert _lines(client, h, acc, status="ignorada")[0]["id"] == tarifa["id"]
    un = client.post(f"{API}/statement-lines/{tarifa['id']}/unignore", headers=h)
    assert un.status_code == 200 and un.json()["status"] == "pendente"

    client.post(f"{API}/bank-accounts/{acc}/auto-reconcile", headers=h)
    assert (
        client.post(
            f"{API}/statement-lines/{line['id']}/ignore", headers=h, json={"reason": "não"}
        ).status_code
        == 409
    )  # conciliada não se ignora
    actions = {a["action"] for a in client.get(f"{API}/audit-events", headers=h).json()}
    assert {
        "statement.import",
        "reconciliation.undo",
        "statement_line.ignore",
        "statement_line.unignore",
    } <= actions


def test_reversals_undo_reconciliation(client, env):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    p0 = env["pay"](insts[0]["id"], "20000.00")
    pb = env["payable"]("4500.00")
    _import(client, h, acc, _csv((_today(), "PIX", "20.000,00"), (_today(), "TED", "-4.500,00")))
    assert {ln["status"] for ln in _lines(client, h, acc)} == {"conciliada"}

    client.post(f"{API}/payments/{p0['id']}/reverse", headers=h, json={"reason": "pix devolvido"})
    client.post(f"{API}/payables/{pb['id']}/reverse", headers=h, json={"reason": "ted rejeitada"})
    lines = _lines(client, h, acc)
    assert {ln["status"] for ln in lines} == {"pendente"}
    d = client.get(f"{API}/statement-lines/{_by_desc(lines, 'PIX')['id']}", headers=h).json()
    assert "recebimento estornado" in d["matches"][0]["undo_reason"]
    # estornado não é mais candidato
    assert (
        client.get(f"{API}/statement-lines/{_by_desc(lines, 'PIX')['id']}/candidates", headers=h).json() == []
    )


def test_cash_is_not_reconcilable_and_summary_counts_unmatched(client, env):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    env["pay"](insts[0]["id"], "20000.00", method="dinheiro")
    env["pay"](insts[1]["id"], insts[1]["amount"])
    env["payable"]("100.00")
    _import(client, h, acc, _csv((_today(), "DEPOSITO", "20.000,00")))
    assert _lines(client, h, acc)[0]["status"] == "pendente"
    s = client.get(f"{API}/bank-accounts/{acc}/reconciliation-summary", headers=h).json()
    assert s["unmatched_payments_count"] == 1 and s["unmatched_payments_total"] == insts[1]["amount"]
    assert s["unmatched_payables_count"] == 1 and D(s["unmatched_payables_total"]) == D("100.00")


# ─────────────────────────── isolamento, RBAC e append-only ───────────────────────────


def test_tenant_isolation(client, env):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    p0 = env["pay"](insts[0]["id"], "20000.00")
    _import(client, h, acc, _csv((_today(), "PIX", "1,00")))
    line = _lines(client, h, acc)[0]

    hb = login(client, "bob", "empresa-b")
    assert client.get(f"{API}/bank-accounts", headers=hb).json() == []
    assert client.get(f"{API}/statement-lines", headers=hb, params={"account_id": acc}).status_code == 404
    assert client.get(f"{API}/statement-lines/{line['id']}", headers=hb).status_code == 404
    assert _import(client, hb, acc, _csv((_today(), "x", "1,00"))).status_code == 404
    acc_b = client.post(
        f"{API}/bank-accounts", headers=hb, json={"name": "Banco Teste"}
    ).json()  # mesmo nome: ok
    _import(client, hb, acc_b["id"], _csv((_today(), "PIX B", "20.000,00")))
    line_b = _lines(client, hb, acc_b["id"])[0]
    assert line_b["status"] == "pendente"  # não casa com recebimento da empresa A
    steal = client.post(
        f"{API}/statement-lines/{line_b['id']}/reconcile", headers=hb, json={"target_ids": [p0["id"]]}
    )
    assert steal.status_code == 409
    assert client.get(f"{API}/statement-lines/{line_b['id']}/candidates", headers=hb).json() == []


def test_rbac(client, env):
    acc = env["acc"]["id"]
    for user in ("dave", "carol", "gil"):
        hh = login(client, user)
        assert client.get(f"{API}/bank-accounts", headers=hh).status_code == 403
        assert _import(client, hh, acc, _csv((_today(), "x", "1,00"))).status_code == 403
    fabi = login(client, "fabi")
    assert _import(client, fabi, acc, _csv((_today(), "x", "1,00"))).status_code == 201
    assert client.post(f"{API}/bank-accounts", headers=fabi, json={"name": "Caixa"}).status_code == 201


def test_append_only(client, env, world, app_engine):
    h, acc = env["h"], env["acc"]["id"]
    insts = env["contract"]()
    env["pay"](insts[0]["id"], "20000.00")
    _import(client, h, acc, _csv((_today(), "PIX", "20.000,00")))
    for sql in (
        "DELETE FROM reconciliations",
        "UPDATE reconciliations SET amount = 1",
        "UPDATE statement_lines SET amount = 1",
        "DELETE FROM statement_lines",
        "UPDATE bank_statements SET file_sha256 = 'x'",
        "DELETE FROM bank_statements",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
            conn.execute(text(sql))
    with app_engine.begin() as conn:  # sem contexto de tenant: nada visível
        assert conn.execute(text("SELECT count(*) FROM statement_lines")).scalar() == 0
