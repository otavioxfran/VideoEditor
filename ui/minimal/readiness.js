import { api, element } from "./api.js";
import { formatBytes, icon } from "./ui.js";

// "Is the studio ready to cut?" FFmpeg, graphics card, speech model, disk.
// One status is shared by every view that shows it (Filmoteca banner, Settings).

const MODEL_SIZES = { "large-v3-turbo": 1.6e9, "large-v3": 3.1e9, medium: 1.5e9, small: 4.9e8, base: 1.5e8, tiny: 7.6e7 };
const views = new Set();
let current = null;
let polling = 0;

function row(ok, title, detail, action) {
  const node = element("div", undefined, `ready-row ${ok ? "ok" : "missing"}`);
  const mark = element("span", undefined, "ready-mark");
  mark.append(icon(ok ? "check-circle" : "warning-circle"));
  const text = element("div", undefined, "ready-text");
  text.append(element("strong", title), element("span", detail));
  node.append(mark, text);
  if (action) node.append(action);
  return node;
}

function modelRow(status) {
  const model = status.model;
  const download = model.download || {};
  const size = formatBytes(MODEL_SIZES[model.name]);
  if (model.ready || download.state === "done") {
    return row(true, "Modelo de transcrição", `${model.name} pronto neste computador.`);
  }
  if (download.state === "downloading") {
    const node = row(false, "Modelo de transcrição", download.progress == null
      ? `Baixando ${model.name}…`
      : `Baixando ${model.name}: ${formatBytes(download.downloaded)} de ${formatBytes(download.total)}`);
    const bar = element("div", undefined, "ready-progress");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Download do modelo");
    const value = Math.round((download.progress || 0) * 100);
    bar.setAttribute("aria-valuenow", String(value));
    bar.style.setProperty("--value", `${value}%`);
    node.querySelector(".ready-text").append(bar);
    return node;
  }
  const button = element("button", undefined, "btn small");
  button.type = "button";
  button.append(icon("download-simple"), element("span", `Baixar (${size})`));
  button.addEventListener("click", () => startDownload(model.name));
  const detail = download.state === "failed"
    ? `O download falhou: ${download.error}. Confira a internet e tente de novo.`
    : `${model.name} ainda não foi baixado. Baixa uma vez só e fica no computador.`;
  return row(false, "Modelo de transcrição", detail, button);
}

function render(view) {
  const status = current;
  view.root.replaceChildren();
  if (!status) {
    view.root.append(element("p", "Verificando o computador…", "hint"));
    return;
  }
  if (view.onlyWhenMissing) {
    view.root.hidden = status.ready && status.model.download?.state !== "downloading";
    if (view.root.hidden) return;
    const head = element("div", undefined, "ready-head");
    head.append(
      element("h2", "Antes do primeiro corte"),
      element("p", "O editor roda no seu computador. Falta preparar uma coisa:", "hint")
    );
    view.root.append(head);
  }
  const list = element("div", undefined, "ready-list");
  const gpu = status.gpu;
  const ffmpeg = status.ffmpeg;
  const items = [
    row(ffmpeg.ok, "FFmpeg", ffmpeg.ok
      ? `Instalado${ffmpeg.version ? ` (${ffmpeg.version})` : ""}.`
      : "Não encontrado. Ele corta e exporta o vídeo; instale e reabra o Studio."),
    row(true, "Placa de vídeo", gpu.ok
      ? `${gpu.name || "NVIDIA"}: transcrição rápida.`
      : "Sem placa NVIDIA: funciona, mas a transcrição é mais lenta. Usando um modelo mais leve."),
    modelRow(status),
    row(status.disk.free_bytes == null || status.disk.free_bytes > 5e9, "Espaço livre",
      `${formatBytes(status.disk.free_bytes)} livres onde o editor guarda os cortes.`),
  ];
  // The banner lists only what is missing; Settings shows the full check.
  for (const item of items) {
    if (!view.onlyWhenMissing || item.classList.contains("missing")) list.append(item);
  }
  if (!ffmpeg.ok && view.onlyWhenMissing) {
    const help = element("a", "Como instalar o FFmpeg", "ready-help");
    help.href = "https://github.com/otavioxfran/VideoEditor#-como-usar-pt-br";
    help.target = "_blank";
    help.rel = "noopener noreferrer";
    list.append(help);
  }
  view.root.append(list);
}

function paint() {
  for (const view of views) render(view);
  document.dispatchEvent(new CustomEvent("system:status", { detail: current }));
}

export async function refreshReadiness() {
  try {
    current = await api("/api/system");
  } catch {
    return current;
  }
  paint();
  if (current.model.download?.state === "downloading") schedule();
  return current;
}

function schedule() {
  window.clearTimeout(polling);
  polling = window.setTimeout(refreshReadiness, 1000);
}

async function startDownload(model) {
  await api("/api/system/model", "POST", { model });
  await refreshReadiness();
}

export function mountReadiness(root, { onlyWhenMissing = false } = {}) {
  if (!root) return;
  views.add({ root, onlyWhenMissing });
  if (current) paint();
}

export function readiness() {
  return current;
}
