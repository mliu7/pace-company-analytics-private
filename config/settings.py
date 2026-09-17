"""Django settings for Pace Company Analytics.

Only the local PostgreSQL database appears in DATABASES. PTT and Dynamics SL are
read through apps.ingestion.sources with the read-only accounts in .env; they
are deliberately not Django databases (spec v3 §2.2).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    value = os.environ.get(key)
    if value is None:
        if default is None:
            raise RuntimeError("%s is not set in .env" % key)
        return default
    return value.strip().strip('"').strip("'")


SECRET_KEY = env("PCA_SECRET_KEY", "dev-only")
DEBUG = env("PCA_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in env("PCA_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")]
if DEBUG:
    ALLOWED_HOSTS.append("testserver")   # Django test client

# ---- Access control (Access Spec v1) ----
# "dev": auto-login as the local superadmin — permitted ONLY with DEBUG on localhost (enforced in apps.access.apps + middleware).
# "sso": Microsoft Entra OIDC via mozilla-django-oidc; MFA enforced by Entra Conditional Access on the app registration.
# The company profile never discovers or loads local extensions. An explicit
# settings module is required for any separate local application.
PRIVATE_MODE = False
PCA_AUTH_MODE = env("PCA_AUTH_MODE", "dev")
PCA_DB_MODE = "shared"
LOGIN_URL = "/access/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/access/login/"
SESSION_COOKIE_AGE = 43200
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
if PCA_AUTH_MODE == "sso":
    AUTHENTICATION_BACKENDS = ["apps.access.oidc.PCAOIDCBackend"] + AUTHENTICATION_BACKENDS
    OIDC_RP_CLIENT_ID = env("PCA_OIDC_CLIENT_ID")
    OIDC_RP_CLIENT_SECRET = env("PCA_OIDC_CLIENT_SECRET")
    _TENANT = env("PCA_OIDC_TENANT_ID")
    OIDC_OP_AUTHORIZATION_ENDPOINT = "https://login.microsoftonline.com/%s/oauth2/v2.0/authorize" % _TENANT
    OIDC_OP_TOKEN_ENDPOINT = "https://login.microsoftonline.com/%s/oauth2/v2.0/token" % _TENANT
    OIDC_OP_USER_ENDPOINT = "https://graph.microsoft.com/oidc/userinfo"
    OIDC_OP_JWKS_ENDPOINT = "https://login.microsoftonline.com/%s/discovery/v2.0/keys" % _TENANT
    OIDC_RP_SIGN_ALGO = "RS256"
    OIDC_RP_SCOPES = "openid email profile"
    OIDC_CREATE_USER = False   # pre-provisioned accounts only (spec §3); our backend overrides matching

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.ingestion",
    "apps.finance",
    "apps.operations",
    "apps.geo",
    "apps.analytics",
    "apps.dashboard",
    "apps.sales",
    "apps.access",
    "apps.bids",
    "apps.planning",
    "apps.documents",
    "apps.estimating",
    "apps.planner",
    "apps.scheduling",
    "apps.reports",
]
if PCA_AUTH_MODE == "sso":
    INSTALLED_APPS.append("mozilla_django_oidc")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.access.middleware.AccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.dashboard.context_processors.app_context",
                "apps.access.context_processors.access",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# The ONLY database Django manages. Source systems are not listed here on purpose.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("PCA_DB_NAME", "pace_company_analytics"),
        "USER": env("PCA_DB_USER", "pca_app"),
        "PASSWORD": env("PCA_DB_PASSWORD", ""),
        "HOST": env("PCA_DB_HOST", "127.0.0.1"),
        "PORT": env("PCA_DB_PORT", "5433"),
        "CONN_MAX_AGE": 60,
        # The session time zone. Django pins a PostgreSQL session to UTC unless this is set, which made every raw-SQL
        # day cutoff (`sl_created_at::date`, date parameters against timestamptz columns) a midnight-UTC cutoff: a GL
        # batch keyed at 7:30 PM Central counted as the next day (687 postings / $1.8M on 2026-08-27 alone). Every daily
        # cutoff in the app is midnight Central (Owner, 2026-09-08). Stored values are unaffected (timestamptz instants);
        # ORM __date lookups already cast explicitly. docs/02 §15, docs/03 "Conventions".
        "TIME_ZONE": "America/Chicago",
    }
}

# Source connection parameters (NOT Django databases).
PTT_SOURCE = {
    "host": env("PTT_SERVER_IP", ""),
    "port": env("PTT_SERVER_PORT", ""),
    "dbname": env("PTT_SERVER_DATABASE", ""),
    "user": env("PTT_SERVER_USERNAME", ""),
    "password": env("PTT_SERVER_PASSWORD", ""),
    "client_id": 7,
}
SL_SOURCE = {
    "server": env("SQL_SERVER_IP", ""),
    "port": env("SQL_SERVER_PORT", ""),
    "database": env("SQL_SERVER_DATABASE", ""),
    "user": env("SQL_SERVER_USERNAME", ""),
    "password": env("SQL_SERVER_PASSWORD", ""),
}
SOURCE_SQL_DIR = BASE_DIR / "sql" / "source"
# ChannelOnline (CNET) export API — read-only 010 sales ingestion (010 Sales Spec §2.1). Optional in dev.
CNET_SOURCE = {"username": env("CNET_USERNAME", ""), "password": env("CNET_PASSWORD", "")}
# Monthly bank-statement PDFs dropped here are parsed locally and reconciled against SL's cash GL
# (Financial Reports > Bank Reconciliation). Sibling of the repo by default: ../internal_reports/bank
BANK_STATEMENTS_DIR = Path(env("PCA_BANK_STATEMENTS_DIR", str(Path(env("PCA_APP_SUPPORT_DIR", str(Path.home() / "Library" / "Application Support" / "PaceCompanyAnalytics"))) / "bank")))

APP_SUPPORT_DIR = Path(env("PCA_APP_SUPPORT_DIR", str(Path.home() / "Library" / "Application Support" / "PaceCompanyAnalytics")))
LOG_DIR = APP_SUPPORT_DIR / "logs"
BACKUP_DIR = APP_SUPPORT_DIR / "backups"

# Business configuration (spec v3)
MODELLED_DIVISION_CODE = "070"

# SharePoint / Planner reader (Entra app "Pace Secure AI SharePoint Reader", read-only roles) — SharePoint spec §2, §13.
PACE_SHAREPOINT_READER = {
    "client_id": env("PACE_SHAREPOINT_READER_APPLICATION_CLIENT_ID", ""),
    "tenant_id": env("PACE_SHAREPOINT_READER_DIRECTORY_TENANT_ID", ""),
    "client_secret": env("PACE_SHAREPOINT_READER_CLIENT_SECRET_VALUE", ""),
}
SHAREPOINT_PROJECT_PORTAL_SITE = "/sites/ProjectPortal"
# The network share ("P:" = \\PACE-FPS3\projects). Read-only mount; the walk is limited to the roots listed and skips
# the excludes (SharePoint spec §7.1, §13.3). Override with PCA_SHARE_ROOT for a fixture tree.
SHARE_MOUNT = Path(env("PCA_SHARE_ROOT", "/Volumes/projects"))
SHARE_EXCLUDE_DIRS = ("PACE_Dashboard", "HR", "Personnel", "Human Resources", "Genetec", "$RECYCLE.BIN", "System Volume Information")
SHARE_MAX_TEXT_BYTES = 50 * 1024 * 1024
STABILIZATION_DAYS = 45
DORMANT_DAYS = 45
PTT_TIME_ENTRY_OVERLAP_DAYS = 45
SL_TRANSACTION_OVERLAP_DAYS = 30
MODEL_WINDOW_START = "2021-01-01"

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Chicago"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

REPORT_FILES_DIR = Path(env("PCA_REPORT_FILES_DIR", str(APP_SUPPORT_DIR / "reports")))
BANK_UPLOAD_MAX_BYTES = 30 * 1024 * 1024
REPORT_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
DATABASE_ROUTERS = ["config.db_router.WorkspaceRouter"]
