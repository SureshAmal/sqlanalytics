from django.urls import path

from analytics.views import ReportQueryView

urlpatterns = [
    path("reports/query/", ReportQueryView.as_view(), name="report-query"),
]
