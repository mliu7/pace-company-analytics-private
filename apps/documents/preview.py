"""In-app previews for the office formats a browser cannot show by itself (SharePoint spec §7.2 "previews").

Word / RTF / OpenDocument → HTML through macOS `textutil` (native, handles the legacy binary .doc as well as .docx);
Excel → HTML tables through openpyxl (every sheet, capped); CSV → a table. Anything QuickLook can draw (.pptx, .xls,
.vsdx, .heic, .dwg with a QuickLook plug-in …) falls back to a first-page PNG through `qlmanage -t`. Everything is
read-only, works on the cached copy of the file, and is cached again next to it (`views._cache_path`). The HTML is
served from a sandboxed frame (no scripts, opaque origin) — see `views.document_html`.
"""

import csv
import html
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

TEXTUTIL_EXTS = ("doc", "docx", "rtf", "odt", "wordml", "webarchive")
SHEET_EXTS = ("xlsx", "xlsm")
XLS_EXTS = ("xls",)                 # legacy Excel through xlrd
PPTX_EXTS = ("pptx",)               # python-pptx: slide titles, text frames and tables (no pictures)
CSV_EXTS = ("csv", "tsv")
# QuickLook only for Apple-native generators: the Office ones spawn PowerPoint / Excel and hang (a .pptx took > 60 s even
# from a local copy on 2026-09-10), so pptx / xls go through the Python readers above instead.
QUICKLOOK_EXTS = ("heic", "key", "pages", "numbers")
MAX_SHEET_ROWS = 400
MAX_SHEET_COLS = 60
MAX_SHEETS = 12
MAX_SLIDES = 80
CONVERT_TIMEOUT_S = 40
QUICKLOOK_TIMEOUT_S = 15

_SCRIPT = re.compile(r"<\s*(script|iframe|object|embed|link|meta\s+http-equiv)[^>]*>.*?<\s*/\s*\1\s*>|<\s*(script|iframe|object|embed|link|meta)[^>]*/?>", re.I | re.S)
_ON_ATTR = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_JS_HREF = re.compile(r"(href|src)\s*=\s*([\"']?)\s*javascript:[^\"'>\s]*\2", re.I)

FRAME_CSS = """<style>
html { color-scheme: light; }
body { margin: 18px 22px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; font-size: 13px; color: #1b2430; background: #fff; }
table { border-collapse: collapse; }
table.sheet td, table.sheet th { border: 1px solid #d9dee6; padding: 2px 6px; font-size: 12px; white-space: nowrap; max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
table.sheet th { background: #f2f5f9; font-weight: 600; position: sticky; top: 0; }
table.sheet td.num { text-align: right; font-variant-numeric: tabular-nums; }
h3.sheet { margin: 18px 0 6px; font-size: 13px; color: #5d6b7c; }
.note { color: #5d6b7c; font-size: 12px; margin-bottom: 8px; }
img.page { max-width: 100%; height: auto; box-shadow: 0 1px 4px rgba(0,0,0,.2); }
section.slide { border-top: 1px solid #d9dee6; padding: 6px 0 10px; } section.slide ul { margin: 4px 0 8px 16px; }
</style>"""


def kind(ext):
    """'html' | 'image' | None — what preview this extension can get on this machine."""
    e = (ext or "").lower()
    if e in TEXTUTIL_EXTS and shutil.which("textutil"):
        return "html"
    if e in SHEET_EXTS or e in CSV_EXTS or e in XLS_EXTS or e in PPTX_EXTS:
        return "html"
    if e in QUICKLOOK_EXTS and shutil.which("qlmanage"):
        return "image"
    return None


def sanitize(markup):
    """Strip scripts, frames, external resources and inline handlers from converter output; keep styles and layout.
    Belt and braces: the frame that shows it is sandboxed as well."""
    s = _SCRIPT.sub("", markup or "")
    s = _ON_ATTR.sub("", s)
    s = _JS_HREF.sub(r"\1=\2#\2", s)
    return s


class _Failed:
    returncode, stdout, stderr = 1, b"", b"timed out"


def _run(cmd, timeout=CONVERT_TIMEOUT_S):
    """A converter that hangs (QuickLook over SMB has) must not hang the request: killed at the timeout."""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("converter %s failed: %s", cmd[0], e)
        return _Failed()


def word_to_html(src_path, ext):
    """textutil: .doc / .docx / .rtf / .odt -> HTML (body + the converter's own style block). None when it fails."""
    if not shutil.which("textutil"):
        return None
    with tempfile.TemporaryDirectory(prefix="pca-preview-") as td:
        # textutil picks the reader from the extension: give the cached copy its real name
        link = Path(td) / ("source." + ext)
        try:
            os.symlink(src_path, link)
        except OSError:
            shutil.copyfile(src_path, link)
        r = _run(["textutil", "-convert", "html", "-stdout", str(link)])
    if r.returncode != 0 or not r.stdout:
        log.warning("textutil failed for %s: %s", src_path, (r.stderr or b"")[:200])
        return None
    out = r.stdout.decode("utf-8", "replace")
    m = re.search(r"<body[^>]*>(.*)</body>", out, re.S | re.I)
    body = m.group(1) if m else out
    style = "".join(re.findall(r"<style[^>]*>.*?</style>", out, re.S | re.I))
    return sanitize(style + body)


def _cell(v):
    if v is None:
        return "", ""
    if isinstance(v, (int, float)):
        return ("%s" % v) if isinstance(v, int) else ("%.6g" % v if abs(v) >= 1e6 or abs(v) < 1e-3 else ("%.2f" % v).rstrip("0").rstrip(".")), "num"
    if hasattr(v, "isoformat"):
        return v.isoformat()[:19].replace("T", " "), ""
    return str(v), ""


def xlsx_raw_to_html(src_path):
    """Fallback for workbooks openpyxl refuses (a malformed styles part is enough): read the sheet XML straight out of
    the zip — shared strings, inline strings, numbers — first sheets only, same caps."""
    import zipfile
    import xml.etree.ElementTree as ET
    NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    try:
        z = zipfile.ZipFile(src_path)
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                strings.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        names = {}
        try:
            wbx = ET.fromstring(z.read("xl/workbook.xml"))
            rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
            for sh in wbx.find("m:sheets", NS):
                tgt = rels.get(sh.get("{%s}id" % NS["r"]), "")
                names["xl/" + tgt.lstrip("/").replace("xl/", "", 1) if not tgt.startswith("xl/") else tgt] = sh.get("name")
        except Exception:  # noqa
            pass
        sheets = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    except Exception as e:  # noqa
        log.warning("raw xlsx read failed for %s: %s", src_path, e)
        return None
    parts = []
    for si, name in enumerate(sheets[:MAX_SHEETS]):
        try:
            root = ET.fromstring(z.read(name))
        except Exception:  # noqa
            continue
        rows = []
        for r, row in enumerate(root.iter("{%s}row" % NS["m"])):
            if r >= MAX_SHEET_ROWS:
                rows.append('<tr><td colspan="%d" class="note">… truncated at %d rows</td></tr>' % (MAX_SHEET_COLS, MAX_SHEET_ROWS))
                break
            cells, last_col = [], -1
            for c in row.findall("m:c", NS):
                ref = re.match(r"([A-Z]+)", c.get("r") or "")
                col = 0
                if ref:
                    for ch in ref.group(1):
                        col = col * 26 + (ord(ch) - 64)
                    col -= 1
                while last_col + 1 < col and len(cells) < MAX_SHEET_COLS:
                    cells.append("<td></td>"); last_col += 1
                if len(cells) >= MAX_SHEET_COLS:
                    break
                t, v = c.get("t"), c.find("m:v", NS)
                val = ""
                if t == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(strings):
                    val, cls = strings[int(v.text)], ""
                elif t == "inlineStr":
                    val, cls = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"])), ""
                elif v is not None and v.text is not None:
                    try:
                        num = float(v.text)
                        val, cls = _cell(int(num) if num.is_integer() else num)
                    except ValueError:
                        val, cls = v.text, ""
                else:
                    val, cls = "", ""
                cells.append('<td%s>%s</td>' % (' class="num"' if cls else "", html.escape(val)))
                last_col = col
            if any(x != "<td></td>" for x in cells):
                rows.append("<tr>" + "".join(cells) + "</tr>")
        if rows:
            parts.append('<h3 class="sheet">%s</h3><table class="sheet"><tbody>%s</tbody></table>' % (html.escape(names.get(name) or "Sheet %d" % (si + 1)), "".join(rows)))
    return "".join(parts) if parts else '<div class="note">The workbook has no cell values to show.</div>'


def sheet_to_html(src_path):
    """openpyxl: every sheet as a table (values as last saved, formulas shown by their cached result), capped. A
    workbook openpyxl cannot open (malformed styles are common from exporters) falls back to the raw sheet XML."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(src_path, read_only=True, data_only=True)
    except Exception as e:  # noqa
        log.warning("openpyxl failed for %s (%s) — reading the sheet XML directly", src_path, str(e)[:120])
        return xlsx_raw_to_html(src_path)
    parts = []
    for i, ws in enumerate(wb.worksheets[:MAX_SHEETS]):
        rows = []
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= MAX_SHEET_ROWS:
                rows.append('<tr><td colspan="%d" class="note">… truncated at %d rows</td></tr>' % (MAX_SHEET_COLS, MAX_SHEET_ROWS))
                break
            cells = []
            for v in row[:MAX_SHEET_COLS]:
                text, cls = _cell(v)
                cells.append('<td%s>%s</td>' % (' class="num"' if cls else "", html.escape(text)))
            if any(c != "<td></td>" for c in cells):
                rows.append("<tr>" + "".join(cells) + "</tr>")
        if not rows:
            continue
        head = "<tr>" + "".join("<th>%s</th>" % _col(c) for c in range(min(ws.max_column or 1, MAX_SHEET_COLS))) + "</tr>"
        parts.append('<h3 class="sheet">%s</h3><table class="sheet"><thead>%s</thead><tbody>%s</tbody></table>' % (html.escape(ws.title), head, "".join(rows)))
    if len(wb.worksheets) > MAX_SHEETS:
        parts.append('<div class="note">%d more sheets not shown</div>' % (len(wb.worksheets) - MAX_SHEETS))
    return "".join(parts) if parts else '<div class="note">The workbook has no cell values to show.</div>'


def _col(i):
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _looks_like_html(src_path):
    head = Path(src_path).read_bytes()[:512].lstrip().lower()
    return head.startswith(b"<html") or head.startswith(b"<!doctype html") or b"<table" in head[:200]


def html_file_to_html(src_path):
    """Many '.xls' exports (SL, CNET, banks) are HTML tables with an Excel extension: show them as the HTML they are."""
    raw = Path(src_path).read_bytes()[:4_000_000]
    for enc in ("utf-8", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    m = re.search(r"<body[^>]*>(.*)</body>", text, re.S | re.I)
    body = m.group(1) if m else text
    return sanitize(body).replace("<table", '<table class="sheet"')


def xls_to_html(src_path):
    """xlrd: legacy .xls sheets as tables, same caps as the xlsx path. An HTML file wearing .xls is shown as HTML."""
    if _looks_like_html(src_path):
        return html_file_to_html(src_path)
    try:
        import xlrd
        wb = xlrd.open_workbook(src_path, on_demand=True)
    except Exception as e:  # noqa
        log.warning("xlrd failed for %s: %s", src_path, e)
        return None
    parts = []
    for si in range(min(wb.nsheets, MAX_SHEETS)):
        ws = wb.sheet_by_index(si)
        rows = []
        for r in range(min(ws.nrows, MAX_SHEET_ROWS)):
            cells = []
            for c in range(min(ws.ncols, MAX_SHEET_COLS)):
                cell = ws.cell(r, c)
                v = cell.value
                if cell.ctype == 3:      # XL_CELL_DATE
                    try:
                        v = xlrd.xldate.xldate_as_datetime(v, wb.datemode)
                    except Exception:  # noqa
                        pass
                elif cell.ctype == 2 and float(v).is_integer():
                    v = int(v)
                text, cls = _cell(v if v != "" else None)
                cells.append('<td%s>%s</td>' % (' class="num"' if cls else "", html.escape(text)))
            if any(c != "<td></td>" for c in cells):
                rows.append("<tr>" + "".join(cells) + "</tr>")
        if ws.nrows > MAX_SHEET_ROWS:
            rows.append('<tr><td colspan="%d" class="note">… truncated at %d rows</td></tr>' % (MAX_SHEET_COLS, MAX_SHEET_ROWS))
        if rows:
            head = "<tr>" + "".join("<th>%s</th>" % _col(c) for c in range(min(ws.ncols or 1, MAX_SHEET_COLS))) + "</tr>"
            parts.append('<h3 class="sheet">%s</h3><table class="sheet"><thead>%s</thead><tbody>%s</tbody></table>' % (html.escape(ws.name), head, "".join(rows)))
    return "".join(parts) if parts else '<div class="note">The workbook has no cell values to show.</div>'


def pptx_to_html(src_path):
    """python-pptx: one block per slide — title, text frames (paragraph by paragraph, bullets kept), tables; pictures
    and layout are not reproduced."""
    try:
        from pptx import Presentation
        from pptx.util import Emu  # noqa: F401 - import check
        prs = Presentation(src_path)
    except Exception as e:  # noqa
        log.warning("python-pptx failed for %s: %s", src_path, e)
        return None
    parts = []
    for n, slide in enumerate(prs.slides, 1):
        if n > MAX_SLIDES:
            parts.append('<div class="note">%d more slides not shown</div>' % (len(prs.slides._sldIdLst) - MAX_SLIDES))
            break
        blocks = []
        title = ""
        try:
            if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
                title = slide.shapes.title.text_frame.text.strip()
        except Exception:  # noqa
            title = ""
        for shape in slide.shapes:
            try:
                if shape.has_text_frame and shape != slide.shapes.title:
                    paras = [pg for pg in shape.text_frame.paragraphs if pg.text.strip()]
                    if paras:
                        blocks.append("<ul>" + "".join('<li style="margin-left:%dpx">%s</li>' % (14 * (pg.level or 0), html.escape(pg.text.strip())) for pg in paras) + "</ul>")
                elif getattr(shape, "has_table", False) and shape.has_table:
                    rows = ["<tr>" + "".join("<td>%s</td>" % html.escape(c.text.strip()) for c in row.cells) + "</tr>" for row in shape.table.rows]
                    blocks.append('<table class="sheet"><tbody>%s</tbody></table>' % "".join(rows))
                elif shape.shape_type == 13:   # MSO_SHAPE_TYPE.PICTURE
                    blocks.append('<div class="note">[picture]</div>')
            except Exception:  # noqa - one odd shape must not sink the slide
                continue
        notes = ""
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:  # noqa
            notes = ""
        parts.append('<section class="slide"><h3 class="sheet">Slide %d%s</h3>%s%s</section>' % (
            n, (" · " + html.escape(title)) if title else "", "".join(blocks) or '<div class="note">(no text)</div>',
            ('<div class="note">Notes: %s</div>' % html.escape(notes)) if notes else ""))
    return "".join(parts) if parts else '<div class="note">The deck has no slides.</div>'


def csv_to_html(src_path):
    raw = Path(src_path).read_bytes()[:2_000_000]
    text = raw.decode("utf-8-sig", "replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for r, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if r >= MAX_SHEET_ROWS:
            rows.append('<tr><td colspan="%d" class="note">… truncated at %d rows</td></tr>' % (MAX_SHEET_COLS, MAX_SHEET_ROWS))
            break
        rows.append("<tr>" + "".join("<td>%s</td>" % html.escape(c) for c in row[:MAX_SHEET_COLS]) + "</tr>")
    return '<table class="sheet"><tbody>%s</tbody></table>' % "".join(rows)


def to_html(src_path, ext):
    """The full HTML document for the sandboxed frame, or None when no converter handles this file."""
    e = (ext or "").lower()
    body = None
    if e in TEXTUTIL_EXTS:
        body = word_to_html(src_path, e)
        note = "Converted from Word by macOS textutil — images and exact page layout are not reproduced; open the file for the original."
    elif e in SHEET_EXTS:
        body = sheet_to_html(src_path)
        note = "Cell values as last saved (formula results, no formatting); the first %d rows × %d columns of each sheet." % (MAX_SHEET_ROWS, MAX_SHEET_COLS)
    elif e in XLS_EXTS:
        body = xls_to_html(src_path)
        note = "Cell values as last saved (no formatting); the first %d rows × %d columns of each sheet." % (MAX_SHEET_ROWS, MAX_SHEET_COLS)
    elif e in PPTX_EXTS:
        body = pptx_to_html(src_path)
        note = "Slide text only — pictures and layout are not reproduced; open the deck for the original."
    elif e in CSV_EXTS:
        body = csv_to_html(src_path)
        note = ""
    if body is None:
        return None
    return "<!doctype html><html><head><meta charset=\"utf-8\">%s</head><body>%s%s</body></html>" % (
        FRAME_CSS, ('<div class="note">%s</div>' % html.escape(note)) if note else "", body)


def to_png(src_path, ext, out_path, size=1600):
    """QuickLook first-page thumbnail (`qlmanage -t`) written to out_path. True on success."""
    if not shutil.which("qlmanage"):
        return False
    with tempfile.TemporaryDirectory(prefix="pca-ql-") as td:
        link = Path(td) / ("source." + (ext or "bin"))
        try:
            os.symlink(src_path, link)
        except OSError:
            shutil.copyfile(src_path, link)
        r = _run(["qlmanage", "-t", "-s", str(size), "-o", td, str(link)], timeout=QUICKLOOK_TIMEOUT_S)
        made = [p for p in Path(td).glob("*.png")]
        if r.returncode != 0 or not made:
            log.warning("qlmanage produced nothing for %s: %s", src_path, (r.stderr or b"")[:200])
            return False
        shutil.move(str(made[0]), out_path)
    return True
