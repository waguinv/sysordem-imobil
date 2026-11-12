"""Leitura de extratos bancários: OFX (1.x SGML e 2.x XML) e CSV (padrão brasileiro).

Saída normalizada: data, valor com sinal (+ crédito, − débito, `Decimal`), descrição e um
identificador estável do lançamento (`fit_id`), usado para não duplicar linhas quando extratos com
períodos sobrepostos são importados.

- OFX: FITID do banco. Sem FITID, usa um hash (data, valor, descrição, ocorrência).
- CSV: colunas reconhecidas pelo cabeçalho (data; descrição/histórico; valor — ou crédito e débito;
  id/documento opcional). Aceita `;`, `,` ou tab, data dd/mm/aaaa ou aaaa-mm-dd, valor "1.234,56" ou
  "1234.56". Linhas de saldo ("SALDO ...") são puladas. Sem coluna de id, o hash inclui a ocorrência
  do lançamento no arquivo: dois lançamentos idênticos no mesmo dia continuam sendo dois, e reimportar
  o mesmo arquivo não duplica nada.
"""

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_CHARS = 2_000_000
MAX_LINES = 5000
CENT = Decimal("0.01")


class StatementParseError(Exception):
    """Arquivo inválido (mensagem pronta para o usuário)."""


@dataclass(frozen=True)
class ParsedLine:
    posted_on: date
    amount: Decimal
    description: str
    fit_id: str


@dataclass(frozen=True)
class ParsedStatement:
    format: str  # ofx | csv
    lines: list[ParsedLine]
    account_hint: str | None = None


def parse(content: str) -> ParsedStatement:
    if len(content) > MAX_CHARS:
        raise StatementParseError("Arquivo muito grande (máx. 2 MB)")
    head = content.lstrip("﻿").lstrip()[:2000].upper()
    st = _parse_ofx(content) if ("OFXHEADER" in head or "<OFX" in head) else _parse_csv(content)
    if not st.lines:
        raise StatementParseError("Nenhum lançamento encontrado no arquivo")
    if len(st.lines) > MAX_LINES:
        raise StatementParseError(f"Arquivo com mais de {MAX_LINES} lançamentos — divida por período")
    return st


# ─────────────────────────── utilitários ───────────────────────────


def parse_amount(raw: str) -> Decimal:
    s = raw.strip().replace("R$", "").replace(" ", "").replace(" ", "")
    neg = False
    if s.endswith("-") or (s.startswith("(") and s.endswith(")")):
        neg, s = True, s.strip("()-")
    if s.upper().endswith("D"):  # "123,45 D" (débito)
        neg, s = True, s[:-1]
    elif s.upper().endswith("C"):
        s = s[:-1]
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"valor inválido '{raw.strip()}'") from None
    if not v.is_finite() or v != v.quantize(CENT) or abs(v) >= Decimal("1e12"):
        raise ValueError(f"valor inválido '{raw.strip()}'")
    return -v if neg else v


def parse_date(raw: str) -> date:
    s = raw.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida '{s}'")


def _hash_id(prefix: str, d: date, amount: Decimal, desc: str, occurrence: int) -> str:
    h = hashlib.sha256(f"{d.isoformat()}|{amount}|{desc.lower()}|{occurrence}".encode()).hexdigest()
    return f"{prefix}:{h[:40]}"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


def _clean_desc(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()[:300] or "(sem descrição)"


# ─────────────────────────── OFX ───────────────────────────

_TRN = re.compile(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>|$)", re.I | re.S)


def _tag(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>\s*([^<\r\n]*)", block, re.I)
    return m.group(1).strip() if m and m.group(1).strip() else None


def _parse_ofx(content: str) -> ParsedStatement:
    lines: list[ParsedLine] = []
    seen: Counter[tuple[date, Decimal, str]] = Counter()
    for n, m in enumerate(_TRN.finditer(content), start=1):
        block = m.group(1)
        raw_date, raw_amount = _tag(block, "DTPOSTED"), _tag(block, "TRNAMT")
        if not raw_date or not raw_amount:
            raise StatementParseError(f"Lançamento {n}: sem data ou valor")
        try:
            d = datetime.strptime(raw_date[:8], "%Y%m%d").date()
            amount = parse_amount(raw_amount)
        except ValueError as e:
            raise StatementParseError(f"Lançamento {n}: {e}") from None
        if amount == 0:
            continue
        desc = _clean_desc(" ".join(x for x in (_tag(block, "NAME"), _tag(block, "MEMO")) if x) or "")
        fit = _tag(block, "FITID")
        key = (d, amount, desc)
        seen[key] += 1
        fit_id = f"ofx:{fit}"[:120] if fit else _hash_id("ofxh", d, amount, desc, seen[key])
        lines.append(ParsedLine(d, amount, desc, fit_id))
    return ParsedStatement("ofx", lines, _tag(content, "ACCTID"))


# ─────────────────────────── CSV ───────────────────────────

_COLS = {
    "date": {
        "data",
        "date",
        "dt",
        "data lancamento",
        "data do lancamento",
        "data movimento",
        "dt lancamento",
    },
    "desc": {"descricao", "historico", "description", "lancamento", "memo", "detalhe", "historico descricao"},
    "amount": {"valor", "amount", "valor rs", "valor r", "quantia"},
    "credit": {"credito", "entrada", "credit", "creditos"},
    "debit": {"debito", "saida", "debit", "debitos"},
    "id": {"id", "documento", "doc", "fitid", "identificador", "n documento", "numero documento"},
}


def _parse_csv(content: str) -> ParsedStatement:
    text = content.lstrip("﻿")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = max(";,\t", key=first.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header_idx: int | None = None
    cols: dict[str, int] = {}
    for i, row in enumerate(rows[:15]):  # bancos costumam pôr linhas de título antes do cabeçalho
        found: dict[str, int] = {}
        for j, raw in enumerate(row):
            name = _norm(raw)
            for col, names in _COLS.items():
                if name in names and col not in found:
                    found[col] = j
        if "date" in found and ("amount" in found or "credit" in found or "debit" in found):
            header_idx, cols = i, found
            break
    if header_idx is None:
        raise StatementParseError(
            "Formato não reconhecido: use OFX, ou CSV com cabeçalho Data; Descrição; Valor "
            "(ou Crédito e Débito)"
        )

    def cell(row: list[str], key: str) -> str:
        j = cols.get(key)
        return row[j].strip() if j is not None and j < len(row) else ""

    lines: list[ParsedLine] = []
    seen: Counter[tuple[date, Decimal, str]] = Counter()
    for i, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        if not any(c.strip() for c in row):
            continue
        desc = _clean_desc(cell(row, "desc"))
        if _norm(desc).startswith("saldo"):
            continue
        raw_date = cell(row, "date")
        if not raw_date:
            continue
        try:
            d = parse_date(raw_date)
            if "amount" in cols and cell(row, "amount"):
                amount = parse_amount(cell(row, "amount"))
            else:
                credit = cell(row, "credit")
                debit = cell(row, "debit")
                amount = (parse_amount(credit) if credit else Decimal(0)) - (
                    abs(parse_amount(debit)) if debit else Decimal(0)
                )
        except ValueError as e:
            raise StatementParseError(f"Linha {i}: {e}") from None
        if amount == 0:
            continue
        key = (d, amount, desc)
        seen[key] += 1
        ext = cell(row, "id")
        fit_id = (
            f"csv:{ext}|{d.isoformat()}|{amount}"[:120]
            if ext
            else _hash_id("csvh", d, amount, desc, seen[key])
        )
        lines.append(ParsedLine(d, amount, desc, fit_id))
    return ParsedStatement("csv", lines)
