from django.contrib import admin

from .models import PunchDepartment, StatusVocabulary


@admin.register(StatusVocabulary)
class StatusVocabularyAdmin(admin.ModelAdmin):
    list_display = ("key", "order", "bg", "fg", "aliases", "active")
    list_editable = ("order", "bg", "fg", "active")


@admin.register(PunchDepartment)
class PunchDepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "order", "active")
    list_editable = ("order", "active")
