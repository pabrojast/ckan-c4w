/* Citizens4Water portal behaviour.
 *
 * Vanilla, IIFE, no jQuery and no ckan.module: everything on this portal is a
 * progressive enhancement over markup that already works without JavaScript,
 * and the server re-validates and re-sanitises whatever the client sends.
 */
(function () {
  "use strict";

  // Mark the document so CSS can hide what only makes sense once the
  // enhancement is running. Nothing may depend on this class for legibility --
  // only for the JS-only affordances.
  document.documentElement.classList.add("c4w-enhanced");
})();
