"""Project-materials loaders — the SL documents behind "materials on the job" (docs/02 §3b material path):

  sales-order lines that name the project (what was ordered for the job from stock, shipped, back-ordered, at what
  cost/price, from which ChannelOnline document) → finance_projectsalesorderline
  shipper lines that name the project (what actually left the warehouse for the job, when, by whom, on which
  invoice)                                                                    → finance_projectshipmentline
  AP documents against purchase orders (what the vendor billed per PO + freight) → finance_povoucher

PO lines and PO receipts themselves are loaded by finance_loaders.load_project_events (same window for
project-tied lines). Trailing MATERIALS_DAYS_BACK window, full replace, read-only pulls; project resolved locally.
Runs inside refresh_all only (it is project-ops data, not part of the fast finance refresh).
"""

import logging
from datetime import timedelta

from django.utils import timezone

from apps.core import rules
from apps.core.models import Project
from .finance_loaders import MATERIALS_DAYS_BACK, D0, _replace
from .loaders import _date, _dec, _s, set_watermark
from .sources import sl_client

log = logging.getLogger(__name__)


def _rdate(v):
    d_ = _date(v)
    return None if (d_ and d_.year < 1990) else d_   # SL uses 1900-01-01 as "not entered"


def load_project_materials(run):
    now = timezone.now()
    since = timezone.localdate() - timedelta(days=MATERIALS_DAYS_BACK)
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}

    def pid(raw):
        try:
            pk = rules.canonical_project_number(raw) if _s(raw) else ""
        except ValueError:
            return None, ""
        return proj.get(pk), pk

    so_rows, so_skipped = [], 0
    for r in sl_client.iter_rows("sl.project_so_lines", [since]):
        p, pk = pid(r["project_id"])
        if not p:
            so_skipped += 1
            continue
        so_rows.append((_s(r["so_nbr"])[:15], _s(r["line_ref"])[:8], _rdate(r["ord_date"]), _s(r["so_type"])[:6], _s(r["behavior"])[:6],
                        _s(r["status"])[:2], _s(r["line_status"])[:2], _s(r["cnet_quote"])[:16], _s(r["crtd_user"])[:16], _s(r["slsper_id"])[:10],
                        _s(r["cust_ord_nbr"])[:48], _s(r["ship_name"])[:64], _dec(r["tot_frt"]),
                        _s(r["item_id"])[:32], _s(r["descr"])[:64], _dec(r["qty_ord"]), _dec(r["qty_ship"]), _dec(r["qty_bo"]),
                        _dec(r["unit_cost"]), _dec(r["tot_cost"]), _dec(r["sls_price"]), _dec(r["tot_ord"]),
                        _s(r["task_id"])[:32], _s(r["site_id"])[:10], _rdate(r["prom_date"]), _rdate(r["req_date"]), bool(int(r["drop_ship"] or 0)),
                        p, pk[:16], run.id))
    n_so = _replace("finance_projectsalesorderline",
                    ["so_nbr", "line_ref", "ord_date", "so_type", "behavior", "status", "line_status", "cnet_quote", "crtd_user", "slsper_id",
                     "cust_ord_nbr", "ship_name", "tot_frt", "item_id", "descr", "qty_ord", "qty_ship", "qty_bo", "unit_cost", "tot_cost",
                     "sls_price", "tot_ord", "task_id", "site_id", "prom_date", "req_date", "drop_ship", "project_id", "project_id_raw",
                     "ingestion_run_id"], so_rows)

    sh_rows, sh_skipped = [], 0
    for r in sl_client.iter_rows("sl.project_ship_lines", [since]):
        p, pk = pid(r["project_id"])
        if not p:
            sh_skipped += 1
            continue
        sh_rows.append((_s(r["shipper_id"])[:15], _rdate(r["ship_date"]), _rdate(r["ship_date_plan"]), _s(r["status"])[:2], _s(r["so_nbr"])[:15],
                        _s(r["invc_nbr"])[:15], _rdate(r["invc_date"]), _s(r["crtd_user"])[:16], _s(r["ship_via"])[:16], _s(r["tracking_nbr"])[:40],
                        _dec(r["tot_frt_cost"]), _dec(r["tot_frt_invc"]), _s(r["line_ref"])[:8], _s(r["ord_line_ref"])[:8],
                        _s(r["item_id"])[:32], _s(r["descr"])[:64], _dec(r["qty_ship"]), _dec(r["unit_cost"]), _dec(r["tot_cost"]),
                        _dec(r["sls_price"]), _dec(r["tot_invc"]), _s(r["task_id"])[:32], p, pk[:16], run.id))
    n_sh = _replace("finance_projectshipmentline",
                    ["shipper_id", "ship_date", "ship_date_plan", "status", "so_nbr", "invc_nbr", "invc_date", "crtd_user", "ship_via",
                     "tracking_nbr", "tot_frt_cost", "tot_frt_invc", "line_ref", "ord_line_ref", "item_id", "descr", "qty_ship", "unit_cost",
                     "tot_cost", "sls_price", "tot_invc", "task_id", "project_id", "project_id_raw", "ingestion_run_id"], sh_rows)

    v_rows = []
    for r in sl_client.iter_rows("sl.project_po_vouchers", [since]):
        v_rows.append((_s(r["ref_nbr"])[:10], _s(r["doc_type"])[:2], _rdate(r["doc_date"]), _s(r["vendor_id"])[:15], _s(r["vendor_name"])[:64],
                       _dec(r["amount"]) or D0, _dec(r["freight_amt"]) or D0, _s(r["po_nbr"])[:10], _s(r["status"])[:2], _s(r["crtd_user"])[:16], run.id))
    n_v = _replace("finance_povoucher",
                   ["ref_nbr", "doc_type", "doc_date", "vendor_id", "vendor_name", "amount", "freight_amt", "po_nbr", "status", "crtd_user",
                    "ingestion_run_id"], v_rows)

    res = {"so_lines": n_so, "so_lines_unresolved": so_skipped, "ship_lines": n_sh, "ship_lines_unresolved": sh_skipped, "po_vouchers": n_v}
    set_watermark("sl", "sl.project_so_lines", {"read_at": now.isoformat(), **res}, run)
    return res
