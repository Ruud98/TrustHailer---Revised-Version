from django.urls import path

from . import views

app_name = "placements"

urlpatterns = [
    path("me/placements/", views.mine, name="mine"),
    path("cars/<uuid:uuid>/placed/", views.create, name="create"),
    path("placements/<uuid:uuid>/", views.detail, name="detail"),
    path("placements/<uuid:uuid>/confirm/", views.confirm, name="confirm"),
    path("placements/<uuid:uuid>/end/", views.end, name="end"),
    path("placements/<uuid:uuid>/review/", views.review, name="review"),
    path("u/<slug:handle>/reviews/", views.reviews_for, name="reviews"),
]
