/* Side rails — the carousel crawl and the ad box swap.
   Loaded only on pages that render {% promo_rails %}, and only ever moves a
   class around: every slide and every ad card is already in the DOM, so this
   file failing to load leaves a static first slide rather than a blank rail.

   THE RAILS STOP WHEN NOBODY IS LOOKING. Three ways, all of them cheap:
   the timer only runs above the breakpoint where the rails are visible, it
   stops while the browser tab is hidden, and it never starts at all under
   prefers-reduced-motion. An animation nobody can see is somebody's battery. */
(function () {
  "use strict";

  var MIN_WIDTH = 1280; // must match the media query in app.css
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  var wide = window.matchMedia("(min-width: " + MIN_WIDTH + "px)");

  var rotators = [];

  function Rotator(root, selector, interval, onChange) {
    this.items = Array.prototype.slice.call(root.querySelectorAll(selector));
    this.interval = interval;
    this.onChange = onChange;
    this.index = 0;
    this.timer = null;

    var self = this;
    // Hovering is the one signal we have that somebody is actually reading it.
    root.addEventListener("mouseenter", function () { self.stop(); });
    root.addEventListener("mouseleave", function () { self.start(); });
  }

  Rotator.prototype.advance = function () {
    var previous = this.items[this.index];
    this.index = (this.index + 1) % this.items.length;
    var next = this.items[this.index];

    previous.classList.remove("is-current");
    // The leaving slide keeps a class for the length of its exit animation,
    // because a display:none swap is what makes a carousel look like a bug.
    previous.classList.add("is-leaving");
    setTimeout(function () { previous.classList.remove("is-leaving"); }, 600);

    next.classList.add("is-current");
    if (this.onChange) { this.onChange(this.index); }
  };

  Rotator.prototype.start = function () {
    if (this.timer || this.items.length < 2) { return; }
    if (reduced.matches || !wide.matches || document.hidden) { return; }
    var self = this;
    this.timer = setInterval(function () { self.advance(); }, this.interval);
  };

  Rotator.prototype.stop = function () {
    clearInterval(this.timer);
    this.timer = null;
  };

  function readInterval(el, fallback) {
    var value = parseInt(el.dataset.interval || "", 10);
    return isNaN(value) ? fallback : value;
  }

  // --------------------------------------------------------------- carousels

  document.querySelectorAll(".js-promo-car").forEach(function (car) {
    var dots = car.querySelectorAll(".promo-car__dot");
    var rotator = new Rotator(car, ".promo-car__slide", readInterval(car, 5000),
      function (index) {
        dots.forEach(function (dot, i) {
          dot.classList.toggle("is-current", i === index);
        });
      });
    rotators.push(rotator);
  });

  // ----------------------------------------------------------------- ad box

  var ANIMATIONS = ["fade", "slide", "zoom", "flip", "swipe"];

  document.querySelectorAll(".js-promo-ad").forEach(function (box) {
    var rotator = new Rotator(box, ".promo-ad__card", readInterval(box, 6500));

    // "Surprise me" is resolved here rather than in Python so that a card set
    // to shuffle gets a different entrance each time round, instead of one
    // random choice frozen at render and repeated for the life of the page.
    var advance = rotator.advance;
    rotator.advance = function () {
      advance.call(this);
      var card = this.items[this.index];
      var anim = card.dataset.anim;
      if (!anim || anim === "shuffle") {
        anim = ANIMATIONS[Math.floor(Math.random() * ANIMATIONS.length)];
      }
      card.dataset.playing = anim;
    };

    rotators.push(rotator);
  });

  // ------------------------------------------------------------- scheduling

  function syncAll() {
    rotators.forEach(function (rotator) {
      if (reduced.matches || !wide.matches || document.hidden) {
        rotator.stop();
      } else {
        rotator.start();
      }
    });
  }

  document.addEventListener("visibilitychange", syncAll);
  // Narrowing the window hides the rails in CSS; the timers have to know too,
  // or a laptop with a browser dragged narrow keeps animating an invisible
  // column forever.
  if (wide.addEventListener) { wide.addEventListener("change", syncAll); }
  if (reduced.addEventListener) { reduced.addEventListener("change", syncAll); }

  syncAll();
})();
