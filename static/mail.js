/* Builds the contact links: the HTML holds only the parts of the address (see build.mail_link),
   so simple scrapers that do not run JavaScript find no address. */
document.querySelectorAll("a.mail[data-u]").forEach(function (a) {
  var m = a.dataset.u + "@" + a.dataset.d;
  a.textContent = m;
  a.href = "mailto:" + m + (a.dataset.s ? "?subject=" + a.dataset.s : "");
});
