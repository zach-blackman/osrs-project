(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtWhen(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return "—";
      return d.toLocaleString(undefined, {
        dateStyle: "medium", timeStyle: "short"
      });
    } catch (e) {
      return "—";
    }
  }

  function renderProfile(u) {
    var el = $("profile-card");
    if (!u) {
      el.textContent = "Not signed in.";
      return;
    }
    var name = u.effective_name || u.username || "user";
    var avatar = u.avatar_url
      ? '<img class="account-avatar" src="' + esc(u.avatar_url) + '" alt="" width="72" height="72">'
      : '<div class="account-avatar-fallback" aria-hidden="true">' +
        esc(name.charAt(0).toUpperCase()) + "</div>";
    var discordLine = u.discord_username
      ? "@" + u.discord_username + (u.discord_id ? " · " + u.discord_id : "")
      : (u.discord_id || "invite account");
    el.innerHTML =
      avatar +
      '<div class="account-meta">' +
      '<div class="name">' + esc(name) + "</div>" +
      "<dl>" +
      "<dt>Discord</dt><dd>" + esc(discordLine) + "</dd>" +
      "<dt>Member since</dt><dd>" + esc(fmtWhen(u.created_at)) + "</dd>" +
      "<dt>Last sign-in</dt><dd>" + esc(fmtWhen(u.last_login_at)) + "</dd>" +
      "</dl>" +
      '<span class="role">' + esc(u.role || "user") + "</span>" +
      "</div>";
  }

  function hydrateForm(me) {
    var u = me.user || {};
    var prefs = me.prefs || {};
    $("pref-display-name").value = u.display_name || "";
    $("pref-rsn").value = u.rsn || "";
    var theme = prefs.theme === "light" ? "light" : "dark";
    $("pref-theme").value = theme;
    if (globalThis.ClanShell && ClanShell.applyTheme) {
      ClanShell.applyTheme(theme);
    }
  }

  function setStatus(msg, kind) {
    var el = $("prefs-status");
    el.hidden = !msg;
    el.textContent = msg || "";
    el.className = "prefs-status" + (kind ? " " + kind : "");
  }

  function onSave(e) {
    e.preventDefault();
    var btn = $("prefs-save");
    btn.disabled = true;
    setStatus("Saving…");
    var theme = $("pref-theme").value === "light" ? "light" : "dark";
    fetch("/api/me/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: $("pref-display-name").value,
        rsn: $("pref-rsn").value,
        theme: theme,
      }),
    })
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        btn.disabled = false;
        if (!res.ok) {
          setStatus(res.d.error || "Save failed", "err");
          return;
        }
        renderProfile(res.d.user);
        if (globalThis.ClanShell && ClanShell.applyTheme) {
          ClanShell.applyTheme(theme);
        }
        if (globalThis.ClanShellUser && ClanShellUser.load) {
          ClanShellUser.load();
        }
        setStatus("Saved", "ok");
      })
      .catch(function () {
        btn.disabled = false;
        setStatus("Save failed", "err");
      });
  }

  function initFromMe(me) {
    if (!me || !me.user) {
      renderProfile(null);
      return;
    }
    renderProfile(me.user);
    hydrateForm(me);
  }

  $("prefs-form").addEventListener("submit", onSave);

  $("pref-theme").addEventListener("change", function () {
    var theme = $("pref-theme").value === "light" ? "light" : "dark";
    if (globalThis.ClanShell && ClanShell.applyTheme) {
      ClanShell.applyTheme(theme);
    }
  });

  document.addEventListener("shell:theme", function (e) {
    var t = (e.detail && e.detail.theme) || "dark";
    var sel = $("pref-theme");
    if (sel && sel.value !== t) sel.value = t;
  });

  document.addEventListener("shell:me", function (e) {
    initFromMe(e.detail);
  });

  if (globalThis.ClanShellUser && ClanShellUser.me && ClanShellUser.me()) {
    initFromMe(ClanShellUser.me());
  }
})();
