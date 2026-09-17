from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("messages/", views.inbox, name="inbox"),
    path("messages/<int:pk>/", views.thread, name="thread"),
    path("u/<slug:handle>/message/", views.start, name="start"),
]
