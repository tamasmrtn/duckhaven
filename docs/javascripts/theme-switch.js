/*
 * Dark / light theme toggle. Dark is the default. A single button flips between
 * the two, persisting the choice in `dh-theme` localStorage (the same key the
 * web app uses) and driving Material's color scheme (slate <-> default).
 */
(function () {
  "use strict";

  var KEY = "dh-theme";

  // Lucide icons (https://lucide.dev) — sun, moon.
  var SUN =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>';
  var MOON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';

  function getTheme() {
    try {
      return localStorage.getItem(KEY) === "light" ? "light" : "dark";
    } catch (e) {
      return "dark";
    }
  }

  function applyScheme(theme) {
    var scheme = theme === "dark" ? "slate" : "default";
    document.documentElement.setAttribute("data-md-color-scheme", scheme);
    if (document.body) {
      document.body.setAttribute("data-md-color-scheme", scheme);
    }
  }

  var btn;

  function render() {
    if (!btn) return;
    var dark = getTheme() === "dark";
    // Show the icon for the mode the click will switch TO.
    btn.innerHTML = dark ? SUN : MOON;
    var label = dark ? "Switch to light mode" : "Switch to dark mode";
    btn.setAttribute("aria-label", label);
    btn.setAttribute("title", label);
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

  function toggle() {
    setTheme(getTheme() === "dark" ? "light" : "dark");
  }

  function build() {
    var inner = document.querySelector(".md-header__inner");
    if (!inner || inner.querySelector(".dh-theme")) return;

    var wrap = document.createElement("div");
    wrap.className = "dh-theme";

    btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dh-theme__trigger md-header__button";
    btn.addEventListener("click", toggle);
    wrap.appendChild(btn);

    var source = inner.querySelector(".md-header__source");
    if (source) inner.insertBefore(wrap, source);
    else inner.appendChild(wrap);

    render();
  }

  function init() {
    applyScheme(getTheme());
    build();
  }

  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
