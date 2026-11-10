/* Let's Gau – sends the contact form in the background and shows the thank-you message on
   the page. Without JavaScript the form is posted normally (the service's answer is shown). */
(function () {
  "use strict";
  var f = document.getElementById("fb");
  if (!f || !window.fetch) return;
  var done = document.getElementById("fb-done"), err = document.getElementById("fb-error");
  var btn = f.querySelector("button[type=submit]"), label = btn.textContent;
  function thanks() { f.hidden = true; done.hidden = false; done.focus(); }
  f.addEventListener("submit", function (e) {
    e.preventDefault();
    if (f.website && f.website.value) return thanks();  /* honeypot filled: a bot; send nothing */
    err.hidden = true; btn.disabled = true; btn.textContent = f.getAttribute("data-sending");
    /* no-cors: the answer cannot be read, so only a network failure is an error */
    fetch(f.action, { method: "POST", mode: "no-cors", body: new URLSearchParams(new FormData(f)) })
      .then(thanks, function () { err.hidden = false; btn.disabled = false; btn.textContent = label; });
  });
})();
