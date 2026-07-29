/* Shell user chip: avatar/name, sign out, admin link, hydrate from /api/me. */
(function (global) {
  "use strict";

  var ME = null;

  function $(id) { return document.getElementById(id); }

  function ensureSlot() {
    var actions = document.querySelector(".shell-actions");
    if (!actions || $("shell-user")) return;
    var wrap = document.createElement("div");
    wrap.id = "shell-user";
    wrap.className = "shell-user";
    actions.insertBefore(wrap, actions.firstChild);
  }

  function render() {
    ensureSlot();
    var el = $("shell-user");
    if (!el) return;
    if (!ME || !ME.user) {
      el.innerHTML = "";
      return;
    }
    var u = ME.user;
    var admin = u.role === "admin"
      ? '<a class="shell-user-admin" href="/admin">Admin</a>' : "";
    el.innerHTML =
      '<span class="shell-user-name" title="' + (u.username || "") + '">' +
      (u.username || "member") + "</span>" +
      admin +
      '<form method="post" action="/logout" class="shell-logout-form">' +
      '<button type="submit" class="shell-logout" title="Sign out" aria-label="Sign out">Out</button></form>';
  }

  function load(cb) {
    fetch("/api/me")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ME = d;
        render();
        document.dispatchEvent(new CustomEvent("shell:me", { detail: d }));
        if (cb) cb(null, d);
      })
      .catch(function (err) { if (cb) cb(err); });
  }

  function init() {
    ensureSlot();
    load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.ClanShellUser = { load: load, me: function () { return ME; } };
})(window);
