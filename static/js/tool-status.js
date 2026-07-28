/* Shared snapshot age lamp for Clan Tools desks. */
(function (global) {
  "use strict";

  function $(id) { return document.getElementById(id); }

  function fmtAge(sec) {
    if (sec == null) return "no snapshot";
    if (sec < 60) return sec + "s ago";
    if (sec < 3600) return Math.round(sec / 60) + "m ago";
    return (sec / 3600).toFixed(1) + "h ago";
  }

  function setLamp(state) {
    var lamp = $("lamp");
    if (!lamp) return;
    lamp.className = "lamp" + (state ? " " + state : "");
  }

  function paintStatus(data) {
    var el = $("status");
    if (!el) return;
    if (!data) {
      el.textContent = "status unavailable";
      setLamp("");
      return;
    }
    var age = fmtAge(data.age_seconds);
    if (data.scanning) {
      el.textContent = age + " · scanning";
      setLamp("busy");
      return;
    }
    el.textContent = age;
    setLamp(data.ready ? "live" : "");
  }

  function refresh(cb) {
    fetch("/api/status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        paintStatus(d);
        if (cb) cb(null, d);
      })
      .catch(function (err) {
        paintStatus(null);
        if (cb) cb(err);
      });
  }

  global.ClanToolStatus = { refresh: refresh, paintStatus: paintStatus, fmtAge: fmtAge };
})(window);
