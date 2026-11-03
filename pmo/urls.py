from django.conf import settings
from django.urls import path, register_converter
from django.contrib.auth import views as auth
from core import views, api, plugin_views, notification_views, onboarding_views
from pmo_plugins.billing import views as billing_views
from pmo_plugins.cashbook import views as cash_views
from pmo_plugins.expenses import views as expense_views

class SafeIdConverter:
    regex = r"[1-9][0-9]{0,17}"
    def to_python(self, value): return int(value)
    def to_url(self, value): return str(int(value))
register_converter(SafeIdConverter, "pid")

urlpatterns = [
    path("plugins/", plugin_views.plugin_settings, name="plugin-settings"),
    path("connections/", views.agent_connections, name="agent-connections"),
    path("connections/<pid:pk>/revoke/", views.agent_token_revoke, name="agent-token-revoke"),
    path("api/v1/me", api.MeView.as_view()),
    path("api/v1/projects", api.ProjectsView.as_view()),
    path("api/v1/projects/<pid:pk>", api.ProjectView.as_view()),
    path("api/v1/projects/<pid:pk>/tasks", api.TasksView.as_view()),
    path("api/v1/timesheets", api.TimesheetView.as_view()),
    path("api/v1/expenses", api.ExpensesView.as_view()),
    path("api/v1/tasks/<pid:pk>/status", api.TaskStatusView.as_view()),
    path("health/", views.health, name="health"),
    path("setup/", onboarding_views.setup, name="setup"),
    path("setup/customer/", onboarding_views.setup_customer, name="setup-customer"),
    path("setup/project/", onboarding_views.setup_project, name="setup-project"),
    path("setup/contract/", onboarding_views.setup_contract, name="setup-contract"),
    path("setup/time/", onboarding_views.setup_time, name="setup-time"),
    path("login/", onboarding_views.SetupLoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/change/", auth.PasswordChangeView.as_view(), name="password_change"),
    path("password/change/done/", auth.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("", views.dashboard, name="dashboard"),
    path("projects/", views.projects, name="projects"),
    path("projects/<pid:pk>/", views.project_detail, name="project-detail"),
    path("projects/<pid:pk>/edit/", views.project_edit, name="project-edit"),
    path("projects/<pid:pk>/transition/", views.transition, name="transition"),
    path("projects/<pid:pk>/contract-change/", views.project_contract_change, name="project-contract-change"),
    path("new/<str:kind>/", views.new_object, name="new-object"),
    path("tasks/<pid:pk>/edit/", views.task_edit, name="task-edit"),
    path("tasks/<pid:pk>/status/", views.task_update, name="task-update"),
    path("milestones/<pid:pk>/accept/", views.milestone_accept, name="milestone-accept"),
    path("risks/<pid:pk>/resolve/", views.risk_resolve, name="risk-resolve"),
    path("plans/<pid:pk>/cancel/", views.cancel_plan, name="cancel-plan"),
    path("approvals/", views.approvals, name="approvals"),
    path("review/<str:kind>/<pid:pk>/", views.review, name="review"),
    path("reverse/<str:kind>/<pid:pk>/", views.reverse, name="reverse"),
    path("finance/", views.finance, name="finance"),
    path("billing/", billing_views.billing, name="billing"),
    path("billing/accounts/save/", billing_views.account_create, name="billing-account-create"),
    path("billing/invoices/save/", billing_views.invoice_create, name="billing-invoice-create"),
    path("billing/invoices/<pid:pk>/void/", billing_views.invoice_void, name="billing-invoice-void"),
    path("billing/receipts/save/", billing_views.receipt_create, name="billing-receipt-create"),
    path("billing/receipts/<pid:pk>/refund/", billing_views.receipt_refund, name="billing-receipt-refund"),
    path("expenses/", expense_views.expenses, name="expenses"),
    path("expenses/register/", expense_views.expense_register, name="expense-register"),
    path("expenses/<pid:pk>/confirm/", expense_views.expense_confirm, name="expense-confirm"),
    path("expenses/<pid:pk>/correct/", expense_views.expense_correct, name="expense-correct"),
    path("expenses/<pid:pk>/payment/", expense_views.expense_payment, name="expense-payment"),
    path("cash/", cash_views.cash, name="cash"),
    path("cash/accounts/save/", cash_views.account_create, name="cash-account-create"),
    path("cash/accounts/<pid:pk>/update/", cash_views.account_update, name="cash-account-update"),
    path("time/", views.timesheets, name="timesheets"),
    path("time/week/", views.timesheets_week, name="timesheets-week"),
    path("assistant/", views.assistant, name="assistant"),
    path("reminders/", views.reminders, name="reminders"),
    path("notifications/", notification_views.notification_list, name="notification-list"),
    path("notifications/sync/", notification_views.notification_sync, name="notification-sync"),
    path("notifications/<uuid:pk>/<str:action>/", notification_views.notification_mark,
         name="notification-mark"),
    path("settings/", views.settings_page, name="settings"),
    path("audit/", views.audit_page, name="audit"),
    path("import/<str:kind>/", views.import_data, name="import-data"),
    path("exports/", views.micro_exports, name="micro-exports"),
    path("exports/<str:kind>.csv", views.micro_export_download, name="micro-export-download"),
    path("due/", views.due_list, name="due-list"),
    path("export/<str:kind>/", views.export_data, name="export-data"),
    path("documents/<pid:pk>/evidence/", views.upload_evidence, name="upload-evidence"),
    path("evidence/<pid:pk>/download/", views.download_evidence, name="download-evidence"),
]

# SDK routes use frozen metadata and server-owned callable bindings only.
plugin_entry_point = getattr(settings, "PMO_PLUGIN_ENTRY_POINT", None)
if plugin_entry_point == "business_v1":
    from pmo_plugins.delivery.urls import entry_point
    plugin_entry_point = entry_point()
    settings.PMO_PLUGIN_ENTRY_POINT = plugin_entry_point
if plugin_entry_point == "contracts_costing":
    from pmo_plugins.costing.urls import entry_point
    plugin_entry_point = entry_point()
    settings.PMO_PLUGIN_ENTRY_POINT = plugin_entry_point
if plugin_entry_point == "contracts":
    from pmo_plugins.contracts.urls import entry_point
    plugin_entry_point = entry_point()
    settings.PMO_PLUGIN_ENTRY_POINT = plugin_entry_point
if plugin_entry_point is not None:
    urlpatterns += plugin_entry_point.urlpatterns(plugin_views.plugin_entry)
