/* The app shell: service worker registration, the install prompt, and push
   permission. Separate from app.js because none of it is about the page it
   happens to be running on, and because all of it is allowed to do nothing —
   every feature in this file is absent in some browser somebody uses, and the
   site has to be exactly as usable when all three are missing. */
(function () {
  "use strict";

  var config = document.currentScript || document.querySelector("script[data-sw-url]");
  if (!config) { return; }

  var swUrl = config.dataset.swUrl;
  var pushKey = config.dataset.pushKey || "";
  var subscribeUrl = config.dataset.pushSubscribe || "";
  var unsubscribeUrl = config.dataset.pushUnsubscribe || "";

  // Same cookie read as app.js does for HTMX. Duplicated rather than shared
  // because these two files load independently and neither should be able to
  // break the other by being absent.
  function csrf() {
    var match = document.cookie.match("(?:^|; )csrftoken=([^;]*)");
    return match ? decodeURIComponent(match[1]) : "";
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body)
    });
  }

  // ------------------------------------------------------ the worker itself

  if (!("serviceWorker" in navigator) || !swUrl) { return; }

  // Registered on load rather than immediately. Registration competes with the
  // page's own requests for the connection, and on the kind of connection this
  // app is built for that is the difference between the first screen arriving
  // promptly and arriving eventually. Nothing it does matters on this page
  // view anyway — it is entirely about the next one.
  window.addEventListener("load", function () {
    navigator.serviceWorker.register(swUrl, { scope: "/" }).then(function (registration) {
      setUpPush(registration);
    }).catch(function (error) {
      // Private browsing refuses registration outright, and so does a page
      // served over plain HTTP from anything but localhost. Both are normal
      // and neither is worth a line in the user's face.
      console.debug("Service worker not registered:", error);
    });
  });

  // ----------------------------------------------------------------- push

  function setUpPush(registration) {
    // No key means push is not configured on this deploy, and no permission
    // prompt should ever appear. Asking for a permission we cannot act on
    // spends the one chance the browser gives us to ask at all.
    if (!pushKey || !subscribeUrl || !("PushManager" in window)) { return; }

    // DELIBERATELY NOT ASKED FOR ON LOAD.
    // A permission prompt in the first seconds of a first visit is the single
    // most-refused dialog on the web, and a refusal is close to permanent —
    // Chrome will not ask again, and the only repair is a path through the
    // browser's own site settings that nobody finds. So this only ever runs
    // off a tap on something that says what it is for, and otherwise the two
    // paths below are all that happen.
    if (Notification.permission === "denied") { return; }

    // Revealed only now, having established that this browser can actually do
    // it. The panel ships hidden for the same reason the install banner does:
    // the server cannot tell which browser is asking, and a switch that does
    // nothing when flipped is worse than an absent switch.
    document.querySelectorAll("[data-push-panel]").forEach(function (panel) {
      panel.hidden = false;
    });

    if (Notification.permission === "granted") {
      // Already said yes, on this or an earlier visit. Re-subscribing every
      // load sounds wasteful and is not: browsers rotate a subscription on
      // their own, and the old one stops working without telling anybody. The
      // server keys on the endpoint, so a repeat is an update, not a row.
      subscribe(registration);
    }

    wireToggle(registration);
  }

  function subscribe(registration) {
    registration.pushManager.getSubscription().then(function (existing) {
      if (existing) { return existing; }
      return registration.pushManager.subscribe({
        // Required to be true, and the reason the payload always carries
        // something to show: every push must result in a visible notification
        // or the browser eventually revokes the permission.
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(pushKey)
      });
    }).then(function (subscription) {
      return postJSON(subscribeUrl, subscription.toJSON());
    }).catch(function (error) {
      console.debug("Push subscription failed:", error);
    });
  }

  /*
   * Any element with `data-push-toggle` becomes the on/off switch, so the
   * control can live wherever the templates decide to put it — the
   * notifications page today, a settings page later — without this file
   * knowing about either.
   */
  function wireToggle(registration) {
    document.addEventListener("click", function (event) {
      var toggle = event.target.closest && event.target.closest("[data-push-toggle]");
      if (!toggle) { return; }
      event.preventDefault();

      registration.pushManager.getSubscription().then(function (existing) {
        if (existing) {
          var endpoint = existing.endpoint;
          return existing.unsubscribe().then(function () {
            return postJSON(unsubscribeUrl, { endpoint: endpoint });
          }).then(function () {
            paintToggle(toggle, false);
          });
        }
        // The tap is the gesture the prompt needs, and the person has just
        // read a line of text saying what they are about to be asked.
        return Notification.requestPermission().then(function (permission) {
          if (permission !== "granted") {
            paintToggle(toggle, false);
            return null;
          }
          subscribe(registration);
          paintToggle(toggle, true);
          return null;
        });
      });
    });

    registration.pushManager.getSubscription().then(function (existing) {
      document.querySelectorAll("[data-push-toggle]").forEach(function (toggle) {
        paintToggle(toggle, Boolean(existing));
      });
    });
  }

  function paintToggle(toggle, on) {
    toggle.dataset.pushOn = on ? "1" : "0";
    toggle.textContent = on ? "Turn off notifications" : "Turn on notifications";
  }

  /*
   * The Push API wants the key as raw bytes; it ships in the page as base64url,
   * which is the form every other tool prints it in. atob only reads standard
   * base64 and insists on padding, hence both substitutions and the padding.
   */
  function urlBase64ToUint8Array(base64) {
    var padded = (base64 + "=".repeat((4 - base64.length % 4) % 4))
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    var raw = window.atob(padded);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) { bytes[i] = raw.charCodeAt(i); }
    return bytes;
  }

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
