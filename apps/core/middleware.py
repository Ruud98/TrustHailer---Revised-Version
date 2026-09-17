"""
Site-level middleware.

Everything in here is development-only and wired up in config/settings/dev.py,
never in prod.py. If you add something to this module that production needs,
say so in its docstring — the next person will assume otherwise.
"""
from django.conf import settings


class NoStoreStaticMiddleware:
    """
    Stop the browser caching static files in development.

    THE PROBLEM THIS SOLVES
    -----------------------
    `runserver` serves /static/ with a `Last-Modified` header and nothing else
    — no `ETag`, no `Cache-Control`. With no explicit freshness information a
    browser is allowed to invent some, and Chrome does: it takes roughly a
    tenth of the file's age and treats the copy as fresh for that long without
    asking us. Edit app.css, reload, and you get the old stylesheet with no
    indication that anything is stale.

    That is not a cosmetic annoyance. A page whose stylesheet is one version
    behind does not look "slightly off" — fixed-position furniture lands in the
    document flow, grids collapse into stacks, and the obvious conclusion is
    that the code is broken rather than the cache. It costs more time than it
    saves, every time.

    WHY THIS IS NOT NEEDED IN PRODUCTION, AND MUST NOT BE USED THERE
    ---------------------------------------------------------------
    prod.py uses `ManifestStaticFilesStorage`, which puts a content hash in
    every static filename. A changed file gets a new URL, so a stale copy can
    never be served and the files can be cached hard and forever — which is
    exactly what you want on connections our members pay for by the megabyte.
    Sending `no-store` there would re-download every asset on every page view.

    WHY PATH-SCOPED RATHER THAN BLANKET
    -----------------------------------
    Only `STATIC_URL` and `MEDIA_URL` are touched. HTML responses already carry
    their own cache headers from Django's session and CSRF handling, and
    overwriting those to debug a stylesheet would change how the app behaves
    rather than how it is delivered.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.prefixes = tuple(
            prefix for prefix in (settings.STATIC_URL, settings.MEDIA_URL) if prefix
        )

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(self.prefixes):
            # `no-store` rather than `no-cache`: no-cache still lets the browser
            # keep the copy and revalidate, which is one conditional request
            # away from the same confusion when a 304 comes back off a stale
            # Last-Modified. no-store means there is nothing to get wrong.
            response["Cache-Control"] = "no-store, must-revalidate"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
