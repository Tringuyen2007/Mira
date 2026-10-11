// Classic (non-module) script loaded render-blocking from <head>, so the stored theme is on <html> before the
// first paint. main.js is a deferred module whose whole import graph loads first, which is too late to avoid a
// light -> projector flash. Same key and values as js/ui/theme.js.
(function () {
  try {
    var t = window.localStorage.getItem("mira.dashboard.theme");
    if (t === "light" || t === "projector") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: keep the default light theme */ }
})();
