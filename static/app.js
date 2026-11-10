/* Let's Gau – date bar arrows and compact header on every page; filters, search,
   grouping and swipe on day pages. Everything works without it. */
(function () {
  "use strict";
  var KEY = "lg.v1", body = document.body, list = document.getElementById("list");
  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }
  var state = load(); state.tags = state.tags || {}; state.group = state.group && state.group !== "time" ? state.group : "none";
  var lang = body.getAttribute("data-lang"), root = body.getAttribute("data-root") || "";

  /* language: remember the choice for the start page */
  try { localStorage.setItem("lg.lang", lang); } catch (e) {}

  /* a stale "today" page (built yesterday) jumps to the real today */
  function iso(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  var today = iso(new Date());
  if (body.getAttribute("data-index") && body.getAttribute("data-day") < today) {
    location.replace(root + lang + "/" + today + "/" + location.hash); return;
  }
  /* date bar: arrow buttons and the mouse wheel page it on desktop */
  var bar = document.querySelector(".dates"), prevBtn = document.querySelector(".dates-nav.prev"),
    nextBtn = document.querySelector(".dates-nav.next");
  function arrows() {
    if (!bar || !prevBtn || !nextBtn) return;
    prevBtn.hidden = bar.scrollLeft <= 2;
    nextBtn.hidden = bar.scrollLeft + bar.clientWidth >= bar.scrollWidth - 2;
  }
  function page(dir) { bar.scrollBy({ left: dir * bar.clientWidth * 0.8, behavior: "smooth" }); }
  if (bar && prevBtn && nextBtn) {
    prevBtn.addEventListener("click", function () { page(-1); });
    nextBtn.addEventListener("click", function () { page(1); });
    bar.addEventListener("scroll", arrows, { passive: true });
    window.addEventListener("resize", arrows);
    bar.addEventListener("wheel", function (e) {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX) || bar.scrollWidth <= bar.clientWidth) return;
      e.preventDefault(); bar.scrollLeft += e.deltaY;
    }, { passive: false });
  }

  /* header slides away when scrolling down, back when scrolling up (transform only) */
  var head = document.getElementById("top"), last = 0, compact = false;
  function setCompact(on) {
    compact = on;
    head.style.transform = on ? "translateY(-100%)" : "";
    document.documentElement.style.setProperty("--head-h", (on ? 0 : head.offsetHeight) + "px");
  }
  window.addEventListener("scroll", function () {
    var y = window.scrollY;
    if (Math.abs(y - last) < 12) return;
    var want = y > 120 && y > last;
    if (want !== compact) setCompact(want);
    last = y;
  }, { passive: true });
  /* layout reads happen after the first paint */
  function later(f) {
    if (window.requestAnimationFrame) requestAnimationFrame(function () { setTimeout(f, 0); });
    else setTimeout(f, 0);
  }
  later(function () {
    setCompact(false);
    var current = document.querySelector(".dates [aria-current]");
    if (current && bar) bar.scrollLeft = current.offsetLeft - (bar.clientWidth - current.offsetWidth) / 2;
    arrows();
  });

  /* everything below is for day pages only */
  if (!list) return;
  var labels = JSON.parse(list.getAttribute("data-labels"));
  var q = document.getElementById("q"), reset = document.getElementById("reset");
  var chips = [].slice.call(document.querySelectorAll(".chip[data-tag]"));

  function words(el, a) { return (el.getAttribute(a) || "").split(" ").filter(Boolean); }
  function passTags(tags) {
    var inc = [], exc = false, any = false;
    for (var t in state.tags) {
      if (state.tags[t] === "in") inc.push(t);
      if (state.tags[t] === "out" && tags.indexOf(t) >= 0) exc = true;
    }
    if (exc) return false;
    if (!inc.length) return true;
    inc.forEach(function (t) { if (tags.indexOf(t) >= 0) any = true; });
    return any;
  }
  function container() { return document.getElementById("grouped") || list; }

  function apply() {
    var active = !!(q && q.value.trim()), shown = 0;
    for (var t in state.tags) if (state.tags[t]) active = true;
    var box = container(), rows = box.querySelectorAll(".ev");
    var visible = {};
    [].forEach.call(rows, function (el) {
      if (el.classList.contains("copy")) return;
      var ok = passTags(words(el, "data-tags"));
      el.hidden = !ok; if (ok) { shown++; visible[el.id] = true; }
    });
    [].forEach.call(rows, function (el) {
      if (!el.classList.contains("copy")) return;
      var ok = active && !visible[el.getAttribute("data-parent")] &&
        passTags(words(el, "data-tags"));
      el.classList.toggle("show", ok); el.hidden = !ok; if (ok) shown++;
    });
    [].forEach.call(box.querySelectorAll(".sec"), function (sec) {
      sec.hidden = !sec.querySelector(".ev:not([hidden])");
    });
    var none = list.querySelector(".nomatch"); if (none) none.hidden = shown > 0 || !rows.length;
    if (reset) reset.hidden = !active;
    if (cnt) {
      var n = 0; for (var k in state.tags) if (state.tags[k]) n++;
      cnt.textContent = n ? String(n) : ""; cnt.hidden = !n;
    }
    chips.forEach(function (c) {
      var s = state.tags[c.getAttribute("data-tag")] || "";
      c.setAttribute("data-state", s); c.setAttribute("aria-pressed", s === "in" ? "true" : s === "out" ? "mixed" : "false");
    });
  }

  chips.forEach(function (c) {
    c.addEventListener("click", function () {
      var t = c.getAttribute("data-tag"), s = state.tags[t];
      state.tags[t] = !s ? "in" : s === "in" ? "out" : undefined;
      if (!state.tags[t]) delete state.tags[t];
      save(); apply(); document.dispatchEvent(new Event("lg:filter"));
    });
  });
  /* category chips fold in and out (remembered; base.html applies it before paint) */
  var toggle = document.getElementById("cats-toggle"), html = document.documentElement;
  var cnt = toggle && toggle.querySelector(".cnt");
  function showCats(open) {
    html.classList.toggle("cats-closed", !open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  if (toggle) {
    showCats(state.cats !== false);
    toggle.addEventListener("click", function () {
      state.cats = html.classList.contains("cats-closed"); save(); showCats(state.cats);
    });
  }
  if (reset) reset.addEventListener("click", function () {
    state.tags = {}; save(); apply();
    if (q && q.value) { q.value = ""; q.dispatchEvent(new Event("input")); }
  });
  if (q) q.addEventListener("input", apply);  /* only the reset button; search.js searches */

  /* grouping: none = page order (untimed first, then by start time);
     category / place = generated sections of clones in the same order */
  function group(kind) {
    var old = document.getElementById("grouped"); if (old) old.remove();
    [].forEach.call(list.querySelectorAll(":scope > .sec"), function (s) { s.style.display = kind === "none" ? "" : "none"; });
    if (kind !== "none") {
      var rows = [].slice.call(list.querySelectorAll(".ev")), groups = {}, order = [];
      rows.forEach(function (el) {
        var key = kind === "category" ? el.getAttribute("data-primary") : el.getAttribute("data-place");
        if (!groups[key]) { groups[key] = []; order.push(key); }
        groups[key].push(el);
      });
      if (kind === "category") order.sort(function (a, b) { return (labels.tags[a] || a).localeCompare(labels.tags[b] || b); });
      else order.sort(function (a, b) {
        return parseFloat(groups[a][0].getAttribute("data-drive")) - parseFloat(groups[b][0].getAttribute("data-drive")) || a.localeCompare(b);
      });
      var box = document.createElement("div"); box.id = "grouped";
      order.forEach(function (key) {
        var sec = document.createElement("section"), h = document.createElement("h2");
        sec.className = "sec"; h.textContent = kind === "category" ? (labels.tags[key] || key) : key;
        sec.appendChild(h);
        /* rows arrive in page order, which is already sorted; keep it */
        groups[key].forEach(function (el) { var c = el.cloneNode(true); c.removeAttribute("id"); sec.appendChild(c); });
        box.appendChild(sec);
      });
      list.insertBefore(box, list.querySelector(".nomatch"));
    }
    [].forEach.call(document.querySelectorAll("[data-group]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-group") === kind ? "true" : "false");
    });
    state.group = kind; save(); apply();
  }
  [].forEach.call(document.querySelectorAll("[data-group]"), function (b) {
    b.addEventListener("click", function () { group(b.getAttribute("data-group")); });
  });

  /* open the event named in the URL fragment */
  if (location.hash.length > 1) {
    var target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target) {
      for (var p = target; p; p = p.parentElement) if (p.tagName === "DETAILS") p.open = true;
      target.scrollIntoView();
    }
  }

  /* swipe left/right on the list changes the day */
  var x0 = null, y0 = 0;
  list.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  list.addEventListener("touchend", function (e) {
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
    if (Math.abs(dx) < 80 || Math.abs(dy) > 60) return;
    var link = document.querySelector(dx < 0 ? 'a[rel="next"]' : 'a[rel="prev"]');
    if (link) location.href = link.href;
  }, { passive: true });

  /* the initial filter pass happens after the first paint */
  later(function () {
    var filtered = false;
    for (var t in state.tags) if (state.tags[t]) filtered = true;
    if (state.group !== "none") group(state.group);
    else if (filtered) apply();
  });
})();
