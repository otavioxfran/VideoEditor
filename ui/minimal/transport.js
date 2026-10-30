function iconName(button, name) {
  button?.querySelector("use")?.setAttribute("href", `/assets/icons.svg#ph-${name}`);
}

function bind() {
  const video = document.getElementById("video");
  const play = document.getElementById("play-toggle");
  if (!video || !play) return;
  const sync = () => iconName(play, video.paused ? "play" : "pause");
  play.addEventListener("click", async () => {
    if (video.paused) {
      try {
        await video.play();
      } catch {
        return;
      }
    } else {
      video.pause();
    }
    sync();
  });
  video.addEventListener("play", sync);
  video.addEventListener("pause", sync);
  document.getElementById("back-2")?.addEventListener("click", () => {
    video.currentTime = Math.max(0, video.currentTime - 2);
  });
  document.getElementById("forward-2")?.addEventListener("click", () => {
    video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 2);
  });
  // The native controls are off (one transport only), so the frame itself toggles play.
  video.addEventListener("click", () => play.click());

  const mute = document.getElementById("mute-toggle");
  const syncMute = () => iconName(mute, video.muted ? "speaker-slash" : "speaker-high");
  mute?.addEventListener("click", () => {
    video.muted = !video.muted;
  });
  video.addEventListener("volumechange", syncMute);

  const frame = video.closest(".viewer-frame") || video;
  document.getElementById("fullscreen")?.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else frame.requestFullscreen?.();
  });
  sync();
  syncMute();
}

bind();
