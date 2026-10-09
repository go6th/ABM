// Apply a saved user choice before hydration; when unset, CSS follows system preference.
try {
  const choice = localStorage.getItem("abm-theme");
  if (choice === "light" || choice === "dark") {
    document.documentElement.dataset.theme = choice;
  }
} catch {
  // The system preference remains active when browser storage is unavailable.
}
