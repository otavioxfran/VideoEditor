import { api, element, button, time } from "./api.js";
import { Player } from "./player.js";
import { renderTranscript } from "./transcript.js";
import { reviewForm } from "./automatic-review.js";
import { setupProductivity } from "./productivity.js";
import { reviewCard } from "./review-card.js";
import { setupBriefing } from "./editor-briefing.js";
import { setupFinish } from "./editor-finish.js";
import { setupProlixity } from "./editor-prolixity.js";
import "./review-ui.js";
import "./timeline.js";
import "./transport.js";
import "./editor-command.js";

const $ = (id) => document.getElementById(id);
const jobId = new URLSearchParams(location.search).get("job_id");
const base = `/api/jobs/${encodeURIComponent(jobId || "")}`;

const STAGE_NAMES = {
  ready: "Pronto",
  queued: "Na fila",
  processing: "Processando",
  preparing: "Preparando",
  transcribing: "Transcrevendo",
  analyzing: "Analisando",
  analyzing_takes: "Analisando tomadas",
  validating: "Validando",
  checking_sequence: "Conferindo",
  exporting: "Exportando",
  complete: "Concluído",
  failed: "Falhou",
  interrupted: "Interrompido",
  created: "Criado",
};
const AUTOMATIC_NAMES = {
  queued: "Na fila",
  preparing: "Preparando gravação",
  analyzing: "Analisando cortes e ritmo",
  analyzing_takes: "Analisando tomadas e contexto",
  validating: "Validando cortes",
  checking_sequence: "Conferindo a sequência editada",
  exporting: "Exportando MP4 + legendas",
  complete: "Edição automática concluída",
  failed: "Edição interrompida",
  interrupted: "Edição interrompida",
};
const CLASS_NAMES = {
  stumble: "tropeços",
  repeated_take: "retakes",
  repeated_idea: "ideias repetidas",
  retake: "reinícios",
  false_start: "tentativas abandonadas",
  correction: "autocorreções",
  adaptive_silence: "pausas",
  contextual_restart: "reinícios contextuais",
};

for (const id of ["video", "delivery-video"]) {
  if ($(id)) $(id).poster = `${base}/poster`;
}
if ($("timeline")) $("timeline").dataset.waveform = `${base}/waveform`;

const player = new Player($("video"), $("seek"), $("clock"));

let current = null;
let busy = false;
let polling = false;
let automaticSignature = "";
let mediaIdentity = "";
let deliveryIdentity = "";
let deliveryCurrent = false;
let deliveryReport = null;
let deliveryCutSignature = "";

function message(text) {
  $("message").textContent = text;
}

async function action(callback) {
  try {
    await callback();
  } catch (error) {
    message(error.message);
  }
}

function enable() {
  const ready = !!current && !busy;
  for (const id of ["undo", "export", "mode", "restore", "protect", "copy-prompt", "import", "assist"]) $(id).disabled = !ready;
  $("range").querySelector("button").disabled = !ready;
}

async function edit(data) {
  if (!current || busy) return;
  busy = true;
  enable();
  try {
    current = await api(`${base}/edit`, "POST", { expected_revision: current.edit.revision, ...data });
    render();
    message("Edição salva.");
    await loadExports();
  } catch (error) {
    if (error.status === 409) {
      current = await api(`${base}/edit`);
      render();
    }
    message(error.message);
  } finally {
    busy = false;
    enable();
  }
}

function review(start, end) {
  $("delivery-video").pause();
  $("mode").textContent = "Voltar ao rough cut";
  return player.context(start, end);
}

async function audition(id) {
  $("delivery-video").pause();
  const value = await api(`${base}/audition?suggestion_id=${encodeURIComponent(id)}`);
  $("mode").textContent = "Voltar ao rough cut";
  await player.audition(value.timeline, value.start, value.end);
}

async function watchJoin(seconds) {
  if (!deliveryCurrent) throw new Error("Exporte a edição atual para conferir esta emenda no MP4.");
  $("video").pause();
  $("delivery").scrollIntoView({ block: "center" });
  $("delivery-video").currentTime = Math.max(0, seconds - 2);
  try {
    await $("delivery-video").play();
  } catch (error) {
    if (error.name !== "AbortError") throw error;
  }
}

// Shared with the panel modules; `current` is read live.
const ctx = {
  $,
  base,
  get current() {
    return current;
  },
  edit,
  review,
  action,
  message,
};
const briefing = setupBriefing(ctx);
const finish = setupFinish(ctx);
const prolixity = setupProlixity(ctx);
const productivity = setupProductivity($("productivity"), base, () => current, message);
$("productivity-panel").addEventListener("toggle", () => {
  if ($("productivity-panel").open) action(() => productivity.refresh());
});

/* Delivery: the last current export, played next to the editor. */

$("video").addEventListener("play", () => $("delivery-video").pause());
$("delivery-video").addEventListener("play", () => $("video").pause());
$("delivery-cuts").onclick = () => {
  const details = $("delivery-review");
  details.open = true;
  details.scrollIntoView({ block: "start" });
};
$("delivery-original").onclick = () => {
  $("delivery-video").pause();
  player.setVirtual(false);
  $("mode").textContent = "Rough cut virtual";
  $("video").scrollIntoView({ block: "center" });
};

function overlap(cuts, start, end) {
  return cuts.reduce((sum, [a, b]) => sum + Math.max(0, Math.min(b, end) - Math.max(a, start)), 0);
}

function renderDeliveryCuts() {
  if (!current || !deliveryReport) return;
  const signature = JSON.stringify([deliveryReport.applied_cuts, current.timeline.cuts, deliveryCurrent]);
  if (signature === deliveryCutSignature) return;
  deliveryCutSignature = signature;
  const list = $("delivery-cut-list");
  list.replaceChildren();
  for (const cut of deliveryReport.applied_cuts || []) {
    const remaining = overlap(current.timeline.cuts, cut.start, cut.end);
    const state = remaining < 1e-7 ? "Restaurado" : remaining < cut.end - cut.start - 1e-7 ? "Parcialmente restaurado" : "Aplicado";
    const row = element("div", undefined, "item");
    row.append(element("p", `${time(cut.start)} no original · ${state} · ${cut.reason}`));
    const watch = button("Ver emenda no MP4", () => watchJoin(cut.join_output_seconds));
    watch.disabled = !deliveryCurrent || !Number.isFinite(cut.join_output_seconds);
    const restore = button("Restaurar este corte", () => edit({ action: "restore", start: cut.start, end: cut.end }));
    restore.disabled = remaining < 1e-7;
    row.append(watch, button("Ouvir original", () => review(cut.start, cut.end)), restore);
    list.append(row);
  }
  if (!(deliveryReport.applied_cuts || []).length) list.append(element("p", "Nenhum corte automático aplicado nesta versão."));
}

async function loadExports() {
  const values = await finish.loadExports();
  const completed = values.find((item) => item.state === "complete" && item.status === "current");
  deliveryCurrent = !!completed;
  if (completed) {
    $("delivery").hidden = false;
    const local =
      deliveryReport?.editorial_delivery === "local_cleanup" && deliveryReport?.provider === "unavailable_or_invalid"
        ? "Limpeza local: assistência editorial indisponível. "
        : "";
    $("delivery-status").textContent = `${completed.mode === "evaluation" ? "Edição experimental · " : ""}MP4 exportado com áudio e microfades. ${local}A naturalidade ainda depende de avaliação.`;
    if (deliveryIdentity !== completed.render_id) {
      deliveryIdentity = completed.render_id;
      $("delivery-video").src = `${base}/exports/${completed.render_id}/video.mp4`;
    }
  } else if (deliveryIdentity) {
    $("delivery-status").textContent =
      "A edição mudou. O vídeo abaixo é a versão anterior; use Exportar MP4 + SRT para assistir aos seus ajustes.";
  }
  renderDeliveryCuts();
}

/* Automatic edit: status strip, report and the human evaluation form. */

async function loadAutomatic() {
  const value = await api(`${base}/automatic`);
  $("automatic-panel").hidden = value.state === "not_requested" && !current;
  deliveryReport = value.report || null;
  renderDeliveryCuts();
  let status = `${value.options?.mode === "evaluation" ? "Edição experimental · " : ""}${AUTOMATIC_NAMES[value.state] || value.state}${value.error ? ` · ${value.error}` : ""}`;
  if (value.state === "complete" && value.options?.send_text && value.report?.editorial_delivery === "local_cleanup") {
    status += " · Assistência editorial indisponível; limpeza local concluída.";
  }
  if (value.state === "analyzing_takes" && Number.isFinite(value.analyzed_groups)) status += ` · ${value.analyzed_groups}/${value.total_groups} grupos`;
  if (value.state === "checking_sequence" && Number.isFinite(value.checked_joins)) status += ` · ${value.checked_joins}/${value.total_joins} emendas`;
  $("automatic-status").textContent = status;
  $("automatic-retry").hidden = !["failed", "interrupted", "complete"].includes(value.state);
  $("automatic-retry").textContent = value.state === "complete" ? "Reavaliar e exportar esta edição" : "Retomar edição automática";

  const signature = JSON.stringify([value.state, value.report, value.stale, value.acceptance]);
  if (signature === automaticSignature) return;
  automaticSignature = signature;
  if (value.options?.mode === "evaluation" && value.state === "complete") $("export-history").open = true;
  if (!value.report) return;

  const report = value.report;
  const classes = value.acceptance?.enabled_classes?.map((k) => CLASS_NAMES[k] || k).join(", ") || "nenhum";
  $("automatic-report").textContent = `${report.message || ""} ${report.applied} cortes na edição · ${report.uncertain} trechos preservados para revisão · ${time(report.removed_seconds)} removidos. ${
    value.stale ? "Relatório de uma revisão anterior." : `Avaliação humana: ${value.acceptance.reviewed}/5. Cortes liberados por padrão: ${classes}.`
  }`;
  $("context-reasons").replaceChildren();
  const contextual = report.contextual;
  if (contextual) {
    $("context-reasons").append(
      element(
        "p",
        contextual.failure_reason ||
          `${contextual.processed_groups || 0}/${contextual.total_groups || 0} grupos analisados · ${report.contextual_applied || 0} reinícios contextuais aplicados.`
      )
    );
    for (const item of contextual.rejected || []) {
      const row = element("div", undefined, "item");
      const group = (contextual.groups || []).find((g) => g.id === item.group_id);
      row.append(element("p", `${group ? `${time(group.start)} → ${time(group.end)} · ` : ""}${item.reason}`));
      if (group) row.append(button("Ouvir trecho preservado", () => review(group.start, group.end)));
      $("context-reasons").append(row);
    }
  }
  if (!value.stale && value.state === "complete") {
    reviewForm(
      $("human-review"),
      report,
      async (data) => {
        try {
          await api(`${base}/automatic/review`, "POST", { expected_revision: report.revision, ...data });
          automaticSignature = "";
          await loadAutomatic();
          message("Avaliação registrada.");
        } catch (error) {
          message(error.message);
        }
      },
      review,
      audition,
      watchJoin
    );
  } else {
    $("human-review").replaceChildren();
  }
}

$("automatic-retry").onclick = () =>
  action(async () => {
    await api(`${base}/automatic`, "POST", { expected_revision: current?.edit.revision ?? 0 });
    await refresh();
  });
$("evaluation-create").onclick = () =>
  action(async () => {
    if (!current) return;
    $("evaluation-create").disabled = true;
    try {
      const child = await api(`${base}/automatic/evaluation`, "POST", {
        expected_revision: current.edit.revision,
        send_text: $("evaluation-consent").checked,
      });
      location.href = `/editor.html?job_id=${encodeURIComponent(child.job_id)}`;
    } finally {
      $("evaluation-create").disabled = false;
    }
  });

/* The edit: transport clock, transcript, review cards and markers. */

function render() {
  player.setTimeline(current.timeline);
  $("duration").textContent = `Raw ${time(current.timeline.source_duration)} · virtual ${time(current.timeline.duration)} · revisão ${current.edit.revision}`;
  renderTranscript($("transcript"), current.transcript.segments, current.timeline, $("search").value, review, (kind, start, end) =>
    edit({ action: kind, start, end })
  );
  $("cut-count").textContent = `(${current.suggestions.length})`;
  const cuts = $("cuts");
  cuts.replaceChildren();
  const decided = current.decisions || {};
  for (const s of current.suggestions) {
    cuts.append(
      reviewCard(s, {
        decision: decided[s.id],
        base,
        listen: () => review(s.start, s.end),
        cut: () => edit({ action: "cut", suggestion_id: s.id }),
        keep: () => edit({ action: "restore", suggestion_id: s.id }),
        protect: () => edit({ action: "protect", suggestion_id: s.id }),
      })
    );
  }
  if (!current.suggestions.length) cuts.append(element("p", "Nenhuma sugestão mecânica. Cortes manuais continuam disponíveis."));
  finish.renderMarkers(current);
  $("download-prompt").href = `${base}/editorial/prompt`;
  enable();
  prolixity.render();
  document.dispatchEvent(new CustomEvent("editor:render", { detail: { current, player } }));
}

async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const info = await api(base);
    $("name").textContent = info.name;
    $("status").textContent = `${STAGE_NAMES[info.stage] || info.stage}${info.error ? ` · ${info.error}` : ""}${info.source_error ? ` · ${info.source_error}` : ""}`;
    $("retry").hidden = !["failed", "interrupted", "created"].includes(info.state);
    $("migrate").hidden = !info.legacy;
    if (!current && info.state !== "ready") {
      $("review-progress").textContent =
        info.state === "failed" || info.state === "interrupted" ? "Sem decisões: o preparo parou" : "Aguardando o preparo";
    }
    $("evaluation-create").hidden = info.state !== "ready" || info.legacy || info.editing_mode === "evaluation";
    $("evaluation-parent").hidden = !info.evaluation_parent;
    if (info.evaluation_parent) $("evaluation-parent").href = `/editor.html?job_id=${encodeURIComponent(info.evaluation_parent.job_id)}`;
    if (info.preview && mediaIdentity !== info.preview.identity) {
      mediaIdentity = info.preview.identity;
      $("video").src = `${base}/media?preview=${encodeURIComponent(mediaIdentity)}`;
    }
    if (!info.legacy) await loadAutomatic();
    await loadExports();
    if (info.state === "ready") {
      const data = await api(`${base}/edit`);
      const changed =
        !current ||
        (!busy &&
          (current.edit.revision !== data.edit.revision || JSON.stringify(current.suggestions) !== JSON.stringify(data.suggestions)));
      if (changed) {
        const first = !current;
        current = data;
        render();
        if (first) {
          player.setVirtual(true);
          $("mode").textContent = "Ver original";
          // Read the cached rhythm analysis once, so the tab can show its count.
          action(() => prolixity.load(false));
        }
      }
      if (briefing.isOpen() && !$("panel-briefing").hidden) await briefing.load();
    }
    if (current && $("productivity-panel").open) await productivity.refresh();
  } catch (error) {
    message(error.message);
  } finally {
    polling = false;
  }
}

/* Controls and keyboard. */

function mark() {
  if (current) return edit({ action: "marker", source_time: $("video").currentTime, label: "Marcador" });
  message("Marcadores ficam disponíveis após preparar o vídeo.");
}

function rangeEdit(kind) {
  return edit({ action: kind, start: Number($("start").value), end: Number($("end").value) });
}

$("mark").onclick = mark;
$("mode").onclick = () => {
  player.setVirtual($("mode").textContent === "Voltar ao rough cut" || !player.virtual);
  $("mode").textContent = player.virtual ? "Ver original" : "Rough cut virtual";
};
$("undo").onclick = () => edit({ action: "undo" });
$("search").oninput = () => current && render();
$("range").onsubmit = (event) => {
  event.preventDefault();
  rangeEdit("cut");
};
$("restore").onclick = () => rangeEdit("restore");
$("protect").onclick = () => rangeEdit("protect");
$("retry").onclick = () =>
  action(async () => {
    await api(`${base}/prepare`, "POST");
    await refresh();
  });
$("migrate").onclick = () =>
  action(async () => {
    $("migrate").disabled = true;
    message("Verificando a origem e preservando decisões humanas…");
    try {
      await api(`${base}/migrate`, "POST");
      await refresh();
      message("Migração concluída. Cortes automáticos antigos precisam de revisão.");
    } finally {
      $("migrate").disabled = false;
    }
  });
$("export").onclick = () =>
  action(async () => {
    const result = await api(`${base}/exports`, "POST", { expected_revision: current.edit.revision });
    message(`Exportação ${result.render_id.slice(0, 8)} na fila.`);
    await loadExports();
  });

document.addEventListener("keydown", (event) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  const delivery = $("delivery") && !$("delivery").hidden && !$("delivery-video").paused;
  const video = delivery ? $("delivery-video") : $("video");
  if (event.code === "Space" || key === " " || (key === "k" && !event.ctrlKey && !event.metaKey)) {
    event.preventDefault();
    if (video.paused) video.play();
    else video.pause();
  } else if (key === "m") {
    event.preventDefault();
    mark();
  } else if ((event.ctrlKey || event.metaKey) && key === "z") {
    event.preventDefault();
    if (!busy && current) edit({ action: "undo" });
  } else if (key === "j" || key === "arrowleft") {
    event.preventDefault();
    video.currentTime = Math.max(0, video.currentTime - 2);
  } else if (key === "l" || key === "arrowright") {
    event.preventDefault();
    video.currentTime = Math.min(video.duration || video.currentTime + 2, video.currentTime + 2);
  }
});

api("/api/editorial/settings")
  .then((settings) => {
    $("evaluation-consent").checked = settings.enabled && settings.text_consent;
  })
  .catch(() => {});

if (!jobId) {
  message("Abra um job pela filmoteca.");
} else {
  $("video").src = `${base}/media`;
  refresh();
  setInterval(refresh, 1200);
}
