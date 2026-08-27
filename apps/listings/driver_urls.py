"""
The `/drivers/` half of the listings app.

Two URL modules rather than one, because the two sides of the marketplace get
two namespaces — `listings:` for cars, `drivers:` for people — and a template
that says `drivers:detail` is telling you which one it means.
"""
from django.urls import path

from . import views

app_name = "drivers"

urlpatterns = [
    path("", views.driver_browse, name="browse"),
    path("new/", views.driver_create, name="create"),
    path("ratings/", views.ratings, name="ratings"),
    path("ratings/<int:pk>/delete/", views.rating_delete, name="rating_delete"),
    path("<uuid:uuid>/", views.driver_detail, name="detail"),
    path("<uuid:uuid>/edit/", views.driver_edit, name="edit"),
    path("<uuid:uuid>/status/", views.driver_set_status, name="set_status"),
]
