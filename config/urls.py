from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views
from apps.listings import views as listing_views
from apps.safety import views as safety_views

admin_url = getattr(settings, "ADMIN_URL", "admin/")

urlpatterns = [
    path(admin_url, admin.site.urls),
    path("", core_views.home, name="home"),
    path("", include("apps.accounts.urls")),
    path("cars/", include("apps.listings.urls")),
    path("drivers/", include("apps.listings.driver_urls")),
    path("requests/", include("apps.intros.urls")),
    path("", include("apps.safety.urls")),
    path("", include("apps.placements.urls")),
    path("", include("apps.follows.urls")),
    path("", include("apps.messaging.urls")),
    path("feed/", include("apps.feed.urls")),
    path("me/notifications/", include("apps.notifications.urls")),
    path("directory/", include("apps.directory.urls")),
    path("geo/", include("apps.geo.urls")),

    # Site-level utilities. Named without a namespace, the same way `home` and
    # `healthz` are: they belong to the site rather than to one app, even where
    # the view that serves them currently lives inside one.
    path("search/", core_views.search, name="search"),
    path("me/saved-searches/", listing_views.saved_searches, name="saved_searches"),
    path("me/saved-searches/new/", listing_views.save_search, name="save_search"),
    path(
        "me/saved-searches/<int:pk>/delete/",
        listing_views.delete_saved_search,
        name="delete_saved_search",
    ),

    path("safety/", safety_views.safety, name="safety"),

    path("healthz/", core_views.healthz, name="healthz"),
]

if settings.DEBUG:
    # Static files are served here rather than by staticfiles' own runserver
    # handler, which sits in front of the middleware chain. See the note in
    # config/settings/dev.py and apps/core/management/commands/runserver.py.
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
