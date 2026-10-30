import { element } from "./api.js";

const SVG_NS = "http://www.w3.org/2000/svg";
let messageTimer = 0;

export function icon(name, className = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `/assets/icons.svg#ph-${name}`);
  svg.append(use);
  return svg;
}

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const rounded = Math.round(seconds);
  const h = Math.floor(rounded / 3600);
  const m = Math.floor((rounded % 3600) / 60);
  const s = rounded % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

const STATES = {
  created: ["Criado", "neutral"],
  queued: ["Na fila", "info"],
  processing: ["Processando", "info"],
  preparing: ["Preparando", "info"],
  transcribing: ["Transcrevendo", "info"],
  analyzing: ["Analisando", "info"],
  analyzing_takes: ["Analisando tomadas", "info"],
  validating: ["Validando", "info"],
  checking_sequence: ["Conferindo", "info"],
  exporting: ["Exportando", "info"],
  complete: ["Concluído", "success"],
  ready: ["Pronto", "success"],
  failed: ["Falhou", "danger"],
  interrupted: ["Interrompido", "danger"],
  not_requested: ["Só preparado", "neutral"],
};

export function stateLabel(state) {
  return STATES[state]?.[0] || state || "—";
}

export function stateTone(state) {
  return STATES[state]?.[1] || "neutral";
}

export function showMessage(text, kind = "") {
  const node = document.getElementById("message");
  if (!node) return;
  clearTimeout(messageTimer);
  node.textContent = text || "";
  if (kind) node.dataset.kind = kind;
  else delete node.dataset.kind;
  if (text) {
    messageTimer = window.setTimeout(() => {
      node.textContent = "";
    }, 7000);
  }
}

export function openDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog) return;
  if (!dialog.open) dialog.showModal();
  window.setTimeout(() => {
    const input = dialog.querySelector("input:not([type=checkbox]):not([type=hidden]), select");
    input?.focus();
  }, 30);
}

export function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog?.open) dialog.close();
}

export function wireDialogs() {
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(button.dataset.close));
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const inside =
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom;
      if (!inside) dialog.close();
    });
  });
}

export function wireCommand(actions) {
  const dialog = document.getElementById("command-dialog");
  const input = document.getElementById("command-input");
  const list = document.getElementById("command-list");
  const openButton = document.getElementById("open-command");
  if (!dialog || !input || !list) return;

  let filtered = actions;
  const run = (action) => {
    dialog.close();
    action.run();
  };
  const draw = () => {
    const query = input.value.trim().toLocaleLowerCase();
    filtered = actions.filter((action) => action.label.toLocaleLowerCase().includes(query));
    list.replaceChildren();
    for (const action of filtered) {
      const row = element("button", "", "command-item");
      row.type = "button";
      row.append(icon(action.icon || "caret-right"));
      row.append(element("span", action.label));
      if (action.hint) row.append(element("span", action.hint, "kbd"));
      row.addEventListener("click", () => run(action));
      list.append(row);
    }
    if (!filtered.length) {
      list.append(element("p", "Nenhum comando encontrado.", "muted"));
    }
    list.querySelector(".command-item")?.focus();
  };
  const open = () => {
    if (!dialog.open) {
      input.value = "";
      draw();
      dialog.showModal();
      input.focus();
    }
  };
  input.addEventListener("input", draw);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && filtered[0]) {
      event.preventDefault();
      run(filtered[0]);
    }
  });
  openButton?.addEventListener("click", open);
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      open();
    }
  });
}

// Simple mode (default) hides research and expert controls marked with data-advanced.
// It is a per-viewer preference, so it lives in this browser only.
const MODE_KEY = "videoeditor.advanced";

export function isAdvanced() {
  try {
    return localStorage.getItem(MODE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setAdvanced(value) {
  try {
    localStorage.setItem(MODE_KEY, value ? "1" : "0");
  } catch {
    // Private windows may refuse storage; the mode still applies to this page.
  }
  document.documentElement.dataset.mode = value ? "advanced" : "simple";
  document.dispatchEvent(new CustomEvent("mode:change", { detail: { advanced: value } }));
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

document.documentElement.dataset.mode = isAdvanced() ? "advanced" : "simple";
