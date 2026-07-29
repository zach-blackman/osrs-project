(function () {
  "use strict";

  var state = { q: "", status: "all", page: 1, perPage: 25, total: 0 };
  var debounceTimer = null;

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

  function loadUsers() {
    var el = $("user-list");
    el.textContent = "loading…";
    var params = new URLSearchParams({
      q: state.q,
      page: String(state.page),
      per_page: String(state.perPage),
      status: state.status,
    });
    fetch("/api/admin/users?" + params.toString())
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          el.textContent = res.d.error || "Admin only";
          $("member-summary").textContent = "";
          $("member-pager").hidden = true;
          return;
        }
        state.total = res.d.total || 0;
        state.page = res.d.page || state.page;
        renderSummary();
        renderPager();
        var users = res.d.users || [];
        if (!users.length) {
          el.textContent = state.q ? "No members match that search." : "No users yet.";
          return;
        }
        el.innerHTML = users.map(rowHtml).join("");
        el.querySelectorAll("button[data-id]").forEach(function (b) {
          b.addEventListener("click", function () {
            if (!confirm("Disable this user?")) return;
            fetch("/api/admin/users/" + b.getAttribute("data-id") + "/disable",
              { method: "POST" }).then(loadUsers);
          });
        });
      })
      .catch(function () {
        el.textContent = "Failed to load members.";
      });
  }

  function rowHtml(u) {
    var name = u.effective_name || u.username || "user";
    var dis = !!u.disabled_at;
    var avatar = u.avatar_url
      ? '<img class="user-avatar" src="' + esc(u.avatar_url) + '" alt="" width="36" height="36">'
      : '<div class="user-avatar-fallback" aria-hidden="true">' +
        esc(String(name).charAt(0).toUpperCase()) + "</div>";
    var provider = u.auth_provider === "discord"
      ? ("discord" + (u.discord_username ? " @" + u.discord_username : "") +
         (u.discord_id ? " · " + u.discord_id : ""))
      : "invite";
    var rsn = u.rsn ? " · rsn " + u.rsn : "";
    var btn = dis ? "" :
      '<button type="button" data-id="' + u.id + '">Disable</button>';
    return '<div class="user-row' + (dis ? " disabled" : "") + '">' +
      '<div class="user-row-main">' + avatar +
      "<div><div class=\"name\">" + esc(name) + "</div>" +
      '<div class="meta">#' + u.id + " · " + esc(u.role) + " · " + esc(provider) +
      esc(rsn) + (dis ? " · disabled" : "") + "</div>" +
      '<div class="times">' +
      "<span>Last sign-in " + esc(fmtWhen(u.last_login_at)) + "</span>" +
      "<span>Last seen " + esc(fmtWhen(u.last_seen_at)) + "</span>" +
      "<span>Joined " + esc(fmtWhen(u.created_at)) + "</span>" +
      "</div></div></div>" + btn + "</div>";
  }

  function renderSummary() {
    var start = state.total ? (state.page - 1) * state.perPage + 1 : 0;
    var end = Math.min(state.page * state.perPage, state.total);
    $("member-summary").textContent = state.total
      ? ("Showing " + start + "–" + end + " of " + state.total)
      : "0 members";
  }

  function pages() {
    return Math.max(1, Math.ceil(state.total / state.perPage));
  }

  function renderPager() {
    var pager = $("member-pager");
    var totalPages = pages();
    if (state.total <= state.perPage) {
      pager.hidden = true;
      return;
    }
    pager.hidden = false;
    $("member-page-label").textContent = "Page " + state.page + " of " + totalPages;
    $("member-prev").disabled = state.page <= 1;
    $("member-next").disabled = state.page >= totalPages;
  }

  function scheduleSearch() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () {
      state.q = $("member-q").value.trim();
      state.page = 1;
      loadUsers();
    }, 250);
  }

  $("member-q").addEventListener("input", scheduleSearch);
  $("member-status").addEventListener("change", function () {
    state.status = $("member-status").value;
    state.page = 1;
    loadUsers();
  });
  $("member-prev").addEventListener("click", function () {
    if (state.page > 1) { state.page -= 1; loadUsers(); }
  });
  $("member-next").addEventListener("click", function () {
    if (state.page < pages()) { state.page += 1; loadUsers(); }
  });

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
