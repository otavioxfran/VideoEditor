import { api, element, button, time } from "./api.js";
import { icon } from "./ui.js";

// Rhythm tab: anti-prolixity suggestions. Opening the tab only reads the cache;
// the button explicitly asks the provider. Nothing is cut without a click.
export function setupProlixity(ctx) {
  const { $ } = ctx;
  let data = null;

  const noted = (name, text) => {
    const node = element("p", undefined, "noted");
    node.append(icon(name), element("span", text));
    return node;
  };
  const iconButton = (name, text, callback) => {
    const node = button(text, callback);
    node.className = "btn small";
    node.prepend(icon(name));
    return node;
  };

  function setCount(count) {
    const badge = $("rhythm-count");
    if (!badge) return;
    badge.hidden = !count;
    badge.textContent = count ? String(count) : "";
  }

  async function load(analyze = false) {
    if (!ctx.current) return;
    $("prolixity-status").textContent = analyze
      ? "Analisando o roteiro (pode levar até um minuto)…"
      : "Carregando a análise salva…";
    try {
      const value = await api(`${ctx.base}/editorial/anti-prolixity`, analyze ? "POST" : "GET");
      if (value.state === "not_analyzed") {
        data = null;
        setCount(0);
        $("prolixity-status").textContent = "Ainda não analisado.";
        render();
        return;
      }
      data = value.issues || [];
      setCount(data.length);
      const origin = value.state === "local_only" ? " Gemini indisponível: só repetições medidas localmente." : "";
      $("prolixity-status").textContent =
        (data.length
          ? `${data.length} ${data.length === 1 ? "trecho" : "trechos"} para você avaliar. Nada é cortado sem seu clique.`
          : "Nenhum trecho prolixo sugerido.") + origin;
      render();
    } catch (error) {
      $("prolixity-status").textContent = `Erro ao analisar: ${error.message}`;
    }
  }

  function render() {
    const list = $("prolixity-list");
    const current = ctx.current;
    if (!current || !list) return;
    list.replaceChildren();
    if (!data || !data.length) {
      list.append(
        element(
          "p",
          data === null
            ? "Clique em Analisar ritmo e roteiro para procurar trechos arrastados."
            : "Nenhum trecho prolixo detectado.",
          "hint"
        )
      );
      return;
    }
    for (const issue of data) {
      const row = element("div", undefined, "item");
      const duration = issue.end - issue.start;
      const overlap = current.timeline.cuts.reduce(
        (sum, [a, b]) => sum + Math.max(0, Math.min(b, issue.end) - Math.max(a, issue.start)),
        0
      );
      const isCut = duration > 0 && overlap >= duration * 0.85;
      const head = element("div", undefined, "toolbar");
      head.append(
        element("span", isCut ? "Cortado" : `~${issue.saved_seconds}s a mais`, `badge ${isCut ? "success" : "review"}`),
        element("h3", issue.title)
      );
      row.append(
        head,
        element("p", `${time(issue.start)} → ${time(issue.end)} · Economia estimada: ~${issue.saved_seconds}s`),
        element("p", issue.issue),
        noted("lightbulb", `Recomendação: ${issue.recommendation}`),
        element("blockquote", issue.preview_quote, "quote")
      );
      if (issue.risk) row.append(noted("warning-circle", `Risco: ${issue.risk}`));
      if (issue.confidence !== undefined) {
        row.append(
          element(
            "p",
            `Confiança: ${Math.round(issue.confidence * 100)}% · ${issue.origin === "local" ? "medido localmente" : "sugestão do Gemini"}`
          )
        );
      }
      const actions = element("div", undefined, "toolbar");
      actions.append(iconButton("headphones", "Ouvir trecho", () => ctx.review(issue.start, issue.end)));
      actions.append(
        isCut
          ? iconButton("arrow-counter-clockwise", "Restaurar trecho", () =>
              ctx.edit({ action: "restore", start: issue.start, end: issue.end })
            )
          : iconButton("scissors", "Cortar trecho", () => ctx.edit({ action: "cut", start: issue.start, end: issue.end }))
      );
      row.append(actions);
      list.append(row);
    }
  }

  $("analyze-prolixity").onclick = () => ctx.action(() => load(true));
  document.querySelector('[data-tab="rhythm"]')?.addEventListener("click", () => {
    if (data === null && ctx.current) ctx.action(() => load(false));
  });

  return { load, render };
}
