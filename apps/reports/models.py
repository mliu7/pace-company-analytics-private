"""Shared reports use explicit audiences; possession of a URL is never a grant."""

import uuid
from django.db import models
from django.db.models import Q


class ReportGroup(models.Model):
    name = models.CharField(max_length=100, unique=True)
    members = models.ManyToManyField(
        "access.Account", related_name="report_groups", blank=True
    )
    can_create_reports = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ReportQuerySet(models.QuerySet):
    def visible_to(self, account):
        if not account or account.status != "active":
            return self.none()
        if account.is_superadmin:
            return self.all()
        return self.filter(
            Q(owner=account) | Q(readers=account) | Q(groups__members=account)
        ).distinct()


class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=160)
    summary = models.TextField(blank=True)
    owner = models.ForeignKey(
        "access.Account", on_delete=models.PROTECT, related_name="created_reports"
    )
    readers = models.ManyToManyField(
        "access.Account", related_name="readable_reports", blank=True
    )
    groups = models.ManyToManyField(ReportGroup, related_name="reports", blank=True)
    storage_name = models.CharField(max_length=80)
    content_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ReportQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at"]

    def can_manage(self, account):
        return bool(
            account
            and account.status == "active"
            and (account.is_superadmin or account.pk == self.owner_id)
        )

    def audience(self):
        # Include the owner's and administrators' implicit access: there is no
        # misleading "only these readers" claim while an administrator can see it.
        from apps.access.models import Account

        return (
            Account.objects.filter(status="active")
            .filter(
                Q(pk=self.owner_id)
                | Q(is_superadmin=True)
                | Q(readable_reports=self)
                | Q(report_groups__reports=self)
            )
            .distinct()
            .order_by("display_name")
        )
