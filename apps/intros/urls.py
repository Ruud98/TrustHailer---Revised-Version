from django.urls import path

from . import views

app_name = "intros"

# No "new/" route any more. Introductions are no longer how a conversation
# starts — "I am interested" opens a thread instead, see
# `apps.messaging.services.express_interest`. Everything below stays so the
# requests already in the database can still be read and answered.
urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<uuid:uuid>/", views.detail, name="detail"),
    path("<uuid:uuid>/approve/", views.approve, name="approve"),
    path("<uuid:uuid>/decline/", views.decline, name="decline"),
    path("<uuid:uuid>/withdraw/", views.withdraw, name="withdraw"),
]
