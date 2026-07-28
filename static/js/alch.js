/* Alch Desk stub — status + thin /api/alch summary. */
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }

  function load() {
    if (window.ClanToolStatus) ClanToolStatus.refresh();
    var box = $("stub-summary");
    if (!box) return;
    fetch("/api/alch?top=5")
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        var d = res.d || {};
        if (!res.ok) {
          box.textContent = d.error || "No snapshot yet — wait for the writer.";
          return;
        }
        var lines = [
          "nature_cost " + (d.nature_cost || 0).toLocaleString() + " gp",
          "top " + (d.rows || []).length + " of candidates (scaffold)"
        ];
        (d.rows || []).slice(0, 5).forEach(function (r, i) {
          lines.push((i + 1) + ". " + r.name + "  +" + (r.profit || 0).toLocaleString() + " gp");
        });
        if (!(d.rows || []).length) {
          lines.push("(no profitable alchs yet — need highalch on items + a pulse)");
        }
        box.textContent = lines.join("\n");
      })
      .catch(function () {
        box.textContent = "Could not reach /api/alch.";
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
