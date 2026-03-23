document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  const header = document.getElementById("header");
  const objectTools = document.querySelector(".object-tools");
  const filterPanel = document.getElementById("changelist-filter");
  let floatingTools = null;
  let filterButton = null;

  const syncLayoutOffsets = () => {
    const offset = (header?.offsetHeight || 64) + 10;
    document.documentElement.style.setProperty("--jh-header-offset", `${offset}px`);
    const toolsHeight = floatingTools?.offsetHeight || 0;
    document.documentElement.style.setProperty("--jh-floating-tools-height", `${toolsHeight}px`);

    if (filterPanel && floatingTools) {
      const rect = floatingTools.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const width = Math.min(352, viewportWidth - 24);
      const right = Math.max(12, viewportWidth - rect.right);
      const top = Math.round(rect.bottom + 8);
      filterPanel.style.right = `${right}px`;
      filterPanel.style.left = "auto";
      filterPanel.style.top = `${top}px`;
      filterPanel.style.width = `${width}px`;
    }
  };

  syncLayoutOffsets();
  window.addEventListener("resize", syncLayoutOffsets);

  if (!objectTools && !filterPanel) {
    return;
  }

  body.classList.add("jh-has-floating-tools");

  floatingTools = document.createElement("div");
  floatingTools.className = "jh-floating-tools";
  document.body.appendChild(floatingTools);

  if (filterPanel) {
    filterButton = document.createElement("button");
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
    document.body.appendChild(filterPanel);

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

  syncLayoutOffsets();
});
