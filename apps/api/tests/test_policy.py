"""Política de contratos por empresa: padrões, limites, Price × SAC, isolamento e permissões."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
DUE = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
URL = f"{API}/settings/contract-policy"

POLICY = {
    "default_interest_monthly_pct": "0.5",
    "max_interest_monthly_pct": "1.5",
    "default_amortization": "sac",
    "allowed_amortizations": ["price", "sac"],
    "default_adjustment_index": "IPCA",
    "late_fee_pct": "2.00",
    "late_interest_monthly_pct": "1.00",
    "max_installments": 180,
    "min_down_payment_pct": "10.00",
    "allow_override_late_charges": False,
    "reason": "política comercial 2026",
}


@pytest.fixture()
def reservation(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Política"}).json()
    blk = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "P"}).json()
    lot = client.post(
        f"{API}/developments/{dev['id']}/lots",
        headers=h,
        json={"block_id": blk["id"], "number": "1", "area_m2": "300", "price": "120000.00"},
    ).json()
    cust = client.post(f"{API}/customers", headers=h, json={"name": "Cliente Política"}).json()
    res = client.post(
        f"{API}/reservations", headers=h, json={"lot_id": lot["id"], "customer_id": cust["id"]}
    ).json()
    return {"h": h, "res": res}


def _contract(client, h, res_id, **over):
    body = {
        "reservation_id": res_id,
        "down_payment": "12000.00",
        "installments_count": 120,
        "first_due_date": DUE,
    }
    body.update(over)
    return client.post(f"{API}/contracts", headers=h, json=body)


def test_default_policy_when_not_configured(client):
    p = client.get(URL, headers=login(client, "alice")).json()
    assert p["is_default"] is True
    assert p["default_amortization"] == "price" and p["allowed_amortizations"] == ["price", "sac"]
    assert p["late_fee_pct"] == "2.00" and p["max_installments"] == 240


def test_owner_saves_policy_and_it_is_audited(client):
    h = login(client, "alice")
    r = client.put(URL, headers=h, json=POLICY)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["is_default"] is False and p["default_amortization"] == "sac" and p["max_installments"] == 180
    events = client.get(f"{API}/audit-events", headers=h).json()
    ev = next(e for e in events if e["action"] == "contract_policy.update")
    assert ev["reason"] == "política comercial 2026"
    assert ev["data"]["before"]["default_amortization"] == "price"
    assert ev["data"]["after"]["default_amortization"] == "sac"


def test_contract_uses_policy_defaults(client, reservation):
    h = reservation["h"]
    client.put(URL, headers=h, json=POLICY)
    c = _contract(client, h, reservation["res"]["id"]).json()
    t = c["terms"]
    assert t["amortization"] == "sac" and t["adjustment_index"] == "IPCA"
    assert t["interest_monthly_pct"] == "0.5000"
    # SAC: 108.000/120 = 900 de amortização + 0,5% de 108.000 = 540 → 1ª parcela 1.440
    assert t["installment_amount"] == "1440.00"


def test_customer_and_company_choose_price_within_policy(client, reservation):
    h = reservation["h"]
    client.put(URL, headers=h, json=POLICY)
    c = _contract(client, h, reservation["res"]["id"], amortization="price", interest_monthly_pct="0")
    assert c.status_code == 201, c.text
    assert (
        c.json()["terms"]["amortization"] == "price" and c.json()["terms"]["installment_amount"] == "900.00"
    )


@pytest.mark.parametrize(
    ("over", "msg"),
    [
        ({"interest_monthly_pct": "1.6"}, "Juros acima do máximo"),
        ({"installments_count": 200}, "parcelas acima do máximo"),
        ({"down_payment": "11999.99"}, "Entrada abaixo do mínimo"),
        ({"late_fee_pct": "5.00"}, "política da empresa"),
    ],
)
def test_contract_outside_policy_is_rejected(client, reservation, over, msg):
    h = reservation["h"]
    client.put(URL, headers=h, json=POLICY)
    r = _contract(client, h, reservation["res"]["id"], **over)
    assert r.status_code == 409 and msg in r.json()["detail"], r.text


def test_amortization_not_allowed(client, reservation):
    h = reservation["h"]
    client.put(
        URL, headers=h, json={**POLICY, "default_amortization": "price", "allowed_amortizations": ["price"]}
    )
    r = _contract(client, h, reservation["res"]["id"], amortization="sac")
    assert r.status_code == 409 and "amortização" in r.json()["detail"]


def test_override_of_late_charges_when_allowed(client, reservation):
    h = reservation["h"]
    client.put(URL, headers=h, json={**POLICY, "allow_override_late_charges": True})
    r = _contract(client, h, reservation["res"]["id"], late_fee_pct="1.50")
    assert r.status_code == 201 and r.json()["terms"]["late_fee_pct"] == "1.50"


def test_invalid_policy_rejected(client):
    h = login(client, "alice")
    r = client.put(
        URL, headers=h, json={**POLICY, "default_amortization": "sac", "allowed_amortizations": ["price"]}
    )
    assert r.status_code == 422
    r = client.put(
        URL, headers=h, json={**POLICY, "default_interest_monthly_pct": "2", "max_interest_monthly_pct": "1"}
    )
    assert r.status_code == 422


def test_policy_is_per_company_and_restricted(client):
    client.put(URL, headers=login(client, "alice"), json=POLICY)
    bob = login(client, "bob", "empresa-b")
    assert client.get(URL, headers=bob).json()["is_default"] is True  # B não vê a política de A
    for user in ("dave", "fabi"):  # comercial e financeiro consultam, mas não alteram
        h = login(client, user)
        assert client.get(URL, headers=h).status_code == 200
        assert client.put(URL, headers=h, json=POLICY).status_code == 403
    assert client.get(URL, headers=login(client, "carol")).status_code == 403
    assert client.put(URL, headers=login(client, "erin"), json=POLICY).status_code == 200  # admin
