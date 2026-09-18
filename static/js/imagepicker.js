/* The photo picker: thumbnails of what you chose, each with a way to drop one
   before you send the form.

   Used by the post composer and by the listing photo form. Both are a file
   input that takes several images at once, and both had the same hole in them;
   one file is loaded by both pages rather than two that drift apart.

   WHY THIS EXISTS
   A file input is write-only. Pick four photos, realise the second one is the
   blurry one, and the only move the browser gives you is to open the dialog
   again and reselect the other three — on a phone, from a gallery of nine
   hundred. One × per thumbnail is the whole feature.

   WHY IT REWRITES THE INPUT RATHER THAN TRACKING ITS OWN LIST
   The form posts `input.files`, so that is the thing that has to change. A
   parallel array of "files the user still wants" would be a second source of
   truth, and the first time the two disagreed the post would publish a photo
   that had been visibly removed. `DataTransfer` is the only way to build a
   fresh FileList, so removing photo 2 means rebuilding the input from 1, 3, 4.

   PROGRESSIVE, LIKE THE GALLERY
   This file failing to load leaves the browser's own file input, which works.
   Nothing here is required to send either form. */
(function () {
  "use strict";

  var pickers = document.querySelectorAll("[data-imagepicker]");
  if (!pickers.length || typeof DataTransfer === "undefined") { return; }

  pickers.forEach(function (picker) {
    var input = picker.querySelector('input[type="file"]');
    var list = picker.querySelector("[data-imagepicker-list]");
    // Optional, and its words belong to the page rather than to this file:
    // "Ready to upload" on a listing, "Ready to post" in the composer. All the
    // script owns is whether it is on screen.
    var ready = picker.querySelector("[data-imagepicker-ready]");
    if (!input || !list) { return; }

    // Object URLs hold the file in memory until they are revoked. Six phone
    // photos is a few megabytes to leave behind on a page somebody is going to
    // sit on while they write the post.
    var urls = [];

    function releaseUrls() {
      urls.forEach(URL.revokeObjectURL);
      urls = [];
    }

    function rebuild(files) {
      var transfer = new DataTransfer();
      files.forEach(function (file) { transfer.items.add(file); });
      input.files = transfer.files;
      render();
    }

    function render() {
      releaseUrls();
      list.textContent = "";

      var files = Array.prototype.slice.call(input.files);
      list.hidden = files.length === 0;
      if (ready) { ready.hidden = list.hidden; }

      files.forEach(function (file, index) {
        var url = URL.createObjectURL(file);
        urls.push(url);

        var item = document.createElement("li");
        item.className = "imagepicker__item";

        var thumb = document.createElement("img");
        thumb.className = "imagepicker__thumb";
        thumb.src = url;
        thumb.alt = "";

        var drop = document.createElement("button");
        drop.type = "button";  // not submit: this button sits inside a form
        drop.className = "imagepicker__drop";
        drop.innerHTML = "&times;";
        // The filename, because "Remove photo 2" is no help to somebody who
        // cannot see the thumbnail it sits on.
        drop.setAttribute("aria-label", "Remove " + file.name);
        drop.addEventListener("click", function () {
          var kept = Array.prototype.slice.call(input.files);
          kept.splice(index, 1);
          rebuild(kept);
          // Focus would otherwise land on <body> as the button it was on is
          // removed, which loses a keyboard user their place in the form.
          (list.querySelector(".imagepicker__drop") || input).focus();
        });

        item.appendChild(thumb);
        item.appendChild(drop);
        list.appendChild(item);
      });
    }

    input.addEventListener("change", render);
    // Picking files, navigating away and coming back restores the selection in
    // some browsers, so render once now rather than only on change.
    render();
  });
})();
