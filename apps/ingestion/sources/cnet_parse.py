"""Parse ChannelOnline export_documents_response XML into plain dicts (010 Sales Spec §2.1).

Schema facts (verified live 2026-08-28): documents carry status/stage/probability/deleted, created|ordered|modified
events with users, customer.customerNumber == SL CustId, salesPerson + accountManager, payment/PONumber, notes by
type, ship/bill-to, totalItemCost/subTotal/tax/shippingHandling/miscAmount/total; lines carry cost AND price,
manufacturer/part, per-line status, qty(+cancelled), multiple supplierProduct (selected="true" wins), serialNumber
children (appear post-shipment), productLinks. Real payloads contain stray control chars → recover=True always.
"""

from datetime import datetime, timezone as dt_tz
from decimal import Decimal, InvalidOperation

from lxml import etree


def _t(el, name):
    child = el.find(name) if el is not None else None
    return child.text.strip() if child is not None and child.text else ""


def _dec(text):
    if text in (None, ""):
        return None
    try:
        return Decimal(str(text).replace(",", ""))
    except InvalidOperation:
        return None


def _dt(text):
    if not text:
        return None
    try:
        d = datetime.fromisoformat(text)
        return d.astimezone(dt_tz.utc) if d.tzinfo else d.replace(tzinfo=dt_tz.utc)
    except ValueError:
        return None


def _user(el):
    if el is None:
        return "", ""
    name = ("%s %s" % (_t(el, "firstName"), _t(el, "lastName"))).strip()
    return name, _t(el, "email").lower()


def parse_response(xml_bytes):
    """-> (documents:list[dict], error:str|None)"""
    root = etree.fromstring(xml_bytes, parser=etree.XMLParser(recover=True))
    if root is None:
        return [], "unparseable response"
    err = root.find(".//error")
    if err is not None:
        return [], "API error %s: %s" % (err.get("code"), err.text)
    container = root.find("documents")
    if container is None:
        return [], None
    return [parse_document(d) for d in container], None


def parse_document(d):
    events = {}
    for ev in d.findall("event"):
        name, email = _user(ev.find("user"))
        events.setdefault(ev.get("type"), []).append({"when": _dt(ev.get("when")), "name": name, "email": email})
    created = (events.get("created") or [{}])[0]
    ordered = (events.get("ordered") or [{}])[0]
    modified = (events.get("modified") or [{}])[-1]

    cust = d.find("customer")
    tax_el = cust.find("tax") if cust is not None else None
    sp_name, sp_email = _user(d.find("salesPerson"))
    am_name, am_email = _user(d.find("accountManager"))
    ship = d.find("shipTo")
    bill = d.find("billTo")
    pay = d.find("payment")
    notes = {n.get("type", ""): (n.text or "").strip() for n in d.findall("note")}

    doc = {
        "document_id": int(d.get("documentId")),
        "document_number": d.get("documentNumber", "").strip(),
        "doc_type": "quote" if d.get("documentType") == "Quote" else "sales_order",
        "revision": int(d.get("revisionNumber") or 1),
        "status": _t(d, "status"),
        "stage": _t(d, "stage"),
        "probability": _dec(_t(d, "probability")),
        "deleted": _t(d, "deleted").lower() == "true",
        "description": _t(d, "description"),
        "created_at": created.get("when"), "created_by_name": created.get("name", ""), "created_by_email": created.get("email", ""),
        "ordered_at": ordered.get("when"), "ordered_by_name": ordered.get("name", ""), "ordered_by_email": ordered.get("email", ""),
        "modified_at": modified.get("when"),
        "customer_sl_id": (cust.get("customerNumber") or "").strip().upper() if cust is not None else "",
        "customer_company": _t(cust, "companyName") if cust is not None else "",
        "tax_status": (_t(tax_el, "rate") if tax_el is not None else ""),
        "terms": _t(cust.find("terms"), "description") if cust is not None and cust.find("terms") is not None else "",
        "price_profile": _t(cust, "priceProfile") if cust is not None else "",
        "salesperson_name": sp_name, "salesperson_email": sp_email,
        "account_manager_name": am_name, "account_manager_email": am_email,
        "customer_po": _t(pay, "PONumber") if pay is not None else "",
        "ship_company": _t(ship, "companyName"), "ship_attn": ("%s %s" % (_t(ship, "firstName"), _t(ship, "lastName"))).strip(),
        "ship_city": _t(ship, "city"),
        "ship_state": (ship.find("state").get("abbreviation") if ship is not None and ship.find("state") is not None else ""),
        "ship_zip": _t(ship, "postalCode"), "ship_addr1": _t(ship, "addressLine1"),
        "bill_city": _t(bill, "city"),
        "note_internal": notes.get("Internal", ""), "note_external": notes.get("External", ""), "note_shipping": notes.get("Shipping", ""),
        "total_item_cost": _dec(_t(d, "totalItemCost")), "subtotal": _dec(_t(d, "subTotal")),
        "tax": _dec(_t(d, "tax")), "shipping_handling": _dec(_t(d, "shippingHandling")),
        "misc_amount": _dec(_t(d, "miscAmount")), "total": _dec(_t(d, "total")),
        "lines": [],
    }
    item_list = d.find("itemList")
    if item_list is not None:
        for p in item_list.findall("product"):
            doc["lines"].append(parse_line(p))
    return doc


def parse_line(p):
    qty_el = p.find("quantity")
    cost, price = _dec(_t(p, "cost")), _dec(_t(p, "price"))
    qty = _dec(qty_el.text if qty_el is not None else None) or Decimal(0)
    sps = p.findall("supplierProduct")
    sp = next((x for x in sps if x.get("selected") == "true"), sps[0] if sps else None)
    supplier_el = sp.find("supplier") if sp is not None else None
    mfr = p.find("manufacturer")
    return {
        "line_item_id": int(p.get("lineItemId") or 0),
        "line_number": int(p.get("lineItemNumber") or 0),
        "line_order": int(p.get("lineOrder") or 0),
        "product_type": p.get("productType", ""),
        "status": _t(p, "status"),
        "manufacturer": (mfr.text or "").strip() if mfr is not None else "",
        "manufacturer_id": mfr.get("manufacturerId", "") if mfr is not None else "",
        "part_number": _t(p, "partNumber"),
        "description": (" ".join(x for x in [_t(p, "descriptionLine1"), _t(p, "descriptionLine2")] if x))[:255],
        "category": _t(p, "category"), "sub_category": _t(p, "subCategory"),
        "upc": _t(p, "upc"), "dropship": _t(p, "dropship").lower() == "true",
        "taxable": _t(p, "taxable").lower() == "true",
        "unit_cost": cost, "unit_price": price,
        "qty": qty, "qty_cancelled": _dec(qty_el.get("cancelled") if qty_el is not None else None) or Decimal(0),
        "ext_cost": (cost * qty) if cost is not None else None,
        "ext_price": (price * qty) if price is not None else None,
        "weight": _dec(_t(p, "weight")),
        "note": _t(p, "note")[:255],
        "supplier_name": (supplier_el.text or "").strip() if supplier_el is not None else "",
        "supplier_account": supplier_el.get("accountNumber", "") if supplier_el is not None else "",
        "supplier_sku": _t(sp, "SKU") if sp is not None else "",
        "serials": [s.text.strip() for s in p.findall("serialNumber") if s.text and s.text.strip()],
    }
