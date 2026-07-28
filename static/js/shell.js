/* Clan app shell — tool registry, drawer, theme. */
(function (global) {
  "use strict";

  var TOOLS = [
    { id: "merch", href: "/merch", label: "Merch Desk", status: "live" }
    // Add tools here: { id: "foo", href: "/foo", label: "…", status: "soon" }
  ];

  function $(id) { return document.getElementById(id); }

  function applyTheme(theme) {
    var t = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", t);
    var btn = $("theme-toggle");
    if (btn) {
      var nextLabel = t === "dark" ? "Switch to light mode" : "Switch to dark mode";
      btn.setAttribute("aria-label", nextLabel);
      btn.setAttribute("title", nextLabel);
    }
    try { localStorage.setItem("merchdesk.theme", t); } catch (e) {}
    document.dispatchEvent(new CustomEvent("shell:theme", { detail: { theme: t } }));
  }

  function initTheme() {
    var saved = "dark";
    try { saved = localStorage.getItem("merchdesk.theme") || "dark"; } catch (e) {}
    applyTheme(saved);
    var btn = $("theme-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        applyTheme(next);
      });
    }
  }

  function currentToolId() {
    var el = document.body;
    return (el && el.getAttribute("data-tool")) || "";
  }

  function renderToolList() {
    var ul = $("nav-tools");
    if (!ul) return;
    var active = currentToolId();
    ul.innerHTML = TOOLS.map(function (t) {
      var isActive = t.id === active;
      var badge = t.status === "live" ? (isActive ? "open" : "") : "soon";
      if (t.status === "live") {
        return '<li><a href="' + t.href + '" class="' + (isActive ? "active" : "")
          + '" data-tool-id="' + t.id + '"><span>' + t.label + '</span>'
          + (badge ? '<span class="badge">' + badge + '</span>' : "") + "</a></li>";
      }
      return '<li><span class="nav-soon" aria-disabled="true"><span>' + t.label
        + '</span><span class="badge">soon</span></span></li>';
    }).join("");
  }

  var focusBefore = null;

  function openNav() {
    var drawer = $("nav-drawer");
    var backdrop = $("nav-backdrop");
    if (!drawer) return;
    focusBefore = document.activeElement;
    drawer.classList.add("open");
    if (backdrop) backdrop.classList.add("on");
    document.body.classList.add("nav-open");
    drawer.setAttribute("aria-hidden", "false");
    var closeBtn = $("nav-drawer-close");
    if (closeBtn) closeBtn.focus();
  }

  function closeNav() {
    var drawer = $("nav-drawer");
    var backdrop = $("nav-backdrop");
    if (!drawer) return;
    drawer.classList.remove("open");
    if (backdrop) backdrop.classList.remove("on");
    document.body.classList.remove("nav-open");
    drawer.setAttribute("aria-hidden", "true");
    if (focusBefore && focusBefore.focus) {
      try { focusBefore.focus(); } catch (e) {}
    }
  }

  function toggleNav() {
    var drawer = $("nav-drawer");
    if (drawer && drawer.classList.contains("open")) closeNav();
    else openNav();
  }

  function initDrawer() {
    renderToolList();
    var toggle = $("nav-toggle");
    var closeBtn = $("nav-drawer-close");
    var backdrop = $("nav-backdrop");
    if (toggle) toggle.addEventListener("click", toggleNav);
    if (closeBtn) closeBtn.addEventListener("click", closeNav);
    if (backdrop) backdrop.addEventListener("click", closeNav);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && $("nav-drawer") && $("nav-drawer").classList.contains("open")) {
        e.preventDefault();
        closeNav();
      }
    });
  }

  function init() {
    initTheme();
    initDrawer();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.ClanShell = {
    TOOLS: TOOLS,
    applyTheme: applyTheme,
    openNav: openNav,
    closeNav: closeNav,
    isMobile: function () {
      return window.matchMedia("(max-width: 860px)").matches;
    }
  };
})(window);
