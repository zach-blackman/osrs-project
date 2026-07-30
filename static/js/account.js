(function () {
  "use strict";

  var TABS = ["overview", "hiscores", "clan", "settings"];
  var _activeTab = "overview";

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

  function fmtNum(n) {
    if (n == null || n < 0 || isNaN(n)) return "—";
    return Number(n).toLocaleString();
  }

  function fmtEhp(n) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toFixed(1);
  }

  function discordName(u) {
    if (!u) return "";
    if (u.discord_username) return "@" + u.discord_username;
    return u.username || "";
  }

  function icons() {
    return globalThis.OsrsIcons || null;
  }

  function normalizeTab(id) {
    var raw = String(id || "").replace(/^#/, "").toLowerCase();
    if (TABS.indexOf(raw) >= 0) return raw;
    return "overview";
  }

  function visibleTabs() {
    return TABS.filter(function (id) {
      var tab = $("tab-" + id);
      return tab && !tab.hidden;
    });
  }

  function showTab(id, opts) {
    opts = opts || {};
    var next = normalizeTab(id);
    var hiscoresTab = $("tab-hiscores");
    if (next === "hiscores" && hiscoresTab && hiscoresTab.hidden) {
      next = "overview";
    }

    _activeTab = next;
    TABS.forEach(function (tid) {
      var tab = $("tab-" + tid);
      var panel = $("panel-" + tid);
      var selected = tid === next;
      if (tab) {
        tab.setAttribute("aria-selected", selected ? "true" : "false");
        tab.tabIndex = selected ? 0 : -1;
      }
      if (panel) panel.hidden = !selected;
    });

    if (!opts.skipHash) {
      var hash = "#" + next;
      if (location.hash !== hash) {
        history.replaceState(null, "", hash);
      }
    }

    if (opts.focusTab) {
      var focusEl = $("tab-" + next);
      if (focusEl) focusEl.focus();
    }
  }

  function tabFromHash() {
    return normalizeTab(location.hash);
  }

  function initTabs() {
    var list = document.querySelector(".account-tabs");
    if (!list) return;

    list.addEventListener("click", function (e) {
      var btn = e.target.closest(".account-tab");
      if (!btn || btn.hidden) return;
      showTab(btn.getAttribute("data-tab"));
    });

    list.addEventListener("keydown", function (e) {
      var tabs = visibleTabs();
      if (!tabs.length) return;
      var idx = tabs.indexOf(_activeTab);
      if (idx < 0) idx = 0;
      var nextIdx = idx;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        nextIdx = (idx + 1) % tabs.length;
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        nextIdx = (idx - 1 + tabs.length) % tabs.length;
      } else if (e.key === "Home") {
        nextIdx = 0;
      } else if (e.key === "End") {
        nextIdx = tabs.length - 1;
      } else {
        return;
      }
      e.preventDefault();
      showTab(tabs[nextIdx], { focusTab: true });
    });

    window.addEventListener("hashchange", function () {
      showTab(tabFromHash(), { skipHash: true });
    });

    document.addEventListener("click", function (e) {
      var link = e.target.closest("[data-goto-tab]");
      if (!link) return;
      e.preventDefault();
      showTab(link.getAttribute("data-goto-tab"), { focusTab: true });
    });

    showTab(tabFromHash(), { skipHash: location.hash.length > 1 });
  }

  function renderProfile(u, clan) {
    var el = $("profile-card");
    if (!el) return;
    if (!u) {
      el.textContent = "Not signed in.";
      return;
    }
    var name = discordName(u) || u.username || "user";
    var avatar = u.avatar_url
      ? '<img class="account-avatar" src="' + esc(u.avatar_url) + '" alt="" width="72" height="72">'
      : '<div class="account-avatar-fallback" aria-hidden="true">' +
        esc(name.replace(/^@/, "").charAt(0).toUpperCase()) + "</div>";
    var discordLine = u.discord_username
      ? "@" + u.discord_username + (u.discord_id ? " · " + u.discord_id : "")
      : (u.discord_id || "invite account");
    var clanLine = "—";
    if (clan && (clan.name || clan.trackscape_code)) {
      clanLine = clan.name
        ? esc(clan.name)
        : "Linked";
      if (clan.trackscape_code) {
        clanLine += " · code set";
      }
    }
    el.innerHTML =
      avatar +
      '<div class="account-meta">' +
      '<div class="name">' + esc(name) + "</div>" +
      "<dl>" +
      "<dt>Discord</dt><dd>" + esc(discordLine) + "</dd>" +
      "<dt>RSN</dt><dd>" + esc(u.rsn || "—") + "</dd>" +
      "<dt>Clan</dt><dd>" + clanLine + "</dd>" +
      "<dt>Member since</dt><dd>" + esc(fmtWhen(u.created_at)) + "</dd>" +
      "<dt>Last sign-in</dt><dd>" + esc(fmtWhen(u.last_login_at)) + "</dd>" +
      "</dl>" +
      '<span class="role">' + esc(u.role || "user") + "</span>" +
      "</div>";
  }

  function setHiscoresVisible(show) {
    var tab = $("tab-hiscores");
    if (tab) tab.hidden = !show;
    if (!show && _activeTab === "hiscores") {
      showTab("overview");
      return;
    }
    if (show && normalizeTab(location.hash) === "hiscores" && _activeTab !== "hiscores") {
      showTab("hiscores", { skipHash: true });
    }
  }

  function hiscoresMessage(msg, kind) {
    var el = $("hiscores-panel");
    if (!el) return;
    el.className = "hiscores-panel" + (kind ? " " + kind : "");
    el.textContent = msg;
  }

  function hiscoresNeedRsn() {
    var el = $("hiscores-panel");
    if (!el) return;
    el.className = "hiscores-panel is-muted";
    el.innerHTML =
      '<div class="hiscores-empty">' +
        "<span>Set your RSN in Settings to load hiscores.</span>" +
        '<button type="button" class="ghost" data-goto-tab="settings">' +
          "Open Settings</button>" +
      "</div>";
  }

  function renderHiscores(player, meta) {
    var el = $("hiscores-panel");
    var ic = icons();
    if (!el || !player) return;

    var display = player.displayName || player.username || "—";
    var uname = player.username || "";
    var combat = player.combatLevel != null ? player.combatLevel : "—";
    var ptype = player.type || "regular";
    var skills = player.skills || {};
    var bosses = player.bosses || {};
    var overall = skills.overall || {};
    meta = meta || {};

    var combatIcon = ic
      ? '<img class="hiscores-icon" src="' + esc(ic.combatIconUrl()) +
        '" alt="" width="24" height="24">'
      : "";
    var badgeUrl = ic && ic.typeBadgeUrl(ptype);
    var typeHtml = "";
    if (ptype && ptype !== "regular" && ptype !== "unknown") {
      typeHtml =
        '<span class="hs-type">' +
        (badgeUrl
          ? '<img class="hiscores-icon" src="' + esc(badgeUrl) +
            '" alt="" width="24" height="24">'
          : "") +
        esc(ptype) +
        "</span>";
    }

    var womHref = "https://wiseoldman.net/players/" +
      encodeURIComponent(uname || display);

    var order = (ic && ic.SKILL_ORDER) || [];
    var cells = [];
    for (var i = 0; i < order.length; i++) {
      var metric = order[i];
      var row = skills[metric] || {};
      var lvl = row.level;
      if (lvl == null || lvl < 0) lvl = "—";
      var label = ic ? ic.skillLabel(metric) : metric;
      var iconUrl = ic ? ic.skillIconUrl(metric) : null;
      var xp = row.experience;
      var rank = row.rank;
      var tip = label;
      if (xp != null && xp >= 0) tip += " · " + fmtNum(xp) + " xp";
      if (rank != null && rank >= 0) tip += " · rank " + fmtNum(rank);
      cells.push(
        '<div class="skill-cell" title="' + esc(tip) + '">' +
          (iconUrl
            ? '<img class="hiscores-icon" src="' + esc(iconUrl) +
              '" alt="" width="24" height="24">'
            : "") +
          '<span class="skill-lvl">' + esc(String(lvl)) + "</span>" +
          '<span class="skill-name">' + esc(label) + "</span>" +
        "</div>"
      );
    }

    var bossOrder = (ic && ic.BOSS_ORDER) || Object.keys(bosses);
    var bossCells = [];
    for (var b = 0; b < bossOrder.length; b++) {
      var bMetric = bossOrder[b];
      var bRow = bosses[bMetric] || {};
      var kills = bRow.kills;
      var killsDisp = (kills == null || kills < 0) ? "—" : fmtNum(kills);
      var bLabel = ic ? ic.bossLabel(bMetric) : bMetric;
      var bIconUrl = ic ? ic.bossIconUrl(bMetric) : null;
      var bRank = bRow.rank;
      var bEhb = bRow.ehb;
      var bTip = bLabel;
      if (kills != null && kills >= 0) bTip += " · " + fmtNum(kills) + " kc";
      if (bRank != null && bRank >= 0) bTip += " · rank " + fmtNum(bRank);
      if (bEhb != null && bEhb >= 0) bTip += " · " + fmtEhp(bEhb) + " ehb";
      bossCells.push(
        '<div class="skill-cell boss-cell" title="' + esc(bTip) + '">' +
          (bIconUrl
            ? '<img class="hiscores-icon" src="' + esc(bIconUrl) +
              '" alt="" width="24" height="24">'
            : "") +
          '<span class="skill-lvl">' + esc(killsDisp) + "</span>" +
          '<span class="skill-name">' + esc(bLabel) + "</span>" +
        "</div>"
      );
    }

    var cacheNote = "";
    if (meta.cached && meta.cached_at) {
      cacheNote = "<span><strong>Cache</strong> " +
        esc(fmtWhen(new Date(meta.cached_at * 1000).toISOString())) +
        "</span>";
    }

    el.className = "hiscores-panel";
    el.innerHTML =
      '<div class="hiscores-head">' +
        '<span class="hs-name">' + esc(display) + "</span>" +
        '<span class="hs-combat">' + combatIcon +
          "Lvl " + esc(String(combat)) + "</span>" +
        typeHtml +
        '<a class="hs-wom" href="' + esc(womHref) +
          '" target="_blank" rel="noopener noreferrer">Wise Old Man ↗</a>' +
      "</div>" +
      '<div class="hiscores-meta">' +
        "<span><strong>Total</strong> " +
          esc(String(overall.level != null && overall.level >= 0
            ? overall.level : "—")) + "</span>" +
        "<span><strong>XP</strong> " + esc(fmtNum(player.exp)) + "</span>" +
        "<span><strong>EHP</strong> " + esc(fmtEhp(player.ehp)) + "</span>" +
        "<span><strong>EHB</strong> " + esc(fmtEhp(player.ehb)) + "</span>" +
        (player.updatedAt
          ? "<span><strong>Updated</strong> " +
            esc(fmtWhen(player.updatedAt)) + "</span>"
          : "") +
        cacheNote +
      "</div>" +
      '<h2 class="hiscores-subhead">Skills</h2>' +
      '<div class="skill-grid">' + cells.join("") + "</div>" +
      '<h2 class="hiscores-subhead">Boss KC</h2>' +
      '<p class="hiscores-note">Lifetime kill counts from Wise Old Man hiscores. Unranked bosses show —.</p>' +
      '<div class="skill-grid boss-grid">' + bossCells.join("") + "</div>";
  }

  var _womReq = 0;

  function loadHiscores(rsn) {
    if (!rsn) {
      setHiscoresVisible(true);
      hiscoresNeedRsn();
      return;
    }
    setHiscoresVisible(true);
    hiscoresMessage("Loading hiscores…", "is-muted");
    var req = ++_womReq;
    fetch("/api/me/wom")
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; });
      })
      .then(function (res) {
        if (req !== _womReq) return;
        if (!res.ok) {
          var err = (res.d && (res.d.error || res.d.detail)) ||
            ("Could not load hiscores (" + res.status + ")");
          hiscoresMessage(err, "is-err");
          return;
        }
        renderHiscores(res.d.player, res.d);
      })
      .catch(function () {
        if (req !== _womReq) return;
        hiscoresMessage("Could not load hiscores", "is-err");
      });
  }

  function hydrateForm(me) {
    var u = me.user || {};
    var prefs = me.prefs || {};
    var clan = me.clan || {};
    $("pref-discord-name").value = discordName(u) || "—";
    $("pref-rsn").value = u.rsn || "";
    $("pref-trackscape").value = clan.trackscape_code || "";
    var theme = prefs.theme === "light" ? "light" : "dark";
    $("pref-theme").value = theme;
    var hint = $("clan-hint");
    if (hint) {
      hint.textContent = clan.name
        ? ("Joined clan: " + clan.name + ". Clear the code and save to leave.")
        : "Same code as the RuneLite Trackscape Connector. Clears membership if left empty and saved.";
    }
  }

  function renderClan(u, clan) {
    var el = $("clan-panel");
    var verify = $("clan-verify");
    var statsSec = $("clan-stats");
    if (!el) return;
    if (!u) {
      el.className = "clan-panel is-muted";
      el.textContent = "Sign in to join a clan.";
      if (verify) verify.hidden = true;
      if (statsSec) statsSec.hidden = true;
      return;
    }
    var status = u.membership_status || "none";
    if (!clan || !clan.id) {
      el.className = "clan-panel is-muted";
      el.innerHTML =
        "Not in a clan yet — enter the clan join code in " +
        '<button type="button" class="account-text-link" data-goto-tab="settings">' +
        "Settings</button> and save.";
      if (verify) verify.hidden = true;
      if (statsSec) statsSec.hidden = true;
      return;
    }
    var providers = clan.providers || {};
    var badges =
      '<div class="clan-badges">' +
        '<span class="clan-badge' + (providers.trackscape ? " on" : "") +
          '">Trackscape</span>' +
        '<span class="clan-badge' + (providers.wom ? " on" : "") +
          '">Wise Old Man</span>' +
        '<span class="clan-badge' +
          (status === "verified" ? " tier-verified" : " on") + '">' +
          esc(status) + "</span>" +
      "</div>";
    var womLink = clan.wom_url
      ? '<a href="' + esc(clan.wom_url) +
        '" target="_blank" rel="noopener noreferrer">Clan WOM group ↗</a>'
      : "—";
    el.className = "clan-panel";
    el.innerHTML =
      '<div class="clan-name">' +
        esc(clan.name || ("Clan #" + clan.id)) + "</div>" +
      badges +
      "<dl>" +
      "<dt>Join code</dt><dd>" +
        esc(clan.trackscape_code ? "set" : "—") + "</dd>" +
      "<dt>WOM group</dt><dd>" + womLink + "</dd>" +
      "<dt>Membership</dt><dd>" + esc(status) + "</dd>" +
      "</dl>";
    if (verify) verify.hidden = false;
    if (statsSec) {
      if (status === "verified" && providers.wom) {
        statsSec.hidden = false;
        loadClanStats();
      } else {
        statsSec.hidden = true;
      }
    }
  }

  function setVerifyStatus(msg, kind) {
    var el = $("verify-status");
    if (!el) return;
    el.hidden = !msg;
    el.textContent = msg || "";
    el.className = "prefs-status" + (kind ? " " + kind : "");
  }

  function postJson(url) {
    return fetch(url, { method: "POST" }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    });
  }

  function refreshMeThen(cb) {
    if (globalThis.ClanShellUser && ClanShellUser.load) {
      ClanShellUser.load(function (err, me) {
        if (!err && me) initFromMe(me);
        if (cb) cb(me || null);
      });
    } else if (cb) cb(null);
  }

  function onChallengeStart() {
    setVerifyStatus("Starting challenge…");
    postJson("/api/me/rsn/challenge/start").then(function (res) {
      if (!res.ok) {
        setVerifyStatus(res.d.error || "Failed", "err");
        return;
      }
      setVerifyStatus(
        "Snapshot XP " + res.d.overall_xp +
          ". Gain XP in-game, wait for hiscores, then confirm.",
        "ok");
    }).catch(function () { setVerifyStatus("Failed", "err"); });
  }

  function onChallengeConfirm() {
    setVerifyStatus("Checking XP…");
    postJson("/api/me/rsn/challenge/confirm").then(function (res) {
      if (!res.ok) {
        setVerifyStatus(res.d.error || "Failed", "err");
        return;
      }
      setVerifyStatus("RSN ownership verified (+" + res.d.gained + " XP)", "ok");
      refreshMeThen();
    }).catch(function () { setVerifyStatus("Failed", "err"); });
  }

  function onRosterVerify() {
    setVerifyStatus("Checking WOM roster…");
    postJson("/api/me/clan/verify-roster").then(function (res) {
      if (!res.ok) {
        setVerifyStatus(res.d.error || "Failed", "err");
        return;
      }
      setVerifyStatus("Roster verified — membership is verified.", "ok");
      refreshMeThen();
    }).catch(function () { setVerifyStatus("Failed", "err"); });
  }

  var METRIC_LABEL = {
    ehb: "EHB",
    ehp: "EHP",
    chambers_of_xeric: "CoX",
    chambers_of_xeric_challenge_mode: "CoX CM",
    theatre_of_blood: "ToB",
    theatre_of_blood_hard_mode: "ToB HM",
    tombs_of_amascut: "ToA",
    tombs_of_amascut_expert: "ToA Expert",
  };

  function loadClanStats() {
    var el = $("clan-stats-panel");
    if (!el) return;
    el.className = "clan-stats-panel is-muted";
    el.textContent = "Loading clan stats…";
    fetch("/api/clan/stats")
      .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      })
      .then(function (res) {
        if (!res.ok) {
          el.className = "clan-stats-panel is-err";
          el.textContent = res.d.error || "Could not load clan stats";
          return;
        }
        var stats = res.d.stats || {};
        var totals = stats.totals || {};
        var keys = stats.metrics || Object.keys(totals);
        var cells = keys.map(function (m) {
          var v = totals[m];
          if (v == null || v === 0) return "";
          var shown = (m === "ehb" || m === "ehp")
            ? Number(v).toFixed(1)
            : fmtNum(Math.round(v));
          return '<div class="clan-stat-cell">' +
            '<div class="label">' + esc(METRIC_LABEL[m] || m) + "</div>" +
            '<div class="val">' + esc(String(shown)) + "</div></div>";
        }).filter(Boolean);
        el.className = "clan-stats-panel";
        el.innerHTML =
          (stats.note
            ? '<p class="field-hint">' + esc(stats.note) + "</p>"
            : "") +
          (cells.length
            ? '<div class="clan-stats-grid">' + cells.join("") + "</div>"
            : '<p class="field-hint">No weekly gains recorded yet for this roster.</p>');
      })
      .catch(function () {
        el.className = "clan-stats-panel is-err";
        el.textContent = "Could not load clan stats";
      });
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
        rsn: $("pref-rsn").value,
        trackscape_code: $("pref-trackscape").value,
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
        renderProfile(res.d.user, res.d.clan);
        renderClan(res.d.user, res.d.clan);
        hydrateForm(res.d);
        loadHiscores(res.d.user && res.d.user.rsn);
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
      renderClan(null, null);
      setHiscoresVisible(false);
      return;
    }
    renderProfile(me.user, me.clan);
    renderClan(me.user, me.clan);
    hydrateForm(me);
    loadHiscores(me.user.rsn);
  }

  initTabs();

  $("prefs-form").addEventListener("submit", onSave);

  var btnCh = $("btn-rsn-challenge");
  var btnCf = $("btn-rsn-confirm");
  var btnRo = $("btn-roster-verify");
  if (btnCh) btnCh.addEventListener("click", onChallengeStart);
  if (btnCf) btnCf.addEventListener("click", onChallengeConfirm);
  if (btnRo) btnRo.addEventListener("click", onRosterVerify);

  $("pref-theme").addEventListener("change", function () {
    var theme = $("pref-theme").value === "light" ? "light" : "dark";
    if (globalThis.ClanShell && ClanShell.persistTheme) {
      ClanShell.persistTheme(theme);
    } else if (globalThis.ClanShell && ClanShell.applyTheme) {
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
