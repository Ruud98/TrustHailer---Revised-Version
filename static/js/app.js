/* Total client-side budget for this project: this file plus HTMX.
   If something here grows past a screenful, it probably belongs on the server. */
(function () {
  "use strict";

  // Resend cooldown. The server enforces this too — this is only so the button
  // doesn't look tappable when it isn't.
  var resend = document.getElementById("resend-btn");
  if (resend) {
    var seconds = parseInt(resend.dataset.cooldown || "60", 10);
    var label = resend.textContent.trim();
    resend.disabled = true;
    var tick = function () {
      if (seconds <= 0) {
        resend.disabled = false;
        resend.textContent = label;
        return;
      }
      resend.textContent = label + " (" + seconds + ")";
      seconds -= 1;
      setTimeout(tick, 1000);
    };
    tick();
  }

  // Auto-submit the OTP form once all digits are in. Saves a tap, and on
  // Android the SMS autofill fires this immediately.
  var otp = document.querySelector(".otp-input");
  if (otp) {
    otp.addEventListener("input", function () {
      var digits = otp.value.replace(/\D/g, "");
      otp.value = digits;
      if (digits.length === 6 && otp.form) {
        otp.form.requestSubmit();
      }
    });
  }

  // Mark the current item in both navs. They are separate elements showing the
  // same destinations at different widths, so they have to agree — a sidebar
  // highlighting Cars while the bottom bar highlights Feed is worse than
  // neither highlighting anything.
  var path = window.location.pathname;
  document.querySelectorAll(".bottomnav a, .sidenav__link").forEach(function (link) {
    var href = link.getAttribute("href");
    link.classList.toggle("is-active", href === path);
  });

  // Grow the comment box with what is typed in it. Delegated, because a reply
  // box is revealed long after this runs and the composer is re-rendered on
  // every page load; binding per element would miss both.
  //
  // Height is cleared before it is read: scrollHeight only ever grows while an
  // explicit height is set, so deleting a paragraph would leave the box tall.
  //
  // Overflow is toggled rather than left on `auto`. Chasing the exact height —
  // scrollHeight plus borders — loses to subpixel rounding on a border-box
  // element, and being one pixel short raises a scrollbar on a single-line
  // reply. Hidden until the box actually hits its max-height cannot round
  // wrong, and the scrollbar then appears exactly when there is something to
  // scroll.
  document.body.addEventListener("input", function (event) {
    var box = event.target;
    if (!box.classList || !box.classList.contains("compose__box")) { return; }
    box.style.height = "auto";
    var max = parseFloat(window.getComputedStyle(box).maxHeight) || Infinity;
    // +1 for rounding, not for looks. `offsetHeight - clientHeight` is an
    // integer, but the border can compute to a fraction (0.8px on a scaled
    // display), so the difference can come back a pixel short and clip the
    // bottom of the content box. A pixel of extra leading is invisible; a
    // clipped descender is not.
    var wanted = box.scrollHeight + (box.offsetHeight - box.clientHeight) + 1;
    box.style.overflowY = wanted > max ? "auto" : "hidden";
    box.style.height = Math.min(wanted, max) + "px";
  });

  // Long-press opens the reaction picker on touch. The only thing script does
  // for reactions: hover and focus-within already open it on a pointer device,
  // both from CSS, and the trigger button likes and unlikes on its own whether
  // or not any of this runs.
  //
  // Delegated from the document because HTMX replaces the whole reaction bar
  // on every press — a listener bound to the button would be swapped away with
  // it on the first tap.
  var pressTimer = null;
  var opened = null;

  function closePicker() {
    if (opened) {
      opened.classList.remove("is-open");
      opened = null;
    }
  }

  document.addEventListener("touchstart", function (event) {
    var trigger = event.target.closest && event.target.closest(".reactions__trigger");
    if (!trigger) {
      closePicker();
      return;
    }
    pressTimer = setTimeout(function () {
      pressTimer = null;
      closePicker();
      opened = trigger.closest(".reactions__wrap");
      opened.classList.add("is-open");
    }, 450);
  }, { passive: true });

  document.addEventListener("touchend", function (event) {
    if (pressTimer) {
      clearTimeout(pressTimer);
      pressTimer = null;
      return; // A short tap. Let the button do its normal thing.
    }
    // The press already opened the picker, so the click this touch is about to
    // synthesise would toggle a reaction nobody asked for.
    if (opened && opened.contains(event.target)) {
      event.preventDefault();
    }
  });

  // Choosing one closes it; so does pressing anything else on the page. The
  // same click closes any open comment menu it did not land inside — <details>
  // does not do that for itself, and a menu left hanging open after you have
  // moved on reads as broken.
  document.body.addEventListener("click", function (event) {
    if (!event.target.closest) { return; }
    if (!event.target.closest(".reactions__wrap")) {
      closePicker();
    }
    var inside = event.target.closest(".comment__menu");
    document.querySelectorAll(".comment__menu[open]").forEach(function (menu) {
      if (menu !== inside) { menu.removeAttribute("open"); }
    });
  });
  document.body.addEventListener("htmx:afterSwap", closePicker);

  // ------------------------------------------------------- photo galleries
  //
  // The scrolling is the browser's: the track is a scroll-snap strip, so
  // swiping works with this file missing entirely. All of this adds is the two
  // arrows and the dots, which is why the arrows ship `hidden` and are revealed
  // here — an arrow that does nothing is worse than no arrow.
  //
  // Everything is delegated rather than bound per gallery, because the feed
  // appends more posts over HTMX as you scroll and a bound-once init would
  // leave every gallery past the first page dead. The same handlers drive a
  // listing's photos, which differ only in how the stylesheet dresses them.
  //
  // WHICH PHOTO WE ARE ON IS COUNTED, NOT MEASURED
  // The obvious way to know whether "next" has anywhere to go is to compare
  // scrollLeft against scrollWidth. It is also wrong at the only moment that
  // matters: this runs on DOMContentLoaded, the photos are `loading="lazy"`
  // and have no height yet, so the track is exactly as wide as its own box and
  // every gallery decides it has nothing to scroll to — disabling the arrow
  // permanently, because a disabled button never gets another click to fix it.
  // The number of photos is known from the markup and true immediately.

  function galleryPhotos(gal) {
    return gal.querySelectorAll(".gallery__img");
  }

  function galleryIndex(track) {
    var width = track.clientWidth;
    // Rounded, because a snapped scroller lands a fraction of a pixel off and
    // `Math.floor` would report photo 2 as photo 1 for the whole of it.
    return width ? Math.round(track.scrollLeft / width) : 0;
  }

  function galleryStep(gal, direction) {
    var track = gal.querySelector(".gallery__track");
    var photos = galleryPhotos(gal);
    if (!track || !photos.length) { return; }

    var index = galleryIndex(track) + direction;
    index = Math.max(0, Math.min(index, photos.length - 1));
    // Scrolled to the photo's own offset rather than by one track-width, so
    // the gap between photos does not accumulate into a drift by the sixth.
    track.scrollTo({ left: photos[index].offsetLeft - photos[0].offsetLeft });
  }

  function paintGallery(gal) {
    var track = gal.querySelector(".gallery__track");
    if (!track) { return; }
    var count = galleryPhotos(gal).length;
    var index = Math.min(galleryIndex(track), count - 1);

    gal.querySelectorAll(".gallery__dot").forEach(function (dot, i) {
      dot.classList.toggle("is-current", i === index);
    });

    var prev = gal.querySelector("[data-gallery-prev]");
    var next = gal.querySelector("[data-gallery-next]");
    if (prev) { prev.disabled = index <= 0; }
    if (next) { next.disabled = index >= count - 1; }
  }

  function wakeGalleries() {
    document.querySelectorAll("[data-gallery]").forEach(function (gal) {
      gal.querySelectorAll(".gallery__nav").forEach(function (nav) {
        nav.hidden = false;
      });
      paintGallery(gal);
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-gallery-prev], [data-gallery-next]");
    if (!button) { return; }
    var gal = button.closest(".gallery");
    if (gal) {
      galleryStep(gal, button.hasAttribute("data-gallery-next") ? 1 : -1);
    }
  });

  // `scroll` does not bubble, so this listens in the capture phase instead of
  // binding one listener per track. rAF-coalesced: a swipe fires scroll events
  // faster than anything needs repainting.
  var galleryPending = false;
  document.addEventListener("scroll", function (event) {
    var track = event.target;
    if (!track.classList || !track.classList.contains("gallery__track")) { return; }
    if (galleryPending) { return; }
    galleryPending = true;
    requestAnimationFrame(function () {
      galleryPending = false;
      paintGallery(track.closest(".gallery"));
    });
  }, true);

  wakeGalleries();
  // More posts arrived from the feed's infinite scroll, or a card was swapped.
  document.body.addEventListener("htmx:afterSwap", wakeGalleries);

  // CSRF token on every HTMX request that isn't a GET. Every earlier hx-*
  // attribute in this app was hx-get (browse filters, the suburb lookup), so
  // this was never needed before the feed's like button — the first hx-post
  // on the site. Reads the cookie rather than a hidden input because one
  // listener here covers every current and future hx-post without each
  // template remembering to carry a token.
  function readCookie(name) {
    var match = document.cookie.match("(?:^|; )" + name + "=([^;]*)");
    return match ? decodeURIComponent(match[1]) : null;
  }
  document.body.addEventListener("htmx:configRequest", function (event) {
    if (event.detail.verb !== "get") {
      event.detail.headers["X-CSRFToken"] = readCookie("csrftoken");
    }
  });
})();
