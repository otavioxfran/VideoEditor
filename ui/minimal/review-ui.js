import { REVIEW_KINDS } from "./review-card.js";

let selectedId = null;

function activateTab(name) {
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.setAttribute("aria-selected", tab.dataset.tab === name ? "true" : "false");
  });
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== name;
  });
}

function activateFilter(name) {
  const cuts = document.getElementById("cuts");
  if (!cuts) return;
  cuts.dataset.mode = name;
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.filter === name ? "true" : "false");
  });
  if (selectedId && !visibleCards().some((card) => card.dataset.id === selectedId)) select(null);
}

function updateCounts(detail) {
  const suggestions = detail?.current?.suggestions || [];
  const decided = detail?.current?.decisions || {};
  const review = suggestions.filter((s) => !s.auto && REVIEW_KINDS.has(s.kind));
  const pending = review.filter((s) => !decided[s.id]).length;
  const count = document.getElementById("review-count");
  if (count) count.textContent = String(pending);
  const progress = document.getElementById("review-progress");
  if (progress) {
    const done = review.length - pending;
    progress.textContent =
      pending === 0
        ? review.length
          ? `Tudo decidido (${review.length})`
          : "Nada pendente"
        : `${pending} ${pending === 1 ? "pendente" : "pendentes"} · ${done} de ${review.length} decididas`;
  }
  const empty = document.getElementById("cuts-empty");
  if (empty) empty.hidden = suggestions.length > 0;
}

function visibleCards() {
  return [...document.querySelectorAll("#cuts .review-card")].filter((card) => card.offsetParent !== null);
}

function cardFor(id) {
  return id ? document.querySelector(`#cuts .review-card[data-id="${CSS.escape(id)}"]`) : null;
}

export function select(id, { scroll = true, focus = false } = {}) {
  selectedId = id;
  document.querySelectorAll("#cuts .review-card.selected").forEach((card) => card.classList.remove("selected"));
  const card = cardFor(id);
  if (card) {
    card.classList.add("selected");
    if (scroll) card.scrollIntoView({ block: "nearest" });
    if (focus) card.focus({ preventScroll: true });
  }
  document.dispatchEvent(new CustomEvent("review:selected", { detail: { id: card ? id : null } }));
}

function step(delta) {
  const cards = visibleCards();
  if (!cards.length) return;
  const index = cards.findIndex((card) => card.dataset.id === selectedId);
  const next = index < 0 ? (delta > 0 ? 0 : cards.length - 1) : Math.max(0, Math.min(cards.length - 1, index + delta));
  select(cards[next].dataset.id, { focus: true });
}

function press(label) {
  const card = cardFor(selectedId);
  if (!card || card.offsetParent === null) return false;
  const button = [...card.querySelectorAll(".rc-actions > button")].find(
    (node) => node.textContent.trim() === label
  );
  if (!button) return false;
  button.click();
  return true;
}

function onKey(event) {
  const target = event.target;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const panel = document.getElementById("panel-review");
  if (!panel || panel.hidden) return;
  // Buttons keep their own Enter; the rail keys act on the selected card.
  if (target.tagName === "BUTTON" || target.tagName === "SUMMARY" || target.tagName === "A") {
    if (event.key === "Enter" || event.key === " ") return;
  }
  const key = event.key.toLowerCase();
  let handled = false;
  if (key === "arrowdown") {
    step(1);
    handled = true;
  } else if (key === "arrowup") {
    step(-1);
    handled = true;
  } else if (key === "enter") {
    handled = press("Ouvir");
  } else if (key === "a" || key === "r") {
    handled = key === "a" ? press("Aplicar corte") : press("Manter") || press("Restaurar");
    // Decide and move on: the next card is selected before the edit re-renders the list.
    if (handled) step(1);
  }
  if (handled) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}

function bind() {
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  });
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => activateFilter(button.dataset.filter));
  });
  document.addEventListener("editor:render", (event) => {
    document.body.classList.remove("is-loading");
    updateCounts(event.detail);
    // Cards are rebuilt on every render; keep the selection by id.
    if (selectedId) select(cardFor(selectedId) ? selectedId : null, { scroll: false });
  });
  document.addEventListener("review:select", (event) => {
    const id = event.detail?.id;
    if (!id) return;
    activateTab("review");
    if (!cardFor(id) || cardFor(id).offsetParent === null) activateFilter("all");
    select(id);
  });
  const cuts = document.getElementById("cuts");
  cuts?.addEventListener("click", (event) => {
    const card = event.target.closest(".review-card");
    if (card && !event.target.closest("button, summary, a")) select(card.dataset.id);
  });
  // Leaving advanced mode hides the Briefing tab; never leave its panel orphaned on screen.
  document.addEventListener("mode:change", (event) => {
    const briefing = document.querySelector('[data-tab="briefing"]');
    if (!event.detail?.advanced && briefing?.getAttribute("aria-selected") === "true") activateTab("review");
  });
  // Capture phase: the rail keys run before the editor's global shortcuts.
  document.addEventListener("keydown", onKey, true);
  activateTab("review");
  activateFilter("review");
}

bind();
