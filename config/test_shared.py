"""Explicit test profile: never inherit private credentials from the local .env."""

import os

os.environ.update(
    PCA_PRIVATE_DB_NAME="",
    PCA_AUTH_MODE="dev",
    PCA_DEBUG="1",
    PCA_DB_USER=os.environ.get("PCA_TEST_ADMIN", os.environ.get("USER", "")),
    PCA_DB_PASSWORD="",
    PCA_DB_NAME="pca_suite_shared",
    PCA_DB_HOST="127.0.0.1",
    PCA_DB_PORT=os.environ.get("PCA_TEST_PORT", "5433"),
)
from .settings import *

PCA_BUSINESS_SETTINGS = {
    "related_parties": {
        "customer": ["REL002"],
        "vendor": ["REL001", "REL002", "REL202"],
        "employee": [
            "REL001",
            "REL002",
            "REL003",
            "REL101",
            "OWNER1",
            "PTT-9901",
            "PTT-9902",
        ],
    },
    "bank_account_gl": {"000-000-000-0": "10250"},
}

PCA_BUSINESS_SETTINGS["bid_fixed_aliases"] = {
    "BIRCHFIELD": "Mike Birchfield",
    "OAKRIDGE": "Mike Oakridge",
    "KESTREL": "Casey Kestrel",
    "AMBERSTONE": "Stephanie Amberstone",
    "REDWOOD": "Jim Redwood",
    "CEDARWELL": "Herb Cedarwell",
    "DRIFTWOOD": "Bruce Driftwood",
    "FERN": "Mike Fernlake",
    "GLEN": "GLENBROOK",
}
