"""Teste ponta a ponta contra o SANDBOX REAL do Asaas (nada de dinheiro de verdade).

    ASAAS_SANDBOX_API_KEY=... python -m scripts.asaas_smoke

A chave também pode estar no .env da raiz do repositório (linha ASAAS_SANDBOX_API_KEY=...). O script
recusa chave de produção. Faz: valida a chave → cobrança Pix (cliente de teste) → confirma o pagamento
pelo endpoint de simulação do sandbox → consulta (mesmo caminho do botão "Atualizar situação") →
boleto → cancela o boleto. Imprime só ids e situações, nunca a chave.
"""

import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.core.clock import business_today
from app.services.psp.asaas import AsaasClient, AsaasProvider
from app.services.psp.base import EVENT_CANCELLED, EVENT_PAID, ChargeRequest, ProviderError

ROOT = Path(__file__).resolve().parents[3]


def _key() -> str:
    key = os.environ.get("ASAAS_SANDBOX_API_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ASAAS_SANDBOX_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("defina ASAAS_SANDBOX_API_KEY (ambiente ou .env da raiz)")
    if "_prod_" in key:
        sys.exit("isto é uma chave de PRODUÇÃO — o teste só roda no sandbox")
    return key


def main() -> int:
    client = AsaasClient(_key(), "sandbox")
    provider = AsaasProvider(client, webhook_token_sha256=None)
    print("conta:", client.account_name() or "(sem nome)")
    run = uuid.uuid4().hex[:8]
    base = {
        "payer_name": "Comprador Teste SysOrdem",
        "payer_document": "24971563792",  # CPF de exemplo da documentação do Asaas
        "payer_reference": f"teste-{run}",
        "late_fee_pct": Decimal("2"),
        "late_interest_monthly_pct": Decimal("1"),
    }
    pix = provider.create_charge(
        ChargeRequest(
            reference=f"teste-{run}-pix",
            method="pix",
            amount=Decimal("12.34"),
            due_date=business_today() + timedelta(days=3),
            description=f"Teste SysOrdem Imobil {run} (Pix)",
            **base,  # type: ignore[arg-type]
        )
    )
    print("pix:", pix.external_id, "copia-e-cola", (pix.pix_code or "")[:20] + "…", pix.payment_url)
    assert pix.pix_code, "sem Pix copia e cola"
    before = provider.fetch_event(pix.external_id)
    print("antes de pagar:", before.type if before else "pendente (nada a aplicar)")
    assert before is None
    confirmed = client.sandbox_confirm(pix.external_id)
    print("simulação de pagamento:", confirmed.get("status"))
    ev = provider.fetch_event(pix.external_id)
    print("depois:", ev and (ev.type, ev.amount, ev.paid_on))
    assert ev is not None and ev.type == EVENT_PAID and ev.amount == Decimal("12.34")

    boleto = provider.create_charge(
        ChargeRequest(
            reference=f"teste-{run}-boleto",
            method="boleto",
            amount=Decimal("56.78"),
            due_date=business_today() + timedelta(days=5),
            description=f"Teste SysOrdem Imobil {run} (boleto)",
            **base,  # type: ignore[arg-type]
        )
    )
    print("boleto:", boleto.external_id, "linha", boleto.boleto_line)
    assert boleto.boleto_line and len(boleto.boleto_line) >= 44
    provider.cancel_charge(boleto.external_id)
    gone = provider.fetch_event(boleto.external_id)
    print("boleto cancelado:", gone and gone.type)
    assert gone is not None and gone.type == EVENT_CANCELLED
    try:
        provider.cancel_charge(pix.external_id)
        print("ATENÇÃO: o Asaas deixou cancelar uma cobrança já paga")
    except ProviderError as e:
        print("cancelar cobrança paga é recusado:", e)
    print("OK — sandbox do Asaas respondeu como o adaptador espera")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
