"""CNET loader idempotency tests (010 Sales Spec §7) — upsert semantics against a real (test) DB."""

from datetime import datetime, timezone as dt_tz
from decimal import Decimal

from django.test import TestCase

from apps.core.models import Customer
from apps.sales.loaders import upsert_documents
from apps.sales.models import CnetDocument, CnetDocumentLine, CnetLineSerial


def _doc(**over):
    d = {
        "document_id": 555001, "document_number": "S555001", "doc_type": "sales_order", "revision": 1,
        "status": "Submitted", "stage": "", "probability": None, "deleted": False, "description": "test order",
        "created_at": datetime(2026, 8, 1, 14, 0, tzinfo=dt_tz.utc), "created_by_name": "Cathy Creator",
        "created_by_email": "cc@example.invalid", "ordered_at": datetime(2026, 8, 2, 15, 0, tzinfo=dt_tz.utc),
        "ordered_by_name": "Olly Orders", "ordered_by_email": "oo@example.invalid",
        "modified_at": datetime(2026, 8, 2, 15, 0, tzinfo=dt_tz.utc),
        "customer_sl_id": "ACME01", "customer_company": "Acme Corp", "tax_status": "", "terms": "Net 30",
        "price_profile": "", "salesperson_name": "Sam Sales", "salesperson_email": "ss@example.invalid",
        "account_manager_name": "", "account_manager_email": "", "customer_po": "PO-1",
        "ship_company": "", "ship_attn": "", "ship_city": "", "ship_state": "", "ship_zip": "", "ship_addr1": "",
        "bill_city": "", "note_internal": "", "note_external": "", "note_shipping": "",
        "total_item_cost": Decimal("400"), "subtotal": Decimal("500"), "tax": Decimal("0"),
        "shipping_handling": Decimal("10"), "misc_amount": Decimal("0"), "total": Decimal("510"),
        "lines": [{
            "line_item_id": 1, "line_number": 1, "line_order": 1, "product_type": "Hardware", "status": "",
            "manufacturer": "TestMfr", "manufacturer_id": "77", "part_number": "TM-100", "description": "widget",
            "category": "", "sub_category": "", "upc": "", "dropship": False, "taxable": False,
            "unit_cost": Decimal("40"), "unit_price": Decimal("50"), "qty": Decimal("10"),
            "qty_cancelled": Decimal("0"), "ext_cost": Decimal("400"), "ext_price": Decimal("500"),
            "weight": None, "note": "", "supplier_name": "Supp", "supplier_account": "", "supplier_sku": "SKU1",
            "serials": ["SER-A", "SER-B", "SER-A"],   # dup must collapse
        }],
    }
    d.update(over)
    return d


class UpsertTests(TestCase):
    def test_create_then_unchanged(self):
        s1 = upsert_documents([_doc()])
        self.assertEqual((s1["new"], s1["lines"], s1["serials"]), (1, 1, 2))
        s2 = upsert_documents([_doc()])
        self.assertEqual((s2["new"], s2["updated"], s2["unchanged"]), (0, 0, 1))
        self.assertEqual(CnetDocument.objects.count(), 1)
        self.assertEqual(CnetDocumentLine.objects.count(), 1)
        self.assertEqual(CnetLineSerial.objects.count(), 2)

    def test_content_change_replaces_lines_and_preserves_sl_link(self):
        upsert_documents([_doc()])
        doc = CnetDocument.objects.get()
        doc.sl_ord_nbr = "0012345"; doc.sl_so_type = "SO1"
        doc.realized_revenue = Decimal("510"); doc.save()
        s = upsert_documents([_doc(status="Shipped")])
        self.assertEqual(s["updated"], 1)
        doc.refresh_from_db()
        self.assertEqual(doc.status, "Shipped")
        self.assertEqual(doc.sl_ord_nbr, "0012345")                  # link survives re-import
        self.assertEqual(doc.realized_revenue, Decimal("510"))
        self.assertEqual(CnetDocumentLine.objects.count(), 1)        # replaced, not duplicated
        self.assertEqual(CnetLineSerial.objects.count(), 2)

    def test_lower_revision_ignored(self):
        upsert_documents([_doc(revision=3, status="Shipped")])
        s = upsert_documents([_doc(revision=2, status="Submitted")])
        self.assertEqual(s["unchanged"], 1)
        self.assertEqual(CnetDocument.objects.get().status, "Shipped")

    def test_higher_revision_wins(self):
        upsert_documents([_doc(revision=1)])
        s = upsert_documents([_doc(revision=2, status="Cancelled")])
        self.assertEqual(s["updated"], 1)
        self.assertEqual(CnetDocument.objects.get().status, "Cancelled")

    def test_customer_fk_resolution(self):
        c = Customer.objects.create(sl_customer_id="ACME01", canonical_name="Acme Corp")
        upsert_documents([_doc()])
        self.assertEqual(CnetDocument.objects.get().customer_id, c.id)
