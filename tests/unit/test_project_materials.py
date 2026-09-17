"""Unit tests for the materials-on-the-job assembly (apps/analytics/project_materials.py) — no database."""

import os
import unittest
from datetime import date
from decimal import Decimal

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.analytics.project_materials import assemble  # noqa: E402

D = Decimal
PROJ = {"actual_material": D("1000"), "actual_purchase_variance": D("-10"), "budget_material": D("1200")}


def so(nbr, item, qty, ship, cost, price, quote="Q1", typ="SO2", beh="SO", status="C", bo=0, descr="", user="SYSADMIN", drop=False):
    return {"so_nbr": nbr, "ord_date": date(2025, 1, 31), "so_type": typ, "behavior": beh, "status": status, "line_status": "C", "cnet_quote": quote,
            "crtd_user": user, "slsper_id": "MB00", "cust_ord_nbr": "PO-1", "ship_name": "Site", "tot_frt": D(0), "item_id": item, "descr": descr or item,
            "qty_ord": D(qty), "qty_ship": D(ship), "qty_bo": D(bo), "unit_cost": D(cost), "tot_cost": D(cost) * D(qty), "sls_price": D(price),
            "tot_ord": D(price) * D(qty), "task_id": "IT", "site_id": "WAREHOUSE", "drop_ship": drop}


def ship(shp, nbr, item, qty, cost, when=date(2025, 2, 19), user="JCLEMONS", inv="IN1", frt=0):
    return {"shipper_id": shp, "ship_date": when, "status": "C", "so_nbr": nbr, "invc_nbr": inv, "invc_date": when, "crtd_user": user, "ship_via": "UPS",
            "tracking_nbr": "", "tot_frt_cost": D(frt), "tot_frt_invc": D(0), "item_id": item, "descr": item, "qty_ship": D(qty), "unit_cost": D(cost),
            "tot_cost": D(cost) * D(qty), "sls_price": D(0), "tot_invc": D(0), "task_id": "IT"}


def po(nbr, item, qty, rcvd, cost, status="M", user="EACOSTA", vendor="INGRAM", site="WAREHOUSE", typ="OR", direct=True, frt=0, vouched=None, billed=None):
    """vouched / billed default to SL's usual state: the received quantity vouchered at the PO price."""
    qv = D(rcvd) if vouched is None else D(vouched)
    return {"po_nbr": nbr, "po_date": date(2025, 2, 3), "vendor_id": "ING001", "vendor_name": vendor, "status": status, "buyer": "", "crtd_user": user,
            "po_type": typ, "ship_via": "", "po_freight": D(frt), "last_rcpt_date": None, "line_ref": "00001", "item_id": item, "descr": item,
            "qty_ord": D(qty), "qty_rcvd": D(rcvd), "unit_cost": D(cost), "ext_cost": D(cost) * D(qty), "qty_vouched": qv,
            "cost_vouched": D(cost) * qv if billed is None else D(billed), "prom_date": date(2025, 2, 10),
            "site_id": site, "direct": direct, "deduce_basis": None if direct else "so"}


def rcpt(nbr, ponbr, item, qty, cost, when=date(2025, 2, 12), user="HVESTER"):
    return {"rcpt_nbr": nbr, "rcpt_date": when, "po_nbr": ponbr, "vendor_name": "INGRAM", "item_id": item, "descr": item, "qty": D(qty),
            "unit_cost": D(cost), "ext_cost": D(cost) * D(qty), "crtd_user": user, "vend_invc_nbr": "V1"}


def voucher(ref, ponbr, amt, frt=0, typ="VO"):
    return {"ref_nbr": ref, "doc_type": typ, "doc_date": date(2025, 2, 20), "vendor_id": "ING001", "vendor_name": "INGRAM", "amount": D(amt),
            "freight_amt": D(frt), "po_nbr": ponbr, "status": "A", "crtd_user": "DREDWOOD"}


CNET_DOCS = [{"document_number": "Q1", "doc_type": "sales_order", "total_item_cost": D("2200"), "subtotal": D("2600"), "created_by_name": "Rep", "ordered_by_name": ""}]
CNET_LINES = [{"document_number": "Q1", "part_number": "monitor", "unit_cost": D("100"), "unit_price": D("120"), "qty": D(20), "ext_cost": D("2000"), "ext_price": D("2400")},
              {"document_number": "Q1", "part_number": "CABLE", "unit_cost": D("2"), "unit_price": D("3"), "qty": D(100), "ext_cost": D("200"), "ext_price": D("300")}]


class ItemStatusTests(unittest.TestCase):
    def build(self, **kw):
        args = dict(so_lines=[], ship_lines=[], po_lines=[], receipt_lines=[], vouchers=[], cnet_docs=CNET_DOCS, cnet_lines=CNET_LINES, transactions=[])
        args.update(kw)
        return assemble(PROJ, **args)

    def by_item(self, res):
        return {it["item_id"]: it for it in res["items"]}

    def test_full_flow_shipped_and_quote_delta(self):
        res = self.build(so_lines=[so("ORD1", "MONITOR", 20, 20, 95, 120)], po_lines=[po("P1", "MONITOR", 20, 20, 95)],
                         receipt_lines=[rcpt("R1", "P1", "MONITOR", 20, 95)], ship_lines=[ship("SH1", "ORD1", "MONITOR", 20, 95)],
                         vouchers=[voucher("V1", "P1", 1900, frt=25), voucher("V2", "P1", 50, typ="AD"), voucher("V3", "P1", 999, typ="VT")])
        it = self.by_item(res)["MONITOR"]
        self.assertEqual(it["status"], "shipped")
        self.assertEqual(it["cnet_unit_cost"], D("100"))            # matched case-insensitively (CNET 'monitor')
        self.assertEqual(it["so_unit_cost"], D("95"))
        self.assertEqual(it["delta_vs_quote"], D("-100"))            # 20 × (95 − 100): bought below quote
        self.assertEqual((it["buy_basis"], it["buy_unit"], it["buy_qty"], it["buy_ext"]), ("po", D("95"), D(20), D("1900")))
        self.assertEqual((it["vendor_unit_cost"], it["po_qty_vouched"], it["po_cost_vouched"], it["vendor_vs_po"]), (D("95"), D(20), D("1900"), D("0")))
        self.assertEqual((it["drift_unit"], it["drift_pct"], it["drift_qty"], it["drift_notes"]), (D("-5"), D("-0.05"), D(20), []))
        self.assertEqual((it["cnet_unit_price"], it["sale_unit"], it["so_entered_by"]), (D("120"), D("120"), ["ChannelOnline · Rep"]))
        self.assertEqual(it["ordered_by"], ["EACOSTA"])
        p = res["pos"][0]
        self.assertEqual((p["status_label"], p["vouchered"], p["voucher_freight"], p["n_vouchers"]), ("Complete", D("1850"), D("25"), 2))   # VT void excluded, AD negative
        self.assertEqual(res["totals"]["quoted_cost"], D("2200"))
        self.assertEqual(res["totals"]["quote_delta"], D("-100"))
        self.assertEqual(res["totals"]["shipped_cost"], D("1900"))
        self.assertEqual(res["sos"][0]["entered_by_label"], "ChannelOnline · Rep")
        self.assertEqual(res["sos"][0]["shippers"][0]["shipped_by"], "JCLEMONS")

    def test_on_order_partial_and_not_ordered(self):
        res = self.build(so_lines=[so("ORD1", "A", 10, 0, 5, 8, bo=10), so("ORD1", "B", 10, 0, 5, 8, bo=10), so("ORD1", "C", 4, 0, 5, 8, bo=4),
                                   so("ORD1", "E", 6, 3, 5, 8, bo=3)],
                         po_lines=[po("P1", "A", 10, 0, 5, status="P"), po("P2", "B", 10, 4, 5, status="O")],
                         receipt_lines=[rcpt("R1", "P2", "B", 4, 5)], ship_lines=[ship("SH1", "ORD1", "E", 3, 5)])
        b = self.by_item(res)
        self.assertEqual(b["A"]["status"], "on_order")
        self.assertEqual(b["B"]["status"], "partial_rcvd")
        self.assertEqual(b["C"]["status"], "not_ordered")
        self.assertEqual(b["E"]["status"], "partial_ship")
        self.assertEqual(dict((k, n) for k, _, _, n in res["status_counts"]), {"not_ordered": 1, "on_order": 1, "partial_rcvd": 1, "partial_ship": 1})
        self.assertEqual(res["totals"]["n_open_pos"], 2)

    def test_received_dropship_cancelled_returned_and_stock_only(self):
        res = self.build(so_lines=[so("ORD1", "W", 5, 0, 10, 12), so("ORD1", "DS", 2, 0, 10, 12, drop=True), so("ORD1", "X", 3, 0, 10, 12),
                                   so("ORD1", "R", 2, 2, 10, 12), so("RM1", "R", -2, -2, 10, 12, typ="RM1", beh="RMA", user="LTRELLIS")],
                         po_lines=[po("P1", "W", 5, 5, 10), po("P2", "DS", 2, 2, 10, site="DROPSHIP", typ="DP"), po("P3", "X", 3, 0, 10, status="X"),
                                   po("P4", "STOCK", 7, 7, 3, direct=False)],
                         ship_lines=[ship("SH1", "ORD1", "R", 2, 10), ship("SR1", "RM1", "R", -2, 10)])
        b = self.by_item(res)
        self.assertEqual(b["W"]["status"], "received")
        self.assertEqual(b["DS"]["status"], "dropshipped")
        self.assertEqual(b["X"]["status"], "po_cancelled")
        self.assertEqual(b["R"]["status"], "returned")
        self.assertEqual((b["STOCK"]["status"], b["STOCK"]["no_so"], b["W"]["no_so"]), ("received", True, False))
        self.assertEqual(res["totals"]["n_cancelled_pos"], 1)
        self.assertEqual(res["totals"]["n_deduced_pos"], 1)
        self.assertEqual(res["totals"]["po_ordered"], D("50") + D("20") + D("21"))   # cancelled PO excluded
        self.assertTrue(res["sos"][1]["is_return"])

    def test_price_chain_drift_vendor_variance_stock_and_notes(self):
        # MONITOR: quoted 20 @ $100; the job put 15 on its sales order (stock cost $100); a first PO at $130 was cancelled, the
        # live PO bought 15 @ $90 and the vendor invoiced $1,360 for them (+$10 over the PO). CABLE came from stock, no PO.
        # STOCK was bought on a PO with no sales order and no quote.
        res = self.build(so_lines=[so("ORD1", "MONITOR", 15, 15, 100, 150), so("ORD1", "CABLE", 100, 100, 2, 3)],
                         po_lines=[po("P0", "MONITOR", 15, 0, 130, status="X", vendor="OLDVENDOR"), po("P1", "MONITOR", 15, 15, 90, billed=1360, user="LTRELLIS"),
                                   po("P4", "STOCK", 7, 7, 3, direct=False)])
        b = self.by_item(res)
        m = b["MONITOR"]
        self.assertEqual((m["buy_basis"], m["buy_unit"], m["buy_qty"], m["buy_ext"]), ("po", D("90"), D(15), D("1350")))       # cancelled PO ignored
        self.assertEqual((m["vendor_unit_cost"].quantize(D("0.01")), m["po_cost_vouched"], m["vendor_vs_po"]), (D("90.67"), D("1360"), D("10")))
        self.assertEqual((m["drift_unit"], m["drift_pct"], m["drift_qty"], m["delta_vs_quote"]), (D("-10"), D("-0.1"), D(15), D("-150")))
        self.assertEqual(m["drift_notes"], ["earlier PO cancelled", "qty 15 vs 20 quoted"])
        self.assertEqual((m["ordered_by"], m["vendors"], m["so_entered_by"]), (["LTRELLIS"], ["INGRAM"], ["ChannelOnline · Rep"]))   # cancelled PO's vendor/user drop out
        c = b["CABLE"]
        self.assertEqual((c["buy_basis"], c["buy_unit"], c["buy_qty"], c["buy_ext"]), ("stock", D("2"), D(100), D("200")))
        self.assertEqual((c["vendor_unit_cost"], c["vendor_vs_po"], c["drift_unit"], c["delta_vs_quote"], c["drift_notes"]), (None, None, D("0"), D("0"), []))
        st = b["STOCK"]
        self.assertEqual((st["buy_basis"], st["buy_unit"], st["drift_qty"], st["drift_unit"], st["delta_vs_quote"], st["vendor_unit_cost"]), ("po", D("3"), D(7), None, None, D("3")))
        t = res["totals"]
        self.assertEqual((t["quote_delta"], t["quote_matched_items"]), (D("-150"), 2))
        self.assertEqual((t["items_quoted_ext"], t["items_bought_ext"], t["items_vendor_ext"], t["items_sale_ext"]), (D("2200"), D("1571"), D("1381"), D("2550")))
        # a PO quantity that differs from the job's is flagged
        res2 = self.build(so_lines=[so("ORD1", "MONITOR", 10, 0, 100, 150)], po_lines=[po("P1", "MONITOR", 12, 0, 100)])
        self.assertEqual(self.by_item(res2)["MONITOR"]["drift_notes"], ["qty 10 vs 20 quoted", "PO qty 12 for 10 on the job"])

    def test_extras_from_ledger(self):
        tx = [{"sl_acct": "ODC", "comment": "ING001 FREIGHT", "amount": D("2"), "system_cd": "AP", "batch_type": "VO"},
              {"sl_acct": "PURCHASEVARIANCE", "comment": "price variance", "amount": D("-180.9"), "system_cd": "AP", "batch_type": "VO"},
              {"sl_acct": "MATERIALS", "comment": "return", "amount": D("-507.5"), "system_cd": "OM", "batch_type": "CM"},
              {"sl_acct": "MATERIALS", "comment": "fix", "amount": D("-24.34"), "system_cd": "GL", "batch_type": "GJ"},
              {"sl_acct": "MATERIALS", "comment": "HP monitor", "amount": D("500"), "system_cd": "OM", "batch_type": "IN"}]
        res = self.build(transactions=tx, po_lines=[po("P1", "A", 1, 1, 5, frt=15)], vouchers=[voucher("V1", "P1", 5, frt=7)])
        e = res["extras"]
        self.assertEqual((e["freight"]["total"], e["variance"]["total"], e["credits"]["total"], e["adjustments"]["total"]), (D("2"), D("-180.9"), D("-507.5"), D("-24.34")))
        self.assertEqual((e["voucher_freight"], e["po_freight"]), (D("7"), D("15")))
        self.assertFalse(assemble(PROJ, [], [], [], [], [], [], [], [])["has_data"])


if __name__ == "__main__":
    unittest.main()
