#!/usr/bin/env python3
"""Fail a company checkout containing private artifacts or literal personal config.

Use --tree REF in pre-push/CI to inspect precisely the committed tree. The default
inspects tracked and proposed files without staging them. An optional owner-only
PCA_PRIVACY_DENYLIST can add private names/identifiers without putting them in Git.
Gitleaks is an independent secret scan; this check covers data and private paths.
"""

import argparse
import os
import re
import subprocess
from pathlib import Path

# Keep this boundary generic. A company checkout should reveal neither the
# names nor the contents of an owner's local extension files. Ignore rules alone
# cannot stop `git add -f`, so inspect tracked paths as well as proposed files.
FORBIDDEN = ("private/", "private_data/", ".claude/", ".codex/", "media/", "uploads/")
EMAIL = re.compile(rb"[A-Za-z0-9_.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PRIVATE_IP = re.compile(
    rb"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b"
)
BANK = re.compile(rb"\b\d{3}-\d{3}-\d{3}-\d\b")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tree")
    args = p.parse_args()
    if args.tree:
        paths = (
            subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", "-z", args.tree]
            )
            .decode()
            .split("\0")
        )
    else:
        paths = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
            )
            .decode()
            .split("\0")
        )
    deny = []
    # A local Git setting lets the owner maintain a name/identifier denylist
    # outside source control. Other checkouts and CI still run generic checks.
    configured = subprocess.run(
        ["git", "config", "--get", "pca.privacyDenylist"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    denylist = os.environ.get("PCA_PRIVACY_DENYLIST") or configured
    if denylist:
        deny = [
            x.strip().lower().encode()
            for x in Path(denylist).expanduser().read_text().splitlines()
            if x.strip()
        ]
    findings = []
    for path in set(paths) - {""}:
        if not args.tree and not Path(path).is_file():
            continue
        if (
            path.startswith(FORBIDDEN)
            or path.endswith((".dump", ".bundle", ".backup", ".tar.gz", ".sql.gz", ".sqlite3"))
            or (Path(path).name.startswith(".env") and path != ".env.example")
        ):
            findings.append((path, "private artifact"))
            continue
        data = (
            subprocess.check_output(["git", "show", args.tree + ":" + path])
            if args.tree
            else Path(path).read_bytes()
        )
        if "/vendor/" in path:
            continue  # Preserve required third-party license/author attribution.
        for domain in EMAIL.findall(data):
            if not domain.lower().endswith(
                (
                    b".invalid",
                    b".local",
                    b".example",
                    b"example.com",
                    b"example.org",
                    b"users.noreply.github.com",
                )
            ):
                findings.append((path, "non-placeholder email"))
                break
        if PRIVATE_IP.search(data):
            findings.append((path, "literal internal IP"))
        if any(x != b"000-000-000-0" for x in BANK.findall(data)):
            findings.append((path, "literal bank account"))
        if any(value in data.lower() for value in deny):
            findings.append((path, "owner denylist match"))
    for path, reason in sorted(set(findings)):
        print(path + ": " + reason)
    if findings:
        raise SystemExit(1)
    print("Company privacy boundary check passed.")


if __name__ == "__main__":
    main()
