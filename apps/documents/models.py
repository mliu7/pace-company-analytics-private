"""Documents (SharePoint spec §3, §7): a read-only index of the allowlisted SharePoint libraries and the P: drive
project folders, the links that tie files and folders to SL projects / bids / customers, the extracted text and the
proposal-check findings. Nothing here writes back to SharePoint or the share — every row is PCA's own copy.
"""

from django.conf import settings
from django.db import models

from apps.bids.models import Bid
from apps.core.models import Customer, Project, TimeStampedModel
from apps.ingestion.models import IngestionRun


class Repo(TimeStampedModel):
    """One indexed location: a SharePoint document library (Graph drive) or the share root."""

    class Kind(models.TextChoices):
        SHAREPOINT = "sharepoint_drive", "SharePoint library"
        SHARE = "share", "Network share"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    key = models.CharField(max_length=64, unique=True)                     # stable config key ('sp:/sites/SSPM:Documents', 'share:projects')
    name = models.CharField(max_length=128)
    site_path = models.CharField(max_length=128, blank=True)               # '/sites/PremiseSecurity'
    drive_id = models.CharField(max_length=128, blank=True)
    root_path = models.CharField(max_length=500, blank=True)               # share: the mounted root; SharePoint: the library's root folder
    web_url = models.URLField(max_length=500, blank=True)
    division = models.CharField(max_length=8, blank=True)                  # the division the library belongs to ('' = company-wide)
    delta_link = models.TextField(blank=True)                              # Graph @odata.deltaLink after the last successful index
    last_indexed = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    file_count = models.PositiveIntegerField(default=0)
    folder_count = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name

    @property
    def is_share(self):
        return self.kind == self.Kind.SHARE


class Folder(models.Model):
    repo = models.ForeignKey(Repo, on_delete=models.CASCADE, related_name="folders")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    path = models.CharField(max_length=1000)                               # relative to the repo root, '/'-separated, no leading slash
    name = models.CharField(max_length=255)
    item_id = models.CharField(max_length=255)                             # Graph item id; share: the relative path
    depth = models.PositiveSmallIntegerField(default=0)
    mtime = models.DateTimeField(null=True, blank=True)
    web_url = models.URLField(max_length=1000, blank=True)
    is_deleted = models.BooleanField(default=False)
    walked_at = models.DateTimeField(null=True, blank=True)                # share: when this top-level subtree was last walked to the end
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["repo", "item_id"], name="uniq_folder_repo_item")]
        indexes = [models.Index(fields=["repo", "path"]), models.Index(fields=["repo", "depth"])]

    def __str__(self):
        return "%s:%s" % (self.repo_id, self.path)


class File(models.Model):
    class TextStatus(models.TextChoices):
        NONE = "none", "Not extracted"
        OK = "ok", "Extracted"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    repo = models.ForeignKey(Repo, on_delete=models.CASCADE, related_name="files")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.SET_NULL, related_name="files")
    item_id = models.CharField(max_length=255)
    path = models.CharField(max_length=1000)
    name = models.CharField(max_length=255)
    ext = models.CharField(max_length=16, blank=True)                      # lower-case, no dot
    size = models.BigIntegerField(default=0)
    mtime = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(null=True, blank=True)
    modified_by = models.CharField(max_length=128, blank=True)
    etag = models.CharField(max_length=128, blank=True)                    # Graph eTag; share: sha of (size, mtime)
    web_url = models.URLField(max_length=1000, blank=True)
    unc = models.CharField(max_length=1000, blank=True)                    # \\server\share\path for the Windows desktop
    is_deleted = models.BooleanField(default=False)
    text_status = models.CharField(max_length=8, choices=TextStatus.choices, default=TextStatus.NONE)
    text_error = models.CharField(max_length=300, blank=True)
    checked_etag = models.CharField(max_length=128, blank=True)            # the version the proposal checks last ran on
    # effective link (own link first, else the nearest linked ancestor folder) — denormalised for the pages and the
    # unlinked queue; DocLink keeps every candidate with its rule, confidence and review state
    linked_project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents")
    linked_bid = models.ForeignKey(Bid, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents")
    linked_customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents")
    link_rule = models.CharField(max_length=32, blank=True)
    link_confidence = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    link_via = models.CharField(max_length=8, blank=True)                  # 'file' | 'folder'
    indexed_at = models.DateTimeField(null=True, blank=True)
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["repo", "item_id"], name="uniq_file_repo_item")]
        indexes = [models.Index(fields=["repo", "path"]), models.Index(fields=["linked_project"]), models.Index(fields=["linked_bid"]),
                   models.Index(fields=["mtime"]), models.Index(fields=["ext"]), models.Index(fields=["text_status"])]

    def __str__(self):
        return self.path

    @property
    def location(self):
        return "P: drive" if self.repo.is_share else self.repo.name

    @property
    def folder_path(self):
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""

    @property
    def open_url(self):
        """What a click opens: the SharePoint webUrl (the user's own login) or the smb:// link for the share."""
        if self.web_url:
            return self.web_url
        if self.repo.is_share:
            from apps.ingestion.sources import share_client
            return share_client.smb_url(self.path)
        return ""


LOW_CONFIDENCE = 0.8     # links below this are marked for review on every page


class DocLink(TimeStampedModel):
    """A file or folder ↔ project | bid | customer, with the rule that made it and its confidence. State is PCA-owned:
    a confirmed link is kept whatever the rules say next; a rejected one is never re-created."""

    class State(models.TextChoices):
        AUTO = "auto", "Automatic"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"

    file = models.ForeignKey(File, null=True, blank=True, on_delete=models.CASCADE, related_name="links")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE, related_name="links")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="doc_links")
    bid = models.ForeignKey(Bid, null=True, blank=True, on_delete=models.CASCADE, related_name="doc_links")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.CASCADE, related_name="doc_links")
    rule = models.CharField(max_length=32)                                 # linking.RULES
    confidence = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    state = models.CharField(max_length=10, choices=State.choices, default=State.AUTO)
    evidence = models.JSONField(default=dict)                              # {'matched': '265092', 'text': '265092 - Village of Skokie …'}
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    decided_at = models.DateTimeField(null=True, blank=True)
    # dedupe key: subject + target (a Postgres unique constraint cannot express "one of" nullable columns)
    dedupe = models.CharField(max_length=80, unique=True)

    class Meta:
        indexes = [models.Index(fields=["project"]), models.Index(fields=["bid"]), models.Index(fields=["customer"]), models.Index(fields=["state"])]

    @staticmethod
    def dedupe_key(file_id, folder_id, project_id, bid_id, customer_id):
        return "f%s:d%s:p%s:b%s:c%s" % (file_id or 0, folder_id or 0, project_id or 0, bid_id or 0, customer_id or 0)

    @property
    def is_low(self):
        return float(self.confidence or 0) < LOW_CONFIDENCE

    @property
    def target_label(self):
        if self.project_id:
            return self.project.display_number
        if self.bid_id:
            return "bid %s" % (self.bid.job_number_raw or self.bid.project_name)
        if self.customer_id:
            return self.customer.canonical_name
        return ""


class DocText(models.Model):
    file = models.OneToOneField(File, on_delete=models.CASCADE, related_name="text")
    extractor = models.CharField(max_length=32)                            # pdfplumber / pypdf / docx / xlsx / txt / msg / ocr
    pages = models.PositiveIntegerField(default=0)
    page_offsets = models.JSONField(default=list)                          # character offset where each page / sheet starts
    text = models.TextField(blank=True)
    chars = models.PositiveIntegerField(default=0)
    truncated = models.BooleanField(default=False)
    extracted_at = models.DateTimeField(auto_now=True)

    def page_of(self, offset):
        n = 1
        for i, o in enumerate(self.page_offsets or []):
            if offset >= o:
                n = i + 1
        return n


class DocFinding(TimeStampedModel):
    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARN = "warn", "Warning"
        ERROR = "error", "Error"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        FIXED = "fixed", "Fixed"

    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="findings")
    bid = models.ForeignKey(Bid, null=True, blank=True, on_delete=models.SET_NULL, related_name="doc_findings")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="doc_findings")
    check_id = models.CharField(max_length=48)                             # checks.CHECKS
    severity = models.CharField(max_length=8, choices=Severity.choices, default=Severity.WARN)
    message = models.CharField(max_length=500)
    evidence = models.JSONField(default=dict)                              # {'page': 3, 'quote': '…', 'expected': …, 'found': …}
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN)
    fingerprint = models.CharField(max_length=64)                          # sha of (check_id, message, evidence) — one row per distinct finding
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["file", "fingerprint"], name="uniq_finding_file_fp")]
        indexes = [models.Index(fields=["status", "severity"]), models.Index(fields=["check_id"]), models.Index(fields=["bid"])]


class ListAttachment(models.Model):
    """Attachments on Project List rows (§7.1). Graph cannot read them without the certificate credential, so rows are
    placeholders keyed on the bid until then (readable=False)."""

    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="attachments")
    name = models.CharField(max_length=255, blank=True)
    size = models.BigIntegerField(null=True, blank=True)
    readable = models.BooleanField(default=False)
    web_url = models.URLField(max_length=1000, blank=True)
    seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["bid", "name"], name="uniq_attachment_bid_name")]
