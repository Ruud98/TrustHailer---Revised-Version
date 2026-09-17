from django.urls import path

from . import views

app_name = "follows"

urlpatterns = [
    path("u/<slug:handle>/follow/", views.toggle, name="toggle"),
    path("u/<slug:handle>/followers/", views.followers, name="followers"),
    path("u/<slug:handle>/following/", views.following, name="following"),
]
