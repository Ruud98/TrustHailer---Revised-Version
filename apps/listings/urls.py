from django.urls import path

from . import views

app_name = "listings"

urlpatterns = [
    path("", views.browse, name="browse"),
    path("new/", views.create, name="create"),
    path("mine/", views.my_listings, name="mine"),

    # Staff only. Above the <uuid:uuid> patterns so the words never read as ids.
    path("import/", views.import_advert, name="import"),
    path("imports/", views.import_queue, name="import_queue"),
    path("<uuid:uuid>/", views.detail, name="detail"),
    path("<uuid:uuid>/edit/", views.edit, name="edit"),
    path("<uuid:uuid>/photos/", views.photos, name="photos"),
    path("<uuid:uuid>/photos/<int:photo_id>/delete/", views.photo_delete, name="photo_delete"),
    path("<uuid:uuid>/photos/<int:photo_id>/primary/", views.photo_primary, name="photo_primary"),
    path("<uuid:uuid>/photos/reorder/", views.photo_reorder, name="photo_reorder"),
    path("<uuid:uuid>/notes/", views.notes, name="notes"),
    path("<uuid:uuid>/notes/<int:note_id>/delete/", views.note_delete,
         name="note_delete"),
    path("<uuid:uuid>/status/", views.set_status, name="set_status"),
    path("<uuid:uuid>/boost/", views.boost, name="boost"),
    path("<uuid:uuid>/claim/", views.claim, name="claim"),
]
