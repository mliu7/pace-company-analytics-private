from django.urls import path

from django.conf import settings
from apps.finance import views_files

from . import views, views_ar, views_billings, views_customer, views_sales, views_sales_pipeline, views_sales_snapshot, views_salestax, views_search

app_name = "dashboard"
urlpatterns = [
    path("", views.command_center, name="command_center"),
    path("cc/jobs/", views.command_center_jobs, name="command_center_jobs"),   # JSON: jobs behind a histogram bar / overlapping scatter dots
    path("projects/", views.project_list, name="project_list"),
    path("projects/snapshot/", views.project_snapshot, name="project_snapshot"),
    path("projects/<str:key>/", views.project_detail, name="project_detail"),
    path("projects/<str:key>/drill/", views.project_account_drill, name="project_account_drill"),
    path("projects/<str:key>/contract-value/", views.project_contract_value, name="project_contract_value"),
    path("projects/<str:key>/entries/", views.project_entries, name="project_entries"),
    path("projects/<str:key>/transactions/", views.project_transactions, name="project_transactions"),   # JSON: the full SL ledger, filtered / sorted / paged
    path("forecast/", views.forecast, name="forecast"),
    path("people/", views.people, name="people"),
    path("people/<str:key>/", views.person_detail, name="person_detail"),
    path("field/", views.field, name="field"),
    path("customers/", views.customers, name="customers"),
    path("customers/<str:cust>/", views.customer_detail, name="customer_detail"),
    path("customers/<str:cust>/payments/", views_customer.customer_payments, name="customer_payments"),   # JSON: paged applications; effective customer_payments rule
    path("data-quality/", views.data_quality, name="data_quality"),
    path("refresh/", views.refresh, name="refresh"),
    path("refresh/status/", views.refresh_status, name="refresh_status"),
    path("finance/daily/", views.finance_daily, name="finance_daily"),
    path("finance/daily/drill/", views.finance_drill, name="finance_drill"),
    path("finance/daily/liabilities/", views.finance_liabilities, name="finance_liabilities"),
    path("finance/daily/assets/", views.finance_assets, name="finance_assets"),
    path("map/", views.project_map, name="project_map"),
    path("map/data/", views.project_map_data, name="project_map_data"),
    path("map/locate/", views.project_map_locate, name="project_map_locate"),
    path("finance/daily/bank/", views.finance_bank_figures, name="finance_bank_figures"),
    path("finance/daily/refresh/", views.finance_refresh, name="finance_refresh"),
    path("finance/ar/", views_ar.finance_ar, name="finance_ar"),
    path("finance/ar/doc/", views_ar.finance_ar_doc, name="finance_ar_doc"),
    path("finance/billings/", views_billings.finance_billings, name="finance_billings"),                 # what was billed, day by day (docs/billings_page_plan.md)
    path("finance/billings/doc/", views_billings.finance_billings_doc, name="finance_billings_doc"),     # JSON: one billing's lines / payments / job context
    path("finance/sales-tax/", views_salestax.finance_salestax, name="finance_salestax"),                 # superadmin only (concealed): docs/sales_tax_page_plan.md
    path("finance/wip/", views.finance_wip, name="finance_wip"),
    path("finance/allocations/", views.finance_allocations, name="finance_allocations"),
    path("finance/pnl/", views.finance_pnl, name="finance_pnl"),
    path("finance/pnl/lines/", views.finance_pnl_lines, name="finance_pnl_lines"),
    path("finance/pnl/<str:div>/<str:period>/", views.finance_pnl_detail, name="finance_pnl_detail"),
    path("finance/payments/", views.finance_payments, name="finance_payments"),
    path("finance/vendors/", views.vendors, name="finance_vendors"),
    path("finance/vendors/<str:vendor_id>/", views.vendor_detail, name="finance_vendor"),
    path("finance/bank/upload/", views_files.bank_upload, name="finance_bank_upload"),
    path("finance/bank/files/<int:pk>/", views_files.bank_file, name="finance_bank_file"),
    path("finance/bank/", views.finance_bank, name="finance_bank"),
    path("finance/bank/reconcile/", views.finance_bank_reconcile, name="finance_bank_reconcile"),
    path("finance/bank/<int:pk>/", views.finance_bank_detail, name="finance_bank_detail"),
    path("sales/010/", views_sales.overview, name="sales010_overview"),
    path("sales/010/snapshot/", views_sales_snapshot.snapshot, name="sales010_snapshot"),
    path("sales/010/pipeline/", views_sales_pipeline.pipeline, name="sales010_pipeline"),
    path("sales/010/pipeline/quote/<str:number>/", views_sales_pipeline.quote_json, name="sales010_quote_json"),
    path("sales/010/orders/", views_sales.orders, name="sales010_orders"),
    path("sales/010/orders/<str:number>/", views_sales.order_detail, name="sales010_order_detail"),
    path("sales/010/products/", views_sales.products, name="sales010_products"),
    path("sales/010/serials/", views_sales.serials, name="sales010_serials"),
    path("sales/010/hygiene/", views_sales.hygiene, name="sales010_hygiene"),
    path("about/", views.about, name="about"),
    path("search/", views_search.search, name="search"),                       # global search results page
    path("search/suggest/", views_search.search_suggest, name="search_suggest"),   # JSON behind the sidebar search box
]

# Extensions are supplied only by an explicitly selected settings module.
if extension := getattr(settings, "PCA_DASHBOARD_URLCONF", ""):
    from importlib import import_module
    urlpatterns += import_module(extension).urlpatterns
