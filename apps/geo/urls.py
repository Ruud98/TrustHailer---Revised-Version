from django.urls import path

from . import views

app_name = "geo"

urlpatterns = [
    path("suburb-options/", views.suburb_options, name="suburb_options"),
    path("suburb-search/", views.suburb_search, name="suburb_search"),
]
