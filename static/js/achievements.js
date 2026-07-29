(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }

  function loadFeed() {
    var q = ["top=80"];
    var t = $("f-type").value;
    var min = $("f-min").value.trim();
    var uid = $("f-user").value.trim();
    if (t) q.push("type=" + encodeURIComponent(t));
    if (min) q.push("min_value=" + encodeURIComponent(min.replace(/[^\d]/g, "") || "0"));
    if (uid) q.push("user=" + encodeURIComponent(uid));
    fetch("/api/achievements?" + q.join("&"))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var list = $("ach-list");
        var rows = d.rows || [];
        if (!rows.length) {
          list.innerHTML = '<div class="empty">No achievements yet — connect a RuneLite ingest token.</div>';
          return;
        }
        list.innerHTML = rows.map(function (r) {
          var wiki = r.item_id
            ? ' · <a href="https://oldschool.runescape.wiki/w/Special:Lookup?type=item&id=' +
              r.item_id + '" target="_blank" rel="noopener">Wiki</a>'
            : "";
          var val = r.value_gp != null ? (Number(r.value_gp).toLocaleString() + " gp") : "—";
          var when = r.occurred_at ? new Date(r.occurred_at * 1000).toLocaleString() : "";
          return '<article class="ach-card"><div class="top"><span class="who">' +
            esc(r.username || ("user " + r.user_id)) + '</span><span class="type">' +
            esc(r.event_type) + '</span></div><div class="title">' + esc(r.title) +
            '</div><div class="meta">' + esc(when) + " · " + val + wiki +
            (r.rsn ? " · " + esc(r.rsn) : "") +
            (r.detail ? "<br>" + esc(r.detail) : "") + "</div></article>";
        }).join("");
      })
      .catch(function () {
        $("ach-list").innerHTML = '<div class="empty">Could not load feed.</div>';
      });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function onMe(me) {
    if (!me || !me.user) return;
    $("token-prefix").textContent = "prefix: " + (me.ingest_token_prefix || "—");
  }

  $("f-apply").addEventListener("click", loadFeed);
  $("token-rotate").addEventListener("click", function () {
    fetch("/api/me/ingest-token/rotate", { method: "POST" })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          alert(res.d.error || "Sign in required to rotate token");
          return;
        }
        $("token-prefix").textContent = "prefix: " + res.d.prefix;
        var once = $("token-once");
        once.hidden = false;
        once.textContent = res.d.token;
      });
  });

  document.addEventListener("shell:me", function (ev) { onMe(ev.detail); });
  if (window.ClanToolStatus) ClanToolStatus.refresh();
  loadFeed();
})();
