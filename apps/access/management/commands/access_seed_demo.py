"""Create demo accounts for each role for testing (manage.py access_seed_demo)."""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.access.models import Account, RoleAssignment

DEMO = [
    ("demo-exec@pca.local", "Demo Executive", "executive", None),
    ("demo-dm070@pca.local", "Demo DM (070)", "division_manager", ["070"]),
    ("demo-finance@pca.local", "Demo Finance", "finance", None),
    ("demo-pm@pca.local", "Demo PM", "project_manager", None),
    ("demo-estimator@pca.local", "Demo Estimator", "estimator", None),
    ("demo-hradmin@pca.local", "Demo HR Admin", "permission_admin", None),
    ("demo-sales@pca.local", "Demo Sales (010)", "sales", None),
    ("demo-norole@pca.local", "Demo No-Role", None, None),
]


class Command(BaseCommand):
    def handle(self, *args, **o):
        for email, name, role, divs in DEMO:
            user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
            user.set_unusable_password(); user.save()
            acct, _ = Account.objects.get_or_create(email=email, defaults={"display_name": name, "user": user})
            if acct.user_id is None:
                acct.user = user; acct.save(update_fields=["user"])
            if role:
                RoleAssignment.objects.get_or_create(account=acct, role=role, defaults={"division_codes": divs})
            self.stdout.write("ok %s (%s)" % (email, role or "no role"))
