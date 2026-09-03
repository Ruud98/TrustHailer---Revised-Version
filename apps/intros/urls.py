from django.urls import path

from . import views

app_name = "intros"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("new/", views.create, name="create"),
    path("<uuid:uuid>/", views.detail, name="detail"),
    path("<uuid:uuid>/approve/", views.approve, name="approve"),
    path("<uuid:uuid>/decline/", views.decline, name="decline"),
    path("<uuid:uuid>/withdraw/", views.withdraw, name="withdraw"),
]
