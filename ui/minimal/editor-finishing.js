import { api, element, button, time } from "./api.js";
import { icon } from "./ui.js";

const TYPE_LABELS = { overlay: "Imagem", cover: "Cobertura", banner: "Correção", card: "Correção" };
const STATUS_LABELS = { pending: "Pendente", accepted: "Aceito", rejected: "Recusado", edited: "Editado" };
const TYPE_FILTERS = {
  image: new Set(["overlay"]),
  cover: new Set(["cover"]),
  correction: new Set(["banner", "card"]),
};

function typeLabel(kind) { return TYPE_LABELS[kind] || kind; }
function accepted(item) { return ["accepted", "edited"].includes(item.status); }
function duration(item) { return Math.max(0, Number(item.end) - Number(item.start)); }
function timecode(value) {
  // Items are often shorter than a second, so show tenths: 00:03:05.0 → 00:03:05.8.
  const seconds = Math.max(0, Number(value) || 0);
  return `${time(seconds)}.${Math.floor((seconds % 1) * 10)}`;
}

export function setupFinishing(ctx) {
  const { $, base } = ctx;
  const section = $("finishing");
  if (!section) return { refresh() {} };
  let documentValue = { schema: 1, revision: 0, items: [] };
  let currentExport = null;
  let renderStatus = { state: "idle", done: 0, total: 0 };
  let selectedId = null;
  let drafts = {};
  let renderedResult = "";
  let polling = false;
  let timer = 0;

  const list = $("finishing-list");
  const empty = $("finishing-empty");
  const progress = $("finishing-progress");
  const renderButton = $("finishing-render");

  function clearTimer() {
    if (timer) clearTimeout(timer);
    timer = 0;
  }

  function schedule() {
    clearTimer();
    if (!["queued", "running"].includes(renderStatus.state)) return;
    timer = window.setTimeout(() => refresh().catch(() => {}), 900);
  }

  function matches(item) {
    const type = $("finishing-filter-type").value;
    const status = $("finishing-filter-status").value;
    if (type !== "all" && !(TYPE_FILTERS[type] || new Set()).has(item.kind)) return false;
    if (status !== "all") {
      if (status === "accepted" && !accepted(item)) return false;
      if (status !== "accepted" && item.status !== status) return false;
    }
    return true;
  }

  async function decide(item, data) {
    const preserved = drafts[item.id] || {};
    try {
      // decide returns the updated document; re-rendering from it avoids the slow
      // exports listing on every card (it takes seconds on a long job).
      documentValue = await api(`${base}/finishing/suggestions/decide`, "POST", {
        expected_revision: documentValue.revision,
        item_id: item.id,
        ...data,
      });
      delete drafts[item.id];
      render();
    } catch (error) {
      drafts[item.id] = preserved;
      if (error.status === 409) {
        await refresh();
        ctx.message("A revisão mudou em outra aba. Recarreguei as sugestões e preservei seu texto.");
      } else {
        ctx.message(error.message);
      }
    }
  }

  function draftValue(item, key) {
    return drafts[item.id]?.[key] ?? item.payload?.[key] ?? "";
  }

  function setDraft(item, key, value) {
    drafts[item.id] = { ...(drafts[item.id] || {}), [key]: value };
    renderRenderButton();
  }

  function actionButton(name, label, className, callback) {
    const node = element("button", undefined, className);
    node.type = "button";
    node.setAttribute("aria-label", label);
    node.append(icon(name), element("span", label));
    node.addEventListener("click", (event) => {
      event.stopPropagation();
      callback();
    });
    return node;
  }

  function preview(item) {
    const image = element("img");
    image.className = "finishing-preview";
    image.loading = "lazy";
    image.alt = `Prévia em ${timecode((item.start + item.end) / 2)}`;
    image.src = `${base}/finishing/suggestions/${encodeURIComponent(item.id)}/preview.jpg`;
    image.addEventListener("error", () => image.closest(".finishing-preview-wrap")?.classList.add("is-missing"));
    return image;
  }

  function correctionFields(item) {
    const box = element("div", undefined, "finishing-edit-fields");
    const save = element("button", "Salvar texto", "btn small");
    save.type = "button";
    if (item.kind === "banner") {
      const first = element("input", undefined, "input");
      first.value = draftValue(item, "first");
      first.setAttribute("aria-label", "Texto principal da correção");
      first.addEventListener("input", () => setDraft(item, "first", first.value));
      const second = element("input", undefined, "input");
      second.value = draftValue(item, "second");
      second.setAttribute("aria-label", "Complemento da correção");
      second.addEventListener("input", () => setDraft(item, "second", second.value));
      box.append(element("span", "Revise o texto", "finishing-review-label"), first, second);
      save.onclick = () => decide(item, { action: "edit", payload: { first: first.value, second: second.value } });
    } else {
      const lines = element("textarea", undefined, "input");
      lines.rows = 2;
      lines.value = draftValue(item, "lines").join?.("\n") ?? String(draftValue(item, "lines"));
      lines.setAttribute("aria-label", "Linhas da correção");
      lines.addEventListener("input", () => setDraft(item, "lines", lines.value.split("\n").filter(Boolean)));
      const note = element("input", undefined, "input");
      note.value = draftValue(item, "note");
      note.setAttribute("aria-label", "Nota da correção");
      note.addEventListener("input", () => setDraft(item, "note", note.value));
      box.append(element("span", "Revise o texto", "finishing-review-label"), lines, note);
      save.onclick = () => decide(item, {
        action: "edit",
        payload: { lines: lines.value.split("\n").filter(Boolean), note: note.value },
      });
    }
    box.append(save);
    return box;
  }

  function timeEditor(item) {
    const box = element("div", undefined, "finishing-times");
    const start = element("input", undefined, "input finishing-time");
    start.type = "number";
    start.step = ".25";
    start.min = "0";
    start.value = Number(item.start).toFixed(2);
    start.setAttribute("aria-label", "Início da sugestão");
    const end = element("input", undefined, "input finishing-time");
    end.type = "number";
    end.step = ".25";
    end.min = "0";
    end.value = Number(item.end).toFixed(2);
    end.setAttribute("aria-label", "Fim da sugestão");
    const shift = (delta) => {
      const left = Math.max(0, Number(start.value) + delta);
      const right = Math.max(left + .1, Number(end.value) + delta);
      start.value = left.toFixed(2);
      end.value = right.toFixed(2);
      decide(item, { action: "edit", start: left, end: right });
    };
    const minus = actionButton("minus", "-0,25 s", "btn ghost small", () => shift(-.25));
    const plus = actionButton("plus", "+0,25 s", "btn ghost small", () => shift(.25));
    const save = element("button", "Salvar tempos", "btn small");
    save.type = "button";
    save.onclick = () => {
      const left = Number(start.value), right = Number(end.value);
      if (!(right > left)) { ctx.message("O fim precisa ser maior que o início."); return; }
      decide(item, { action: "edit", start: left, end: right });
    };
    box.append(element("span", "Tempo de saída", "finishing-review-label"), start, minus, plus, end, save);
    return box;
  }

  function alternatives(item) {
    const values = item.payload?.alternatives || [];
    if (!values.length) return null;
    const box = element("div", undefined, "finishing-alternatives");
    values.forEach((value, index) => {
      const choice = element("button", undefined, "finishing-alternative");
      choice.type = "button";
      choice.setAttribute("aria-label", `Usar alternativa ${index + 1}`);
      const image = element("img");
      image.loading = "lazy";
      image.alt = `Alternativa ${index + 1}`;
      image.src = `${base}/finishing/suggestions/${encodeURIComponent(item.id)}/alternative/${index}/image`;
      choice.append(image, element("span", "Usar esta"));
      choice.addEventListener("click", () => decide(item, { action: "edit", payload: { image: value } }));
      box.append(choice);
    });
    return box;
  }

  function card(item) {
    const node = element("article", undefined, "finishing-card");
    node.dataset.id = item.id;
    node.dataset.status = item.status;
    node.tabIndex = 0;
    if (item.id === selectedId) node.classList.add("is-selected");
    node.addEventListener("focus", () => {
      selectedId = item.id;
      section.querySelectorAll(".finishing-card.is-selected").forEach((value) => value.classList.remove("is-selected"));
      node.classList.add("is-selected");
    });
    node.addEventListener("click", () => {
      selectedId = item.id;
      node.focus();
      section.querySelectorAll(".finishing-card.is-selected").forEach((value) => value.classList.remove("is-selected"));
      node.classList.add("is-selected");
    });
    const head = element("div", undefined, "finishing-card-head");
    const badges = element("div", undefined, "badges");
    badges.append(
      element("span", typeLabel(item.kind), "badge neutral"),
      element("span", STATUS_LABELS[item.status] || item.status, `badge ${accepted(item) ? "success" : item.status === "rejected" ? "danger" : "review"}`),
    );
    if (item.long) badges.append(element("span", "Longo", "badge review"));
    if (item.needs_text) badges.append(element("span", "Revise o texto", "badge review"));
    head.append(badges, element("span", `${timecode(item.start)} → ${timecode(item.end)}`, "timecode"));
    node.append(head, element("p", item.reason || "", "finishing-reason"));
    const frame = element("div", undefined, "finishing-preview-wrap");
    frame.append(preview(item));
    node.append(frame);
    if (item.kind === "banner" || item.kind === "card") node.append(correctionFields(item));
    const alt = alternatives(item);
    if (alt) node.append(alt);
    node.append(timeEditor(item));

    const actions = element("div", undefined, "finishing-actions");
    actions.append(
      actionButton("check", "Aceitar (A)", "btn small", () => decide(item, { action: "accept" })),
      actionButton("x", "Recusar (R)", "btn ghost small", () => decide(item, { action: "reject" })),
    );
    node.append(actions);
    return node;
  }

  function renderRenderButton() {
    const items = documentValue.items || [];
    const chosen = items.filter(accepted);
    const blocked = chosen.some((item) => item.needs_text);
    renderButton.disabled = !chosen.length || blocked || !currentExport;
    renderButton.title = blocked
      ? "Edite o texto de todas as correções aceitas."
      : !chosen.length ? "Aceite ao menos uma sugestão." : !currentExport ? "Exporte a revisão atual." : "";
  }

  function renderResult() {
    const result = $("finishing-result");
    progress.hidden = !["queued", "running"].includes(renderStatus.state);
    if (["queued", "running"].includes(renderStatus.state)) {
      progress.textContent = `Gerando vídeo final: ${renderStatus.done || 0}/${renderStatus.total || "?"}`;
    } else if (renderStatus.state === "failed") {
      progress.hidden = false;
      progress.textContent = `Falhou: ${renderStatus.error || "erro no render"}`;
    }
    if (renderStatus.state === "complete") {
      result.hidden = false;
      const stamp = renderStatus.completed_at || renderStatus.created_at || "";
      if (renderedResult !== stamp) {
        renderedResult = stamp;
        $("finishing-result-video").src = `${base}/finishing/render/video?${encodeURIComponent(stamp)}`;
      }
      $("finishing-result-video-link").href = `${base}/finishing/render/video`;
      $("finishing-result-srt-link").href = `${base}/finishing/render/srt`;
      $("finishing-result-description-link").href = `${base}/finishing/render/description`;
    } else {
      result.hidden = true;
    }
  }

  function render() {
    renderRenderButton();
    empty.textContent = "";
    if (!currentExport) {
      empty.textContent = "Exporte a revisão atual para gerar sugestões.";
      list.replaceChildren();
      renderResult();
      return;
    }
    if (!documentValue.items?.length) {
      empty.textContent = "Ainda não há sugestões. Use Gerar sugestões.";
      list.replaceChildren();
    } else {
      const visible = documentValue.items.filter(matches);
      const hadFocus = list.contains(document.activeElement);
      list.replaceChildren();
      for (const item of visible) list.append(card(item));
      if (!visible.length) empty.textContent = "Nenhuma sugestão neste filtro.";
      // Rebuilding the list drops focus; give it back to the selected card so A/R/J/K keep working.
      if (hadFocus && selectedId) {
        list.querySelector(`[data-id="${CSS.escape(selectedId)}"]`)?.focus({ preventScroll: true });
      }
    }
    const items = documentValue.items || [];
    const counts = {
      pending: items.filter((item) => item.status === "pending").length,
      accepted: items.filter(accepted).length,
      rejected: items.filter((item) => item.status === "rejected").length,
    };
    $("finishing-counts").textContent = `${counts.pending} pendente(s) · ${counts.accepted} aceito(s) · ${counts.rejected} recusado(s)`;
    $("finishing-warning").textContent = documentValue.warning || "";
    $("finishing-generate").textContent = items.length ? "Atualizar sugestões" : "Gerar sugestões";
    renderResult();
  }

  async function refresh() {
    try {
      const [documentRequest, exports, status] = await Promise.all([
        api(`${base}/finishing/suggestions`),
        api(`${base}/exports`),
        api(`${base}/finishing/render`),
      ]);
      documentValue = documentRequest;
      currentExport = (exports || []).find((item) => item.state === "complete" && item.status === "current") || null;
      renderStatus = status || { state: "idle" };
      section.hidden = false;
      render();
      schedule();
    } catch (error) {
      section.hidden = false;
      empty.textContent = error.message;
    }
  }

  $("finishing-generate").onclick = () => ctx.action(async () => {
    if (!currentExport) { ctx.message("Exporte a revisão atual para gerar sugestões."); return; }
    await api(`${base}/finishing/suggestions/generate`, "POST", {});
    await refresh();
    ctx.message("Sugestões atualizadas.");
  });

  renderButton.onclick = () => ctx.action(async () => {
    if (polling) return;
    polling = true;
    renderButton.disabled = true;
    try {
      await api(`${base}/finishing/render`, "POST", {});
      await refresh();
      ctx.message("Render do vídeo final na fila.");
    } finally {
      polling = false;
      renderRenderButton();
    }
  });

  $("finishing-result-copy").onclick = () => ctx.action(async () => {
    const response = await fetch(`${base}/finishing/render/description`);
    if (!response.ok) throw new Error("Descrição ainda não está pronta.");
    await navigator.clipboard.writeText(await response.text());
    ctx.message("Descrição copiada.");
  });

  $("finishing-filter-type").onchange = render;
  $("finishing-filter-status").onchange = render;

  document.addEventListener("keydown", (event) => {
    if (section.hidden || !section.contains(document.activeElement)) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    const key = event.key.toLowerCase();
    const visible = (documentValue.items || []).filter(matches);
    const index = visible.findIndex((item) => item.id === selectedId);
    if (key === "a" && index >= 0) {
      event.preventDefault(); event.stopPropagation();
      decide(visible[index], { action: "accept" });
    } else if (key === "r" && index >= 0) {
      event.preventDefault(); event.stopPropagation();
      decide(visible[index], { action: "reject" });
    } else if (key === "j" || key === "k") {
      event.preventDefault(); event.stopPropagation();
      if (!visible.length) return;
      const nextIndex = key === "j"
        ? Math.min(visible.length - 1, index < 0 ? 0 : index + 1)
        : Math.max(0, index <= 0 ? 0 : index - 1);
      selectedId = visible[nextIndex].id;
      const target = list.querySelector(`[data-id="${CSS.escape(selectedId)}"]`);
      target?.focus();
      target?.scrollIntoView({ block: "nearest" });
    }
  });

  section.addEventListener("click", () => {});
  refresh().catch((error) => ctx.message(error.message));
  return { refresh };
}
