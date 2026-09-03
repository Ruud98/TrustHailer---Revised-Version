from django.urls import path

from . import views

app_name = "directory"

urlpatterns = [
    path("", views.browse, name="browse"),
    path("list-your-business/", views.create, name="create"),
    path("mine/", views.mine, name="mine"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/status/", views.set_status, name="set_status"),
    path("<int:pk>-<slug:slug>/", views.detail, name="detail"),
    # Category comes last: it is the loosest pattern here and would otherwise
    # swallow /list-your-business/ and /mine/ if it were checked first.
    path("<slug:category>/", views.browse, name="browse_category"),
]
