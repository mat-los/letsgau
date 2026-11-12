/* Let's Gau – search across all displayed days, on every page. The index
   (data/search.<lang>.json) is fetched only when a search starts; without JavaScript
   the field is hidden. */
(function () {
  "use strict";
  var MIN = 3, MAX = 50, q = document.getElementById("q"), box = document.getElementById("results");
  if (!q || !box) return;
  var body = document.body, lang = body.getAttribute("data-lang"), root = body.getAttribute("data-root") || "";
  var L = JSON.parse(box.getAttribute("data-labels")), index = null, loading = null, timer = 0;
  /* while searching, the results replace the page content */
  var day = [].slice.call(box.parentNode.children).filter(function (e) { return e !== box; });
  var chips = !!document.querySelector(".chip[data-tag]");
  var dateFmt = new Intl.DateTimeFormat(lang, { weekday: "long", day: "numeric", month: "long" });

  function fold(s) { return s.toLowerCase().replace(/ß/g, "ss").normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  function fill(s, v) { return s.replace(/\{(\w+)\}/g, function (m, k) { return k in v ? v[k] : m; }); }
  function tags() { try { return (JSON.parse(localStorage.getItem("lg.v1")) || {}).tags || {}; } catch (e) { return {}; } }
  function passTags(g, st) {
    var inc = false, any = false;
    for (var t in st) {
      if (st[t] === "out" && g.indexOf(t) >= 0) return false;
      if (st[t] === "in") { inc = true; if (g.indexOf(t) >= 0) any = true; }
    }
    return !inc || any;
  }
  function load() {
    if (!loading) loading = fetch(root + "data/search." + lang + ".json").then(function (r) {
      if (!r.ok) throw new Error(r.status); return r.json();
    }).then(function (d) { index = d.events; return index; });
    return loading;
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e;
  }
  function show(on) { box.hidden = !on; day.forEach(function (e) { e.hidden = on; }); }
  function msg(text) { box.textContent = ""; box.appendChild(el("p", "empty", text)); show(true); }

  function render(text) {
    var terms = fold(text).split(/\s+/).filter(Boolean), st = tags(), hits = [];
    index.forEach(function (e) {
      for (var i = 0; i < terms.length; i++) if (e.s.indexOf(terms[i]) < 0) return;
      if (!chips || passTags(e.g, st)) hits.push(e);  /* category filters only where shown */
    });
    if (!hits.length) return msg(fill(L.none, { q: text }));
    box.textContent = "";
    box.appendChild(el("h1", "day", fill(L.hits, { count: hits.length, q: text })));
    var list = el("div"), foot = el("div", "more-hits"), last = "", sec, shown = 0;
    box.appendChild(list); box.appendChild(foot);
    function more() {
      hits.slice(shown, shown + MAX).forEach(function (e) {
        if (e.d !== last) {
          last = e.d; sec = el("section", "sec");
          sec.appendChild(el("h2", "", dateFmt.format(new Date(e.d + "T12:00:00"))));
          list.appendChild(sec);
        }
        /* a card like on the day pages */
        var c = el("details", "ev t-" + e.g[0] + (e.x ? " st-" + e.x : "")), s = el("summary"), b = el("div", "body");
        s.appendChild(el("span", "time" + (/:/.test(e.u) ? "" : " untimed"), e.u));
        s.appendChild(el("span", "title", e.n)); s.appendChild(el("span", "place", e.p));
        if (e.x) { var m = el("span", "meta"); m.appendChild(el("span", "badge " + e.x, L[e.x])); s.appendChild(m); }
        b.appendChild(el("p", "desc", e.t)); if (e.v) b.appendChild(el("p", "venue", e.v));
        var p = el("p", "src", L.source + ": "), a = el("a", "", e.l.replace(/^https?:\/\//, ""));
        a.href = e.l; a.rel = "nofollow noopener"; a.target = "_blank"; p.appendChild(a); b.appendChild(p);
        a = el("a", "to-day", L.day + " →"); a.href = root + lang + "/" + e.d + "/" + (e.h ? "#" + e.h : "");
        b.appendChild(a); c.appendChild(s); c.appendChild(b); sec.appendChild(c);
      });
      shown = Math.min(hits.length, shown + MAX);
      foot.textContent = "";
      if (shown < hits.length) {
        foot.appendChild(el("p", "", fill(L.more, { shown: shown, count: hits.length })));
        var b = el("button", "load", fill(L.load, { n: Math.min(MAX, hits.length - shown) }));
        b.type = "button"; b.addEventListener("click", more); foot.appendChild(b);
      }
    }
    more();
    show(true);
  }

  function run() {
    var text = q.value.trim();
    if (!text) return show(false);
    if (text.length < MIN) return msg(fill(L.min, { min: MIN }));
    if (index) return render(text);
    load().then(function () { if (q.value.trim() === text) render(text); }, function () { msg(L.error); });
  }
  q.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(run, 200); });
  q.addEventListener("keydown", function (e) { if (e.key === "Escape") { q.value = ""; run(); } });
  document.addEventListener("lg:filter", function () { if (!box.hidden) run(); });
})();
