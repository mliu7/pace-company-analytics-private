"""Immutable content-addressed files, outside static/media and outside Git.

There is no public media URL. Every response goes through object authorization.
"""

import hashlib
import os
from pathlib import Path
from django.core.exceptions import ValidationError


def store_upload(upload, root, limit, suffix, signature=None):
    if upload.size > limit:
        raise ValidationError("File is too large.")
    data = upload.read(limit + 1)
    if len(data) > limit or not data:
        raise ValidationError("File is empty or too large.")
    if signature and not data.startswith(signature):
        raise ValidationError("Please upload a valid PDF file.")
    if suffix == ".html":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationError("Report HTML must use UTF-8.")
    digest = hashlib.sha256(data).hexdigest()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = digest + suffix
    try:
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Never trust an existing pathname unless its contents still match.
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValidationError("Stored file failed its integrity check.")
    else:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    return name, digest


def safe_file(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        from django.http import Http404

        raise Http404
    return path
