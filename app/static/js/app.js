// Tiny hash router: #/record, #/history, #/session/<id>, #/progress,
// #/practice/<topic>. No build step, no framework - just enough to switch
// between the view modules loaded above.
(() => {
  const TAB_FOR_ROUTE = { record: "record", history: "history", session: "history", progress: "progress", practice: "progress" };

  let currentView = null;

  function parseHash() {
    const hash = location.hash.replace(/^#\/?/, "");
    const [route, param] = hash.split("/");
    return { route: route || "record", param };
  }

  function setActiveTab(route) {
    const tab = TAB_FOR_ROUTE[route] || "record";
    document.querySelectorAll(".tabs a").forEach((a) => {
      a.classList.toggle("active", a.dataset.route === tab);
    });
  }

  function render() {
    const { route, param } = parseHash();
    const container = document.getElementById("app");

    if (currentView && typeof currentView.dispose === "function") {
      currentView.dispose();
    }

    const view = (window.Views && window.Views[route]) || window.Views.record;
    currentView = view;
    setActiveTab(route);
    container.innerHTML = "";
    view.render(container, param);
  }

  window.addEventListener("hashchange", render);
  window.addEventListener("DOMContentLoaded", () => {
    if (!location.hash) location.hash = "#/record";
    render();
  });
})();
