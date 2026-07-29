(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }

  function loadUsers() {
    fetch("/api/admin/users")
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        var el = $("user-list");
        if (!res.ok) {
          el.textContent = res.d.error || "Admin only";
          return;
        }
        el.innerHTML = (res.d.users || []).map(function (u) {
          var dis = u.disabled_at ? " · disabled" : "";
          var btn = u.disabled_at ? "" :
            '<button type="button" data-id="' + u.id + '">Disable</button>';
          return '<div class="user-row"><div><strong>' + esc(u.username) +
            '</strong><div class="meta">#' + u.id + " · " + esc(u.role) +
            (u.discord_id ? " · discord " + esc(u.discord_id) : " · invite") +
            dis + "</div></div>" + btn + "</div>";
        }).join("") || "No users yet.";
        el.querySelectorAll("button[data-id]").forEach(function (b) {
          b.addEventListener("click", function () {
            if (!confirm("Disable this user?")) return;
            fetch("/api/admin/users/" + b.getAttribute("data-id") + "/disable",
              { method: "POST" }).then(loadUsers);
          });
        });
      });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  $("mk-invite").addEventListener("click", function () {
    fetch("/api/admin/invites", { method: "POST" })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        var out = $("invite-out");
        out.hidden = false;
        if (!res.ok) {
          out.textContent = res.d.error || "failed";
          return;
        }
        out.textContent = location.origin + res.d.url + "\n\ntoken: " + res.d.token;
      });
  });

  loadUsers();
})();
