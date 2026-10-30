import { isAdvanced, setAdvanced, wireCommand } from "./ui.js";

function tab(name) {
  document.querySelector(`[data-tab="${name}"]`)?.click();
}

function openManualCut() {
  const details = document.querySelector(".manual-cut");
  if (details) details.open = true;
  document.getElementById("start")?.focus();
}

wireCommand([
  { label: "Revisar decisões", icon: "scissors", hint: "1", run: () => tab("review") },
  {
    label: "Abrir transcrição",
    icon: "text-t",
    hint: "2",
    run: () => {
      tab("transcript");
      document.getElementById("search")?.focus();
    },
  },
  { label: "Ritmo e prolixidade", icon: "waveform", run: () => tab("rhythm") },
  { label: "Abrir briefing", icon: "file-video", hint: "3", run: () => tab("briefing") },
  {
    label: "Finalizar para o CapCut",
    icon: "arrow-square-out",
    run: () => document.getElementById("finish")?.scrollIntoView({ block: "start", behavior: "smooth" }),
  },
  { label: "Buscar na transcrição", icon: "magnifying-glass", run: () => {
    tab("transcript");
    document.getElementById("search")?.focus();
  } },
  { label: "Cortar intervalo", icon: "selection", run: openManualCut },
  { label: "Marcar", icon: "clock", hint: "M", run: () => document.getElementById("mark")?.click() },
  { label: "Exportar MP4 + SRT", icon: "download-simple", run: () => document.getElementById("export")?.click() },
  { label: "Desfazer", icon: "arrow-counter-clockwise", hint: "Ctrl Z", run: () => document.getElementById("undo")?.click() },
  { label: "Voltar para a Filmoteca", icon: "film-strip", run: () => (location.href = "/") },
  { label: "Mostrar ou esconder opções avançadas", icon: "sliders-horizontal", run: () => setAdvanced(!isAdvanced()) },
]);
