from django.urls import path

from . import views

app_name = "safety"

urlpatterns = [
    path("report/", views.report, name="report"),
    path("me/blocked/", views.blocked, name="blocked"),
    path("u/<slug:handle>/block/", views.block, name="block"),
    path("u/<slug:handle>/unblock/", views.unblock, name="unblock"),
]
