from django.urls import path, register_converter
from django.contrib.auth import views as auth
from core import views, api, plugin_views

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
    path("login/", auth.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/change/", auth.PasswordChangeView.as_view(), name="password_change"),
    path("password/change/done/", auth.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("", views.dashboard, name="dashboard"),
    path("projects/", views.projects, name="projects"),
    path("projects/<pid:pk>/", views.project_detail, name="project-detail"),
    path("projects/<pid:pk>/edit/", views.project_edit, name="project-edit"),
    path("projects/<pid:pk>/transition/", views.transition, name="transition"),
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
    path("time/", views.timesheets, name="timesheets"),
    path("assistant/", views.assistant, name="assistant"),
    path("settings/", views.settings_page, name="settings"),
    path("audit/", views.audit_page, name="audit"),
    path("import/<str:kind>/", views.import_data, name="import-data"),
    path("export/<str:kind>/", views.export_data, name="export-data"),
    path("documents/<pid:pk>/evidence/", views.upload_evidence, name="upload-evidence"),
    path("evidence/<pid:pk>/download/", views.download_evidence, name="download-evidence"),
]
