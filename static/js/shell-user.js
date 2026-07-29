/* Shell user chip: sign-in modal, avatar/name, sign out, admin link, hydrate from /api/me. */
(function (global) {
  "use strict";

  var ME = null;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function ensureSlot() {
    var actions = document.querySelector(".shell-actions");
    if (!actions || $("shell-user")) return;
    var wrap = document.createElement("div");
    wrap.id = "shell-user";
    wrap.className = "shell-user";
    actions.insertBefore(wrap, actions.firstChild);
  }

  function ensureModal() {
    if ($("shell-auth-modal")) return;
    var backdrop = document.createElement("div");
    backdrop.id = "shell-auth-backdrop";
    backdrop.className = "shell-auth-backdrop";
    backdrop.setAttribute("hidden", "");
    var modal = document.createElement("div");
    modal.id = "shell-auth-modal";
    modal.className = "shell-auth-modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "shell-auth-title");
    modal.setAttribute("hidden", "");
    modal.innerHTML =
      '<button type="button" class="shell-auth-close" aria-label="Close">&times;</button>' +
      '<h2 id="shell-auth-title">Sign in</h2>' +
      '<p class="shell-auth-blurb">Use Discord to verify you are a real person and open your profile.</p>' +
      '<div class="shell-auth-actions"></div>';
    document.body.appendChild(backdrop);
    document.body.appendChild(modal);
    backdrop.addEventListener("click", closeModal);
    modal.querySelector(".shell-auth-close").addEventListener("click", closeModal);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("shell-auth-modal").hasAttribute("hidden")) {
        closeModal();
      }
    });
  }

  function openModal() {
    ensureModal();
    var auth = (ME && ME.auth) || {};
    var actions = $("shell-auth-modal").querySelector(".shell-auth-actions");
    var html = "";
    if (auth.discord) {
      html += '<a class="shell-auth-discord" href="/auth/discord">Continue with Discord</a>';
    } else {
      html += '<p class="shell-auth-note">Discord sign-in is not configured on this server.</p>';
    }
    if (auth.invites) {
      html += '<a class="shell-auth-secondary" href="/login">Use invite account</a>';
    }
    actions.innerHTML = html;
    $("shell-auth-backdrop").removeAttribute("hidden");
    $("shell-auth-modal").removeAttribute("hidden");
    var focusEl = actions.querySelector("a, button");
    if (focusEl) focusEl.focus();
  }

  function closeModal() {
    var backdrop = $("shell-auth-backdrop");
    var modal = $("shell-auth-modal");
    if (backdrop) backdrop.setAttribute("hidden", "");
    if (modal) modal.setAttribute("hidden", "");
  }

  function render() {
    ensureSlot();
    var el = $("shell-user");
    if (!el) return;
    if (!ME || !ME.user) {
      el.innerHTML =
        '<button type="button" class="shell-signin" id="shell-signin">Sign in</button>';
      var btn = $("shell-signin");
      if (btn) btn.addEventListener("click", openModal);
      return;
    }
    var u = ME.user;
    var name = u.effective_name || u.username || "user";
    var admin = u.role === "admin"
      ? '<a class="shell-user-admin" href="/admin">Admin</a>' : "";
    var avatar = u.avatar_url
      ? '<img class="shell-user-avatar" src="' + esc(u.avatar_url) + '" alt="" width="24" height="24">'
      : "";
    el.innerHTML =
      '<a class="shell-user-link" href="/account" title="My Account">' +
      avatar +
      '<span class="shell-user-name">' + esc(name) + "</span></a>" +
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

  global.ClanShellUser = {
    load: load,
    me: function () { return ME; },
    openSignIn: openModal,
    closeSignIn: closeModal,
  };
})(window);
