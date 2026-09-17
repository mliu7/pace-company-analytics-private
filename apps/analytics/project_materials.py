"""Materials on a job — one picture from five SL document streams plus the ChannelOnline quote (docs/02 §3b flow):

  ChannelOnline document (quoted unit cost / price)  →  SOHeader.User2 links it to the sales order
  sales-order lines that name the project            →  what was ordered FOR the job from stock, shipped, back-ordered
  purchase-order lines (project-tied or deduced)     →  what was bought, from whom, by whom, promise dates, PO status
  PO receipt lines                                   →  what reached the warehouse (or the site, for drop-ships) and when
  shipper lines that name the project                →  what left the warehouse for the job, when, by whom, on which invoice
  AP documents against those POs                     →  what the vendor billed, incl. freight
  the project's own ledger rows                      →  freight / purchase-variance / credit postings already in cost

Per item the price chain is quoted → bought → vendor-billed → sale (2026-09-08): `cnet_unit_cost` (the ChannelOnline
line the estimate was built on), `buy_unit` (the PO unit cost = the actual purchase price; SL's stock cost on the sales
order when the job never raised a PO — `buy_basis` 'po' / 'stock'), `vendor_unit_cost` (PurOrdDet.CostVouched ÷
QtyVouched — what the vendor's invoice actually charged; `vendor_vs_po` is the item's purchase variance), `sale_unit`
(SL SlsPrice on the sales order). Drift = bought − quoted per unit (`drift_unit`, `drift_pct`) and on the quantity the
job ordered (`delta_vs_quote`, the number the "bought vs quote" chip sums); `drift_notes` flags a cancelled earlier PO,
a quantity that differs from the quote, or a PO quantity that differs from the job's.

`assemble` is pure (lists of dicts in, one dict out) so it is unit-tested; the project view feeds it from the local
tables. Nothing here reads SL. Status vocabulary (per item, PM's point of view):
  not_ordered   ordered for the job on a sales order, no PO found            (red — someone still has to buy it)
  on_order      PO placed, nothing received yet                              (purple)
  partial_rcvd  some of the PO quantity received                             (yellow)
  received      fully received at the warehouse (or delivered, drop-ship)    (blue)
  partial_ship  some of the job's quantity has shipped to the site           (yellow)
  shipped       the job's full quantity has shipped to the site              (green)
  po_cancelled  every PO for the item was cancelled                          (grey)
  returned      net quantity ≤ 0 after returns                               (grey)
"""

from collections import OrderedDict, defaultdict
from decimal import Decimal

D0 = Decimal("0")
CENT = Decimal("0.01")

PO_STATUS = {"O": ("Open", "moderate"), "P": ("Placed", "awarded_not_started"), "M": ("Complete", "low"),
             "C": ("Closed", "low"), "X": ("Cancelled", "canceled")}
SO_STATUS = {"O": ("Open", "moderate"), "C": ("Complete", "low"), "X": ("Cancelled", "canceled")}
ITEM_STATUS = OrderedDict([
    ("not_ordered", ("No PO", "critical")), ("on_order", ("On order", "awarded_not_started")),
    ("partial_rcvd", ("Partly received", "moderate")), ("received", ("At warehouse", "ok")),
    ("dropshipped", ("Drop-shipped", "ok")), ("partial_ship", ("Partly shipped", "moderate")),
    ("shipped", ("Shipped to job", "low")), ("po_cancelled", ("PO cancelled", "canceled")),
    ("returned", ("Returned", "canceled")), ("orphan", ("Receipt / shipment only", "unknown")),
])
FREIGHT_WORDS = ("FREIGHT", "SHIPPING", "SHIP CHG", "DELIVERY")
AP_SIGN = {"VO": 1, "AC": 1, "AD": -1}     # VT (void) and anything else excluded


def _d(v):
    return v if isinstance(v, Decimal) else (Decimal(str(v)) if v is not None else D0)


def _avg(total, qty):
    return (total / qty) if qty else None


def _qty(q):
    s = ("%f" % q).rstrip("0").rstrip(".")
    return s or "0"


def _key(item_id, descr):
    return (item_id or "").strip().upper() or ("~" + (descr or "").strip().upper()[:40])


def assemble(project, so_lines, ship_lines, po_lines, receipt_lines, vouchers, cnet_docs, cnet_lines, transactions, since=None):
    """project: object/dict with actual_material, actual_purchase_variance, budget_material. Every other argument is a
    list of dicts shaped like the finance_* / sales_* tables (see the view for the exact SELECTs)."""
    g = (lambda k, d=None: getattr(project, k, d)) if not isinstance(project, dict) else (lambda k, d=None: project.get(k, d))
    items = OrderedDict()

    def item(item_id, descr):
        k = _key(item_id, descr)
        it = items.get(k)
        if it is None:
            it = items[k] = {"item_id": (item_id or "").strip(), "descr": (descr or "").strip(), "qty_ord": D0, "qty_ship": D0, "qty_bo": D0,
                             "qty_returned": D0, "so_tot_cost": D0, "so_tot_ord": D0, "so_nbrs": [], "po_qty_ord": D0, "po_qty_rcvd": D0,
                             "po_ext_cost": D0, "po_nbrs": [], "po_statuses": set(), "vendors": [], "ordered_by": [], "dropship": False,
                             "rcpt_qty": D0, "rcpt_cost": D0, "last_rcpt": None, "ship_tot_cost": D0, "ship_tot_invc": D0, "last_ship": None,
                             "cnet_qty": D0, "cnet_ext_cost": D0, "cnet_ext_price": D0, "cnet_docs": [],
                             "po_qty_vouched": D0, "po_cost_vouched": D0, "so_entered_by": [], "ordered_by_live": [], "vendors_live": []}
        elif not it["descr"] and descr:
            it["descr"] = descr.strip()
        return it

    def add_unique(lst, v):
        if v and v not in lst:
            lst.append(v)

    # ---- ChannelOnline documents linked through SOHeader.User2 (prefer the sales-order version of a number)
    docs = {}
    for d in cnet_docs or []:
        cur = docs.get(d["document_number"])
        if cur is None or (d.get("doc_type") == "sales_order" and cur.get("doc_type") != "sales_order"):
            docs[d["document_number"]] = d
    cnet_by_part = defaultdict(list)
    for l in cnet_lines or []:
        cnet_by_part[(l["document_number"], (l.get("part_number") or "").strip().upper())].append(l)

    # ---- sales orders (what the job asked for from stock)
    sos = OrderedDict()
    for l in so_lines:
        so = sos.get(l["so_nbr"])
        if so is None:
            doc = docs.get(l.get("cnet_quote") or "")
            so = sos[l["so_nbr"]] = {"so_nbr": l["so_nbr"], "ord_date": l.get("ord_date"), "so_type": l.get("so_type") or "", "behavior": l.get("behavior") or "",
                                     "status": l.get("status") or "", "cnet_quote": l.get("cnet_quote") or "", "cnet_doc": doc,
                                     "entered_by": l.get("crtd_user") or "", "slsper_id": l.get("slsper_id") or "", "cust_ord_nbr": l.get("cust_ord_nbr") or "",
                                     "ship_name": l.get("ship_name") or "", "tot_frt": _d(l.get("tot_frt")), "n_lines": 0, "qty_ord": D0, "qty_ship": D0,
                                     "qty_bo": D0, "tot_cost": D0, "tot_ord": D0, "shippers": [], "lines": []}
            so["status_label"], so["status_cls"] = SO_STATUS.get(so["status"], (so["status"] or "?", "unknown"))
            so["is_return"] = so["behavior"] in ("RMA", "RMSH", "CM") or so["so_type"].startswith("RM")
            if so["entered_by"] == "SYSADMIN" and doc:
                so["entered_by_label"] = "ChannelOnline · " + (doc.get("ordered_by_name") or doc.get("created_by_name") or "import")
            else:
                so["entered_by_label"] = so["entered_by"] or "—"
        so["n_lines"] += 1
        for k, src in (("qty_ord", "qty_ord"), ("qty_ship", "qty_ship"), ("qty_bo", "qty_bo"), ("tot_cost", "tot_cost"), ("tot_ord", "tot_ord")):
            so[k] += _d(l.get(src))
        so["lines"].append(l)
        if so["status"] == "X" or (l.get("line_status") == "X"):
            continue
        it = item(l.get("item_id"), l.get("descr"))
        add_unique(it["so_nbrs"], l["so_nbr"])
        if so["entered_by_label"] != "—":
            add_unique(it["so_entered_by"], so["entered_by_label"])
        if so["is_return"]:
            it["qty_returned"] += -_d(l.get("qty_ord"))
            it["so_tot_cost"] += _d(l.get("tot_cost")); it["so_tot_ord"] += _d(l.get("tot_ord"))
            continue
        it["qty_ord"] += _d(l.get("qty_ord")); it["qty_ship"] += _d(l.get("qty_ship")); it["qty_bo"] += _d(l.get("qty_bo"))
        it["so_tot_cost"] += _d(l.get("tot_cost")); it["so_tot_ord"] += _d(l.get("tot_ord"))
        if l.get("drop_ship"):
            it["dropship"] = True
        q = so["cnet_quote"]
        if q and q in docs:
            for cl in cnet_by_part.get((q, (l.get("item_id") or "").strip().upper()), []):
                if q not in it["cnet_docs"]:
                    it["cnet_docs"].append(q)
                    it["cnet_qty"] += _d(cl.get("qty")); it["cnet_ext_cost"] += _d(cl.get("ext_cost")); it["cnet_ext_price"] += _d(cl.get("ext_price"))

    # ---- shipments (what actually left for the job)
    shippers = OrderedDict()
    for l in ship_lines:
        sh = shippers.get(l["shipper_id"])
        if sh is None:
            sh = shippers[l["shipper_id"]] = {"shipper_id": l["shipper_id"], "ship_date": l.get("ship_date"), "status": l.get("status") or "", "so_nbr": l.get("so_nbr") or "",
                                              "invc_nbr": l.get("invc_nbr") or "", "invc_date": l.get("invc_date"), "shipped_by": l.get("crtd_user") or "",
                                              "ship_via": l.get("ship_via") or "", "tracking_nbr": l.get("tracking_nbr") or "", "tot_frt_cost": _d(l.get("tot_frt_cost")),
                                              "tot_frt_invc": _d(l.get("tot_frt_invc")), "n_lines": 0, "qty": D0, "tot_cost": D0, "tot_invc": D0}
            sh["is_return"] = sh["shipper_id"].startswith("SR")
            so = sos.get(sh["so_nbr"])
            if so is not None:
                so["shippers"].append(sh)
        sh["n_lines"] += 1; sh["qty"] += _d(l.get("qty_ship")); sh["tot_cost"] += _d(l.get("tot_cost")); sh["tot_invc"] += _d(l.get("tot_invc"))
        it = item(l.get("item_id"), l.get("descr"))
        it["ship_tot_cost"] += _d(l.get("tot_cost")); it["ship_tot_invc"] += _d(l.get("tot_invc"))
        if l.get("ship_date") and (it["last_ship"] is None or l["ship_date"] > it["last_ship"]):
            it["last_ship"] = l["ship_date"]

    # ---- purchase orders (what was bought) + receipts + vouchers
    pos = OrderedDict()
    v_by_po = defaultdict(list)
    for v in vouchers or []:
        if v.get("doc_type") in AP_SIGN:
            v_by_po[v["po_nbr"]].append(v)
    rc_by_po = defaultdict(list)
    for r in receipt_lines:
        rc_by_po[r["po_nbr"]].append(r)
        it = item(r.get("item_id"), r.get("descr"))
        it["rcpt_qty"] += _d(r.get("qty")); it["rcpt_cost"] += _d(r.get("ext_cost"))
        if r.get("rcpt_date") and (it["last_rcpt"] is None or r["rcpt_date"] > it["last_rcpt"]):
            it["last_rcpt"] = r["rcpt_date"]
    for l in po_lines:
        po = pos.get(l["po_nbr"])
        if po is None:
            po = pos[l["po_nbr"]] = {"po_nbr": l["po_nbr"], "po_date": l.get("po_date"), "vendor_id": l.get("vendor_id") or "", "vendor_name": l.get("vendor_name") or "",
                                     "status": l.get("status") or "", "po_type": l.get("po_type") or "", "ordered_by": l.get("crtd_user") or "", "buyer": l.get("buyer") or "",
                                     "ship_via": l.get("ship_via") or "", "po_freight": _d(l.get("po_freight")), "last_rcpt_date": l.get("last_rcpt_date"),
                                     "tie": "direct" if l.get("direct", True) else "deduced", "deduce_basis": l.get("deduce_basis") or "", "n_lines": 0,
                                     "qty_ord": D0, "qty_rcvd": D0, "ext_cost": D0, "cost_vouched": D0, "prom_date": None, "dropship": False, "lines": [],
                                     "receipts": rc_by_po.get(l["po_nbr"], []), "vouchers": v_by_po.get(l["po_nbr"], [])}
            po["status_label"], po["status_cls"] = PO_STATUS.get(po["status"], (po["status"] or "?", "unknown"))
            po["vouchered"] = sum((AP_SIGN[v["doc_type"]] * _d(v["amount"]) for v in po["vouchers"]), D0)
            po["voucher_freight"] = sum((AP_SIGN[v["doc_type"]] * _d(v["freight_amt"]) for v in po["vouchers"]), D0)
            po["n_vouchers"] = len(po["vouchers"])
            po["n_receipts"] = len({r["rcpt_nbr"] for r in po["receipts"]})
        po["n_lines"] += 1
        po["qty_ord"] += _d(l.get("qty_ord")); po["qty_rcvd"] += _d(l.get("qty_rcvd")); po["ext_cost"] += _d(l.get("ext_cost")); po["cost_vouched"] += _d(l.get("cost_vouched"))
        if l.get("prom_date") and (po["prom_date"] is None or l["prom_date"] > po["prom_date"]):
            po["prom_date"] = l["prom_date"]
        ds = (l.get("site_id") or "").upper().startswith("DROP") or po["po_type"] == "DP"
        po["dropship"] = po["dropship"] or ds
        l["rcvd_label"] = ("cancelled" if po["status"] == "X" else "received" if _d(l.get("qty_rcvd")) >= _d(l.get("qty_ord")) and _d(l.get("qty_ord")) > 0
                           else "partial" if _d(l.get("qty_rcvd")) > 0 else "open")
        po["lines"].append(l)
        it = item(l.get("item_id"), l.get("descr"))
        add_unique(it["po_nbrs"], l["po_nbr"]); it["po_statuses"].add(po["status"])
        add_unique(it["vendors"], po["vendor_name"] or po["vendor_id"]); add_unique(it["ordered_by"], po["ordered_by"] or po["buyer"])
        if ds:
            it["dropship"] = True
        if po["status"] != "X":
            it["po_qty_ord"] += _d(l.get("qty_ord")); it["po_qty_rcvd"] += _d(l.get("qty_rcvd")); it["po_ext_cost"] += _d(l.get("ext_cost"))
            it["po_qty_vouched"] += _d(l.get("qty_vouched")); it["po_cost_vouched"] += _d(l.get("cost_vouched"))
            add_unique(it["ordered_by_live"], po["ordered_by"] or po["buyer"]); add_unique(it["vendors_live"], po["vendor_name"] or po["vendor_id"])

    # ---- per-item status + costs
    counts = defaultdict(int)
    for it in items.values():
        net_ord = it["qty_ord"] - it["qty_returned"]
        live_po = bool(it["po_statuses"] - {"X"})
        if it["qty_ord"] > 0 and net_ord <= 0:
            st = "returned"
        elif it["qty_ord"] > 0:
            if it["qty_ship"] >= it["qty_ord"]:
                st = "shipped"
            elif it["qty_ship"] > 0:
                st = "partial_ship"
            elif live_po and it["po_qty_ord"] > 0 and it["po_qty_rcvd"] >= it["po_qty_ord"]:
                st = "dropshipped" if it["dropship"] else "received"
            elif live_po and it["po_qty_rcvd"] > 0:
                st = "partial_rcvd"
            elif live_po and it["po_qty_ord"] > 0:
                st = "on_order"
            elif it["po_statuses"]:
                st = "po_cancelled"
            else:
                st = "not_ordered"
        else:   # bought for the job but never put on a sales order (drop-ship, consumables, or SO not yet entered)
            if live_po and it["po_qty_ord"] > 0 and it["po_qty_rcvd"] >= it["po_qty_ord"]:
                st = "dropshipped" if it["dropship"] else "received"
            elif live_po and it["po_qty_rcvd"] > 0:
                st = "partial_rcvd"
            elif live_po and it["po_qty_ord"] > 0:
                st = "on_order"
            elif it["po_statuses"]:
                st = "po_cancelled"
            else:
                st = "orphan"
        it["no_so"] = it["qty_ord"] == 0 and it["qty_returned"] == 0
        if it["ordered_by_live"]:   # vendor / "ordered by" = the PO that actually bought it; cancelled POs count only when nothing else did
            it["ordered_by"], it["vendors"] = it["ordered_by_live"], it["vendors_live"]
        it["status"] = st
        it["status_label"], it["status_cls"] = ITEM_STATUS[st]
        counts[st] += 1
        it["net_qty_ord"] = net_ord
        it["so_unit_cost"] = _avg(it["so_tot_cost"], it["qty_ord"] - it["qty_returned"]) if it["qty_ord"] else None
        it["po_unit_cost"] = _avg(it["po_ext_cost"], it["po_qty_ord"])
        it["sale_unit"] = _avg(it["so_tot_ord"], it["qty_ord"] - it["qty_returned"]) if it["qty_ord"] else None
        it["cnet_unit_cost"] = _avg(it["cnet_ext_cost"], it["cnet_qty"])
        it["cnet_unit_price"] = _avg(it["cnet_ext_price"], it["cnet_qty"])
        # ---- price chain: quoted → bought → vendor-billed → sale
        # bought = the actual purchase price: the job's PO lines (cancelled excluded); an item the job never raised a PO for
        # came out of inventory at SL's cost on the sales order ('stock')
        if it["po_unit_cost"] is not None:
            it["buy_basis"], it["buy_unit"], it["buy_qty"], it["buy_ext"] = "po", it["po_unit_cost"], it["po_qty_ord"], it["po_ext_cost"]
        elif it["so_unit_cost"] is not None:
            it["buy_basis"], it["buy_unit"], it["buy_qty"], it["buy_ext"] = "stock", it["so_unit_cost"], net_ord, it["so_tot_cost"]
        else:
            it["buy_basis"] = it["buy_unit"] = it["buy_qty"] = it["buy_ext"] = None
        # vendor-billed = what the vendor's invoice actually charged for the vouched quantity (SL matches AP vouchers to PO lines)
        it["vendor_unit_cost"] = _avg(it["po_cost_vouched"], it["po_qty_vouched"])
        it["vendor_vs_po"] = (it["po_cost_vouched"] - it["po_unit_cost"] * it["po_qty_vouched"]).quantize(CENT) if (it["po_qty_vouched"] and it["po_unit_cost"] is not None) else None
        # drift = bought − quoted, per unit and on the quantity the job ordered (the PO quantity when nothing is on a sales order)
        it["drift_unit"] = (it["buy_unit"] - it["cnet_unit_cost"]) if (it["buy_unit"] is not None and it["cnet_unit_cost"] is not None) else None
        it["drift_pct"] = (it["drift_unit"] / it["cnet_unit_cost"]) if (it["drift_unit"] is not None and it["cnet_unit_cost"]) else None
        it["drift_qty"] = net_ord if net_ord > 0 else (it["buy_qty"] or D0)
        it["delta_vs_quote"] = (it["drift_unit"] * it["drift_qty"]).quantize(CENT) if it["drift_unit"] is not None else None
        notes = []
        if "X" in it["po_statuses"] and live_po:
            notes.append("earlier PO cancelled")
        if it["cnet_qty"] and net_ord > 0 and it["cnet_qty"] != net_ord:
            notes.append("qty %s vs %s quoted" % (_qty(net_ord), _qty(it["cnet_qty"])))
        if it["buy_basis"] == "po" and net_ord > 0 and it["po_qty_ord"] != net_ord:
            notes.append("PO qty %s for %s on the job" % (_qty(it["po_qty_ord"]), _qty(net_ord)))
        it["drift_notes"] = notes
        it["ext_cost"] = it["so_tot_cost"] if it["qty_ord"] else it["po_ext_cost"]
        it["po_statuses"] = sorted(it["po_statuses"])
    item_list = sorted(items.values(), key=lambda x: (-(x["ext_cost"] or D0), x["item_id"]))

    # ---- extra costs already in the ledger
    tx = {"freight": [], "variance": [], "credits": [], "adjustments": []}
    for t in transactions or []:
        acct, comment = (t.get("sl_acct") or ""), (t.get("comment") or "").upper()
        if acct == "PURCHASEVARIANCE":
            tx["variance"].append(t)
        elif any(w in comment for w in FREIGHT_WORDS) and acct in ("ODC", "MATERIALS"):
            tx["freight"].append(t)
        elif acct == "MATERIALS" and (t.get("system_cd") == "GL" or t.get("batch_type") in ("AJ",)):
            tx["adjustments"].append(t)
        elif acct == "MATERIALS" and t.get("batch_type") == "CM":
            tx["credits"].append(t)
    extras = {k: {"rows": v, "total": sum((_d(t.get("amount")) for t in v), D0), "n": len(v)} for k, v in tx.items()}
    extras["voucher_freight"] = sum((po["voucher_freight"] for po in pos.values()), D0)
    extras["po_freight"] = sum((po["po_freight"] for po in pos.values()), D0)
    extras["ship_freight_cost"] = sum((sh["tot_frt_cost"] for sh in shippers.values()), D0)
    extras["ship_freight_billed"] = sum((sh["tot_frt_invc"] for sh in shippers.values()), D0)

    live_pos = [po for po in pos.values() if po["status"] != "X"]
    linked_docs = [docs[q] for q in OrderedDict.fromkeys(so["cnet_quote"] for so in sos.values() if so["cnet_quote"] in docs)]
    quoted_cost = sum((_d(d.get("total_item_cost")) for d in linked_docs), D0) if linked_docs else None
    so_orders = [so for so in sos.values() if not so["is_return"] and so["status"] != "X"]
    totals = {
        "quoted_cost": quoted_cost, "quoted_price": sum((_d(d.get("subtotal")) for d in linked_docs), D0) if linked_docs else None,
        "so_cost": sum((so["tot_cost"] for so in sos.values() if so["status"] != "X"), D0), "so_price": sum((so["tot_ord"] for so in sos.values() if so["status"] != "X"), D0),
        "so_qty": sum((so["qty_ord"] for so in so_orders), D0), "so_qty_ship": sum((so["qty_ship"] for so in so_orders), D0), "so_qty_bo": sum((so["qty_bo"] for so in so_orders), D0),
        "po_ordered": sum((po["ext_cost"] for po in live_pos), D0), "po_received": sum((_d(r.get("ext_cost")) for r in receipt_lines), D0),
        "po_vouchered": sum((po["vouchered"] for po in live_pos), D0),
        "shipped_cost": sum((sh["tot_cost"] for sh in shippers.values()), D0), "shipped_invc": sum((sh["tot_invc"] for sh in shippers.values()), D0),
        "posted_material": _d(g("actual_material")), "purchase_variance": _d(g("actual_purchase_variance")), "budget_material": g("budget_material"),
        "n_items": len(item_list), "n_pos": len(pos), "n_open_pos": sum(1 for po in live_pos if po["status"] in ("O", "P")), "n_cancelled_pos": len(pos) - len(live_pos),
        "n_sos": len(sos), "n_shippers": len(shippers), "n_receipts": len({r["rcpt_nbr"] for r in receipt_lines}),
        "last_rcpt": max((r["rcpt_date"] for r in receipt_lines if r.get("rcpt_date")), default=None),
        "last_ship": max((sh["ship_date"] for sh in shippers.values() if sh.get("ship_date")), default=None),
        "n_deduced_pos": sum(1 for po in pos.values() if po["tie"] == "deduced"),
    }
    matched = [it for it in item_list if it["delta_vs_quote"] is not None]
    totals["quote_delta"] = sum((it["delta_vs_quote"] for it in matched), D0) if matched else None
    totals["quote_matched_items"] = len(matched)
    # items-table footer: the quote's own extended cost (its quantities), what was bought, what vendors billed, the sale price
    totals["items_quoted_ext"] = sum((it["cnet_ext_cost"] for it in matched), D0) if matched else None
    totals["items_bought_ext"] = sum((it["buy_ext"] or D0 for it in item_list), D0)
    totals["items_vendor_ext"] = sum((it["po_cost_vouched"] for it in item_list), D0)
    totals["items_sale_ext"] = sum((it["so_tot_ord"] for it in item_list), D0)
    status_counts = [(k, ITEM_STATUS[k][0], ITEM_STATUS[k][1], counts[k]) for k in ITEM_STATUS if counts.get(k)]
    return {"has_data": bool(items or pos or sos), "items": item_list, "pos": list(pos.values()), "sos": list(sos.values()), "shippers": list(shippers.values()),
            "extras": extras, "totals": totals, "status_counts": status_counts, "cnet_docs": linked_docs, "since": since}
