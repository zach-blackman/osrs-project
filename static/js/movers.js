/* Movers Desk stub — status + thin /api/movers summary. */
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }

  function load() {
    if (window.ClanToolStatus) ClanToolStatus.refresh();
    var box = $("stub-summary");
    if (!box) return;
    fetch("/api/movers?window=6&top=5")
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        var d = res.d || {};
        var lines = [
          "scans_used " + (d.scans_used || 0) + " / window " + (d.window || 6),
          "top " + (d.rows || []).length + " movers (scaffold)"
        ];
        (d.rows || []).slice(0, 5).forEach(function (r, i) {
          var pct = r.pct_low != null ? r.pct_low + "%" : "—";
          var spike = r.vol_spike != null ? "×" + r.vol_spike : "—";
          lines.push((i + 1) + ". " + r.name + "  Δlow " + pct + "  vol " + spike);
        });
        if (!(d.rows || []).length) {
          lines.push("(need ≥2 ok pulsed scans with item_snapshots)");
        }
        box.textContent = lines.join("\n");
      })
      .catch(function () {
        box.textContent = "Could not reach /api/movers.";
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
