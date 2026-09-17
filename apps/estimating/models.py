"""Estimating workbench (SharePoint spec §3, §8): the vendor price catalog with its hygiene state, the labor rate card,
and PCA-owned estimates (rooms, lines, versions). Everything here is local — vendor price lists are third-party data
loaded by `loaders.py`; estimates are written only through the views (CSRF + `estimating.write`) with audit events."""

from decimal import Decimal

from django.db import models

from apps.core.models import Customer, Project, TimeStampedModel

MONEY = {"max_digits": 14, "decimal_places": 2}
PRICE = {"max_digits": 14, "decimal_places": 4}
HOURS = {"max_digits": 8, "decimal_places": 2}


class CatalogSource(models.Model):
    """One vendor price file (or the one-time dashboard bootstrap, or an in-app upload)."""
    class Kind(models.TextChoices):
        FILE = "file", "Share folder file"
        BOOTSTRAP = "bootstrap", "Dashboard bootstrap"
        UPLOAD = "upload", "Uploaded in PCA"

    name = models.CharField(max_length=255, unique=True)                   # file name as shipped by the vendor
    vendor = models.CharField(max_length=120, blank=True)                  # dominant manufacturer in the file
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.FILE)
    path = models.CharField(max_length=500, blank=True)
    date_key = models.IntegerField(default=0)                              # y*10000+m*100+d from the file name / cells; 0 = undated
    date_label = models.CharField(max_length=32, blank=True)
    date_precision = models.CharField(max_length=8, blank=True)            # day / month / year / ''
    rows = models.IntegerField(default=0)
    loaded_at = models.DateTimeField(auto_now=True)
    file_mtime = models.FloatField(null=True, blank=True)
    file_size = models.BigIntegerField(null=True, blank=True)
    mode = models.CharField(max_length=12, blank=True)                     # upsert / add-only / prices-only for uploads
    counts = models.JSONField(default=dict, blank=True)                    # loader result (added / updated / skipped / stale …)
    loaded_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date_key", "name"]

    def __str__(self):
        return self.name


class CatalogItem(models.Model):
    """One price-list row. Hygiene never deletes: losers are `archived` with a reason and point at the winner."""
    class Archive(models.TextChoices):
        NONE = "", "current"
        DUPLICATE = "duplicate", "duplicate part (better row kept)"
        VERSION = "version", "older version of the same family"
        PRE2023 = "pre2023", "priced before 2023"

    source = models.ForeignKey(CatalogSource, on_delete=models.CASCADE, related_name="items")
    manufacturer = models.CharField(max_length=120, db_index=True)         # cleaned (alias table, part-lookalikes → Other)
    manufacturer_raw = models.CharField(max_length=200, blank=True)
    part = models.CharField(max_length=160)
    part_norm = models.CharField(max_length=160, db_index=True)            # rules.norm_part — the search / match key
    description = models.TextField(blank=True)
    cost = models.DecimalField(**PRICE, null=True, blank=True)             # dealer cost; NULL = unknown (never 0)
    msrp = models.DecimalField(**PRICE, null=True, blank=True)
    map_price = models.DecimalField(**PRICE, null=True, blank=True)
    category = models.CharField(max_length=255, blank=True)
    date_key = models.IntegerField(default=0)                              # effective date of the price (source date)
    confidence = models.SmallIntegerField(default=0)                       # rules.confidence at load time
    archived = models.BooleanField(default=False, db_index=True)
    archive_reason = models.CharField(max_length=12, choices=Archive.choices, blank=True, default="")
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="supersedes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "part_norm", "manufacturer_raw"], name="estimating_item_source_part_mfr")]
        indexes = [models.Index(fields=["part_norm"], name="estimating_item_pn_pattern", opclasses=["varchar_pattern_ops"]),
                   models.Index(fields=["archived", "manufacturer"], name="estimating_item_arch_mfr")]

    def __str__(self):
        return "%s %s" % (self.manufacturer, self.part)


class HygieneReport(models.Model):
    """One run of the catalog hygiene pass (rules.hygiene) — what it hid and why."""
    ran_at = models.DateTimeField(auto_now_add=True)
    trigger = models.CharField(max_length=24, blank=True)                  # bootstrap / import / folder / manual
    counts = models.JSONField(default=dict)
    notes = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-ran_at"]


class LaborRate(models.Model):
    """The rate card, effective-dated: the row with the latest effective_from ≤ today is the current rate."""
    rate_id = models.CharField(max_length=40)                              # rules.DEFAULT_RATES ids
    label = models.CharField(max_length=60)
    group = models.CharField(max_length=12, choices=[("union", "Union"), ("non_union", "Non-union")])
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    sell = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    effective_from = models.DateField()
    order = models.SmallIntegerField(default=0)
    set_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rate_id", "effective_from"], name="estimating_rate_id_date")]
        ordering = ["order", "rate_id", "-effective_from"]

    def __str__(self):
        return "%s %s→%s from %s" % (self.rate_id, self.cost, self.sell, self.effective_from)


class Estimate(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"

    title = models.CharField(max_length=200, blank=True)
    client_name = models.CharField(max_length=200, blank=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="estimates")
    notes = models.TextField(blank=True)
    bid = models.ForeignKey("bids.Bid", null=True, blank=True, on_delete=models.SET_NULL, related_name="estimates")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="estimates")
    owner = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="estimates")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    # cached totals (recomputed on every save from the lines and the rate card in force)
    total_cost = models.DecimalField(**MONEY, default=Decimal("0"))
    total_sell = models.DecimalField(**MONEY, default=Decimal("0"))
    equipment_cost = models.DecimalField(**MONEY, default=Decimal("0"))
    equipment_sell = models.DecimalField(**MONEY, default=Decimal("0"))
    labor_cost = models.DecimalField(**MONEY, default=Decimal("0"))
    labor_sell = models.DecimalField(**MONEY, default=Decimal("0"))
    labor_hours = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    line_count = models.IntegerField(default=0)
    room_count = models.IntegerField(default=0)
    version_no = models.IntegerField(default=0)                            # bumps on every save; stale forms are refused
    updated_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approval_ref = models.CharField(max_length=64, blank=True)             # planning ApprovalRequest id once raised
    source_file = models.CharField(max_length=255, blank=True)             # when imported from a workbook

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or self.client_name or ("Estimate %d" % self.pk)

    @property
    def profit(self):
        return self.total_sell - self.total_cost

    @property
    def margin(self):
        return float((self.total_sell - self.total_cost) / self.total_sell * 100) if self.total_sell else None


class EstimateVersion(models.Model):
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name="versions")
    version_no = models.IntegerField()
    snapshot = models.JSONField(default=dict)                              # meta + rooms + lines + rates in force + totals
    totals = models.JSONField(default=dict)
    saved_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    saved_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["estimate", "version_no"], name="estimating_version_no")]
        ordering = ["-version_no"]


class Room(models.Model):
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=120)
    order = models.SmallIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.name


class Line(models.Model):
    class Kind(models.TextChoices):
        ITEM = "item", "Item"
        NOTE = "note", "Note"

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="lines")
    catalog_item = models.ForeignKey(CatalogItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=6, choices=Kind.choices, default=Kind.ITEM)
    order = models.SmallIntegerField(default=0)
    area = models.CharField(max_length=120, blank=True)
    # copied item fields (the line keeps what it was priced from even when the catalog moves on)
    manufacturer = models.CharField(max_length=120, blank=True)
    part = models.CharField(max_length=160, blank=True)
    part_norm = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=255, blank=True)
    source_name = models.CharField(max_length=255, blank=True)
    date_key = models.IntegerField(default=0)
    item_msrp = models.DecimalField(**PRICE, null=True, blank=True)
    item_map = models.DecimalField(**PRICE, null=True, blank=True)
    # the line's own numbers
    qty = models.IntegerField(default=1)
    cost = models.DecimalField(**PRICE, default=Decimal("0"))              # dealer cost each (catalog cost unless overridden)
    markup = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sell = models.DecimalField(**MONEY, default=Decimal("0"))              # sell each — authoritative; markup follows
    misc = models.BooleanField(default=False)                              # consumable / miscellaneous material (policy 1.5)
    # labor hours per type — total billable hours for this line, never multiplied by qty
    lh_union_mobilization = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_field_labor = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_union_rough = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_union_pull = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_union_trim = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_union_test = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_engineering = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_fabrication = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_programming = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_commissioning = models.DecimalField(**HOURS, default=Decimal("0"))
    lh_service = models.DecimalField(**HOURS, default=Decimal("0"))
    # cached line totals (equipment ext + labor at the rate card in force when saved)
    total_cost = models.DecimalField(**MONEY, default=Decimal("0"))
    total_sell = models.DecimalField(**MONEY, default=Decimal("0"))

    class Meta:
        ordering = ["order", "id"]

    def hours(self):
        from . import rules
        return {t["id"]: getattr(self, rules.LABOR_COLUMN[t["id"]]) for t in rules.LABOR_TYPES}
