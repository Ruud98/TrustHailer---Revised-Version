from django.urls import path

from . import views

app_name = "feed"

urlpatterns = [
    path("", views.feed, name="feed"),
    path("new/", views.create, name="create"),
    path("<uuid:uuid>/", views.detail, name="detail"),
    path("<uuid:uuid>/comment/", views.comment, name="comment"),
    path("<uuid:uuid>/react/", views.react, name="react"),
    path("comments/<int:pk>/react/", views.react_comment, name="react_comment"),
    path("<uuid:uuid>/delete/", views.delete, name="delete"),
    path("comments/<int:pk>/delete/", views.delete_comment, name="delete_comment"),
]
