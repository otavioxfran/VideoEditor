import { element } from "./api.js";
import { icon } from "./ui.js";

export const REVIEW_KINDS = new Set([
  "repeated_block",
  "visual_absence",
  "repeated_idea",
  "false_start",
  "contextual_restart",
]);

const KIND_LABELS = {
  repeated_block: "Tomada repetida",
  visual_absence: "Ausência",
  repeated_idea: "Ideia repetida",
  false_start: "Falso começo",
  contextual_restart: "Recomeço",
  retake: "Retake",
  correction: "Correção",
  stumble: "Tropeço",
  silence: "Silêncio",
};

const DECISION_LABELS = { applied: "Aplicado", kept: "Mantido", protected: "Protegido" };

function clock(seconds) {
  return new Date(Math.max(0, seconds) * 1000).toISOString().slice(11, 19);
}

function seconds(value) {
  return `${value.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s`;
}

function actionButton(name, label, className, callback) {
  const node = element("button", undefined, className);
  node.type = "button";
  node.append(icon(name), element("span", label));
  node.addEventListener("click", (event) => {
    event.stopPropagation();
    callback();
  });
  return node;
}

// The category and the take position are shown as data; the reason keeps only what is new.
function reasonText(s, showsLost) {
  let text = s.reason;
  if (s.kind === "repeated_block") text = text.replace(/^Tomada repetida \(\d+ de \d+\):\s*/, "");
  // The lost words get their own line in the evidence, so drop them from the sentence.
  if (showsLost) text = text.replace(/[;.]\s*a versão final não diz:[^.]*\./i, ".");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function lostWords(words) {
  const node = element("p", undefined, "rc-lost");
  node.append(element("span", "Só aqui: ", "rc-lost-label"), element("span", words.join(", ")));
  return node;
}

function takeStack(evidence) {
  const takes = Number(evidence.takes) || 0;
  const position = Number(evidence.position) || 0;
  const stack = element("ol", undefined, "take-stack");
  stack.setAttribute("aria-label", `Tomada ${position} de ${takes}; a última fica`);
  for (let take = 1; take <= takes; take += 1) {
    const state = take === position ? "this" : take === takes ? "keep" : "other";
    const chip = element("li", `Tomada ${take}`, `take ${state}`);
    if (state === "this") chip.append(element("span", "· esta", "take-note"));
    if (state === "keep") chip.append(element("span", "· fica", "take-note"));
    stack.append(chip);
  }
  return stack;
}

function evidenceNode(s, base) {
  const evidence = s.evidence && !Array.isArray(s.evidence) ? s.evidence : {};
  if (s.kind === "repeated_block" && evidence.takes) {
    const box = element("div", undefined, "rc-evidence");
    box.append(takeStack(evidence));
    if (evidence.lost?.length) box.append(lostWords(evidence.lost));
    return box;
  }
  if (s.kind === "visual_absence") {
    const box = element("div", undefined, "rc-evidence rc-frame");
    const image = element("img");
    image.loading = "lazy";
    image.alt = `Quadro em ${clock((s.start + s.end) / 2)}`;
    image.src = `${base}/frame?t=${((s.start + s.end) / 2).toFixed(2)}`;
    image.addEventListener("error", () => image.remove());
    box.append(image);
    if (evidence.speech?.length) {
      box.append(element("p", `Fala no trecho: “${evidence.speech.join(" ")}”`, "rc-speech"));
    }
    return box;
  }
  if (s.detail?.lost?.length) {
    const box = element("div", undefined, "rc-evidence");
    box.append(lostWords(s.detail.lost));
    return box;
  }
  return null;
}

function moreMenu(callbacks) {
  const menu = element("details", undefined, "rc-more");
  const summary = element("summary", undefined, "btn ghost small icon-only");
  summary.setAttribute("aria-label", "Mais ações");
  summary.title = "Mais ações";
  summary.append(icon("dots-three"));
  const list = element("div", undefined, "rc-menu");
  list.append(
    actionButton("shield-check", "Proteger pausa", "menu-item", () => {
      menu.open = false;
      callbacks.protect();
    })
  );
  menu.append(summary, list);
  menu.addEventListener("click", (event) => event.stopPropagation());
  return menu;
}

export function reviewCard(s, { decision, base, listen, cut, keep, protect }) {
  const review = REVIEW_KINDS.has(s.kind) && !s.auto;
  const card = element("article", undefined, "item review-card");
  card.tabIndex = -1;
  card.dataset.id = s.id;
  card.dataset.auto = s.auto ? "1" : "0";
  card.dataset.kind = s.kind;
  card.dataset.applied = decision === "applied" ? "1" : "0";
  card.dataset.review = review ? "1" : "0";
  card.dataset.decision = decision || "pending";

  const head = element("div", undefined, "rc-head");
  const label = decision
    ? DECISION_LABELS[decision]
    : s.auto
      ? "Automático restaurado"
      : KIND_LABELS[s.kind] || "Revisar";
  const tone = decision === "applied" ? "success" : decision ? "neutral" : review ? "review" : "neutral";
  head.append(
    element("span", label, `badge ${tone}`),
    element("span", `${clock(s.start)} → ${clock(s.end)}`, "timecode"),
    element("span", seconds(s.end - s.start), "rc-duration")
  );
  if (decision && KIND_LABELS[s.kind]) head.append(element("span", KIND_LABELS[s.kind], "rc-kind"));

  const lost = s.evidence?.lost?.length || s.detail?.lost?.length;
  card.append(head, element("p", reasonText(s, Boolean(lost)), "rc-reason"));
  const evidence = evidenceNode(s, base);
  if (evidence) card.append(evidence);

  const actions = element("div", undefined, "rc-actions");
  if (decision === "applied") {
    actions.append(actionButton("arrow-counter-clockwise", "Restaurar", "btn small", keep));
  } else {
    actions.append(actionButton("scissors", "Aplicar corte", "btn small", cut));
  }
  actions.append(actionButton("headphones", "Ouvir", "btn ghost small", listen));
  if (!decision) actions.append(actionButton("check", "Manter", "btn ghost small", keep));
  actions.append(moreMenu({ protect }));
  card.append(actions);
  return card;
}
