"""Unit tests for the ChannelOnline XML parser (010 Sales Spec §7) — pure, no DB."""

import unittest
from decimal import Decimal

from apps.ingestion.sources import cnet_parse

DOC_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<export_documents_response version="7.0">
 <documents>
  <document documentId="4200123" documentNumber="S123456" documentType="SalesOrder" revisionNumber="2">
   <status>Partial Ship</status>
   <deleted>false</deleted>
   <description>Rack refresh</description>
   <event type="created" when="2026-08-01T09:15:00-05:00">
    <user><firstName>Cathy</firstName><lastName>Creator</lastName><email>CC@example.invalid</email></user>
   </event>
   <event type="ordered" when="2026-08-02T10:00:00-05:00">
    <user><firstName>Olly</firstName><lastName>Orders</lastName><email>oo@example.invalid</email></user>
   </event>
   <event type="modified" when="2026-08-03T08:00:00-05:00"><user><firstName>A</firstName><lastName>B</lastName></user></event>
   <event type="modified" when="2026-08-04T08:00:00-05:00">
    <user><firstName>Last</firstName><lastName>Modifier</lastName><email>lm@example.invalid</email></user>
   </event>
   <customer customerNumber="acme01"><companyName>Acme Corp</companyName>
     <terms><description>Net 30</description></terms><priceProfile>Standard</priceProfile>
     <tax><rate>Taxable</rate></tax></customer>
   <salesPerson><firstName>Sam</firstName><lastName>Sales</lastName><email>ss@example.invalid</email></salesPerson>
   <payment><PONumber>PO-777</PONumber></payment>
   <shipTo><companyName>Acme Site</companyName><firstName>Dock</firstName><lastName>Door</lastName>
     <addressLine1>1 Way</addressLine1><city>Chicago</city><state abbreviation="IL">Illinois</state>
     <postalCode>60601</postalCode></shipTo>
   <billTo><city>Skokie</city></billTo>
   <note type="Internal">QUOTE #900050 converted</note>
   <note type="Shipping">Liftgate</note>
   <totalItemCost>4000.00</totalItemCost><subTotal>5000.00</subTotal><tax>0.00</tax>
   <shippingHandling>25.00</shippingHandling><miscAmount>0.00</miscAmount><total>5025.00</total>
   <itemList>
    <product lineItemId="9001" lineItemNumber="1" lineOrder="1" productType="Hardware">
     <status>Shipped</status>
     <manufacturer manufacturerId="77">TestMfr</manufacturer>
     <partNumber>TM-100</partNumber>
     <descriptionLine1>Test widget</descriptionLine1><descriptionLine2>rev B</descriptionLine2>
     <cost>40.00</cost><price>50.00</price>
     <quantity cancelled="2">100</quantity>
     <supplierProduct selected="false"><supplier accountNumber="A1">WrongSupplier</supplier><SKU>W-1</SKU></supplierProduct>
     <supplierProduct selected="true"><supplier accountNumber="B2">RightSupplier</supplier><SKU>R-2</SKU></supplierProduct>
     <serialNumber>SER-A</serialNumber><serialNumber> SER-B </serialNumber><serialNumber/>
    </product>
    <product lineItemId="9002" lineOrder="2" productType="Service">
     <partNumber>LABOR</partNumber><price>500.00</price><quantity>1</quantity>
    </product>
   </itemList>
  </document>
 </documents>
</export_documents_response>"""

ERROR_XML = b"""<?xml version="1.0"?><export_documents_response version="7.0">
<error code="105">Invalid User Agent</error></export_documents_response>"""

EMPTY_XML = b"""<?xml version="1.0"?><export_documents_response version="7.0"></export_documents_response>"""

# real payloads contain stray control characters; recover=True must survive them
DIRTY_XML = DOC_XML.replace(b"Rack refresh", b"Rack \x02refresh")


class ParseResponseTests(unittest.TestCase):
    def test_error_response(self):
        docs, err = cnet_parse.parse_response(ERROR_XML)
        self.assertEqual(docs, [])
        self.assertIn("105", err)
        self.assertIn("Invalid User Agent", err)

    def test_empty_response_is_not_error(self):
        docs, err = cnet_parse.parse_response(EMPTY_XML)
        self.assertEqual((docs, err), ([], None))

    def test_control_chars_recovered(self):
        docs, err = cnet_parse.parse_response(DIRTY_XML)
        self.assertIsNone(err)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["document_number"], "S123456")


class ParseDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        docs, err = cnet_parse.parse_response(DOC_XML)
        assert err is None
        cls.doc = docs[0]

    def test_identity_and_type(self):
        d = self.doc
        self.assertEqual(d["document_id"], 4200123)
        self.assertEqual(d["document_number"], "S123456")
        self.assertEqual(d["doc_type"], "sales_order")   # anything not Quote is a sales order
        self.assertEqual(d["revision"], 2)
        self.assertEqual(d["status"], "Partial Ship")
        self.assertFalse(d["deleted"])

    def test_events_users_and_utc(self):
        d = self.doc
        self.assertEqual(d["created_by_name"], "Cathy Creator")
        self.assertEqual(d["created_by_email"], "cc@example.invalid")      # lower-cased
        self.assertEqual(d["ordered_by_name"], "Olly Orders")
        # -05:00 wall time converts to UTC
        self.assertEqual(d["created_at"].isoformat(), "2026-08-01T14:15:00+00:00")
        # the LAST modified event wins
        self.assertEqual(d["modified_at"].isoformat(), "2026-08-04T13:00:00+00:00")

    def test_customer_and_parties(self):
        d = self.doc
        self.assertEqual(d["customer_sl_id"], "ACME01")             # upper-cased == SL CustId
        self.assertEqual(d["customer_company"], "Acme Corp")
        self.assertEqual(d["terms"], "Net 30")
        self.assertEqual(d["salesperson_name"], "Sam Sales")
        self.assertEqual(d["customer_po"], "PO-777")
        self.assertEqual(d["ship_state"], "IL")
        self.assertEqual(d["ship_attn"], "Dock Door")
        self.assertEqual(d["bill_city"], "Skokie")
        self.assertEqual(d["note_internal"], "QUOTE #900050 converted")
        self.assertEqual(d["note_shipping"], "Liftgate")
        self.assertEqual(d["note_external"], "")

    def test_totals(self):
        d = self.doc
        self.assertEqual(d["total_item_cost"], Decimal("4000.00"))
        self.assertEqual(d["subtotal"], Decimal("5000.00"))
        self.assertEqual(d["shipping_handling"], Decimal("25.00"))
        self.assertEqual(d["total"], Decimal("5025.00"))

    def test_line_economics_and_supplier_selection(self):
        l = self.doc["lines"][0]
        self.assertEqual(l["line_item_id"], 9001)
        self.assertEqual(l["manufacturer"], "TestMfr")
        self.assertEqual(l["part_number"], "TM-100")
        self.assertEqual(l["description"], "Test widget rev B")
        self.assertEqual(l["unit_cost"], Decimal("40.00"))
        self.assertEqual(l["unit_price"], Decimal("50.00"))
        self.assertEqual(l["qty"], Decimal("100"))
        self.assertEqual(l["qty_cancelled"], Decimal("2"))
        self.assertEqual(l["ext_cost"], Decimal("4000.00"))
        self.assertEqual(l["ext_price"], Decimal("5000.00"))
        self.assertEqual(l["supplier_name"], "RightSupplier")       # selected="true" wins
        self.assertEqual(l["supplier_sku"], "R-2")
        self.assertEqual(l["serials"], ["SER-A", "SER-B"])          # trimmed, empties dropped

    def test_line_without_cost(self):
        l = self.doc["lines"][1]
        self.assertIsNone(l["unit_cost"])
        self.assertIsNone(l["ext_cost"])                            # no fake zero cost
        self.assertEqual(l["ext_price"], Decimal("500.00"))

    def test_quote_type(self):
        xml = DOC_XML.replace(b'documentType="SalesOrder"', b'documentType="Quote"')
        docs, _ = cnet_parse.parse_response(xml)
        self.assertEqual(docs[0]["doc_type"], "quote")


if __name__ == "__main__":
    unittest.main()
