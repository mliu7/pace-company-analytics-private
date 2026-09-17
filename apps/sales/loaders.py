"""Loaders for 010 sales: CNET document upserts + incremental/backfill drivers (010 Sales Spec §2.4)."""

import logging
from datetime import datetime, timedelta, timezone as dt_tz

from django.db import transaction
from django.utils import timezone

from apps.core.models import Customer
from apps.ingestion.bulk import content_hash
from apps.ingestion.sources import cnet_client, cnet_parse

from .models import CnetDocument, CnetDocumentLine, CnetLineSerial

log = logging.getLogger(__name__)

DOC_FIELDS = ["document_number", "doc_type", "revision", "status", "stage", "probability", "deleted", "description",
              "created_at", "created_by_name", "created_by_email", "ordered_at", "ordered_by_name", "ordered_by_email",
              "modified_at", "customer_sl_id", "customer_company", "tax_status", "terms", "price_profile",
              "salesperson_name", "salesperson_email", "account_manager_name", "account_manager_email", "customer_po",
              "ship_company", "ship_attn", "ship_city", "ship_state", "ship_zip", "ship_addr1", "bill_city",
              "note_internal", "note_external", "note_shipping",
              "total_item_cost", "subtotal", "tax", "shipping_handling", "misc_amount", "total"]


# CNET is free-text in places SL is not (e.g. 20-char customerNumbers on prospect quotes) — clamp every
# string to its column length instead of letting one bad value kill a whole backfill chunk.
_DOC_MAXLEN = {f.name: f.max_length for f in CnetDocument._meta.get_fields() if getattr(f, "max_length", None)}
_LINE_MAXLEN = {f.name: f.max_length for f in CnetDocumentLine._meta.get_fields() if getattr(f, "max_length", None)}


def _clamp(d, maxlen):
    for k, v in list(d.items()):
        if isinstance(v, str) and k in maxlen and len(v) > maxlen[k]:
            d[k] = v[:maxlen[k]]
    return d


def upsert_documents(parsed_docs, run=None):
    """Idempotent upsert by document_id; keeps highest revision; replaces lines when content changes."""
    stats = {"new": 0, "updated": 0, "unchanged": 0, "lines": 0, "serials": 0}
    cust_map = dict(Customer.objects.values_list("sl_customer_id", "id"))
    existing = {c.document_id: c for c in CnetDocument.objects.filter(document_id__in=[d["document_id"] for d in parsed_docs])}
    for d in parsed_docs:
        h = content_hash(*[str(d.get(f)) for f in DOC_FIELDS], str(len(d["lines"])),
                         *[str(sorted(l.items())) for l in d["lines"]])
        obj = existing.get(d["document_id"])
        if obj is not None and obj.content_hash == h and obj.revision >= d["revision"]:
            stats["unchanged"] += 1
            continue
        if obj is not None and obj.revision > d["revision"]:
            stats["unchanged"] += 1
            continue
        fields = _clamp({f: d.get(f) for f in DOC_FIELDS}, _DOC_MAXLEN)
        for k, v in list(fields.items()):
            if v is None and k in ("status", "stage", "description", "customer_sl_id"):
                fields[k] = ""
        fields["customer_id"] = cust_map.get(d["customer_sl_id"])
        fields["line_count"] = len(d["lines"])
        fields["content_hash"] = h
        if run is not None:
            fields["last_seen_run"] = run
        with transaction.atomic():
            if obj is None:
                obj = CnetDocument.objects.create(document_id=d["document_id"], **fields)
                stats["new"] += 1
            else:
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                obj.lines.all().delete()
                stats["updated"] += 1
            line_objs = CnetDocumentLine.objects.bulk_create([
                CnetDocumentLine(document=obj, **_clamp({k: v for k, v in l.items() if k != "serials"}, _LINE_MAXLEN))
                for l in d["lines"]])
            stats["lines"] += len(line_objs)
            serials = []
            for lo, l in zip(line_objs, d["lines"]):
                serials += [CnetLineSerial(line=lo, serial=s[:80]) for s in dict.fromkeys(l["serials"])]
            if serials:
                CnetLineSerial.objects.bulk_create(serials, ignore_conflicts=True)
                stats["serials"] += len(serials)
    return stats


def pull_range(doc_type_key, after, before=None, event="created", run=None, archive_label=None):
    """Fetch + upsert one created/modified window.

    ChannelOnline caps a response at 500 documents (error 507). A window that overflows is bisected (1 s overlap) and
    each half pulled recursively down to cnet_client.MIN_WINDOW; the stats then carry a `splits` count. Since
    2026-08-28 the 45-day incremental windows overflow routinely, so this is the normal path, not an edge case.
    """
    try:
        xml = cnet_client.fetch(doc_type_key, after=after, before=before, event=event, archive_label=archive_label)
    except cnet_client.CnetError as e:
        end = before or datetime.now(dt_tz.utc)
        if e.code != cnet_client.TOO_MANY_DOCUMENTS or end - after <= cnet_client.MIN_WINDOW:
            raise
        (a0, a1), (b0, b1) = cnet_client.split_window(after, end)
        log.info("cnet %s/%s window %s..%s exceeds 500 documents; splitting", doc_type_key, event, after.isoformat(), end.isoformat())
        first = pull_range(doc_type_key, a0, a1, event=event, run=run, archive_label=archive_label and archive_label + "_a")
        second = pull_range(doc_type_key, b0, b1, event=event, run=run, archive_label=archive_label and archive_label + "_b")
        merged = {k: first.get(k, 0) + second.get(k, 0) for k in set(first) | set(second)}
        merged["splits"] = merged.get("splits", 0) + 1
        return merged
    docs, err = cnet_parse.parse_response(xml)
    if err:
        raise cnet_client.CnetError(err)
    stats = upsert_documents(docs, run=run)
    stats["fetched"] = len(docs)
    return stats


def load_cnet_incremental(run, days=45):
    """Nightly: created + modified windows for both types (idempotent)."""
    if not __import__("django.conf", fromlist=["settings"]).settings.CNET_SOURCE["username"]:
        return {"skipped": "no CNET credentials configured"}
    after = datetime.now(dt_tz.utc) - timedelta(days=days)
    totals = {}
    for doc_type in ("sales_order", "quote"):
        for event in ("modified", "created"):
            s = pull_range(doc_type, after, event=event, run=run)
            totals["%s_%s" % (doc_type, event)] = {k: v for k, v in s.items() if v}
    return totals
