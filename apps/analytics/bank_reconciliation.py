"""Bank reconciliation: one imported bank statement vs SL's cash GL account (docs/06 §Bank rec).

Inputs are plain dicts so the core (`run_matching`) is unit-testable without a database:
  lines      statement lines  {id, line_no, date, kind, bucket, desc, amt, check_number}
  gl_rows    GLTran rows on the cash account, a window around the period {dt, per, m, bat, ref, descr, dr, cr, u}
  sl_checks  AP check register on the account {ref, doc_type, dt, vendor_id, payee, amt, cleared}
  book_prev / book_end   AcctHist book balance at the prior and current period end

Matching order (each step only sees what earlier steps left):
  1  bank check list  <-> SL check by number (bank drops the leading digit) — amount must agree
  2  bank check reversals ("Reve Check#") <-> SL void (VC) debits
  3  payroll (Paylocity direct deposits / trust / tax collections) set aside for a category compare
  4  exact 1:1 amount within ±10 days, then ±60 days
  5  one ACH-origination settlement = the set of SL "EFT checks" (01-series) dated 0-7 days before
  6  one lump deposit = AR receipt batch(es), optionally + a small journal item
  7  several same-vendor bank pulls = one SL row
  8  1:1 within 5 cents (rounding)
  9  self-cancelling pairs drop out: void vs its original check, GL reversal vs original entry
Then the bridge: bank ending − outstanding paper checks − SL EFTs not on bank + GL-only debits −
GL-only credits − (payroll GL − payroll bank) + bank-only debits − bank-only credits = book.
Positive WIP-style sign conventions are NOT used here: everything is bank money in/out.
"""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

EFT_CHECK_PREFIXES = ("01",)  # Pace's SL check numbers for ACH/EFT payments — never printed, settle as lump ACH debits
PAYROLL_BANK_PATTERNS = ("PAYLOCITY", "92358 PACE SYSTE")
PAYROLL_GL_RE = re.compile(r"PR-|PAYROLL|NET PAY|CHILD SUPP|PAYLOCITY|^-\d\d/\d\d/\d\d$", re.I)
STALE_CHECK_DAYS = 60
MATCH_WINDOW_DAYS, MATCH_WINDOW_WIDE = 10, 60


def c2(v):
    return int(round(float(v) * 100))


def fm(v):
    """1234567.891 -> '1,234,567.89' for flag text."""
    return "{:,.2f}".format(float(v))


def is_eft(ref):
    return any((ref or "").startswith(p) for p in EFT_CHECK_PREFIXES)


def sl_ref_candidates(num):
    """Bank check number (as printed) -> possible SL RefNbr values. BMO prints Pace's 6-digit
    numbers as 5 digits (162192 -> 62192); older stock may print in full."""
    num = (num or "").strip()
    out = []
    for c in (num, "1" + num, num.zfill(6), "1" + num.zfill(5), num.lstrip("0"), ("1" + num).lstrip("0")):
        if c and c not in out:
            out.append(c)
    return out


def is_payroll_line(line):
    d = (line.get("desc") or "").upper()
    return line.get("bucket") == "payroll" or any(p in d for p in PAYROLL_BANK_PATTERNS)


def is_payroll_gl(row):
    return row["m"] == "GL" and bool(PAYROLL_GL_RE.search(row.get("descr") or ""))


def subset_sum(items, target_cents, maxn=40):
    """Smallest-first-found subset of items (dicts with 'amt') summing exactly to target_cents."""
    items = items[:maxn]
    best = {0: []}
    for it in items:
        c = c2(it["amt"])
        if c <= 0 or c > target_cents:
            continue
        new = {}
        for s, l in best.items():
            ns = s + c
            if ns <= target_cents and ns not in best and ns not in new:
                new[ns] = l + [it]
        best.update(new)
        if target_cents in best:
            return best[target_cents]
    return None


def week_of(d):
    return (d - timedelta(days=d.weekday())).isoformat()


def _gl_brief(g):
    return {"dt": g["dt"].isoformat(), "m": g["m"], "ref": g.get("ref") or "", "descr": (g.get("descr") or "")[:40], "amt": round(float(g["amt"]), 2)}


def run_matching(period_start, period_end, lines, gl_rows, sl_checks, book_prev, book_end, prior_refs=frozenset(), bank_prev=None):
    """Core reconciliation. Returns (result dict, {line_id: match info dict})."""
    lines = [dict(l) for l in lines]
    gl = []
    for r in gl_rows:
        dr, cr = float(r.get("dr") or 0), float(r.get("cr") or 0)
        if dr > 0:
            gl.append(dict(r, amt=dr, side="dr"))
        if cr > 0:
            gl.append(dict(r, amt=cr, side="cr"))
    for i, g in enumerate(gl):
        g["_id"] = i
    ck = {}
    voided = set()
    for r in sl_checks:
        if r["doc_type"] == "CK":
            ck[r["ref"]] = r
        elif r["doc_type"] == "VC":
            voided.add(r["ref"])
    in_period = lambda g: period_start <= g["dt"] <= period_end
    per_key = lambda d: "%04d%02d" % (d.year, d.month)
    PER = per_key(period_end)
    pool = list(gl)
    lm = {}  # line id -> match info
    matched = []  # (lines, gl rows, kind)

    def take(gs):
        for g in gs:
            pool.remove(g)

    def record(ls, gs, kind, sl_ref="", payee=""):
        matched.append((ls, gs, kind))
        for l in ls:
            l["done"] = True
            lm[l["id"]] = {"match_kind": kind, "match_detail": [_gl_brief(g) for g in gs], "sl_ref": sl_ref, "payee": payee}

    flags = []

    def flag(level, code, text, detail=None):
        flags.append({"level": level, "code": code, "text": text, "detail": detail or []})

    # ---- 1. checks ----
    check_lines = [l for l in lines if l["kind"] == "check"]
    reversal_nums = {l["check_number"] for l in lines if l["kind"] == "check_reversal"}
    on_stmt, unknown, mismatch = set(), [], []
    for l in check_lines:
        r = None
        for cand in sl_ref_candidates(l["check_number"]):
            if cand in ck:
                r = ck[cand]
                break
        if not r:
            unknown.append(l)
            continue
        l["sl_ref"] = r["ref"]
        if c2(r["amt"]) != c2(l["amt"]):
            mismatch.append((l, r))
        if l["check_number"] not in reversal_nums:
            on_stmt.add(r["ref"])
        gs = [g for g in pool if g["m"] == "AP" and g["ref"] == r["ref"] and g["side"] == "cr"]
        if gs:
            take(gs[:1])
        record([l], gs[:1], "check" if c2(r["amt"]) == c2(l["amt"]) else "check (amount differs)", sl_ref=r["ref"], payee=r["payee"])
    for l in unknown:
        l["done"] = True
        lm[l["id"]] = {"match_kind": "", "match_detail": [], "sl_ref": "", "payee": ""}
    if unknown:
        flag("critical", "check_not_in_sl", "%d check(s) paid by the bank that do not exist in SL" % len(unknown),
             ["%s %s %s" % (l["date"], l["check_number"], fm(l["amt"])) for l in unknown])
    if mismatch:
        flag("critical", "check_amount_differs", "%d check(s) paid for a different amount than SL recorded" % len(mismatch),
             ["%s bank %s vs SL %s %s %s" % (l["check_number"], fm(l["amt"]), r["ref"], fm(r["amt"]), r["payee"]) for l, r in mismatch])
    # ---- 2. reversals ----
    for l in lines:
        if l["kind"] != "check_reversal":
            continue
        r = None
        for cand in sl_ref_candidates(l["check_number"]):
            if cand in ck:
                r = ck[cand]
                break
        gs = [g for g in pool if r and g["m"] == "AP" and g["ref"] == r["ref"] and g["side"] == "dr"]
        if gs:
            take(gs[:1])
        record([l], gs[:1], "check reversal ↔ SL void" if gs else "check reversal (no SL void found)", sl_ref=r["ref"] if r else "", payee=r["payee"] if r else "")
    if reversal_nums:
        detail = []
        for n in sorted(reversal_nums):
            r = next((ck[c] for c in sl_ref_candidates(n) if c in ck), None)
            if r:
                detail.append("%s %s %s — SL: %s" % (n, r["payee"], fm(r["amt"]), "voided" if r["ref"] in voided else "NOT voided"))
        flag("moderate", "bank_reversed_checks", "%d check(s) were paid and then reversed by the bank — ask why (positive-pay exception? duplicate presentment?)" % len(reversal_nums), detail)
    # ---- zero-amount notices (ACH prenotes) carry no money ----
    for l in lines:
        if not l.get("done") and c2(l["amt"]) == 0:
            l["done"] = True
            lm[l["id"]] = {"match_kind": "zero-amount notice", "match_detail": [], "sl_ref": "", "payee": ""}
    # ---- 3. payroll aside ----
    pr_lines = [l for l in lines if not l.get("done") and is_payroll_line(l)]
    for l in pr_lines:
        l["done"] = True
        lm[l["id"]] = {"match_kind": "payroll (category)", "match_detail": [], "sl_ref": "", "payee": ""}
    pr_gl = [g for g in pool if is_payroll_gl(g)]
    take(pr_gl)
    # ---- 4. exact 1:1 ----
    def cands(side, amt, dt, win):
        return [g for g in pool if g["side"] == side and c2(g["amt"]) == c2(amt) and abs((g["dt"] - dt).days) <= win]
    for l in sorted(lines, key=lambda l: l["date"]):
        if l.get("done"):
            continue
        side = "cr" if l["kind"] == "wd" else "dr"
        gs = cands(side, l["amt"], l["date"], MATCH_WINDOW_DAYS) or cands(side, l["amt"], l["date"], MATCH_WINDOW_WIDE)
        if gs:
            g = min(gs, key=lambda g: abs((g["dt"] - l["date"]).days))
            take([g])
            record([l], [g], "1:1")
    # ---- 5/6. subset sums ----
    for l in sorted(lines, key=lambda l: -l["amt"]):
        if l.get("done"):
            continue
        if l["kind"] == "wd":
            cand = [g for g in pool if g["side"] == "cr" and g["m"] == "AP" and is_eft(g["ref"]) and -1 <= (l["date"] - g["dt"]).days <= 7]
            cand.sort(key=lambda g: -g["amt"])
            hit = subset_sum(cand, c2(l["amt"]))
            if hit:
                take(hit)
                record([l], hit, "ACH settlement = %d SL EFT payments" % len(hit))
        else:
            bats = defaultdict(list)
            for g in pool:
                if g["side"] == "dr" and g["m"] == "AR" and abs((g["dt"] - l["date"]).days) <= 10:
                    bats[g["bat"]].append(g)
            items = sorted([{"amt": sum(g["amt"] for g in b), "gs": b} for b in bats.values()], key=lambda x: -x["amt"])
            hit = subset_sum(items, c2(l["amt"]), maxn=25)
            if hit:
                gs = [g for x in hit for g in x["gs"]]
                take(gs)
                record([l], gs, "deposit = %d AR receipts (%d batch%s)" % (len(gs), len(hit), "" if len(hit) == 1 else "es"))
                continue
            smalls = [{"amt": g["amt"], "gs": [g]} for g in pool if g["side"] == "dr" and g["m"] == "GL" and g["amt"] < 5000 and abs((g["dt"] - l["date"]).days) <= 20]
            items2 = sorted(items + smalls, key=lambda x: -x["amt"])
            hit = subset_sum(items2, c2(l["amt"]), maxn=30)
            if hit:
                gs = [g for x in hit for g in x["gs"]]
                take(gs)
                record([l], gs, "deposit = AR receipts + journal item(s)")
    # ---- 7. several bank items = one SL row ----
    key = lambda l: re.sub(r"[^A-Z]", "", (l["desc"] or "").upper())[:12]
    groups = defaultdict(list)
    for l in lines:
        if not l.get("done"):
            groups[(l["kind"], key(l))].append(l)
    for (kind, k), ls in groups.items():
        if len(ls) < 2:
            continue
        side = "cr" if kind == "wd" else "dr"
        for g in [g for g in pool if g["side"] == side]:
            near = sorted([l for l in ls if not l.get("done") and abs((g["dt"] - l["date"]).days) <= 5], key=lambda l: -l["amt"])
            hit = subset_sum(near, c2(g["amt"]), maxn=12)
            if hit:
                take([g])
                record(hit, [g], "%d bank items = 1 SL payment" % len(hit))
    # ---- 8. pennies ----
    for l in lines:
        if l.get("done"):
            continue
        side = "cr" if l["kind"] == "wd" else "dr"
        gs = [g for g in pool if g["side"] == side and abs(g["amt"] - l["amt"]) <= 0.05 and abs((g["dt"] - l["date"]).days) <= MATCH_WINDOW_DAYS]
        if gs:
            take(gs[:1])
            record([l], gs[:1], "1:1 within 5 cents (bank − GL = %.2f)" % (l["amt"] - gs[0]["amt"]))
    # ---- 9. cancelling pairs ----
    cancelled = []
    for g in [g for g in pool if g["m"] == "AP" and g["side"] == "dr" and g["ref"] in voided]:
        orig = [h for h in pool if h["m"] == "AP" and h["ref"] == g["ref"] and h["side"] == "cr"]
        if orig:
            cancelled.append((g, orig[0], "void ↔ original check"))
        elif g["ref"] in ck and ck[g["ref"]]["dt"] < period_start and ck[g["ref"]]["ref"] not in on_stmt:
            cancelled.append((g, None, "void of a pre-period check that never cleared"))
    for g in [g for g in pool if g["m"] == "GL" and g["side"] == "dr"]:
        for h in pool:
            if h is not g and h["m"] == "GL" and h["side"] == "cr" and c2(h["amt"]) == c2(g["amt"]) and abs((h["dt"] - g["dt"]).days) <= 40:
                cancelled.append((g, h, "reversal ↔ original journal"))
                break
    for g, h, why in cancelled:
        if g in pool:
            take([g])
        if h is not None and h in pool:
            take([h])
    # payroll reversal debits that undo a prior-period credit (June accrual reversed in July): cumulative-neutral
    pr_cancel = []
    for g in [g for g in pr_gl if g["side"] == "dr"]:
        h = next((h for h in pr_gl if h["side"] == "cr" and c2(h["amt"]) == c2(g["amt"]) and h is not g and abs((h["dt"] - g["dt"]).days) <= 40), None)
        if h:
            pr_cancel.append((g, h))
    pr_cancel_ids = {id(x) for pair in pr_cancel for x in pair}
    # ---- outstanding / open items ----
    outstanding = sorted([r for r in ck.values() if not is_eft(r["ref"]) and r["dt"] <= period_end and r["ref"] not in voided
                          and r["ref"] not in on_stmt and r["ref"] not in prior_refs and (not r["cleared"] or r["cleared"] > period_end)],
                         key=lambda r: r["dt"])
    eft_open = [g for g in pool if g["m"] == "AP" and is_eft(g["ref"]) and in_period(g) and g["side"] == "cr"]
    eft_prior = [g for g in pool if g["m"] == "AP" and is_eft(g["ref"]) and g["dt"] < period_start and g["side"] == "cr"
                 and g["ref"] not in prior_refs and not ((ck.get(g["ref"]) or {}).get("cleared") and ck[g["ref"]]["cleared"] <= period_end)]
    paper_refs = on_stmt | {r["ref"] for r in outstanding}
    gl_only = [g for g in pool if g["per"] == PER and not (g["m"] == "AP" and (g["ref"] in paper_refs or is_eft(g["ref"])))]
    bank_only = [l for l in lines if not l.get("done")]
    for l in bank_only:
        lm[l["id"]] = {"match_kind": "", "match_detail": [], "sl_ref": "", "payee": ""}
    # ---- payroll compare ----
    bw, gw = defaultdict(float), defaultdict(float)
    for l in pr_lines:
        bw[week_of(l["date"])] += l["amt"] if l["kind"] == "wd" else -l["amt"]
    pr_gl_per = [g for g in pr_gl if g["per"] == PER and id(g) not in pr_cancel_ids]
    for g in pr_gl_per:
        gw[week_of(g["dt"])] += g["amt"] if g["side"] == "cr" else -g["amt"]
    pr_weeks = [{"week": w, "bank": round(bw[w], 2), "gl": round(gw[w], 2), "diff": round(bw[w] - gw[w], 2)} for w in sorted(set(bw) | set(gw))]
    pr_bank_total = round(sum(bw.values()), 2)
    pr_gl_total = round(sum(gw.values()), 2)
    pr_after = round(sum((g["amt"] if g["side"] == "cr" else -g["amt"]) for g in pr_gl_per if g["dt"] > period_end), 2)
    payroll = {"weeks": pr_weeks, "bank_total": pr_bank_total, "gl_total": pr_gl_total, "gl_minus_bank": round(pr_gl_total - pr_bank_total, 2),
               "gl_dated_after_period": pr_after, "timing_adjusted_diff": round(pr_bank_total - (pr_gl_total - pr_after), 2),
               "gl_lines": [dict(_gl_brief(g), side=g["side"], u=g.get("u", "")) for g in sorted(pr_gl_per, key=lambda g: g["dt"])],
               "cancelled": [[_gl_brief(g), _gl_brief(h)] for g, h in pr_cancel]}
    # ---- opening position (prior period end, from SL alone) ----
    prev_end = period_start - timedelta(days=1)
    o_prev = [r for r in ck.values() if not is_eft(r["ref"]) and r["dt"] <= prev_end and r["ref"] not in voided
              and (not r["cleared"] or r["cleared"] > prev_end)]
    o_prev_eft = [r for r in ck.values() if is_eft(r["ref"]) and r["dt"] <= prev_end and r["ref"] not in voided and (not r["cleared"] or r["cleared"] > prev_end)]
    accrual_rev = round(sum(g["amt"] for g, h in pr_cancel if g["dt"] >= period_start and h["dt"] < period_start), 2)
    opening = None
    if bank_prev is not None and book_prev is not None:
        raw = round(bank_prev - sum(r["amt"] for r in o_prev) - sum(r["amt"] for r in o_prev_eft) - book_prev, 2)
        opening = {"bank_prev": bank_prev, "outstanding_prev": round(sum(r["amt"] for r in o_prev), 2), "outstanding_prev_n": len(o_prev),
                   "eft_prev": round(sum(r["amt"] for r in o_prev_eft), 2), "book_prev": book_prev, "raw_diff": raw,
                   "accruals_reversed_this_period": accrual_rev, "implied_residual": round(raw - accrual_rev, 2)}
    # ---- bridge ----
    o_paper = round(sum(r["amt"] for r in outstanding), 2)
    o_eft, o_eft_prior = round(sum(g["amt"] for g in eft_open), 2), round(sum(g["amt"] for g in eft_prior), 2)
    gl_only_dr = round(sum(g["amt"] for g in gl_only if g["side"] == "dr"), 2)
    gl_only_cr = round(sum(g["amt"] for g in gl_only if g["side"] == "cr"), 2)
    b_only_dr = round(sum(l["amt"] for l in bank_only if l["kind"] == "wd"), 2)
    b_only_cr = round(sum(l["amt"] for l in bank_only if l["kind"] != "wd"), 2)
    bridge = [
        {"label": "Bank ending balance", "amount": None, "key": "bank_end"},
        {"label": "− outstanding paper checks (in SL, not yet presented)", "amount": -o_paper, "key": "outstanding", "n": len(outstanding)},
        {"label": "− SL EFT payments dated this period not yet on the bank", "amount": -o_eft, "key": "eft_open", "n": len(eft_open)},
        {"label": "− SL EFT payments from earlier periods still not on the bank", "amount": -o_eft_prior, "key": "eft_prior", "n": len(eft_prior)},
        {"label": "+ GL debits this period with no bank item (deposits in transit)", "amount": gl_only_dr, "key": "gl_only_dr", "n": sum(1 for g in gl_only if g["side"] == "dr")},
        {"label": "− GL credits this period with no bank item (entries dated after period end, etc.)", "amount": -gl_only_cr, "key": "gl_only_cr", "n": sum(1 for g in gl_only if g["side"] == "cr")},
        {"label": "− payroll entries dated after period end but posted to it (not yet on the bank)", "amount": -pr_after, "key": "payroll_after"},
        {"label": "− payroll: other GL-vs-bank differences this period (live checks, later remittances)", "amount": -round(pr_gl_total - pr_after - pr_bank_total, 2), "key": "payroll_other"},
        {"label": "+ bank debits with no GL entry (unbooked)", "amount": b_only_dr, "key": "bank_only_dr", "n": sum(1 for l in bank_only if l["kind"] == "wd")},
        {"label": "− bank credits with no GL entry (unbooked)", "amount": -b_only_cr, "key": "bank_only_cr", "n": sum(1 for l in bank_only if l["kind"] != "wd")},
    ]
    # ---- flags ----
    sl_clr = {r["ref"] for r in ck.values() if not is_eft(r["ref"]) and r["cleared"] and period_start <= r["cleared"] <= period_end}
    if sl_clr != on_stmt:
        flag("moderate", "sl_clear_flags_differ", "SL's own cleared-check flags for this period differ from the statement (SL-only %d, statement-only %d)" % (len(sl_clr - on_stmt), len(on_stmt - sl_clr)),
             (["SL says cleared but not on statement: %s" % ", ".join(sorted(sl_clr - on_stmt)[:20])] if sl_clr - on_stmt else [])
             + (["On statement but SL not cleared: %s" % ", ".join(sorted(on_stmt - sl_clr)[:20])] if on_stmt - sl_clr else []))
    if bank_only:
        flag("high", "bank_items_unmatched", "%d bank item(s) have no SL counterpart" % len(bank_only),
             ["%s %s %s %s" % (l["date"], l["kind"], fm(l["amt"]), l["desc"][:50]) for l in sorted(bank_only, key=lambda l: -l["amt"])[:25]])
    gl_after = [g for g in gl_only if g["dt"] > period_end]
    if gl_after or pr_after:
        flag("moderate", "posted_early", "Entries dated after %s are posted to this period (%s) — book cash is understated until they hit the bank" % (period_end, fm(pr_after + sum(g["amt"] if g["side"] == "cr" else -g["amt"] for g in gl_after))),
             ["payroll %s" % fm(pr_after)] + ["%s %s %s %s" % (g["dt"], g["m"], g["descr"][:30], fm(g["amt"])) for g in gl_after[:10]])
    if eft_open or eft_prior:
        flag("moderate", "eft_not_on_bank", "%d SL EFT payment(s) (%s) have not appeared on a statement" % (len(eft_open) + len(eft_prior), fm(o_eft + o_eft_prior)),
             ["%s %s %s %s" % (g["dt"], g["ref"], g["descr"][:30], fm(g["amt"])) for g in sorted(eft_open + eft_prior, key=lambda g: g["dt"])[:20]])
    dup_refs = defaultdict(list)
    for r in sl_checks:
        if r["doc_type"] == "CK":
            dup_refs[r["ref"]].append(r)
    dups = {k: v for k, v in dup_refs.items() if len(v) > 1}
    if dups:
        flag("moderate", "duplicate_sl_refs", "%d SL check number(s) used more than once" % len(dups),
             ["%s: %s" % (k, "; ".join("%s %s %s" % (r["dt"], fm(r["amt"]), r["payee"][:20]) for r in v)) for k, v in list(dups.items())[:10]])
    stale = [r for r in outstanding if (period_end - r["dt"]).days > STALE_CHECK_DAYS]
    if stale:
        flag("low", "stale_outstanding", "%d outstanding check(s) older than %d days" % (len(stale), STALE_CHECK_DAYS),
             ["%s %s %s %s" % (r["ref"], r["dt"], fm(r["amt"]), r["payee"]) for r in stale[:15]])
    big_pr = [w for w in pr_weeks if abs(w["diff"]) >= 1000 and w["gl"] != 0 and w["bank"] != 0]
    if big_pr:
        flag("low", "payroll_weekly_gap", "payroll: GL and bank differ by ≥ $1,000 in %d week(s) (live checks / later remittances?)" % len(big_pr),
             ["week of %s: bank %s vs GL %s" % (w["week"], fm(w["bank"]), fm(w["gl"])) for w in big_pr])
    gl_other = [g for g in gl_only if g["dt"] <= period_end]
    if gl_other:
        flag("moderate", "gl_items_unmatched", "%d GL entr%s this period with no bank item" % (len(gl_other), "y" if len(gl_other) == 1 else "ies"),
             ["%s %s %s %s %s" % (g["dt"], g["side"], g["m"], g["descr"][:30], fm(g["amt"])) for g in sorted(gl_other, key=lambda g: -g["amt"])[:15]])
    # ---- summary ----
    how = defaultdict(lambda: {"n": 0, "amount": 0.0})
    for ls, gs, kind in matched:
        k = kind.split(" =")[0].split(" (")[0]
        how[k]["n"] += 1
        how[k]["amount"] += sum(g["amt"] for g in gs) if gs else sum(l["amt"] for l in ls)
    by_age = defaultdict(lambda: {"n": 0, "amount": 0.0})
    for r in outstanding:
        age = (period_end - r["dt"]).days
        b = "0–7 days" if age <= 7 else ("8–30 days" if age <= 30 else ("31–60 days" if age <= 60 else "over 60 days"))
        by_age[b]["n"] += 1
        by_age[b]["amount"] += r["amt"]
    result = {
        "bridge": bridge,
        "checks": {"on_statement": len(check_lines), "matched": len(check_lines) - len(unknown) - len(mismatch), "unknown": len(unknown), "mismatch": len(mismatch),
                   "reversed": sorted(reversal_nums), "sl_cleared_flags_identical": sl_clr == on_stmt},
        "outstanding": {"n": len(outstanding), "total": o_paper, "by_age": dict(by_age),
                        "items": [{"ref": r["ref"], "dt": r["dt"].isoformat(), "amt": round(r["amt"], 2), "payee": r["payee"], "age": (period_end - r["dt"]).days} for r in sorted(outstanding, key=lambda r: -r["amt"])]},
        "eft_open": [dict(_gl_brief(g), payee=(ck.get(g["ref"]) or {}).get("payee", "")) for g in sorted(eft_open, key=lambda g: g["dt"])],
        "eft_prior": [dict(_gl_brief(g), payee=(ck.get(g["ref"]) or {}).get("payee", "")) for g in sorted(eft_prior, key=lambda g: g["dt"])],
        "gl_only": [dict(_gl_brief(g), side=g["side"], per=g["per"], u=g.get("u", "")) for g in sorted(gl_only, key=lambda g: -g["amt"])],
        "bank_only": [{"line_no": l["line_no"], "dt": l["date"].isoformat(), "kind": l["kind"], "desc": l["desc"], "amt": round(l["amt"], 2)} for l in sorted(bank_only, key=lambda l: -l["amt"])],
        "cancelled": [{"why": why, "a": _gl_brief(g), "b": _gl_brief(h) if h else None} for g, h, why in cancelled],
        "payroll": payroll,
        "match_summary": [{"kind": k, "n": v["n"], "amount": round(v["amount"], 2)} for k, v in sorted(how.items(), key=lambda kv: -kv[1]["amount"])],
        "flags": flags,
        "book_prev": book_prev, "book_end": book_end, "opening": opening,
    }
    return result, lm


def finalize_bridge(result, bank_end):
    """Fill the bank line, total the bridge, compute the residual and its flag."""
    for row in result["bridge"]:
        if row["key"] == "bank_end":
            row["amount"] = bank_end
    total = round(sum(row["amount"] or 0 for row in result["bridge"]), 2)
    result["adjusted_bank"] = total
    result["residual"] = round(total - (result["book_end"] or 0), 2) if result.get("book_end") is not None else None
    res = result["residual"]
    if res is not None:
        opening = result.get("opening") or {}
        carried = opening.get("implied_residual")
        change = round(res - carried, 2) if carried is not None else None
        result["residual_change"] = change
        level = "low" if abs(res) < 1 else ("moderate" if abs(res) < 10000 else "critical")
        text = "reconciles to the penny" if abs(res) < 1 else "unexplained difference of %s between adjusted bank and book" % fm(res)
        detail = []
        if carried is not None and abs(res) >= 1:
            detail.append("Opening position implied a carried-forward difference of %s (bank %s − outstanding %s − book %s%s); this month moved it by %s."
                          % (fm(carried), fm(opening["bank_prev"]), fm(opening["outstanding_prev"] + opening["eft_prev"]), fm(opening["book_prev"]),
                             (", net of %s of prior-period accruals reversed this month" % fm(opening["accruals_reversed_this_period"])) if opening.get("accruals_reversed_this_period") else "", fm(change)))
            if abs(change) < 2500 and abs(carried) > 2500:
                level = "moderate"
                text = "%s difference, essentially all carried forward from before this period (this month's change: %s)" % (fm(res), fm(change))
        result["flags"].insert(0, {"level": level, "code": "residual", "text": text, "detail": detail})
    return result


# ---------------------------------------------------------------- SL-backed runner
def book_balance(hist_rows, month):
    """AcctHist rows (one fiscal year, all subs) -> book balance at the end of calendar month `month`."""
    total = Decimal("0")
    for h in hist_rows:
        total += Decimal(str(h["beg_bal"] or 0)) + sum((Decimal(str(h["p%02d" % i] or 0)) for i in range(month)), Decimal("0"))
    return total


def _d(v):
    return v.date() if isinstance(v, datetime) else v


def pull_sl(gl_account, period_start, period_end):
    """All SL data the reconciliation needs, through the guarded read-only client."""
    from apps.ingestion.sources import sl_client
    tr_start, tr_end = period_start - timedelta(days=90), period_end + timedelta(days=25)
    per_lo = "%04d%02d" % ((period_start - timedelta(days=1)).year, (period_start - timedelta(days=1)).month)
    nxt = period_end + timedelta(days=1)
    per_hi = "%04d%02d" % (nxt.year, nxt.month)
    gl = [{"dt": _d(r["tran_date"]), "per": r["per_post"], "m": r["module"], "bat": r["batch_nbr"], "ref": r["ref_nbr"], "descr": r["tran_desc"],
           "dr": float(r["dr_amt"] or 0), "cr": float(r["cr_amt"] or 0), "u": r["created_by"]}
          for r in sl_client.fetch_all("sl.bank_gl_cash_activity", [gl_account, tr_start, tr_end, per_lo, per_hi])]
    checks = []
    for r in sl_client.fetch_all("sl.bank_ap_checks", [gl_account, period_start - timedelta(days=400)]):
        cl = _d(r["clear_date"])
        checks.append({"ref": r["ref_nbr"], "doc_type": r["doc_type"], "dt": _d(r["doc_date"]), "vendor_id": r["vendor_id"], "payee": r["vendor_name"] or "",
                       "amt": float(r["amount"] or 0), "cleared": cl if (cl and cl.year > 1901) else None})
    years = {str(period_end.year), str(period_start.year), str((period_start - timedelta(days=1)).year)}
    hist = {y: sl_client.fetch_all("sl.bank_acct_balances", [gl_account, y]) for y in years}
    prev_end = period_start - timedelta(days=1)
    book_end = book_balance(hist[str(period_end.year)], period_end.month)
    book_prev = book_balance(hist[str(prev_end.year)], prev_end.month)
    return gl, checks, book_prev, book_end


def reconcile_statement(stmt):
    """Run the reconciliation for one imported BankStatement and store the result locally."""
    from django.utils import timezone
    from apps.finance.models import BankStatement, BankStatementLine
    gl, checks, book_prev, book_end = pull_sl(stmt.gl_account, stmt.period_start, stmt.period_end)
    prior_refs = set()
    for s in BankStatement.objects.filter(gl_account=stmt.gl_account, period_end__lt=stmt.period_end):
        for l in s.lines.exclude(sl_ref=""):
            prior_refs.add(l.sl_ref)
        for l in s.lines.exclude(match_detail=[]):
            for d in l.match_detail:
                if d.get("m") == "AP" and d.get("ref"):
                    prior_refs.add(d["ref"])
    lines = [{"id": l.id, "line_no": l.line_no, "date": l.posted_date, "kind": l.kind, "bucket": l.bucket, "desc": l.description,
              "amt": float(l.amount), "check_number": l.check_number} for l in stmt.lines.all()]
    result, lm = run_matching(stmt.period_start, stmt.period_end, lines, gl, checks, float(book_prev), float(book_end), frozenset(prior_refs),
                              bank_prev=float(stmt.previous_balance))
    finalize_bridge(result, float(stmt.ending_balance))
    result["sl_pull"] = {"gl_rows": len(gl), "checks": len(checks), "at": timezone.now().isoformat()}
    with transaction_atomic():
        objs = list(stmt.lines.all())
        for l in objs:
            info = lm.get(l.id, {"match_kind": "", "match_detail": [], "sl_ref": "", "payee": ""})
            l.match_kind, l.match_detail, l.sl_ref, l.payee = info["match_kind"][:64], info["match_detail"], info["sl_ref"], (info["payee"] or "")[:64]
        BankStatementLine.objects.bulk_update(objs, ["match_kind", "match_detail", "sl_ref", "payee"], batch_size=500)
        stmt.book_balance_prev, stmt.book_balance = book_prev, book_end
        stmt.residual = Decimal(str(result["residual"])) if result.get("residual") is not None else None
        stmt.result = result
        stmt.reconciled_at = timezone.now()
        stmt.save()
    return stmt


def transaction_atomic():
    from django.db import transaction
    return transaction.atomic()


def run_bank_reconciliation(run=None, folder=None, force=False, reparse=False):
    """Import any new statement PDFs from the folder (re-parse all with reparse) and reconcile every
    statement that has not been reconciled yet (or all, with force). Used by refresh_finance and
    `manage.py reconcile_bank`."""
    from apps.finance.models import BankStatement
    from apps.ingestion.bank_statements import scan_folder
    imported = scan_folder(folder, force=reparse)
    summary = {"imported": [(str(p.name), st) for p, st, s in imported if st != "unchanged"], "reconciled": [], "errors": []}
    qs = BankStatement.objects.filter(parse_ok=True)
    if not force:
        qs = qs.filter(reconciled_at__isnull=True)
    for stmt in qs.order_by("period_end"):
        try:
            reconcile_statement(stmt)
            summary["reconciled"].append({"period_end": stmt.period_end.isoformat(), "residual": float(stmt.residual) if stmt.residual is not None else None})
        except Exception as e:  # noqa
            summary["errors"].append("%s: %s" % (stmt.period_end, str(e)[:200]))
    return summary
