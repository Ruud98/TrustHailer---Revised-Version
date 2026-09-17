"""
`runserver`, with static files routed through the middleware stack.

WHY THIS COMMAND IS OVERRIDDEN AT ALL
-------------------------------------
`django.contrib.staticfiles` replaces `runserver` with a version that wraps the
WSGI application in a `StaticFilesHandler`. That handler answers anything under
STATIC_URL *itself*, before the request reaches Django's middleware chain — so
a middleware that sets cache headers never sees a stylesheet request. Requests
for MEDIA_URL do go through middleware, because those are served by an ordinary
view in the URLconf, which is why the two behave differently and why this is
easy to get wrong.

This subclass keeps everything the staticfiles version does and only turns the
handler off, leaving `staticfiles_urlpatterns()` in config/urls.py to serve the
files through the normal request path. The result is the same files at the same
URLs, but now `NoStoreStaticMiddleware` can stop the browser caching them.

WHY NOT JUST PASS --nostatic
----------------------------
Because it has to be remembered, every time, by everyone, and the failure mode
is silent: you get a stale stylesheet and conclude your CSS is broken. A flag
that must never be forgotten is a default in the wrong place.

DEVELOPMENT ONLY, BY CONSTRUCTION
---------------------------------
`runserver` is not how this is deployed — prod runs the WSGI application behind
a real server, which serves hashed static filenames directly and never loads
this command.
"""
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)


class Command(StaticfilesRunserverCommand):
    def get_handler(self, *args, **options):
        # Force the --nostatic path. The files are still served in DEBUG, by
        # staticfiles_urlpatterns() in the URLconf, which runs inside the
        # middleware chain instead of in front of it.
        options["use_static_handler"] = False
        return super().get_handler(*args, **options)
