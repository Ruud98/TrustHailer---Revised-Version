{% load static %}/*
 * TrustHailer's service worker. Rendered by Django rather than served straight
 * out of static/, for two reasons.
 *
 * SCOPE. A worker can only control URLs below its own path, so one living at
 * /static/js/sw.js would control /static/js/ and nothing else — useless. It has
 * to be served from the root, and routing it through a view is how it gets
 * there without a web-server rule that only exists in production.
 *
 * HASHED FILENAMES. Production uses ManifestStaticFilesStorage, so app.css is
 * really app.4f3a1c.css. A precache list written by hand would be a list of
 * 404s on the next deploy. Rendering the static tags here means the list is
 * always the one this deploy actually serves — and the cache name changes with
 * it, so stale entries are dropped rather than pinned forever.
 */
"use strict";

// Changing this is how a deploy invalidates the caches below: their names are
// built from it, and activate deletes every cache whose name is not current.
const VERSION = "{{ version }}";

const SHELL = "th-shell-" + VERSION;      // the offline page, our own CSS and JS
const VENDOR = "th-vendor-" + VERSION;    // Bootstrap and HTMX, off the CDN
const PHOTOS = "th-photos-v1";            // car photos and avatars

// Deliberately NOT versioned with the rest. A photo at /media/<hash>.webp is
// immutable — the image pipeline writes a new file rather than overwriting one
// — so emptying the photo cache on every deploy would re-download megabytes of
// identical bytes on a connection the user pays for by the megabyte.
const PHOTO_LIMIT = 120;

const STATIC_PREFIX = "{{ static_url }}";
const MEDIA_PREFIX = "{{ media_url }}";
const OFFLINE_URL = "{% url 'offline' %}";

const PRECACHE = [
  OFFLINE_URL,
  "{% static 'css/app.css' %}",
  "{% static 'js/app.js' %}",
  "{% static 'js/pwa.js' %}",
  "{% static 'img/icon-192.png' %}"
];

// ------------------------------------------------------------------ lifecycle

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(SHELL).then(function (cache) {
      // `reload` so an install never picks the previous copy out of the HTTP
      // cache and then serves it as though it were this version's.
      return cache.addAll(PRECACHE.map(function (url) {
        return new Request(url, { cache: "reload" });
      }));
    }).then(function () {
      // Taking over immediately is safe here because nothing cached is HTML:
      // there is no half-old page a suddenly-new worker could be inconsistent
      // with. Waiting instead would mean a fix is live on the server but not
      // on the phone until every tab is closed, which on a phone is never.
      return self.skipWaiting();
    })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.map(function (name) {
        if (name === SHELL || name === VENDOR || name === PHOTOS) { return null; }
        // Anything else belongs to a previous version, or is a cache this
        // worker no longer has a name for. Either way nothing reads it again.
        return caches.delete(name);
      }));
    }).then(function () {
      if (self.registration.navigationPreload) {
        // Lets the browser start the navigation request in parallel with
        // booting this worker rather than after it. Without this, adding a
        // service worker makes the first navigation of a session slower.
        return self.registration.navigationPreload.enable();
      }
      return null;
    }).then(function () {
      return self.clients.claim();
    })
  );
});

// --------------------------------------------------------------------- fetch

self.addEventListener("fetch", function (event) {
  const request = event.request;

  // Anything that changes state goes to the network, always. A cached POST is
  // not a thing, and replaying "send this message" an hour later out of some
  // background queue is a worse bug than the failure it would be hiding.
  if (request.method !== "GET") { return; }

  const url = new URL(request.url);

  // The admin is staff-only, opened rarely and full of forms — nothing should
  // come between a reviewer and the live record they are deciding on. And a
  // health check that can be answered from a cache is not a health check.
  if (url.pathname.startsWith("/admin/") || url.pathname === "/healthz/") { return; }

  if (request.mode === "navigate") {
    event.respondWith(handleNavigation(event));
    return;
  }

  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith(STATIC_PREFIX)) {
      event.respondWith(staleWhileRevalidate(request, SHELL));
    } else if (url.pathname.startsWith(MEDIA_PREFIX)) {
      event.respondWith(cacheFirst(request, PHOTOS, PHOTO_LIMIT));
    }
    // Everything else same-origin — the HTMX fragment endpoints, the suburb
    // lookup — falls through to the network untouched. They are small, and
    // they are as personal as the pages they belong to.
    return;
  }

  // Bootstrap and HTMX. Cross-origin, so the response is opaque — see the note
  // in cacheFirst about why that is tolerable for exactly these two.
  if (url.hostname === "cdn.jsdelivr.net") {
    event.respondWith(cacheFirst(request, VENDOR, 0));
  }
});

/*
 * HTML IS NEVER CACHED. THIS IS THE IMPORTANT ONE.
 * -------------------------------------------------
 * Every page on this site is personal: a thread, a phone number somebody chose
 * to share with one person, an introduction request. Phones here get lent,
 * resold and shared, and a cache is a file on disk that outlives logging out.
 * Caching pages would mean the next person holding the phone can read the last
 * person's messages with aeroplane mode on, and no amount of emptying caches on
 * logout makes that reliably untrue — logout is exactly the moment a sold phone
 * never reaches.
 *
 * It costs little we would otherwise have. The pages are small server-rendered
 * HTML and HTMX already swaps fragments rather than reloading. What offline
 * support buys here is an honest page instead of the browser's dinosaur, and
 * the app opening at all when the signal drops — not a browsable archive.
 */
function handleNavigation(event) {
  return (async function () {
    try {
      const preloaded = await event.preloadResponse;
      if (preloaded) { return preloaded; }
      return await fetch(event.request);
    } catch (error) {
      // Only a genuine network failure lands here. A 404 or a 500 is a
      // response, and the server's own page for it is the right thing to show.
      const cache = await caches.open(SHELL);
      const offline = await cache.match(OFFLINE_URL);
      return offline || Response.error();
    }
  })();
}

/*
 * Serve the copy we have, fetch a fresh one for next time.
 *
 * Safe for static files because production filenames carry a content hash: a
 * changed file is a different URL, so "one load behind" cannot happen. In
 * development the names are stable and you can get one stale load after an
 * edit — which is why pwa.js does not register this worker against a dev
 * server at all.
 */
function staleWhileRevalidate(request, cacheName) {
  return caches.open(cacheName).then(function (cache) {
    return cache.match(request).then(function (cached) {
      const network = fetch(request).then(function (response) {
        if (response && response.status === 200) {
          cache.put(request, response.clone());
        }
        return response;
      }).catch(function () {
        return cached;   // offline, and there is often a cached copy to give
      });
      return cached || network;
    });
  });
}

/*
 * Cache first, network only for something never seen before.
 *
 * For photos this is the whole point of the exercise. Our users pay for data by
 * the megabyte and the browse page is a wall of images they scroll past
 * repeatedly; the second look should cost nothing.
 *
 * `limit` trims to the most recent entries. Cache Storage keys come back in
 * insertion order, so the oldest are at the front — approximate LRU for the
 * price of one slice, rather than maintaining a real access-time index. On a
 * photo cache nobody can tell the difference.
 */
function cacheFirst(request, cacheName, limit) {
  return caches.open(cacheName).then(function (cache) {
    return cache.match(request).then(function (cached) {
      if (cached) { return cached; }
      return fetch(request).then(function (response) {
        // An opaque response is cross-origin and unreadable, status included.
        // Storing one risks pinning a failure permanently, which is why only
        // the two CDN libraries take this path: if one is wrong the page still
        // renders, whereas a wrongly-pinned photo is a broken image forever.
        if (response && (response.status === 200 || response.type === "opaque")) {
          cache.put(request, response.clone());
          if (limit) { trim(cache, limit); }
        }
        return response;
      });
    });
  });
}

function trim(cache, limit) {
  cache.keys().then(function (keys) {
    if (keys.length <= limit) { return; }
    keys.slice(0, keys.length - limit).forEach(function (key) {
      cache.delete(key);
    });
  });
}

// ---------------------------------------------------------------------- push

/*
 * The server sends the whole notification as JSON inside the encrypted payload,
 * so this never has to go back to the network to find out what to say. A push
 * landing on a phone that has just lost signal is still readable.
 *
 * Chrome will show its own "This site has been updated in the background"
 * notice if a push arrives and nothing is shown, so every path here must end in
 * showNotification — including the one where the payload is unreadable.
 */
self.addEventListener("push", function (event) {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = {};
  }

  const title = payload.title || "{{ SITE_NAME }}";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || "",
      icon: "{% static 'img/icon-192.png' %}",
      badge: "{% static 'img/icon-192.png' %}",
      data: { url: payload.url || "/" },
      // The same tag replaces rather than stacks. Five replies in one thread
      // should be one line on the lock screen, not five.
      tag: payload.tag || "trusthailer",
      renotify: true
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  const target = new URL(
    (event.notification.data && event.notification.data.url) || "/",
    self.location.origin
  ).href;

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then(function (clients) {
        // An app the person already has open gets navigated, rather than a
        // second window of the same site opening beside the first.
        for (const client of clients) {
          if ("focus" in client) {
            client.navigate(target);
            return client.focus();
          }
        }
        return self.clients.openWindow(target);
      })
  );
});
