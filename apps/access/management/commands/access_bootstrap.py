"""Create/repair the superadmin account: manage.py access_bootstrap --superadmin owner@example.invalid"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.access.audit import log
from apps.access.models import Account


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--superadmin", required=True, help="email of the superadmin account")
        parser.add_argument("--name", default="Application Owner")

    def handle(self, *args, **o):
        email = o["superadmin"].strip().lower()
        user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
        user.is_staff = user.is_superuser = True
        user.set_unusable_password(); user.save()
        acct, created = Account.objects.get_or_create(email=email, defaults={"display_name": o["name"], "user": user})
        acct.user, acct.is_superadmin, acct.status = user, True, "active"
        acct.save()
        log("superadmin_changed", actor=acct, target=acct, granted=True)
        self.stdout.write("superadmin %s %s" % (email, "created" if created else "ensured"))
