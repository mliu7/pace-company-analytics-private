"""The network share ("P:", \\PACE-FPS3\projects) — read-only walker over the SMB mount (SharePoint spec §13.3).

Rules: walk only the configured root (`settings.SHARE_MOUNT`, overridable with `PCA_SHARE_ROOT` or an explicit
`root=` for a fixture tree), skip the exclude list (`settings.SHARE_EXCLUDE_DIRS`, case-insensitive, at any depth),
never follow a symlink (and never leave the root through one), open files read-only, cap the bytes read for
extraction (`settings.SHARE_MAX_TEXT_BYTES`). There is no write function in this module; `open_read()` refuses
any mode that could write. Nothing is ever written to the share.

GAP-1 (build log): from this session the real mount answers "Operation not permitted" (macOS network-volume
privacy), so `health()` reports it and the walk is skipped; the fixture tree under tests/fixtures/share_root/ stands
in until Owner grants the permission.
"""

import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

# Windows users open the share through its UNC; macOS through smb:// (the desktop's own credentials, never PCA's).
SHARE_UNC_ROOT = getattr(settings, "SHARE_UNC_ROOT", r"\\PACE-FPS3\projects")
SHARE_SMB_URL = getattr(settings, "SHARE_SMB_URL", "smb://pace-fps3.pace-systems.com/projects")
SHARE_NAME = "P: drive"
# Folders the client never walks whatever settings say: personal data has no place in a company-wide index.
ALWAYS_EXCLUDE = ("Resumes", "Security_Backups")


class WriteAttemptError(Exception):
    """Raised before any file could be opened for writing."""


class ShareUnavailable(Exception):
    """The mount is missing or unreadable (GAP-1) — the caller records it and moves on."""


def root(override=None):
    """The directory the walk starts from: an explicit override, then PCA_SHARE_ROOT, then settings.SHARE_MOUNT."""
    p = override or os.environ.get("PCA_SHARE_ROOT") or settings.SHARE_MOUNT
    return Path(p)


def excludes():
    return {e.lower() for e in tuple(getattr(settings, "SHARE_EXCLUDE_DIRS", ())) + ALWAYS_EXCLUDE}


def max_text_bytes():
    return int(getattr(settings, "SHARE_MAX_TEXT_BYTES", 50 * 1024 * 1024))


def health(override=None):
    """{'ok', 'root', 'exists', 'readable', 'error'} — never raises."""
    r = root(override)
    out = {"ok": False, "root": str(r), "exists": False, "readable": False, "error": ""}
    try:
        out["exists"] = r.is_dir()
        if not out["exists"]:
            out["error"] = "mount not present"
            return out
        with os.scandir(r) as it:                 # a real read: the macOS privacy block fails here, not on is_dir()
            next(iter(it), None)
        out["readable"] = True
        out["ok"] = True
    except PermissionError as e:
        out["error"] = "permission denied (macOS network-volume access not granted?): %s" % e
    except OSError as e:
        out["error"] = str(e)
    return out


def _inside(path, base):
    try:
        Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(base)))
        return True
    except ValueError:
        return False


def _mtime(st):
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)


def walk(override=None, max_depth=64, on_error=None, start_rel="", dirs_only=False):
    """Yield {'rel', 'name', 'is_dir', 'size', 'mtime', 'depth', 'parent_rel'} for every folder and file under the root
    (or under `start_rel`, a sub-folder — rel paths and depth stay relative to the root), depth-first, folders before
    their contents. Excluded directory names are skipped at any depth; symlinks are never followed (a symlinked file
    or directory is skipped, so nothing outside the mount is ever read). Unreadable directories are reported through
    `on_error(rel, exc)` and skipped. `dirs_only` lists the directory tree without stat-ing files (cheap change
    detection over SMB: a directory's mtime moves when an entry is added, removed or renamed inside it)."""
    base = root(override)
    if not base.is_dir():
        raise ShareUnavailable("share root %s is not a directory" % base)
    ex = excludes()
    start_rel = (start_rel or "").strip("/")
    if start_rel:
        if any(part.lower() in ex for part in start_rel.split("/")):
            return
        start = resolve(start_rel, override)
        if not start.is_dir():
            raise ShareUnavailable("share folder %s is not a directory" % start_rel)
        stack = [(start, start_rel, start_rel.count("/") + 1)]
    else:
        stack = [(base, "", 0)]
    while stack:
        d, rel, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError as e:
            if on_error:
                on_error(rel, e)
            if rel == start_rel:
                raise ShareUnavailable("share folder %r unreadable: %s" % (rel or base, e))
            continue
        entries.sort(key=lambda e: e.name.lower())
        subdirs = []
        for e in entries:
            try:
                if e.is_symlink():
                    continue                                                  # never follow a link, in or out of the mount
                if dirs_only and not e.is_dir(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError as err:
                if on_error:
                    on_error(rel + "/" + e.name if rel else e.name, err)
                continue
            child_rel = (rel + "/" + e.name) if rel else e.name
            if stat.S_ISDIR(st.st_mode):
                if e.name.lower() in ex or e.name.startswith("."):
                    continue
                # no realpath check per directory: symlinks are skipped above, so a real directory reached from the
                # root is inside it (realpath cost 5+ lstats per folder over SMB — it halved the walk's speed)
                yield {"rel": child_rel, "name": e.name, "is_dir": True, "size": 0, "mtime": _mtime(st), "depth": depth + 1, "parent_rel": rel}
                if depth + 1 < max_depth:
                    subdirs.append((Path(e.path), child_rel, depth + 1))
            elif stat.S_ISREG(st.st_mode):
                if e.name.startswith(".") or e.name.lower() in ("thumbs.db", "desktop.ini"):
                    continue
                yield {"rel": child_rel, "name": e.name, "is_dir": False, "size": st.st_size, "mtime": _mtime(st), "depth": depth + 1, "parent_rel": rel}
        for sd in reversed(subdirs):
            stack.append(sd)


def resolve(rel, override=None):
    """Absolute path of a relative share path, refusing anything that escapes the root or crosses a symlink."""
    base = root(override)
    p = base / rel
    if not _inside(p, base):
        raise PermissionError("path %r leaves the share root" % rel)
    parts = Path(rel).parts
    cur = base
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise PermissionError("path %r crosses a symlink" % rel)
    return p


def open_read(rel, mode="rb", override=None):
    """Open a share file read-only. Any mode that could write raises WriteAttemptError before the open."""
    if any(ch in mode for ch in "wa+x"):
        raise WriteAttemptError("share_client refuses write mode %r" % mode)
    return open(resolve(rel, override), mode)


def read_bytes(rel, max_bytes=None, override=None):
    """The file's bytes, or None when it is larger than the cap (extraction skips it)."""
    p = resolve(rel, override)
    cap = max_bytes or max_text_bytes()
    if p.stat().st_size > cap:
        return None
    with open_read(rel, "rb", override) as f:
        return f.read()


def signature(size, mtime):
    """Cheap change signature for incremental walks (no content hash: hashing 100k files over SMB is not a refresh)."""
    return hashlib.sha1(("%s|%s" % (size, mtime.isoformat() if mtime else "")).encode()).hexdigest()


def item_key(rel):
    """The share row's item_id: the relative path itself up to 255 characters; longer paths keep their first 230
    characters plus a hash of the whole path, so two long names that share a prefix never collide (2026-09-10: a
    proposal folder under McDonagh Construction ran past 255 characters and its .pdf / .docx twins collided)."""
    rel = rel or ""
    if len(rel) <= 255:
        return rel
    import hashlib
    return rel[:230] + "#" + hashlib.sha1(rel.encode("utf-8", "surrogateescape")).hexdigest()[:24]


def unc_path(rel):
    return SHARE_UNC_ROOT.rstrip("\\") + "\\" + rel.replace("/", "\\") if rel else SHARE_UNC_ROOT


def smb_url(rel):
    from urllib.parse import quote
    return SHARE_SMB_URL.rstrip("/") + "/" + quote(rel) if rel else SHARE_SMB_URL
