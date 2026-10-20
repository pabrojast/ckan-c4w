/*
  Citizens4Water portal script. Vanilla, no jQuery, no ckan.module.

  Three progressive touches: the masthead gains a deeper shadow once the
  page scrolls, the counters on the home page count up when they enter the
  viewport, and Escape closes the mobile menu and the account menu. Every
  page works unchanged without this file.
*/
(function () {
  'use strict';
  var doc = document.documentElement;
  doc.classList.add('c4w-enhanced');
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Escape closes the open menus.
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') { return; }
    var toggle = document.getElementById('c4w-nav-toggle');
    if (toggle && toggle.checked) { toggle.checked = false; }
    var open = document.querySelectorAll('details.c4w-account[open]');
    for (var i = 0; i < open.length; i++) { open[i].removeAttribute('open'); }
  });

  // Masthead shadow once the page is scrolled.
  var header = document.querySelector('.c4w-header');
  if (header) {
    var stuck = false;
    var onScroll = function () {
      var now = window.scrollY > 8;
      if (now !== stuck) { stuck = now; header.classList.toggle('is-stuck', now); }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  // Counters count up when they become visible.
  var counters = document.querySelectorAll('[data-count]');
  if (counters.length && !reduced && 'IntersectionObserver' in window) {
    var format = function (n) {
      return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    };
    var run = function (el) {
      var target = parseInt(el.getAttribute('data-count'), 10);
      if (isNaN(target)) { return; }
      var start = null;
      var duration = 900;
      var step = function (ts) {
        if (start === null) { start = ts; }
        var t = Math.min(1, (ts - start) / duration);
        var eased = 1 - Math.pow(1 - t, 3);
        el.textContent = format(Math.round(target * eased));
        if (t < 1) { window.requestAnimationFrame(step); }
      };
      window.requestAnimationFrame(step);
    };
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        observer.unobserve(entry.target);
        run(entry.target);
      });
    }, { threshold: 0.4 });
    for (var j = 0; j < counters.length; j++) {
      counters[j].textContent = '0';
      observer.observe(counters[j]);
    }
  }
  // Follow processing without posting actions or counting repeated page views.
  var processing = document.querySelector('[data-c4w-processing]');
  if (processing && window.fetch) {
    var checkProcessing = function () {
      if (document.hidden) { window.setTimeout(checkProcessing, 5000); return; }
      fetch(processing.getAttribute('data-c4w-processing'), {
        credentials: 'same-origin', headers: { Accept: 'application/json' }
      }).then(function (response) {
        if (!response.ok) { throw new Error('Status unavailable'); }
        return response.json();
      }).then(function (data) {
        if (data.status !== 'queued' && data.status !== 'processing') {
          window.location.reload();
        } else { window.setTimeout(checkProcessing, 5000); }
      }).catch(function () { window.setTimeout(checkProcessing, 15000); });
    };
    window.setTimeout(checkProcessing, 3000);
  }
})();

// Individual disclosures stay closed on navigation; active filters remain visible.
(function () {
  document.querySelectorAll('[data-facet-panel]').forEach(function (panel) {
    var wide = window.matchMedia('(min-width: 62.001rem)');
    panel.open = wide.matches;
    wide.addEventListener('change', function (event) { panel.open = event.matches; });
    var toggle = panel.querySelector('[data-facets-toggle]');
    var groups = Array.from(panel.querySelectorAll('[data-facet-group]'));
    if (!toggle || !groups.length) return;
    toggle.hidden = false;
    function sync() {
      var anyOpen = groups.some(function (group) { return group.open; });
      toggle.textContent = anyOpen ? toggle.dataset.collapse : toggle.dataset.expand;
    }
    groups.forEach(function (group) { group.addEventListener('toggle', sync); });
    toggle.addEventListener('click', function () {
      var open = !groups.some(function (group) { return group.open; });
      groups.forEach(function (group) { group.open = open; });
      sync();
    });
  });
})();
