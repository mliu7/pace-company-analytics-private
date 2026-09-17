"""Bids & estimators URLs (SharePoint spec §4.5, §5.3). Names are the access-registry keys."""

from django.urls import path

from . import views_bids, views_estimators, views_snapshot

app_name = "bids"

urlpatterns = [
    path("", views_bids.overview, name="bids_overview"),
    path("snapshot/", views_snapshot.pipeline_snapshot, name="bids_snapshot"),
    path("snapshot/data/", views_snapshot.pipeline_snapshot_json, name="bids_snapshot_json"),
    path("board/", views_bids.board, name="bids_board"),
    path("calendar/", views_bids.calendar, name="bids_calendar"),
    path("calendar/data/", views_bids.calendar_json, name="bids_calendar_json"),
    path("list/", views_bids.bids_list, name="bids_list"),
    path("list/data/", views_bids.bids_list_json, name="bids_list_json"),
    path("export/", views_bids.export, name="bids_export"),
    path("analytics/", views_bids.analytics, name="bids_analytics"),
    path("analytics/data/", views_bids.analytics_json, name="bids_analytics_json"),
    path("refresh/", views_bids.refresh_now, name="bids_refresh"),
    path("alias/", views_bids.alias_set, name="bids_alias_set"),
    path("<int:pk>/", views_bids.bid_detail, name="bid_detail"),
    path("<int:pk>/notes/", views_bids.note_add, name="bid_note_add"),
    path("<int:pk>/followups/", views_bids.followup, name="bid_followup"),
    path("<int:pk>/risks/", views_bids.risk, name="bid_risk"),
    path("estimators/", views_estimators.estimators, name="estimators"),
    path("estimators/<str:key>/", views_estimators.estimator_detail, name="estimator_detail"),
]
