from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

admin_url = getattr(settings, "ADMIN_URL", "admin/")

urlpatterns = [
    path(admin_url, admin.site.urls),
    path("", core_views.home, name="home"),
    path("", include("apps.accounts.urls")),
    path("cars/", include("apps.listings.urls")),
    path("geo/", include("apps.geo.urls")),
    path("healthz/", core_views.healthz, name="healthz"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
