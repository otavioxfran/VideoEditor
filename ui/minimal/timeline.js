import { formatDuration } from "./ui.js";

const SVG = "http://www.w3.org/2000/svg";
const HEIGHT = 112;
const TRACK_Y = 30;
const TRACK_H = 30;
const MARK_Y = 70;
const MARK_H = 8;
const RULER_Y = 16;
const DRAG_MIN_PX = 4;
const REVIEW_KINDS = ["repeated_block", "visual_absence", "repeated_idea", "retake", "false_start", "contextual_restart", "correction"];

let latest = null;
let playhead = null;
let wave = null;
let waveUrl = null;
let resizeFrame = 0;
let selected = null;
let zoom = 1;
let range = null;
let drag = null;

function svgNode(name, attrs = {}) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function chooseStep(duration) {
  const target = Math.max(2, duration / 8);
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 180, 300, 600, 900, 1800];
  return steps.find((step) => step >= target) || steps.at(-1);
}

function clock(value) {
  return formatDuration(value);
}

// Hover and selection need sub-second precision: m:ss.d
function fine(value) {
  const tenths = Math.floor((Math.max(0, value) % 1) * 10);
  return `${formatDuration(Math.floor(value))}.${tenths}`;
}

function host() {
  return document.getElementById("timeline");
}

function timeAt(event) {
  const svg = host()?.querySelector("svg");
  const duration = host()?._duration;
  if (!svg || !duration) return null;
  const rect = svg.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  return ratio * duration;
}

function draw() {
  const root = host();
  if (!root || !latest) return;
  const { current } = latest;
  const timeline = current.timeline;
  const duration = Number(timeline?.source_duration || 0);
  const view = Math.max(320, root.clientWidth || 800);
  const width = Math.round(view * zoom);
  const key = `${width}|${current.edit?.revision}|${selected}|${current.suggestions?.length}|${current.timeline?.cuts?.length}|${wave?.peaks?.length || 0}|${JSON.stringify(current.decisions || {}).length}`;
  if (root._timelineKey === key) return;
  root._timelineKey = key;
  const scrollRatio = root.scrollWidth ? root.scrollLeft / root.scrollWidth : 0;
  root.replaceChildren();
  if (!duration || !timeline) {
    const empty = svgNode("svg", { viewBox: `0 0 ${view} ${HEIGHT}`, "aria-hidden": "true" });
    empty.append(svgNode("rect", { x: 0, y: 0, width: view, height: HEIGHT, class: "timeline-bg" }));
    const label = svgNode("text", { x: view / 2, y: HEIGHT / 2, "text-anchor": "middle", class: "timeline-empty" });
    label.textContent = "Timeline disponível depois da preparação.";
    empty.append(label);
    root.append(empty);
    return;
  }
  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${HEIGHT}`,
    width,
    height: HEIGHT,
    preserveAspectRatio: "none",
    "aria-hidden": "true",
  });
  svg.style.width = `${width}px`;
  svg.append(svgNode("rect", { x: 0, y: 0, width, height: HEIGHT, class: "timeline-bg" }));

  const xFor = (time) => Math.max(0, Math.min(width, (time / duration) * width));
  const step = chooseStep(duration / zoom);
  for (let time = 0; time <= duration; time += step) {
    const x = xFor(time);
    svg.append(svgNode("line", { x1: x, y1: 24, x2: x, y2: HEIGHT - 10, class: "timeline-tick" }));
    const label = svgNode("text", { x: Math.min(width - 4, x + 3), y: RULER_Y, class: "timeline-ruler" });
    label.textContent = clock(time);
    svg.append(label);
  }

  for (const keep of timeline.keeps || []) {
    svg.append(
      svgNode("rect", {
        x: xFor(keep.source_start),
        y: TRACK_Y,
        width: Math.max(1, xFor(keep.source_end) - xFor(keep.source_start)),
        height: TRACK_H,
        class: "timeline-keep",
      })
    );
  }
  for (const [start, end] of timeline.cuts || []) {
    const x = xFor(start);
    const w = Math.max(1, xFor(end) - x);
    svg.append(svgNode("rect", { x, y: TRACK_Y, width: w, height: TRACK_H, class: "timeline-cut" }));
    if (w > 3) {
      svg.append(svgNode("line", { x1: x + 0.5, y1: TRACK_Y, x2: x + 0.5, y2: TRACK_Y + TRACK_H, class: "timeline-cut-line" }));
    }
  }

  if (wave && Array.isArray(wave.peaks) && wave.peaks.length) {
    const step = width / wave.peaks.length;
    for (let index = 0; index < wave.peaks.length; index += 1) {
      const height = Math.max(0, Math.min(1, wave.peaks[index])) * TRACK_H * .46;
      if (height < .6) continue;
      const x = index * step;
      const center = TRACK_Y + TRACK_H / 2;
      svg.append(svgNode("line", {
        x1: x, y1: center - height, x2: x, y2: center + height,
        class: "timeline-wave",
      }));
    }
  }

  for (const suggestion of current.suggestions || []) {
    if (suggestion.auto) continue;
    const x = xFor(suggestion.start);
    const w = Math.max(2, xFor(suggestion.end) - x);
    const review = REVIEW_KINDS.includes(suggestion.kind);
    const decided = current.decisions?.[suggestion.id];
    const mark = svgNode("g", {
      class: `timeline-suggest${review ? "" : " auto"}${decided ? " decided" : ""}${suggestion.id === selected ? " selected" : ""}`,
      "data-id": suggestion.id,
    });
    // A taller transparent hit area makes the 8px mark easy to click.
    mark.append(
      svgNode("rect", { x: x - 2, y: MARK_Y - 6, width: w + 4, height: MARK_H + 12, class: "timeline-hit" }),
      svgNode("rect", { x, y: MARK_Y, width: w, height: MARK_H, rx: 1, class: "timeline-mark" })
    );
    const title = svgNode("title");
    title.textContent = `${clock(suggestion.start)}–${clock(suggestion.end)} · ${suggestion.reason}`;
    mark.append(title);
    svg.append(mark);
  }

  for (const span of current.edit?.protected || []) {
    svg.append(
      svgNode("rect", {
        x: xFor(span.start),
        y: MARK_Y + 12,
        width: Math.max(2, xFor(span.end) - xFor(span.start)),
        height: 4,
        rx: 2,
        class: "timeline-protected",
      })
    );
  }

  const selection = svgNode("rect", { x: 0, y: 22, width: 0, height: HEIGHT - 28, class: "timeline-range", visibility: "hidden" });
  svg.append(selection);

  playhead = svgNode("g");
  playhead.append(
    svgNode("line", { x1: 0, y1: 20, x2: 0, y2: HEIGHT - 6, class: "timeline-playhead" }),
    svgNode("rect", { x: -3, y: 18, width: 6, height: 5, rx: 1, class: "timeline-playhead-cap" })
  );
  svg.append(playhead);

  const hover = svgNode("g", { class: "timeline-hover", visibility: "hidden" });
  const hoverLabel = svgNode("text", { x: 0, y: HEIGHT - 4, class: "timeline-hover-label" });
  hover.append(svgNode("line", { x1: 0, y1: 24, x2: 0, y2: HEIGHT - 16 }), hoverLabel);
  svg.append(hover);
  root.append(svg);

  root._xFor = xFor;
  root._duration = duration;
  root._width = width;
  root._hover = hover;
  root._hoverLabel = hoverLabel;
  root._range = selection;
  root.scrollLeft = scrollRatio * root.scrollWidth;
  paintRange();
  movePlayhead();
}

function paintRange() {
  const root = host();
  if (!root?._range || !root._xFor) return;
  const shown = drag?.moved ? drag : range;
  if (!shown) {
    root._range.setAttribute("visibility", "hidden");
    return;
  }
  const a = root._xFor(Math.min(shown.start, shown.end));
  const b = root._xFor(Math.max(shown.start, shown.end));
  root._range.setAttribute("x", a);
  root._range.setAttribute("width", Math.max(1, b - a));
  root._range.setAttribute("visibility", "visible");
}

function movePlayhead() {
  const root = host();
  const video = document.getElementById("video");
  if (!root || !video || !playhead || !root._xFor) return;
  const x = root._xFor(video.currentTime || 0);
  for (const node of playhead.children) {
    if (node.tagName === "line") {
      node.setAttribute("x1", x);
      node.setAttribute("x2", x);
    } else {
      node.setAttribute("x", x - 3);
    }
  }
  // Zoomed in, keep the playhead on screen while it plays.
  if (zoom > 1 && !drag && (x < root.scrollLeft || x > root.scrollLeft + root.clientWidth - 24)) {
    root.scrollLeft = Math.max(0, x - root.clientWidth * 0.2);
  }
}

function reveal(time) {
  const root = host();
  if (!root?._xFor || zoom === 1) return;
  const x = root._xFor(time);
  if (x < root.scrollLeft || x > root.scrollLeft + root.clientWidth) {
    root.scrollLeft = Math.max(0, x - root.clientWidth * 0.3);
  }
}

function setZoom(value) {
  zoom = value;
  document.querySelectorAll("[data-zoom]").forEach((button) => {
    button.setAttribute("aria-pressed", Number(button.dataset.zoom) === zoom ? "true" : "false");
  });
  const root = host();
  if (root) root._timelineKey = null;
  draw();
  const video = document.getElementById("video");
  if (video) reveal(video.currentTime || 0);
}

function useRange(start, end) {
  range = { start: Math.min(start, end), end: Math.max(start, end) };
  paintRange();
  // The manual-cut form is the one place that applies a range; the drag just fills it.
  const form = document.querySelector(".manual-cut");
  const from = document.getElementById("start");
  const to = document.getElementById("end");
  if (from && to) {
    from.value = range.start.toFixed(2);
    to.value = range.end.toFixed(2);
    if (form) form.open = true;
  }
  const label = document.getElementById("timeline-range");
  if (label) {
    label.textContent = `Seleção ${fine(range.start)} – ${fine(range.end)} (${(range.end - range.start).toFixed(1).replace(".", ",")} s) · confira em Corte manual`;
    label.hidden = false;
  }
  const video = document.getElementById("video");
  if (video) video.currentTime = range.start;
}

function clearRange() {
  range = null;
  paintRange();
  const label = document.getElementById("timeline-range");
  if (label) label.hidden = true;
}

async function ensureWaveform(url) {
  if (!url || url === waveUrl) return;
  waveUrl = url;
  try {
    const response = await fetch(url);
    if (response.ok) {
      wave = await response.json();
      draw();
    }
  } catch {
    wave = null;
  }
}

function bind() {
  const root = host();
  const video = document.getElementById("video");
  if (!root || !video) return;
  document.addEventListener("editor:render", (event) => {
    latest = event.detail;
    draw();
    ensureWaveform(root.dataset.waveform);
  });
  video.addEventListener("timeupdate", movePlayhead);
  video.addEventListener("seeking", movePlayhead);

  root.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const time = timeAt(event);
    if (time === null) return;
    drag = { start: time, end: time, x: event.clientX, moved: false };
    root.setPointerCapture(event.pointerId);
  });
  root.addEventListener("pointermove", (event) => {
    const time = timeAt(event);
    if (time === null) return;
    if (root._hover && root._xFor) {
      const x = root._xFor(time);
      const [line, text] = root._hover.children;
      line.setAttribute("x1", x);
      line.setAttribute("x2", x);
      text.textContent = fine(time);
      const flip = x > root._width - 56;
      text.setAttribute("x", flip ? x - 4 : x + 4);
      text.setAttribute("text-anchor", flip ? "end" : "start");
      root._hover.setAttribute("visibility", "visible");
    }
    if (drag) {
      drag.end = time;
      if (Math.abs(event.clientX - drag.x) >= DRAG_MIN_PX) drag.moved = true;
      paintRange();
    }
  });
  root.addEventListener("pointerup", (event) => {
    if (!drag) return;
    const done = drag;
    drag = null;
    if (root.hasPointerCapture(event.pointerId)) root.releasePointerCapture(event.pointerId);
    if (done.moved) {
      useRange(done.start, done.end);
      return;
    }
    // A click seeks; on a review mark it also opens the decision.
    clearRange();
    video.currentTime = done.start;
    const mark = event.target.closest?.("[data-id]") || document.elementFromPoint(event.clientX, event.clientY)?.closest?.("[data-id]");
    if (mark) document.dispatchEvent(new CustomEvent("review:select", { detail: { id: mark.dataset.id } }));
  });
  root.addEventListener("pointercancel", () => {
    drag = null;
    paintRange();
  });
  root.addEventListener("pointerleave", () => {
    if (root._hover && !drag) root._hover.setAttribute("visibility", "hidden");
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && range) clearRange();
  });

  document.addEventListener("review:selected", (event) => {
    selected = event.detail?.id || null;
    root.querySelectorAll(".timeline-suggest").forEach((node) => {
      node.classList.toggle("selected", node.dataset.id === selected);
    });
    const suggestion = latest?.current?.suggestions?.find((item) => item.id === selected);
    if (suggestion) reveal(suggestion.start);
  });
  document.querySelectorAll("[data-zoom]").forEach((button) => {
    button.addEventListener("click", () => setZoom(Number(button.dataset.zoom)));
  });
  document.getElementById("range")?.addEventListener("submit", () => clearRange());

  new ResizeObserver(() => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => draw());
  }).observe(root);
}

bind();
