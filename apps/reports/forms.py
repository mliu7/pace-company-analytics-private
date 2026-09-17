from django import forms
from apps.access.models import Account
from .models import ReportGroup


class AudienceForm(forms.Form):
    readers = forms.ModelMultipleChoiceField(
        Account.objects.filter(status="active"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Individual readers",
    )
    groups = forms.ModelMultipleChoiceField(
        ReportGroup.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Groups (current and future members)",
    )


class ReportForm(AudienceForm):
    title = forms.CharField(max_length=160)
    summary = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    document = forms.FileField(
        label="Self-contained HTML report",
        widget=forms.ClearableFileInput(attrs={"accept": ".html,text/html"}),
    )


class GroupForm(forms.Form):
    name = forms.CharField(max_length=100)
    members = forms.ModelMultipleChoiceField(
        Account.objects.filter(status="active"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    can_create_reports = forms.BooleanField(
        required=False, label="Members may create reports"
    )
