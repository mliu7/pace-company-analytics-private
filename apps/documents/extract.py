"""Text extraction (SharePoint spec §7.3): bytes + extension -> text with page / sheet offsets. Pure apart from the
optional libraries it imports lazily (pdfplumber → pypdf, python-docx, openpyxl, extract-msg; OCR only when
pytesseract happens to be installed — it is not, and this module never installs anything). Bounded: `MAX_CHARS`
caps the stored text and `MAX_PAGES` the pages read, and the result says so (`truncated`).

Result: {'ok', 'extractor', 'text', 'pages', 'page_offsets', 'truncated', 'error'} — page_offsets[i] is the character
offset where page / sheet i+1 starts, so a finding can say "page 3".
"""

import io
import os
import logging
import re

log = logging.getLogger(__name__)

MAX_CHARS = 1_500_000
MAX_PAGES = 400
TEXT_EXTS = {"pdf", "docx", "xlsx", "xlsm", "txt", "md", "csv", "msg", "log", "rtf", "pptx", "xls", "doc", "odt"}
PREVIEW_EXTS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "txt", "md", "csv", "log"}
OCR_AVAILABLE = False
try:  # optional, never installed by PCA
    import pytesseract  # noqa: F401
    OCR_AVAILABLE = True
except Exception:  # noqa
    pass


def supported(ext):
    return (ext or "").lower() in TEXT_EXTS


def _result(ok, extractor, pages_text, error="", pages_total=None):
    offsets, parts, total, truncated = [], [], 0, False
    for t in pages_text:
        t = t or ""
        if total >= MAX_CHARS:
            truncated = True
            break
        offsets.append(total)
        if total + len(t) > MAX_CHARS:
            t = t[:MAX_CHARS - total]
            truncated = True
        parts.append(t)
        total += len(t) + 1
    text = "\n".join(parts)
    n = pages_total if pages_total is not None else len(offsets)
    if pages_total and pages_total > len(offsets):
        truncated = True
    return {"ok": ok, "extractor": extractor, "text": text, "pages": n, "page_offsets": offsets, "truncated": truncated, "error": error[:300]}


def extract(data, ext, name=""):
    """Dispatch on the extension. Never raises: a failure comes back as ok=False with the error text."""
    ext = (ext or "").lower()
    try:
        if ext == "pdf":
            return _pdf(data)
        if ext == "docx":
            return _docx(data)
        if ext in ("xlsx", "xlsm"):
            return _xlsx(data)
        if ext in ("txt", "md", "csv", "log"):
            return _txt(data)
        if ext == "rtf":
            return _rtf(data)
        if ext == "msg":
            return _msg(data)
        if ext == "pptx":
            return _pptx(data)
        if ext == "xls":
            return _xls(data)
        if ext in ("doc", "odt"):
            return _doc(data, ext)
    except Exception as e:  # noqa
        log.info("extract %s failed: %s", name, e)
        return _result(False, ext, [], "%s: %s" % (type(e).__name__, e))
    return _result(False, "", [], "unsupported extension %r" % ext)


# ------------------------------------------------------------------------------------------- pdf
def _pdf(data):
    pages, total, extractor = [], None, "pdfplumber"
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages[:MAX_PAGES]):
                t = page.extract_text() or ""
                if not t.strip() and OCR_AVAILABLE:
                    t = _ocr_page(page) or ""
                    extractor = "pdfplumber+ocr"
                pages.append(t)
    except Exception as e:  # noqa - fall back to pypdf
        log.info("pdfplumber failed (%s), trying pypdf", e)
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        total = len(r.pages)
        pages = [(p.extract_text() or "") for p in r.pages[:MAX_PAGES]]
        extractor = "pypdf"
    if total and not any(p.strip() for p in pages):
        return _result(False, extractor, pages, "no text layer (scanned? OCR not available)" if not OCR_AVAILABLE else "no text found", total)
    return _result(True, extractor, pages, "", total)


def _ocr_page(page):
    """OCR hook — only reached when pytesseract is installed (it is not by default)."""
    try:
        import pytesseract
        img = page.to_image(resolution=200).original
        return pytesseract.image_to_string(img)
    except Exception as e:  # noqa
        log.info("ocr failed: %s", e)
        return ""


# ------------------------------------------------------------------------------------------- docx
def _docx(data):
    import docx
    d = docx.Document(io.BytesIO(data))
    lines = []
    for para in d.paragraphs:
        if para.text.strip():
            lines.append(para.text)
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    for section in d.sections:
        for part, label in ((section.header, "[header] "), (section.footer, "[footer] ")):
            try:
                txt = " ".join(p.text for p in part.paragraphs if p.text.strip())
            except Exception:  # noqa
                txt = ""
            if txt:
                lines.append(label + txt)
    return _result(True, "docx", ["\n".join(lines)])


# ------------------------------------------------------------------------------------------- xlsx
def _xlsx(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets[:MAX_PAGES]:
        rows = ["[sheet] " + ws.title]
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                rows.append("\t".join(cells).rstrip())
            if sum(len(r) for r in rows) > MAX_CHARS:
                break
        sheets.append("\n".join(rows))
    wb.close()
    return _result(True, "xlsx", sheets)


# ------------------------------------------------------------------------------------------- pptx / xls / doc
def _pptx(data):
    """python-pptx: one 'page' per slide — title, text frames, tables, notes."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    slides = []
    for n, slide in enumerate(prs.slides, 1):
        if n > MAX_PAGES:
            break
        lines = ["[slide %d]" % n]
        for shape in slide.shapes:
            try:
                if shape.has_text_frame:
                    lines += [pg.text for pg in shape.text_frame.paragraphs if pg.text.strip()]
                elif getattr(shape, "has_table", False) and shape.has_table:
                    lines += ["\t".join(c.text for c in row.cells) for row in shape.table.rows]
            except Exception:  # noqa
                continue
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None and slide.notes_slide.notes_text_frame.text.strip():
                lines.append("[notes] " + slide.notes_slide.notes_text_frame.text.strip())
        except Exception:  # noqa
            pass
        slides.append("\n".join(lines))
    return _result(True, "pptx", slides, pages_total=len(prs.slides._sldIdLst))


def _xls(data):
    """xlrd for the legacy binary workbook; an HTML table saved as .xls (SL / CNET / bank exports) is read as text."""
    head = data[:512].lstrip().lower()
    if head.startswith(b"<html") or head.startswith(b"<!doctype html") or b"<table" in head[:200]:
        return _result(True, "xls-html", [_HTML_TAG.sub(" ", _decode(data))])
    import xlrd
    wb = xlrd.open_workbook(file_contents=data, on_demand=True)
    sheets = []
    for si in range(min(wb.nsheets, MAX_PAGES)):
        ws = wb.sheet_by_index(si)
        rows = ["[sheet] " + ws.name]
        for r in range(ws.nrows):
            cells = ["" if ws.cell_value(r, c) == "" else str(ws.cell_value(r, c)) for c in range(ws.ncols)]
            if any(c.strip() for c in cells):
                rows.append("\t".join(cells).rstrip())
            if sum(len(x) for x in rows) > MAX_CHARS:
                break
        sheets.append("\n".join(rows))
    return _result(True, "xls", sheets)


def _doc(data, ext):
    """Legacy Word / OpenDocument through macOS textutil (the only native reader for the binary .doc)."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("textutil"):
        return _result(False, "doc", [], "no reader for .%s on this machine (textutil missing)" % ext)
    with tempfile.TemporaryDirectory(prefix="pca-extract-") as td:
        src = os.path.join(td, "source." + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", src], capture_output=True, timeout=60, check=False)
    if r.returncode != 0:
        return _result(False, "doc", [], "textutil: %s" % (r.stderr or b"")[:200].decode("utf-8", "replace"))
    return _result(True, "textutil", [r.stdout.decode("utf-8", "replace")])


_HTML_TAG = re.compile(r"<[^>]+>")


# ------------------------------------------------------------------------------------------- text
def _decode(data):
    encs = ["utf-8-sig", "cp1252", "latin-1"]
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encs.insert(0, "utf-16")
    for enc in encs:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _txt(data):
    return _result(True, "txt", [_decode(data)])


_RTF_CTRL = re.compile(r"\\[a-z]+-?\d* ?|[{}]|\\'[0-9a-f]{2}")


def _rtf(data):
    return _result(True, "rtf", [_RTF_CTRL.sub("", _decode(data))])


# ------------------------------------------------------------------------------------------- msg
def _msg(data):
    import extract_msg
    m = extract_msg.openMsg(io.BytesIO(data))
    try:
        head = "\n".join(x for x in ("Subject: %s" % (m.subject or ""), "From: %s" % (m.sender or ""), "To: %s" % (m.to or ""),
                                     "Date: %s" % (m.date or "")) if x)
        body = m.body or ""
        atts = [a.longFilename or a.shortFilename or "" for a in (m.attachments or [])]
        tail = ("\nAttachments: " + ", ".join(a for a in atts if a)) if atts else ""
        return _result(True, "msg", [head + "\n\n" + body + tail])
    finally:
        try:
            m.close()
        except Exception:  # noqa
            pass
