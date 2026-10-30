import { api, element } from "./api.js";
import {
  closeDialog,
  formatDuration,
  icon,
  isAdvanced,
  openDialog,
  setAdvanced,
  showMessage,
  stateLabel,
  stateTone,
  wireCommand,
  wireDialogs,
} from "./ui.js";
import { mountReadiness, refreshReadiness } from "./readiness.js";

const $ = (id) => document.getElementById(id);
let sourceDir = "";
// A file chosen in the native dialog (it may live outside the source folder).
let pickedPath = "";
// The model and device follow the computer until the person picks them by hand.
let configTouched = false;
const PROCESSING = new Set([
  "created",
  "queued",
  "processing",
  "preparing",
  "transcribing",
  "analyzing",
  "analyzing_takes",
  "validating",
  "checking_sequence",
  "exporting",
]);

function sourceName(job) {
  return (job.source?.path || job.name || "").split(/[\\/]/).pop() || job.name || "Vídeo";
}

function editorUrl(job) {
  return `/editor.html?job_id=${encodeURIComponent(job.job_id)}`;
}

function posterUrl(job) {
  return job.poster_url || `/api/jobs/${encodeURIComponent(job.job_id)}/poster`;
}

function jobScore(job) {
  return (
    (job.legacy ? 0 : 4) +
    (job.state === "ready" ? 3 : 0) +
    (job.export?.status === "current" ? 1 : 0) +
    (job.state === "failed" ? -2 : 0) +
    Date.parse(job.updated_at || job.created_at || 0) / 1e12
  );
}

function uniqueJobs(jobs) {
  const groups = new Map();
  for (const job of jobs) {
    const key = job.source?.path || job.job_id;
    const list = groups.get(key) || [];
    list.push(job);
    groups.set(key, list);
  }
  const result = [];
  for (const list of groups.values()) {
    const best = [...list].sort((a, b) => jobScore(b) - jobScore(a))[0];
    best.versions = Math.max(Number(best.versions) || 1, list.length);
    result.push(best);
  }
  return result;
}

function jobCard(job) {
  const link = editorUrl(job);
  const card = element("article", "", "job-card");
  const pending = Number(job.pending_review || 0);
  if (pending) card.classList.add("pending");
  if (job.state === "failed" || job.state === "interrupted") card.classList.add("failed");

  const thumb = element("a", "", "job-thumb");
  thumb.href = link;
  thumb.setAttribute("aria-label", `Abrir ${job.name || sourceName(job)}`);
  const image = element("img");
  image.loading = "lazy";
  image.alt = "";
  image.src = posterUrl(job);
  image.addEventListener("error", () => {
    image.remove();
    const empty = element("div", "", "thumb-empty");
    empty.append(icon("film-strip", "icon"), element("span", "Sem prévia"));
    thumb.append(empty);
  });
  thumb.append(image);
  // A ready job's section and its decision count already say "review"; the badge is for other states.
  if (job.state !== "ready") {
    thumb.append(element("span", stateLabel(job.state), `badge ${stateTone(job.state)} job-state`));
  }
  if (Number.isFinite(job.duration) && job.duration > 0) {
    thumb.append(element("span", formatDuration(job.duration), "job-duration"));
  }

  const body = element("div", "", "job-body");
  const title = element("h2", job.name || sourceName(job));
  // The full raw path is reference, not something to read on every card.
  if (job.source?.path) title.title = job.source.path;
  body.append(title);

  // Line 1 answers "does this need me?"; line 2 is reference.
  const status = element("div", "", "job-status");
  const separate = (row) => row.childElementCount && row.append(element("span", "", "dot"));
  if (pending) {
    status.append(element("span", `${pending} ${pending === 1 ? "decisão" : "decisões"}`, "pending-note"));
  }
  if (job.export?.state === "complete") {
    const current = job.export.status === "current";
    separate(status);
    const note = element(
      "span",
      current ? "exportado" : "export desatualizado",
      current ? "exported" : "export-stale"
    );
    if (!current) note.title = "O corte ou o perfil mudou depois deste export. Exporte de novo.";
    status.append(note);
  } else if (job.state === "ready" && !job.legacy) {
    separate(status);
    status.append(element("span", "sem export", "export-none"));
  }
  if (status.childElementCount) body.append(status);

  const meta = element("div", "", "job-meta");
  meta.append(element("span", job.config?.model || "large-v3-turbo"));
  if (job.legacy) {
    separate(meta);
    meta.append(element("span", "job antigo"));
  }
  if (job.versions > 1) {
    separate(meta);
    meta.append(element("span", `${job.versions} versões`));
  }
  body.append(meta);

  const actions = element("div", "", "job-actions");
  const open = element("a", pending ? "Revisar" : "Abrir editor", "btn small");
  open.href = link;
  open.append(icon("caret-right"));
  actions.append(open);
  body.append(actions);
  card.append(thumb, body);
  return card;
}

function section(title, note, jobs, emptyText) {
  const wrap = element("section", "", "library-section");
  const head = element("div", "", "section-head");
  const heading = element("h2");
  heading.append(element("span", title), element("span", String(jobs.length), "count"));
  head.append(heading);
  if (note) head.append(element("span", note, "section-note"));
  wrap.append(head);
  if (!jobs.length) {
    wrap.append(
      element("p", emptyText || "Nada aqui por enquanto.", "hint")
    );
    return wrap;
  }
  const grid = element("div", "", "job-grid");
  for (const job of jobs) grid.append(jobCard(job));
  wrap.append(grid);
  return wrap;
}

function renderJobs(allJobs) {
  const root = $("jobs");
  root.replaceChildren();
  const jobs = uniqueJobs(allJobs);
  const pending = jobs.filter(
    (job) => Number(job.pending_review || 0) > 0 || ["failed", "interrupted"].includes(job.state)
  );
  const processing = jobs.filter((job) => PROCESSING.has(job.state));
  const ready = jobs.filter(
    (job) =>
      !job.legacy &&
      job.state === "ready" &&
      Number(job.pending_review || 0) === 0 &&
      !PROCESSING.has(job.state)
  );
  const legacy = jobs.filter((job) => job.legacy);

  if (pending.length) {
    root.append(
      section(
        "Precisa de você",
        "Revise as decisões que o motor deixou para o humano.",
        pending
      )
    );
  }
  if (processing.length) {
    root.append(
      section("Processando", "O estúdio está trabalhando nestes raws.", processing)
    );
  }
  if (ready.length) {
    root.append(section("Prontos", "Sem decisões pendentes.", ready));
  }
  if (legacy.length) {
    root.append(section("Antigos", "Jobs migrados; compatibilidade desconhecida.", legacy));
  }
  if (!jobs.length) {
    const empty = element("div", "", "studio-empty");
    const box = element("div", "", "empty");
    box.append(icon("film-strip", "icon"));
    box.append(element("strong", "Nenhum corte ainda"));
    box.append(
      element(
        "p",
        `Escolha uma gravação em ${sourceDir || "sua pasta de vídeos"}. O raw fica intocado; o estúdio cria um job separado.`,
        "hint"
      )
    );
    const action = element("button", "Novo corte", "btn primary");
    action.type = "button";
    action.addEventListener("click", openNew);
    box.append(action);
    empty.append(box);
    root.append(empty);
  }
  const totalVersions = allJobs.reduce(
    (total, job) => total + (Number(job.versions) || 1),
    0
  );
  $("library-count").textContent =
    totalVersions > allJobs.length
      ? `${allJobs.length} raws · ${totalVersions} versões`
      : `${allJobs.length} ${allJobs.length === 1 ? "job" : "jobs"}`;
}

async function refresh() {
  try {
    const library = await api("/api/library");
    const select = $("source");
    const selected = select.value;
    select.replaceChildren(element("option", "Escolha um vídeo"));
    select.firstChild.value = "";
    for (const source of library.sources || []) {
      const option = element("option", source.name);
      option.value = source.path;
      select.append(option);
    }
    if ([...select.options].some((option) => option.value === selected)) {
      select.value = selected;
    }
    sourceDir = library.source_dir || "";
    if (sourceDir && $("source-dir")) $("source-dir").textContent = sourceDir;
    if (library.jobs_dir && $("data-dir")) $("data-dir").textContent = library.jobs_dir;
    renderJobs(library.jobs || []);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function loadSettings() {
  try {
    const settings = await api("/api/editorial/settings");
    const status = $("provider-status");
    const badge = $("provider-badge");
    if (!settings.configured) {
      status.textContent = "Nenhuma chave configurada. A limpeza local continua disponível.";
      badge.textContent = "Local";
      badge.className = "badge neutral";
    } else if (settings.enabled) {
      status.textContent = "Chave configurada; assistência textual ativa.";
      badge.textContent = "Ativo";
      badge.className = "badge success";
    } else {
      status.textContent = "Chave salva; assistência desativada.";
      badge.textContent = "Desligado";
      badge.className = "badge neutral";
    }
    $("provider-free").checked = settings.free_tier_confirmed === true;
    $("provider-consent").checked = settings.text_consent === true;
    $("provider-enabled").checked = settings.enabled === true;
    $("automatic-consent").checked = settings.enabled === true && settings.text_consent === true;
  } catch (error) {
    showMessage(error.message, "error");
  }
}

function showPicked(name) {
  const label = $("picked-video");
  label.hidden = !name;
  label.textContent = name || "";
}

async function pickVideo() {
  const button = $("pick-video");
  button.disabled = true;
  try {
    const result = await api("/api/pick/video", "POST");
    if (result.cancelled) return;
    pickedPath = result.path;
    $("source").value = "";
    showPicked(result.name);
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function pickFolder() {
  const button = $("pick-folder");
  button.disabled = true;
  try {
    const result = await api("/api/pick/folder", "POST");
    if (result.cancelled) return;
    await refresh();
    showMessage(
      result.session_only
        ? "Pasta trocada nesta sessão. O start-studio.local.cmd define outra pasta ao reabrir."
        : "Pasta de vídeos trocada.",
      "success"
    );
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function followComputer(status) {
  if (!status || configTouched) return;
  $("model").value = status.model.recommended;
  $("device").value = status.gpu.ok ? "cuda" : "cpu";
}

function openSettings() {
  refreshReadiness();
  openDialog("settings-dialog");
}

function openNew() {
  const source = $("source");
  if (source && !source.value && source.options.length > 1) {
    source.selectedIndex = 1;
  }
  openDialog("new-job-dialog");
}

function bind() {
  wireDialogs();
  $("open-new").addEventListener("click", openNew);
  $("open-settings").addEventListener("click", openSettings);
  $("refresh-library").addEventListener("click", refresh);
  $("pick-video").addEventListener("click", pickVideo);
  $("pick-folder").addEventListener("click", pickFolder);
  $("source").addEventListener("change", () => {
    if ($("source").value) {
      pickedPath = "";
      showPicked("");
    }
  });
  for (const id of ["model", "device"]) {
    $(id).addEventListener("change", () => (configTouched = true));
  }
  document.addEventListener("system:status", (event) => followComputer(event.detail));
  $("advanced-mode").checked = isAdvanced();
  $("advanced-mode").addEventListener("change", (event) => setAdvanced(event.target.checked));
  mountReadiness($("readiness"), { onlyWhenMissing: true });
  mountReadiness($("system-checks"));
  wireCommand([
    { label: "Novo corte", icon: "plus", hint: "N", run: openNew },
    {
      label: "Configurações",
      icon: "gear-six",
      hint: "S",
      run: openSettings,
    },
    {
      label: "Mostrar ou esconder opções avançadas",
      icon: "sliders-horizontal",
      run: () => {
        setAdvanced(!isAdvanced());
        $("advanced-mode").checked = isAdvanced();
      },
    },
    { label: "Atualizar filmoteca", icon: "arrow-counter-clockwise", run: refresh },
  ]);

  $("prepare").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const buttons = [...form.querySelectorAll("button")];
    buttons.forEach((button) => (button.disabled = true));
    const automatic = event.submitter?.value !== "manual";
    const source = $("path").value.trim() || pickedPath || $("source").value;
    if (!source) {
      showMessage("Escolha um vídeo no computador ou da sua pasta de vídeos.", "error");
      buttons.forEach((button) => (button.disabled = false));
      return;
    }
    const device = $("device").value;
    const body = {
      source_path: source,
      config: {
        device,
        compute_type: device === "cpu" ? "int8" : "int8_float16",
        model: $("model").value,
        refine_uncertain: automatic,
      },
    };
    if (automatic) {
      body.automatic = {
        send_text: $("automatic-consent").checked,
        export: true,
        mode: $("evaluation-mode").checked ? "evaluation" : "standard",
      };
    }
    try {
      showMessage(automatic ? "Criando o corte…" : "Preparando a transcrição…");
      const job = await api(
        automatic ? "/api/automatic-jobs" : "/api/jobs",
        "POST",
        body
      );
      closeDialog("new-job-dialog");
      location.href = editorUrl(job);
    } catch (error) {
      showMessage(error.message, "error");
      buttons.forEach((button) => (button.disabled = false));
    }
  });

  $("provider-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const key = $("provider-key");
      const body = {
        enabled: $("provider-enabled").checked,
        free_tier_confirmed: $("provider-free").checked,
        text_consent: $("provider-consent").checked,
      };
      if (key.value.trim()) body.api_key = key.value.trim();
      await api("/api/editorial/settings", "POST", body);
      key.value = "";
      await loadSettings();
      showMessage("Configuração salva. Nenhuma gravação foi enviada.", "success");
    } catch (error) {
      $("provider-key").value = "";
      showMessage(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
}

bind();
Promise.all([refresh(), loadSettings(), refreshReadiness()]).catch((error) => showMessage(error.message, "error"));
window.setInterval(() => {
  if (!document.hidden) refresh();
}, 6000);
