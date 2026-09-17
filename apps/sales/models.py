"""010 Hardware Sales models (010 Sales Spec §2.2): ChannelOnline documents + SL realized economics."""

from django.db import models

from apps.core.models import HOURS, MONEY, Customer, TimeStampedModel
from apps.ingestion.models import IngestionRun

PERCENT = {"max_digits": 8, "decimal_places": 4}


class CnetDocument(TimeStampedModel):
    document_id = models.IntegerField(unique=True)            # stable CNET id — the upsert key
    document_number = models.CharField(max_length=16, db_index=True)
    doc_type = models.CharField(max_length=12, choices=[("quote", "Quote"), ("sales_order", "Sales Order")], db_index=True)
    revision = models.IntegerField(default=1)
    status = models.CharField(max_length=24, blank=True, db_index=True)
    stage = models.CharField(max_length=32, blank=True)
    probability = models.DecimalField(**PERCENT, null=True, blank=True)
    deleted = models.BooleanField(default=False)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(null=True, db_index=True)
    created_by_name = models.CharField(max_length=80, blank=True)
    created_by_email = models.CharField(max_length=120, blank=True)
    ordered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ordered_by_name = models.CharField(max_length=80, blank=True)
    ordered_by_email = models.CharField(max_length=120, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)
    customer_sl_id = models.CharField(max_length=15, blank=True, db_index=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="cnet_documents")
    customer_company = models.CharField(max_length=120, blank=True)
    tax_status = models.CharField(max_length=24, blank=True)
    terms = models.CharField(max_length=48, blank=True)
    price_profile = models.CharField(max_length=48, blank=True)
    salesperson_name = models.CharField(max_length=80, blank=True, db_index=True)
    salesperson_email = models.CharField(max_length=120, blank=True)
    account_manager_name = models.CharField(max_length=80, blank=True)
    account_manager_email = models.CharField(max_length=120, blank=True)
    customer_po = models.CharField(max_length=48, blank=True)
    ship_company = models.CharField(max_length=120, blank=True)
    ship_attn = models.CharField(max_length=80, blank=True)
    ship_city = models.CharField(max_length=64, blank=True)
    ship_state = models.CharField(max_length=8, blank=True)
    ship_zip = models.CharField(max_length=16, blank=True)
    ship_addr1 = models.CharField(max_length=120, blank=True)
    bill_city = models.CharField(max_length=64, blank=True)
    note_internal = models.TextField(blank=True)
    note_external = models.TextField(blank=True)
    note_shipping = models.TextField(blank=True)
    total_item_cost = models.DecimalField(**MONEY, null=True, blank=True)     # CNET quoted cost basis
    subtotal = models.DecimalField(**MONEY, null=True, blank=True)
    tax = models.DecimalField(**MONEY, null=True, blank=True)
    shipping_handling = models.DecimalField(**MONEY, null=True, blank=True)   # S&H charged per CNET
    misc_amount = models.DecimalField(**MONEY, null=True, blank=True)
    total = models.DecimalField(**MONEY, null=True, blank=True)
    line_count = models.IntegerField(default=0)
    # ---- SL link + realized economics (filled by sales_link_and_econ)
    sl_ord_nbr = models.CharField(max_length=12, blank=True, db_index=True)
    sl_so_type = models.CharField(max_length=6, blank=True, db_index=True)
    sl_status = models.CharField(max_length=4, blank=True)
    sl_cancelled = models.BooleanField(default=False)
    sl_ord_date = models.DateField(null=True, blank=True)
    realized_revenue = models.DecimalField(**MONEY, null=True, blank=True)    # Σ shipper invoiced
    realized_cost = models.DecimalField(**MONEY, null=True, blank=True)       # Σ shipper cost
    realized_freight_charged = models.DecimalField(**MONEY, null=True, blank=True)
    invoice_count = models.IntegerField(default=0)
    first_ship_date = models.DateField(null=True, blank=True)
    last_ship_date = models.DateField(null=True, blank=True)
    fully_shipped = models.BooleanField(default=False)
    # ---- quote conversion (quotes only)
    converted_document = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="source_quotes")
    conversion_method = models.CharField(max_length=24, blank=True)           # note_ref / heuristic_total / heuristic_lines
    converted_at = models.DateTimeField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    last_seen_run = models.ForeignKey(IngestionRun, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["doc_type", "status"]), models.Index(fields=["doc_type", "ordered_at"]),
                   models.Index(fields=["salesperson_name", "doc_type"])]

    def __str__(self):
        return "%s %s" % (self.doc_type, self.document_number)


class CnetDocumentLine(models.Model):
    document = models.ForeignKey(CnetDocument, on_delete=models.CASCADE, related_name="lines")
    line_item_id = models.BigIntegerField()
    line_number = models.IntegerField(default=0)
    line_order = models.IntegerField(default=0)
    product_type = models.CharField(max_length=24, blank=True)
    status = models.CharField(max_length=24, blank=True)
    manufacturer = models.CharField(max_length=80, blank=True, db_index=True)
    manufacturer_id = models.CharField(max_length=16, blank=True)
    part_number = models.CharField(max_length=64, blank=True, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=64, blank=True)
    sub_category = models.CharField(max_length=64, blank=True)
    upc = models.CharField(max_length=32, blank=True)
    dropship = models.BooleanField(default=False)
    taxable = models.BooleanField(default=False)
    unit_cost = models.DecimalField(**MONEY, null=True, blank=True)
    unit_price = models.DecimalField(**MONEY, null=True, blank=True)
    qty = models.DecimalField(**HOURS, default=0)
    qty_cancelled = models.DecimalField(**HOURS, default=0)
    ext_cost = models.DecimalField(**MONEY, null=True, blank=True)
    ext_price = models.DecimalField(**MONEY, null=True, blank=True)
    weight = models.DecimalField(**HOURS, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    supplier_name = models.CharField(max_length=80, blank=True, db_index=True)
    supplier_account = models.CharField(max_length=32, blank=True)
    supplier_sku = models.CharField(max_length=48, blank=True)
    sl_line_cost = models.DecimalField(**MONEY, null=True, blank=True)        # matched SL SOLine cost basis (per unit)
    sl_line_matched = models.BooleanField(default=False)

    class Meta:
        unique_together = [("document", "line_item_id")]
        indexes = [models.Index(fields=["part_number"]), models.Index(fields=["manufacturer"])]


class CnetLineSerial(models.Model):
    line = models.ForeignKey(CnetDocumentLine, on_delete=models.CASCADE, related_name="serials")
    serial = models.CharField(max_length=80, db_index=True)

    class Meta:
        unique_together = [("line", "serial")]


class SlCnetOrder(models.Model):
    """SL's copy of a CNET-linked sales order (SOHeader where User2 <> ''). Full-replace nightly."""

    ord_nbr = models.CharField(max_length=12, unique=True)
    cnet_number = models.CharField(max_length=30, db_index=True)   # SL User2 is char(30) and contains occasional junk
    so_type = models.CharField(max_length=6, db_index=True)
    cust_id = models.CharField(max_length=15, blank=True, db_index=True)
    slsper_id = models.CharField(max_length=10, blank=True)
    ord_date = models.DateField(null=True, db_index=True)
    status = models.CharField(max_length=4, blank=True)
    cancelled = models.BooleanField(default=False)
    tot_ord = models.DecimalField(**MONEY, null=True)
    tot_merch = models.DecimalField(**MONEY, null=True)
    tot_frt = models.DecimalField(**MONEY, null=True)
    tot_tax = models.DecimalField(**MONEY, null=True)
    cust_ord_nbr = models.CharField(max_length=48, blank=True)
    ship_name = models.CharField(max_length=120, blank=True)
    ship_city = models.CharField(max_length=64, blank=True)
    ship_state = models.CharField(max_length=8, blank=True)


class SlCnetShipper(models.Model):
    """Per-shipment realized economics (SOShipHeader). Full-replace nightly."""

    shipper_id = models.CharField(max_length=12, unique=True)
    ord_nbr = models.CharField(max_length=12, db_index=True)
    ship_date = models.DateField(null=True)
    invc_nbr = models.CharField(max_length=12, blank=True, db_index=True)
    invc_date = models.DateField(null=True)
    status = models.CharField(max_length=4, blank=True)
    tot_invc = models.DecimalField(**MONEY, null=True)
    tot_cost = models.DecimalField(**MONEY, null=True)
    tot_frt = models.DecimalField(**MONEY, null=True)
    tot_merch = models.DecimalField(**MONEY, null=True)


class SlCnetShipperLine(models.Model):
    """SL SOShipLine: what actually shipped on a shipper, item by item, with SL cost and price
    (sl.cnet_shipper_lines; full replace nightly). Feeds the 010 snapshot's shipment expanders."""

    shipper_id = models.CharField(max_length=12, db_index=True)
    line_ref = models.CharField(max_length=8)
    ord_nbr = models.CharField(max_length=12, db_index=True)
    ord_line_ref = models.CharField(max_length=8, blank=True)
    invt_id = models.CharField(max_length=40, blank=True, db_index=True)
    descr = models.CharField(max_length=80, blank=True)
    qty_ship = models.DecimalField(**HOURS, null=True)
    unit_cost = models.DecimalField(**MONEY, null=True)
    sls_price = models.DecimalField(**MONEY, null=True)
    tot_cost = models.DecimalField(**MONEY, null=True)
    tot_invc = models.DecimalField(**MONEY, null=True)
    tot_merch = models.DecimalField(**MONEY, null=True)
    site_id = models.CharField(max_length=10, blank=True)
    sl_created_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shipper_id", "line_ref"], name="uniq_sl_shipper_line")]


class SlCnetShipperSerial(models.Model):
    """SL SOShipLot: serial numbers shipped per shipper line (sl.cnet_shipper_serials). Drilldown only —
    never front and center (Owner's rule 6)."""

    shipper_id = models.CharField(max_length=12, db_index=True)
    line_ref = models.CharField(max_length=8)
    ord_nbr = models.CharField(max_length=12, db_index=True)
    invt_id = models.CharField(max_length=40, blank=True)
    serial = models.CharField(max_length=80, db_index=True)
    qty_ship = models.DecimalField(**HOURS, null=True)
    rma_disposition = models.CharField(max_length=16, blank=True)
    sl_created_at = models.DateTimeField(null=True)


class SlCnetOrderLine(models.Model):
    """SL order lines for CNET-linked orders (for per-line cost drift). Full-replace nightly."""

    ord_nbr = models.CharField(max_length=12, db_index=True)
    line_ref = models.CharField(max_length=8)
    invt_id = models.CharField(max_length=40, blank=True, db_index=True)
    descr = models.CharField(max_length=80, blank=True)
    qty_ord = models.DecimalField(**HOURS, null=True)
    qty_ship = models.DecimalField(**HOURS, null=True)
    qty_bo = models.DecimalField(**HOURS, null=True)
    unit_cost = models.DecimalField(**MONEY, null=True)
    tot_cost = models.DecimalField(**MONEY, null=True)
    sls_price = models.DecimalField(**MONEY, null=True)
    tot_ord = models.DecimalField(**MONEY, null=True)

    class Meta:
        unique_together = [("ord_nbr", "line_ref")]


class Gl010Period(models.Model):
    """Division P&L truth for subs 0100/0101 from AcctHist, by fiscal year/period/account."""

    fiscal_year = models.CharField(max_length=4)
    period = models.IntegerField()
    sub = models.CharField(max_length=8, db_index=True)
    acct = models.CharField(max_length=10)
    acct_class = models.CharField(max_length=16, db_index=True)   # revenue / cogs / freight / other
    amount = models.DecimalField(**MONEY, default=0)              # natural sign: revenue positive

    class Meta:
        unique_together = [("fiscal_year", "period", "sub", "acct")]
