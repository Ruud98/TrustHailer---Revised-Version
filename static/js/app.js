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

  // Mark the current bottom-nav item.
  var path = window.location.pathname;
  document.querySelectorAll(".bottomnav a").forEach(function (link) {
    var href = link.getAttribute("href");
    link.classList.toggle("is-active", href === path);
  });
})();
