from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("messages/", views.inbox, name="inbox"),
    path("messages/<int:pk>/", views.thread, name="thread"),
    path("u/<slug:handle>/message/", views.start, name="start"),
    path("messages/<int:pk>/share-number/", views.share_number, name="share_number"),
    path("messages/<int:pk>/working-together/",
         views.start_placement, name="start_placement"),

    # `kind` rather than two routes, so one template tag serves both card
    # types and the view cannot be reached for a model it does not know.
    path("interested/<str:kind>/<uuid:uuid>/", views.interested, name="interested"),
]
