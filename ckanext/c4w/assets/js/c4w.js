/* Citizens4Water portal behaviour.
 *
 * Vanilla, IIFE, no jQuery and no ckan.module. The header menu is a checkbox
 * and works with no JavaScript; this only closes it on Escape.
 */
(function () {
  "use strict";

  document.documentElement.classList.add("c4w-enhanced");

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    var toggle = document.getElementById("c4w-nav-toggle");
    if (toggle) {
      toggle.checked = false;
    }
    var open = document.querySelector(".c4w-account[open]");
    if (open) {
      open.removeAttribute("open");
    }
  });
})();
