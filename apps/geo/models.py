"""Project locations for the Project Map (docs/project_map_plan.md).

Local-only tables: two full-replace copies of SL address sources, a geocoding cache, and one
resolved location per project. Nothing here writes to SL or PTT.
"""

from django.db import models

from apps.core.models import Customer, Project, TimeStampedModel
from apps.ingestion.models import IngestionRun


class ProjectSiteAddress(models.Model):
    """Where a project's sales orders shipped (sl.project_site_addresses) — one row per project × ship-to."""

    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="site_addresses")
    project_number_raw = models.CharField(max_length=32)
    sl_customer_id = models.CharField(max_length=15, blank=True)
    ship_to_id = models.CharField(max_length=15, blank=True)
    orders = models.IntegerField(default=0)
    last_order_date = models.DateField(null=True, blank=True)
    site_name = models.CharField(max_length=64, blank=True)
    addr1 = models.CharField(max_length=64, blank=True)
    addr2 = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=32, blank=True)
    state = models.CharField(max_length=8, blank=True)
    zip = models.CharField(max_length=12, blank=True)
    country = models.CharField(max_length=8, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["project"])]


class CustomerAddress(models.Model):
    """Customer master street address (sl.customer_addresses) — fallback location."""

    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.CASCADE, related_name="addresses")
    sl_customer_id = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=64, blank=True)
    addr1 = models.CharField(max_length=64, blank=True)
    addr2 = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=32, blank=True)
    state = models.CharField(max_length=8, blank=True)
    zip = models.CharField(max_length=12, blank=True)
    country = models.CharField(max_length=8, blank=True)
    bill_addr1 = models.CharField(max_length=64, blank=True)
    bill_city = models.CharField(max_length=32, blank=True)
    bill_state = models.CharField(max_length=8, blank=True)
    bill_zip = models.CharField(max_length=12, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)


class GeocodedAddress(models.Model):
    """Geocoder cache keyed by the normalised address string. ok=False rows are remembered so the
    same miss is not retried on every run (attempts counts how often we tried)."""

    class Source(models.TextChoices):
        CENSUS = "census", "US Census Geocoder"
        NOMINATIM = "nominatim", "Nominatim (OpenStreetMap)"
        MANUAL = "manual", "Entered by hand"

    address_key = models.CharField(max_length=200, unique=True)
    street = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=40, blank=True)
    state = models.CharField(max_length=8, blank=True)
    zip = models.CharField(max_length=12, blank=True)
    ok = models.BooleanField(default=False)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices, blank=True)
    match_type = models.CharField(max_length=24, blank=True)          # census: Exact / Non_Exact; nominatim: osm type/class
    matched_address = models.CharField(max_length=160, blank=True)
    precision = models.CharField(max_length=12, blank=True)           # street | city
    attempts = models.PositiveSmallIntegerField(default=0)
    geocoded_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=160, blank=True)


class ProjectLocation(TimeStampedModel):
    """The one place a project is drawn. source says where the address came from, quality how much
    to trust it; a manual row is never overwritten by the refresh."""

    class Source(models.TextChoices):
        SITE = "site", "Sales-order ship-to"
        CUSTOMER = "customer", "Customer address"
        CITY = "city", "Customer city"
        MANUAL = "manual", "Set by hand"

    class Quality(models.TextChoices):
        EXACT = "exact", "Street-level, exact"
        APPROX = "approx", "Street-level, approximate"
        CITY = "city", "City centroid"

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="location")
    lat = models.FloatField()
    lng = models.FloatField()
    source = models.CharField(max_length=12, choices=Source.choices)
    quality = models.CharField(max_length=12, choices=Quality.choices)
    address = models.CharField(max_length=200, blank=True)          # what was geocoded, as shown to the user
    site_name = models.CharField(max_length=64, blank=True)
    geocoded_address = models.ForeignKey(GeocodedAddress, null=True, blank=True, on_delete=models.SET_NULL)
    note = models.CharField(max_length=200, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["source"]), models.Index(fields=["lat", "lng"])]


class SalesOrderLocation(models.Model):
    """Where a 010 hardware sales order shipped — the map's 010 layer (docs/08_project_map.md). CNET's
    ship-to street, else the customer's street, else the customer's city. One row per located
    sales-order document; rebuilt in full by the geo step (no manual pins — fix the address in CNET)."""

    class Source(models.TextChoices):
        SHIP = "ship", "CNET ship-to"
        CUSTOMER = "customer", "Customer address"
        CITY = "city", "Customer city"

    document = models.OneToOneField("sales.CnetDocument", on_delete=models.CASCADE, related_name="location")
    lat = models.FloatField()
    lng = models.FloatField()
    source = models.CharField(max_length=12, choices=Source.choices)
    quality = models.CharField(max_length=12, choices=ProjectLocation.Quality.choices)
    address = models.CharField(max_length=200, blank=True)
    site_name = models.CharField(max_length=120, blank=True)
    address_key = models.CharField(max_length=200, blank=True)      # groups orders shipped to the same place
    geocoded_address = models.ForeignKey(GeocodedAddress, null=True, blank=True, on_delete=models.SET_NULL)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["address_key"])]
