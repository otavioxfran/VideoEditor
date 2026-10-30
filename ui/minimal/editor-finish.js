import { api, element, button, time } from "./api.js";
import { icon } from "./ui.js";

const STATUS_LABELS = {
  current: "Atual",
  stale: "Desatualizado",
  missing: "Arquivos ausentes",
  historical: "Histórico",
  queued: "Na fila",
  running: "Renderizando",
  failed: "Falhou",
};

function link(text, href, iconName, download) {
  const node = element("a", undefined, "btn small");
  node.href = href;
  if (download) node.download = download;
  else {
    node.target = "_blank";
    node.rel = "noopener";
  }
  node.append(icon(iconName), element("span", text));
  return node;
}

function iconButton(name, text, className, callback) {
  const node = button(text, callback);
  node.className = className;
  node.prepend(icon(name));
  return node;
}

function when(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// Finish panel: the cut ends here; CapCut receives the MP4, the SRT and the markers.
export function setupFinish(ctx) {
  const { $ } = ctx;
  let markers = [];

  function files(item) {
    const row = element("div", undefined, "finish-actions");
    row.append(
      link("Baixar MP4", `${ctx.base}/exports/${item.render_id}/video.mp4`, "download-simple", "video.mp4"),
      link("Baixar SRT", `${ctx.base}/exports/${item.render_id}/subtitles.srt`, "subtitles", "subtitles.srt"),
      link("Assistir", `${ctx.base}/exports/${item.render_id}/video.mp4`, "arrow-square-out")
    );
    return row;
  }

  function renderExports(values) {
    const box = $("export-current");
    box.replaceChildren();
    const latest = values.find((item) => item.render_id && item.state !== "historical");
    if (!latest) {
      box.append(element("p", "Nenhum export ainda. Quando terminar a revisão, use Exportar MP4 + SRT no topo.", "hint"));
    } else {
      const label = STATUS_LABELS[latest.status] || latest.status;
      const tone = latest.status === "current" ? "success" : latest.status === "failed" ? "danger" : "neutral";
      const head = element("div", undefined, "finish-status");
      head.append(
        element("span", label, `badge ${tone}`),
        element("span", `${latest.mode === "evaluation" ? "Edição experimental · " : ""}${when(latest.completed_at || latest.created_at)}`, "meta"),
        element("span", latest.render_id.slice(0, 8), "timecode")
      );
      box.append(head);
      if (latest.status === "stale") {
        box.append(element("p", "O corte ou o perfil mudou depois deste export. Exporte de novo para levar a versão atual ao CapCut.", "hint"));
      } else if (latest.error) {
        box.append(element("p", latest.error, "hint"));
      }
      if (latest.state === "complete" && latest.status !== "missing") box.append(files(latest));
    }

    const older = values.filter((item) => item !== latest);
    $("export-history").hidden = !older.length;
    $("export-history-count").textContent = older.length ? `(${older.length})` : "";
    const list = $("exports");
    list.replaceChildren();
    for (const item of older) {
      const row = element("div", undefined, "item");
      row.append(
        element(
          "p",
          `${item.mode === "evaluation" ? "Edição experimental · " : ""}${STATUS_LABELS[item.status] || item.status} · ${
            item.render_id?.slice(0, 8) || item.filename
          }${item.error ? ` · ${item.error}` : ""}`
        )
      );
      if (item.state === "complete" && item.status !== "missing") row.append(files(item));
      else if (item.state === "historical") {
        const old = element("a", "Baixar histórico · compatibilidade desconhecida");
        old.href = `${ctx.base}/historical/${encodeURIComponent(item.filename)}`;
        row.append(old);
      }
      list.append(row);
    }
  }

  function renderMarkers(current) {
    markers = current.markers || [];
    const list = $("markers");
    list.replaceChildren();
    $("copy-markers").disabled = !markers.length;
    if (!markers.length) {
      list.append(element("p", "Sem marcadores. Aperte M durante a revisão para marcar um ponto para o CapCut.", "hint"));
      return;
    }
    for (const marker of markers) {
      const row = element("div", undefined, "item marker-row");
      row.append(
        element("span", time(marker.output_time), "timecode"),
        element("span", marker.label || "Marcador", "marker-label"),
        iconButton("headphones", "Ouvir", "btn ghost small", () => ctx.review(marker.source_time, marker.source_time + 1)),
        iconButton("trash", "Excluir", "btn ghost small", () => ctx.edit({ action: "delete_marker", marker_id: marker.id }))
      );
      list.append(row);
    }
  }

  $("copy-markers").onclick = () =>
    ctx.action(async () => {
      // Times are in the exported MP4, which is the timeline CapCut sees.
      const text = markers.map((m) => `${time(m.output_time)} ${m.label || "Marcador"}`).join("\n");
      await navigator.clipboard.writeText(text);
      ctx.message(`${markers.length} ${markers.length === 1 ? "marcador copiado" : "marcadores copiados"} com o tempo do MP4.`);
    });

  return {
    renderExports,
    renderMarkers,
    async loadExports() {
      const values = await api(`${ctx.base}/exports`);
      renderExports(values);
      return values;
    },
  };
}
