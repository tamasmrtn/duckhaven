/*
 * Light / Dark / System theme switcher that mirrors the DuckHaven web app
 * (web/src/components/app/TopBar.tsx + web/src/hooks/useTheme.ts): a ghost icon
 * button that opens a small dropdown, with Lucide Sun/Moon/Monitor icons and the
 * same `dh-theme` localStorage key. It drives Material's color scheme.
 */
(function () {
  "use strict";

  var KEY = "dh-theme";

  // Lucide icons (https://lucide.dev) — sun, moon, monitor.
  var ICONS = {
    light:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>',
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>',
    system:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/></svg>',
  };

  var OPTIONS = [
    ["light", "Light"],
    ["dark", "Dark"],
    ["system", "System"],
  ];

  function getTheme() {
    try {
      return localStorage.getItem(KEY) || "system";
    } catch (e) {
      return "system";
    }
  }

  function prefersDark() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function isDark(theme) {
    return theme === "dark" || (theme === "system" && prefersDark());
  }

  function applyScheme(theme) {
    var scheme = isDark(theme) ? "slate" : "default";
    document.documentElement.setAttribute("data-md-color-scheme", scheme);
    if (document.body) {
      document.body.setAttribute("data-md-color-scheme", scheme);
    }
  }

  var wrap;

  function render() {
    if (!wrap) return;
    var theme = getTheme();
    wrap.querySelector(".dh-theme__trigger").innerHTML = ICONS[theme];
    wrap.querySelectorAll(".dh-theme__item").forEach(function (item) {
      item.setAttribute(
        "aria-checked",
        item.dataset.value === theme ? "true" : "false",
      );
    });
  }

  function setTheme(theme) {
    try {
      localStorage.setItem(KEY, theme);
    } catch (e) {
      /* ignore */
    }
    applyScheme(theme);
    render();
  }

  function build() {
    var inner = document.querySelector(".md-header__inner");
    if (!inner || inner.querySelector(".dh-theme")) return;

    wrap = document.createElement("div");
    wrap.className = "dh-theme";

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "dh-theme__trigger md-header__button";
    trigger.setAttribute("aria-label", "Change theme");
    trigger.setAttribute("aria-haspopup", "true");

    var menu = document.createElement("div");
    menu.className = "dh-theme__menu";
    menu.setAttribute("role", "menu");

    OPTIONS.forEach(function (opt) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "dh-theme__item";
      item.setAttribute("role", "menuitemradio");
      item.dataset.value = opt[0];
      item.innerHTML = ICONS[opt[0]] + "<span>" + opt[1] + "</span>";
      item.addEventListener("click", function () {
        setTheme(opt[0]);
        wrap.classList.remove("dh-theme--open");
      });
      menu.appendChild(item);
    });

    wrap.appendChild(trigger);
    wrap.appendChild(menu);

    var source = inner.querySelector(".md-header__source");
    if (source) inner.insertBefore(wrap, source);
    else inner.appendChild(wrap);

    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      wrap.classList.toggle("dh-theme--open");
    });
    menu.addEventListener("click", function (e) {
      e.stopPropagation();
    });
    document.addEventListener("click", function () {
      wrap.classList.remove("dh-theme--open");
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") wrap.classList.remove("dh-theme--open");
    });

    render();
  }

  // Follow the OS when in "system" mode.
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", function () {
      if (getTheme() === "system") applyScheme("system");
    });

  function init() {
    applyScheme(getTheme());
    build();
  }

  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
