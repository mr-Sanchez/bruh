// Tiny hash router: #/today (home), #/practice[/<topic>] («Занятия»),
// #/record[/<prompt>], #/picture, #/talk[/<prompt>], #/shadowing[/<session>:<n>],
// #/dictation[/<videoId>], #/roadmap, #/speak/<lesson>:<task>, #/moduletest/<module>,
// #/history, #/session/<id>, #/progress. No build step,
// no framework - just enough to switch between the view modules loaded above.
(() => {
  const DEFAULT_ROUTE = "today";
  // Activities (the monologue recorder) live under «Занятия».
  const TAB_FOR_ROUTE = {
    today: "today",
    practice: "practice",
    record: "practice",
    picture: "practice",
    talk: "practice",
    shadowing: "practice",
    dictation: "practice",
    roadmap: "practice",
    speak: "practice",
    moduletest: "practice",
    history: "history",
    session: "history",
    progress: "progress",
  };

  let currentView = null;

  function parseHash() {
    const hash = location.hash.replace(/^#\/?/, "");
    const [route, param] = hash.split("/");
    return { route: route || DEFAULT_ROUTE, param };
  }

  function setActiveTab(route) {
    const tab = TAB_FOR_ROUTE[route] || DEFAULT_ROUTE;
    document.querySelectorAll(".tabs a").forEach((a) => {
      const active = a.dataset.route === tab;
      a.classList.toggle("active", active);
      if (active) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function render() {
    const { route, param } = parseHash();
    const container = document.getElementById("app");

    if (currentView && typeof currentView.dispose === "function") {
      currentView.dispose();
    }

    const view = (window.Views && window.Views[route]) || window.Views[DEFAULT_ROUTE];
    currentView = view;
    setActiveTab(route);
    container.innerHTML = "";
    window.scrollTo(0, 0);
    view.render(container, param);
  }

  window.addEventListener("hashchange", render);
  window.addEventListener("DOMContentLoaded", () => {
    if (!location.hash) location.hash = `#/${DEFAULT_ROUTE}`;
    render();
  });
})();
