/* The app shell: service worker registration and the install prompt.
   Separate from app.js because neither is about the page it happens to be
   running on, and because both are allowed to do nothing — each is absent in
   some browser somebody uses, and the site has to be exactly as usable when
   both are missing. */
(function () {
  "use strict";

  var config = document.currentScript || document.querySelector("script[data-sw-url]");
  if (!config) { return; }

  var swUrl = config.dataset.swUrl;

  // ------------------------------------------------------ the worker itself

  if (!("serviceWorker" in navigator) || !swUrl) { return; }

  // Registered on load rather than immediately. Registration competes with the
  // page's own requests for the connection, and on the kind of connection this
  // app is built for that is the difference between the first screen arriving
  // promptly and arriving eventually. Nothing it does matters on this page
  // view anyway — it is entirely about the next one.
  window.addEventListener("load", function () {
    navigator.serviceWorker.register(swUrl, { scope: "/" }).catch(function (error) {
      // Private browsing refuses registration outright, and so does a page
      // served over plain HTTP from anything but localhost. Both are normal
      // and neither is worth a line in the user's face.
      console.debug("Service worker not registered:", error);
    });
  });

  // -------------------------------------------------------- install prompt

  var banner = document.getElementById("install-banner");
  var deferred = null;

  // DISMISSAL IS REMEMBERED, AND FOR A LONG TIME.
  // Somebody who closed this banner answered the question. Asking again next
  // week is nagging, and the browser has its own install path in the menu for
  // anyone who changes their mind. Stored rather than per-session because a
  // phone opens this app many times a day.
  var DISMISSED_KEY = "th-install-dismissed";
  var DISMISSED_DAYS = 90;

  function dismissedRecently() {
    try {
      var at = window.localStorage.getItem(DISMISSED_KEY);
      if (!at) { return false; }
      return (Date.now() - Number(at)) < DISMISSED_DAYS * 86400000;
    } catch (error) {
      // Storage throws rather than returning null when a browser blocks site
      // data. Treat that as "not dismissed": a banner is the lesser harm.
      return false;
    }
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    // Chrome would otherwise show its own mini-infobar. Taking the event over
    // is what lets the banner appear where it does not cover the bottom nav.
    event.preventDefault();
    deferred = event;
    if (banner && !dismissedRecently()) { banner.hidden = false; }
  });

  if (banner) {
    var go = document.getElementById("install-go");
    var close = document.getElementById("install-dismiss");

    if (go) {
      go.addEventListener("click", function () {
        if (!deferred) { return; }
        deferred.prompt();
        // Hidden whichever way they answer: the browser will not let the same
        // event be used twice, so a banner left up would be a dead button.
        banner.hidden = true;
        deferred = null;
      });
    }

    if (close) {
      close.addEventListener("click", function () {
        banner.hidden = true;
        try { window.localStorage.setItem(DISMISSED_KEY, String(Date.now())); } catch (error) {}
      });
    }
  }

  window.addEventListener("appinstalled", function () {
    if (banner) { banner.hidden = true; }
    try { window.localStorage.setItem(DISMISSED_KEY, String(Date.now())); } catch (error) {}
  });
})();
