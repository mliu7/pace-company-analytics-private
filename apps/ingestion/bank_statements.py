"""Bank statement import for the Bank Reconciliation page (docs/06_finance_reports.md).

The monthly BMO Bank N.A. corporate-checking PDF is the one input in this app that is neither PTT
nor SL. It is parsed locally with pdfplumber — positionally: on the transaction pages the
WITHDRAWALS column sits left of the DEPOSITS column, so a line's side is decided by where its
amount ends — and stored in finance.BankStatement / BankStatementLine. A statement is accepted only
if the bank's own summary agrees with what was parsed (deposit and withdrawal counts and totals,
and previous + deposits − withdrawals == ending balance). Nothing here reads or writes SL or PTT.

Layout facts (verified on the 7/2026 statement):
- header block repeats on every page; "STATEMENT PERIOD" is followed by "MM/DD/YY TO MM/DD/YY".
- transaction lines start "MON DD" (posting date); the amount is the last money token on the line.
- the check list follows "THE FOLLOWING CHECKS ARE INCLUDED IN THIS STATEMENT" as triplets of
  number / amount / MM/DD, three per row; an asterisk after the number marks a gap in sequence.
- "Reve Check# NNNNN" credits are checks the bank paid and then reversed.
"""

import hashlib
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction

MONTHS = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
AMOUNT_RE = re.compile(r"-?[\d,]*\.\d{2}")
TX_RE = re.compile(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC) (\d{2}) (.*)$")
CHECK_TOKEN_RE = re.compile(r"\d{4,8}\*?")
DATE_TOKEN_RE = re.compile(r"\d{2}/\d{2}")
DEFAULT_DEPOSIT_X = 520.0  # fallback column split (points) if the WITHDRAWALS / DEPOSITS header is not found

# bank account number / statement title -> SL cash GL account
from apps.core.business_config import get as business_config
TITLE_GL = (("OPERATING", "10250"), ("PAYROLL", "10400"), ("MONEY MARKET", "10350"), ("MMA", "10350"))


def bucket_for(kind, desc):
    """Coarse category used by the page filters and the payroll rule in the reconciliation."""
    d = (desc or "").upper()
    if kind == "check":
        return "check"
    if kind == "check_reversal" or "REVE CHECK#" in d:
        return "check_reversal"
    if any(p in d for p in ("PAYLOCITY", "92358 PACE SYSTE")):
        return "payroll"
    if kind == "wd":
        if "BALANCE TRANSFER TO" in d or "LOAN TRANS" in d or "PAYDOWN" in d:
            return "sweep"
        if "WIRE" in d:
            return "wire"
        if "ACH OFFSET" in d:
            return "ach_batch"
        if "ANALYSIS" in d or "SERV CHG" in d or "FEE" in d:
            return "fee"
        return "ach"
    if "TRANSFER FROM" in d:
        return "loan_draw"
    if "REMOTE DEPOSIT" in d or "CHECK DEPOSIT" in d:
        return "deposit"
    if "WIRE" in d:
        return "wire_in"
    return "ach_in"


def _money(tok):
    return float(tok.replace(",", ""))


def page_lines(page):
    """pdfplumber page -> list of {'text', 'words': [{'text', 'x0', 'x1'}]} in reading order."""
    rows = {}
    for w in page.extract_words():
        rows.setdefault(round(w["top"] / 2), []).append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
    out = []
    for key in sorted(rows):
        ws = sorted(rows[key], key=lambda w: w["x0"])
        out.append({"text": " ".join(w["text"] for w in ws), "words": ws})
    return out


def parse_statement_lines(lines):
    """Pure parser over positional lines (see page_lines). Returns a dict with header, summary,
    tx (deposits / withdrawals), checks, and warnings. Raises ValueError if no period is found."""
    header = {"account": "", "title": "", "bank": ""}
    summary = {}
    tx, checks, warnings = [], [], []
    in_checks = done_tx = False
    deposit_x = DEFAULT_DEPOSIT_X
    period_start = period_end = None
    prev_text = ""
    for ln in lines:
        text, ws = ln["text"], ln["words"]
        up = text.upper()
        if not header["bank"] and re.search(r"\bBANK\b", up) and len(text) < 40:
            header["bank"] = text.strip()
        m = re.search(r"ACCOUNT NUMBER:?\s+([\d-]+)", up)
        if m and not header["account"]:
            header["account"] = m.group(1)
        m = re.search(r"(\d{2}/\d{2}/\d{2,4})\s+TO\s+(\d{2}/\d{2}/\d{2,4})", up)
        if m and period_start is None:
            period_start, period_end = _mdY(m.group(1)), _mdY(m.group(2))
        if "STATEMENT PERIOD" not in up and not header["title"] and prev_text.strip().upper().startswith("PACE SYSTEMS") and "ACCOUNT" in up:
            header["title"] = text.strip()
        if "WITHDRAWALS" in up and "DEPOSITS" in up and "YOUR" not in up:
            wx = [w for w in ws if w["text"].upper().startswith("WITHDRAWALS")]
            dx = [w for w in ws if w["text"].upper().startswith("DEPOSITS")]
            if wx and dx:
                deposit_x = (wx[0]["x1"] + dx[0]["x0"]) / 2
        for lab, key in (("YOUR PREVIOUS BALANCE WAS", "previous_balance"), ("YOUR ENDING BALANCE WAS", "ending_balance")):
            if lab in up:
                amts = AMOUNT_RE.findall(text)
                if amts:
                    summary[key] = _money(amts[-1])
        m = re.match(r"\s*(\d+)\s+(DEPOSITS|WITHDRAWALS)\s+([\d,]*\.\d{2})", up)
        if m:
            summary[m.group(2).lower() + "_count"] = int(m.group(1))
            summary[m.group(2).lower() + "_total"] = _money(m.group(3))
        if "FOLLOWING CHECKS" in up:
            in_checks = done_tx = True
            prev_text = text
            continue
        if up.strip().startswith("SUBTOTAL") or "CLOSING DAILY" in up or "TRANSACTION SUMMARY" in up:
            in_checks = False
            done_tx = True
        m = TX_RE.match(text.strip())
        if m and not done_tx and not in_checks:
            amts = [w for w in ws if AMOUNT_RE.fullmatch(w["text"])]
            if amts:
                a = amts[-1]
                amt = _money(a["text"])
                desc = " ".join(w["text"] for w in ws[2:] if w is not a).strip()
                kind = "dep" if a["x1"] > deposit_x else "wd"
                # zero-amount notices (ACH prenotes) are counted by the bank as items; keep them so counts agree
                tx.append({"mon": m.group(1), "day": int(m.group(2)), "desc": desc, "amt": amt, "kind": kind})
        elif in_checks:
            toks = [w["text"] for w in ws]
            i = 0
            while i <= len(toks) - 3:
                if CHECK_TOKEN_RE.fullmatch(toks[i]) and AMOUNT_RE.fullmatch(toks[i + 1]) and DATE_TOKEN_RE.fullmatch(toks[i + 2]):
                    checks.append({"num": toks[i].rstrip("*"), "gap": toks[i].endswith("*"), "amt": _money(toks[i + 1]), "md": toks[i + 2]})
                    i += 3
                else:
                    i += 1
        prev_text = text
    if not period_end:
        raise ValueError("statement period not found — is this a BMO checking statement?")
    # resolve dates to the statement year (a December statement can list Jan-dated items and vice versa)
    def resolve(month, day):
        y = period_end.year
        if month == 12 and period_end.month == 1:
            y -= 1
        elif month == 1 and period_end.month == 12:
            y += 1
        return date(y, month, day)
    for t in tx:
        t["date"] = resolve(MONTHS[t["mon"]], t["day"])
        if "REVE CHECK#" in t["desc"].upper():
            t["kind"] = "check_reversal"
            t["check_number"] = re.search(r"CHECK#\s*(\d+)", t["desc"].upper()).group(1)
        t["bucket"] = "notice" if t["amt"] == 0 else bucket_for(t["kind"], t["desc"])
    for c in checks:
        mm, dd = c["md"].split("/")
        c["date"] = resolve(int(mm), int(dd))
    gl = business_config("bank_account_gl", {}).get(header["account"])
    if not gl:
        for word, acct in TITLE_GL:
            if word in header["title"].upper():
                gl = acct
                break
    if not gl:
        gl = "10250"
        warnings.append("account %s / '%s' not in the bank->GL map; assumed 10250" % (header["account"], header["title"]))
    deps = [t for t in tx if t["kind"] in ("dep", "check_reversal")]
    wds = [t for t in tx if t["kind"] == "wd"]
    parsed = {
        "header": header, "gl_account": gl, "period_start": period_start, "period_end": period_end, "summary": summary,
        "tx": tx, "checks": checks, "warnings": warnings,
        "totals": {"dep_n": len(deps), "dep_total": round(sum(t["amt"] for t in deps), 2),
                   "wd_n": len(wds) + len(checks), "wd_total": round(sum(t["amt"] for t in wds) + sum(c["amt"] for c in checks), 2),
                   "chk_n": len(checks), "chk_total": round(sum(c["amt"] for c in checks), 2)},
    }
    parsed["integrity"] = integrity_check(parsed)
    return parsed


def integrity_check(parsed):
    """Compare the parse with the bank's own summary block. Returns list of problems ([] = clean)."""
    s, t = parsed["summary"], parsed["totals"]
    probs = []
    if "previous_balance" not in s or "ending_balance" not in s:
        probs.append("summary balances not found")
        return probs
    recomputed = round(s["previous_balance"] + t["dep_total"] - t["wd_total"], 2)
    if abs(recomputed - s["ending_balance"]) > 0.005:
        probs.append("previous + deposits - withdrawals = %.2f but the bank says ending %.2f" % (recomputed, s["ending_balance"]))
    if "deposits_count" in s and s["deposits_count"] != t["dep_n"]:
        probs.append("bank lists %d deposits, parsed %d" % (s["deposits_count"], t["dep_n"]))
    if "withdrawals_count" in s and s["withdrawals_count"] != t["wd_n"]:
        probs.append("bank lists %d withdrawals, parsed %d" % (s["withdrawals_count"], t["wd_n"]))
    if "deposits_total" in s and abs(s["deposits_total"] - t["dep_total"]) > 0.005:
        probs.append("deposit total %.2f vs bank %.2f" % (t["dep_total"], s["deposits_total"]))
    if "withdrawals_total" in s and abs(s["withdrawals_total"] - t["wd_total"]) > 0.005:
        probs.append("withdrawal total %.2f vs bank %.2f" % (t["wd_total"], s["withdrawals_total"]))
    return probs


def _mdY(s):
    m, d, y = s.split("/")
    y = int(y)
    if y < 100:
        y += 2000
    return date(y, int(m), int(d))


def parse_pdf(path):
    import pdfplumber
    lines = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            lines.extend(page_lines(page))
    return parse_statement_lines(lines)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_statement(path, force=False):
    """Parse one PDF into BankStatement + lines. Idempotent: an unchanged file (same sha256) is a
    no-op unless force (re-parse after a parser change); a changed file for the same account/period
    replaces the earlier import (and its reconciliation). Returns (statement, status) with status in
    new | updated | unchanged | error."""
    from apps.finance.models import BankStatement, BankStatementLine, BankUpload
    path = Path(path)
    sha = sha256_of(path)
    existing = BankStatement.objects.filter(file_sha256=sha).first()
    if existing and not force:
        return existing, "unchanged"
    try:
        parsed = parse_pdf(path)
    except Exception as e:  # noqa
        return None, "error: %s" % e
    s = parsed["summary"]
    notes = list(parsed["warnings"]) + parsed["integrity"]
    with transaction.atomic():
        # Upload requests can overlap the scheduled parser. Serialize by account
        # and period even when no statement row exists yet (a row lock alone
        # cannot lock a missing row). This lock only touches the app database.
        identity = f"bank:{parsed['gl_account']}:{parsed['period_end']}".encode()
        lock_key = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])
        existing = BankStatement.objects.filter(file_sha256=sha).first()
        if existing and not force:
            return existing, "unchanged"
        prior = BankStatement.objects.filter(gl_account=parsed["gl_account"], period_end=parsed["period_end"]).first()
        status = "updated" if prior else "new"
        if prior:
            prior.delete()
        stmt = BankStatement.objects.create(
            gl_account=parsed["gl_account"], bank_account=parsed["header"]["account"], account_title=parsed["header"]["title"],
            bank_name=parsed["header"]["bank"], period_start=parsed["period_start"], period_end=parsed["period_end"],
            file_name=path.name, file_sha256=sha,
            previous_balance=Decimal(str(s.get("previous_balance", 0))), ending_balance=Decimal(str(s.get("ending_balance", 0))),
            deposits_count=parsed["totals"]["dep_n"], deposits_total=Decimal(str(parsed["totals"]["dep_total"])),
            withdrawals_count=parsed["totals"]["wd_n"], withdrawals_total=Decimal(str(parsed["totals"]["wd_total"])),
            checks_count=parsed["totals"]["chk_n"], checks_total=Decimal(str(parsed["totals"]["chk_total"])),
            parse_ok=not parsed["integrity"], parse_notes=notes)
        rows, n = [], 0
        for t in sorted(parsed["tx"], key=lambda t: t["date"]):
            n += 1
            rows.append(BankStatementLine(statement=stmt, line_no=n, posted_date=t["date"], kind=t["kind"], bucket=t["bucket"],
                                          description=t["desc"][:160], amount=Decimal(str(t["amt"])), check_number=t.get("check_number", "")))
        for c in sorted(parsed["checks"], key=lambda c: (c["date"], c["num"])):
            n += 1
            rows.append(BankStatementLine(statement=stmt, line_no=n, posted_date=c["date"], kind="check", bucket="check",
                                          description="CHECK %s" % c["num"], amount=Decimal(str(c["amt"])), check_number=c["num"]))
        BankStatementLine.objects.bulk_create(rows, batch_size=500)
        # Re-parsing replaces the derived statement row. Reattach its immutable
        # original PDF so a nightly parser update cannot break the View PDF link.
        upload = BankUpload.objects.filter(sha256=sha).first()
        if upload:
            BankUpload.objects.filter(pk=upload.pk).update(statement=stmt, parse_error="")
            stmt.file_name = upload.original_name
            stmt.save(update_fields=["file_name"])
    return stmt, status


def scan_folder(folder=None, force=False):
    """Import every *.pdf in the statements folder (default settings.BANK_STATEMENTS_DIR).
    Returns [(path, status, statement)]."""
    folder = Path(folder or settings.BANK_STATEMENTS_DIR)
    out = []
    if not folder.exists():
        return out
    for path in sorted(folder.glob("*.pdf")):
        stmt, status = import_statement(path, force=force)
        out.append((path, status, stmt))
    return out
