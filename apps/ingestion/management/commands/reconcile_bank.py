"""reconcile_bank — import bank-statement PDFs and reconcile them against SL's cash GL.

    python manage.py reconcile_bank                 # scan settings.BANK_STATEMENTS_DIR, reconcile new statements
    python manage.py reconcile_bank --all           # re-reconcile every statement (after a matching-rule change)
    python manage.py reconcile_bank --reparse       # re-parse every PDF (after a parser change) and reconcile
    python manage.py reconcile_bank path/to/file.pdf [more.pdf]   # import specific files first

SL is read through the guarded client only; results live in finance_bankstatement(+line).
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.analytics.bank_reconciliation import run_bank_reconciliation
from apps.ingestion.bank_statements import import_statement


class Command(BaseCommand):
    help = "Import bank statement PDFs and reconcile them against SL cash accounts."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="*")
        parser.add_argument("--all", action="store_true", help="re-reconcile every imported statement")
        parser.add_argument("--reparse", action="store_true", help="re-parse every PDF in the folder (after a parser change), then reconcile")
        parser.add_argument("--dir", default=None, help="folder to scan (default settings.BANK_STATEMENTS_DIR)")

    def handle(self, *args, **opts):
        for p in opts["paths"]:
            stmt, status = import_statement(p, force=opts["reparse"])
            self.stdout.write("%s: %s%s" % (p, status, (" (%s %s..%s)" % (stmt.gl_account, stmt.period_start, stmt.period_end)) if stmt else ""))
        res = run_bank_reconciliation(folder=opts["dir"], force=opts["all"] or opts["reparse"], reparse=opts["reparse"])
        self.stdout.write("folder %s" % (opts["dir"] or settings.BANK_STATEMENTS_DIR))
        for name, st in res["imported"]:
            self.stdout.write("imported %s: %s" % (name, st))
        for r in res["reconciled"]:
            self.stdout.write("reconciled %s: residual %s" % (r["period_end"], "%.2f" % r["residual"] if r["residual"] is not None else "n/a"))
        for e in res["errors"]:
            self.stderr.write("ERROR %s" % e)
        if not res["imported"] and not res["reconciled"] and not res["errors"]:
            self.stdout.write("nothing new")
