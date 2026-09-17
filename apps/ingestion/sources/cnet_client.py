"""ChannelOnline (CNET) read-only client (010 Sales Spec §2.1).

The ONLY functions that touch the API are the allowlisted request builders below. Safety by construction:
- documentType limited to Sales Order / Quote (exports only; nothing here can write to ChannelOnline),
- onlyChecked / onlyUnexported always false; no lock/mark option is ever emitted
  (that bookkeeping belongs to PTT's SL importer, which remains the only write path),
- registered userAgent PACEXMLTEST (anything else → error 105),
- pacing + retry with backoff (the endpoint drops connections under rapid sequential large requests),
- every response is gzip-archived under APP_SUPPORT_DIR/cnet_raw/ for reprocessing without re-pulling.
"""

import gzip
import logging
import time
from datetime import datetime, timedelta, timezone as dt_tz
from pathlib import Path

import requests
from django.conf import settings
from lxml import etree

log = logging.getLogger(__name__)

URL = "https://xml.channelonline.com/REQUEST"
SHORTCUT = "pace-systems"
USER_AGENT = "PACEXMLTEST"          # registered with ChannelOnline; do not change
DOC_TYPES = {"sales_order": "Sales Order", "quote": "Quote"}
MIN_INTERVAL_S = 2.5
RETRIES = 3
TOO_MANY_DOCUMENTS = "507"     # "The number of documents is limited to 500. Please update your filters to reduce the result set."
MIN_WINDOW = timedelta(hours=1)  # never bisect an eventInRange window below this

_last_call = [0.0]


class CnetError(Exception):
    """API-level failure. `code` = ChannelOnline's error code as a string (TOO_MANY_DOCUMENTS = more than 500 match), else None."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = str(code) if code is not None else None


def split_window(after, before):
    """Halve [after, before] so each half can be re-pulled when the API caps a response at 500 documents.

    The halves overlap by one second: a document stamped exactly at the midpoint is then fetched twice (the upsert is
    idempotent) instead of falling between two exclusive bounds. Returns ((after, mid), (mid - 1 s, before))."""
    mid = after + (before - after) / 2
    return (after, mid), (mid - timedelta(seconds=1), before)


def _archive_dir():
    d = Path(settings.APP_SUPPORT_DIR) / "cnet_raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_request(doc_type_key, after=None, before=None, event="created", document_number=None):
    assert doc_type_key in DOC_TYPES, doc_type_key
    assert event in ("created", "modified"), event
    root = etree.Element("export_documents_request", schemaVersion="7.0")
    auth = etree.SubElement(root, "authentication")
    etree.SubElement(auth, "shortcut").text = SHORTCUT
    etree.SubElement(auth, "email").text = settings.CNET_SOURCE["username"]
    etree.SubElement(auth, "password").text = settings.CNET_SOURCE["password"]
    etree.SubElement(auth, "userAgent", version="7.0").text = USER_AGENT
    opt = etree.SubElement(root, "options")
    etree.SubElement(opt, "documentType").text = DOC_TYPES[doc_type_key]
    etree.SubElement(opt, "onlyChecked").text = "false"
    etree.SubElement(opt, "onlyUnexported").text = "false"
    if document_number:
        etree.SubElement(opt, "documentNumber").text = str(document_number)
    else:
        rng = etree.SubElement(opt, "eventInRange")
        etree.SubElement(rng, "eventType").text = event
        if after:
            etree.SubElement(rng, "after").text = after.strftime("%Y-%m-%dT%H:%M:%SZ")
        if before:
            etree.SubElement(rng, "before").text = before.strftime("%Y-%m-%dT%H:%M:%SZ")
    return etree.tostring(root)


def _pace():
    wait = _last_call[0] + MIN_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def fetch(doc_type_key, after=None, before=None, event="created", document_number=None, archive_label=None):
    """Returns raw XML bytes; raises CnetError on API-level errors. Archives the response."""
    body = _build_request(doc_type_key, after, before, event, document_number)
    last_exc = None
    for attempt in range(RETRIES + 1):
        _pace()
        try:
            # (connect, read): an unreachable host fails in 20 s instead of hanging for the full read timeout on every retry
            resp = requests.post(URL, data=body, headers={"content-type": "text/xml"}, timeout=(20, 280))
            resp.raise_for_status()
            content = resp.content
            break
        except requests.RequestException as e:   # incl. ChunkedEncodingError etc. — all retryable here
            last_exc = e
            back = 5 * (2 ** attempt)
            log.warning("cnet fetch retry %d in %ds: %s", attempt + 1, back, e)
            time.sleep(back)
    else:
        raise CnetError("cnet fetch failed after retries: %s" % last_exc)
    root = etree.fromstring(content, parser=etree.XMLParser(recover=True))
    err = root.find(".//error") if root is not None else None
    if err is not None:
        raise CnetError("cnet API error %s: %s" % (err.get("code"), err.text), code=err.get("code"))
    label = archive_label or "%s_%s" % (doc_type_key, datetime.now(dt_tz.utc).strftime("%Y%m%dT%H%M%S"))
    path = _archive_dir() / ("%s.xml.gz" % label)
    with gzip.open(path, "wb") as f:
        f.write(content)
    return content


def permissions_note():
    """There is no server-side read-only account concept in this API; safety is the builder allowlist above."""
    return {"api": "channelonline export", "write_paths_present": False, "user_agent": USER_AGENT}
