document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  const header = document.getElementById("header");
  const objectTools = document.querySelector(".object-tools");
  const filterPanel = document.getElementById("changelist-filter");

  const syncHeaderOffset = () => {
    const offset = (header?.offsetHeight || 64) + 10;
    document.documentElement.style.setProperty("--jh-header-offset", `${offset}px`);
  };

  syncHeaderOffset();
  window.addEventListener("resize", syncHeaderOffset);

  if (!objectTools && !filterPanel) {
    return;
  }

  body.classList.add("jh-has-floating-tools");

  const floatingTools = document.createElement("div");
  floatingTools.className = "jh-floating-tools";
  document.body.appendChild(floatingTools);

  if (filterPanel) {
    const filterButton = document.createElement("button");
    filterButton.type = "button";
    filterButton.className = "jh-filter-toggle";
    filterButton.textContent = "Filters";
    filterButton.setAttribute("aria-expanded", "false");
    floatingTools.appendChild(filterButton);

    const backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "jh-filter-backdrop";
    backdrop.setAttribute("aria-label", "Close filters");
    document.body.appendChild(backdrop);

    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "jh-filter-close";
    closeButton.textContent = "Close";
    filterPanel.prepend(closeButton);

    const closeFilters = () => {
      body.classList.remove("jh-filter-open");
      filterButton.setAttribute("aria-expanded", "false");
    };

    const openFilters = () => {
      body.classList.add("jh-filter-open");
      filterButton.setAttribute("aria-expanded", "true");
    };

    filterButton.addEventListener("click", () => {
      if (body.classList.contains("jh-filter-open")) {
        closeFilters();
      } else {
        openFilters();
      }
    });

    closeButton.addEventListener("click", closeFilters);
    backdrop.addEventListener("click", closeFilters);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeFilters();
      }
    });
  }

  if (objectTools) {
    objectTools.classList.add("jh-object-tools-floating");
    floatingTools.appendChild(objectTools);
  }
});
