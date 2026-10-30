import { api, element, button } from "./api.js";

const CLAIM_STATES = ["NEEDS_VERIFICATION", "SUPPORTED", "CONTRADICTED", "INCONCLUSIVE", "NOT_CHECKABLE"];

function contentState(item) {
  return { removed: "Removido da edição", partially_removed: "Parcialmente removido", kept: "Mantido" }[item.content_state] || "";
}

// Briefing tab: optional semantic notes (thesis, chapters, claims, titles). It never changes cuts by itself.
export function setupBriefing(ctx) {
  const { $ } = ctx;
  let editorial = null;
  let signature = "";

  function claimRow(claim, brief, segments) {
    const row = element("div", undefined, "item");
    row.append(element("p", `${claim.status} · ${claim.text} · ${contentState(claim)}`), element("p", `Trecho: ${claim.quote}`));
    const references = segments.filter((s) => claim.segment_ids.includes(s.id));
    if (references.length) {
      row.append(button("Ouvir referência", () => ctx.review(references[0].start, references.at(-1).end)));
    }
    const search = element("a", "Pesquisar fonte");
    search.href = `https://www.google.com/search?q=${encodeURIComponent(claim.text)}`;
    search.target = "_blank";
    search.rel = "noopener noreferrer";
    row.append(search);

    const url = element("input");
    url.placeholder = "URL da fonte";
    url.setAttribute("aria-label", `URL da fonte ${claim.id}`);
    const excerpt = element("textarea");
    excerpt.placeholder = "Trecho da evidência (manual)";
    excerpt.setAttribute("aria-label", `Evidência ${claim.id}`);
    const select = element("select");
    select.setAttribute("aria-label", `Estado ${claim.id}`);
    for (const status of CLAIM_STATES) {
      const option = element("option", status);
      option.value = status;
      select.append(option);
    }
    select.value = claim.status;
    const label = element("label", "Confirmei a relação entre fonte e claim ");
    const confirm = element("input");
    confirm.type = "checkbox";
    label.append(confirm);
    row.append(
      url,
      excerpt,
      select,
      label,
      button("Registrar evidência", async () => {
        await api(`${ctx.base}/editorial/evidence`, "POST", {
          expected_revision: brief.revision,
          claim_id: claim.id,
          status: select.value,
          url: url.value,
          excerpt: excerpt.value,
          human_confirmed: confirm.checked,
        });
        await load();
      })
    );
    for (const evidence of claim.evidence) row.append(element("p", `${evidence.origin}: ${evidence.url} · ${evidence.excerpt}`));
    return row;
  }

  async function load() {
    editorial = await api(`${ctx.base}/editorial`);
    const next = JSON.stringify(editorial);
    if (next === signature) return;
    signature = next;
    const brief = editorial.brief;
    $("editorial-status").textContent =
      editorial.assistance.error ||
      `Assistência: ${editorial.assistance.state}. ${
        brief ? `Briefing ${brief.origin}` : `Sem briefing semântico; ${editorial.local_signals.length} sinais locais para verificar.`
      }`;
    const root = $("brief");
    root.replaceChildren();
    for (const note of editorial.retakes || []) root.append(element("p", `Retake ${note.suggestion_id}: ${note.reason}`));
    if (!brief) return;

    const segments = ctx.current?.transcript.segments || [];
    root.append(element("h3", "Briefing"), element("p", brief.thesis), element("p", brief.summary));
    for (const chapter of brief.chapters) {
      root.append(element("p", `${chapter.title} · ${chapter.segment_ids.join(", ")} · ${contentState(chapter)}`));
    }
    for (const field of ["suggestions", "risks"]) {
      for (const item of brief[field]) {
        const row = element("div", undefined, "item");
        row.append(element("p", `${item.reason} · ${item.segment_ids.join(", ")} · ${contentState(item)}`));
        const matched = segments.filter((s) => item.segment_ids.includes(s.id));
        for (const segment of matched) row.append(button(`Ouvir ${segment.id}`, () => ctx.review(segment.start, segment.end)));
        if (matched.length && field === "suggestions") {
          row.append(button("Cortar após minha revisão", () => ctx.edit({ action: "cut", segment_ids: item.segment_ids })));
        }
        root.append(row);
      }
    }
    root.append(element("h3", "Ideias de títulos — revise contra o conteúdo mantido"));
    for (const title of brief.titles) root.append(element("p", title));
    if (brief.titles_stale) {
      root.append(element("p", "Compatibilidade dos títulos com a timeline atual não confirmada; revise ou gere novamente."));
    }
    root.append(element("h3", "Claims"));
    for (const claim of brief.claims) root.append(claimRow(claim, brief, segments));
  }

  $("copy-prompt").onclick = () =>
    ctx.action(async () => {
      const text = await api(`${ctx.base}/editorial/prompt`);
      $("prompt").hidden = false;
      $("prompt").value = text;
      try {
        await navigator.clipboard.writeText(text);
        ctx.message("Prompt copiado.");
      } catch {
        $("prompt").select();
        ctx.message("Selecione e copie o prompt abaixo.");
      }
    });
  $("import").onclick = () =>
    ctx.action(async () => {
      await api(`${ctx.base}/editorial/import`, "POST", {
        expected_revision: editorial?.brief?.revision ?? -1,
        result: JSON.parse($("response").value),
      });
      await load();
      ctx.message("Briefing importado; nenhuma decisão de corte foi alterada.");
    });
  $("assist").onclick = () =>
    ctx.action(async () => {
      if (!$("consent").checked) throw new Error("Autorize explicitamente o envio de texto.");
      await api(`${ctx.base}/editorial/assist`, "POST", { send_text: true, task: $("task").value });
      await load();
    });
  $("editorial-panel").addEventListener("toggle", () => {
    if ($("editorial-panel").open && ctx.current) ctx.action(load);
  });

  return { load, isOpen: () => $("editorial-panel").open };
}
