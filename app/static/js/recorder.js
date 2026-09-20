// Microphone capture shared by every spoken activity (monologue, picture
// description, «60 секунд», shadowing): MediaRecorder + a level meter.
//   Recorder.fillMics(select)          - list the microphones in a <select>
//   Recorder.create({ onTick, onStop, maxSeconds })
//     .start(deviceId)  - ask for the mic and start; throws if access fails
//     .stop()           - finish; onStop({ blob, durationSeconds }) follows
//     .dispose()        - stop everything without calling onStop
// Duration is measured here, in the browser, on purpose: the server never
// decodes audio.
const Recorder = (() => {
  function pickMimeType() {
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"];
    for (const type of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) return type;
    }
    return "";
  }

  async function fillMics(select) {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const mics = devices.filter((d) => d.kind === "audioinput");
      const current = select.value;
      select.innerHTML = mics.length
        ? mics
            .map((d, i) => `<option value="${escapeHtml(d.deviceId)}">${escapeHtml(d.label || "Микрофон " + (i + 1))}</option>`)
            .join("")
        : `<option value="">Микрофон не найден</option>`;
      if (current && mics.some((d) => d.deviceId === current)) select.value = current;
    } catch (err) {
      select.innerHTML = `<option value="">(нет доступа к устройствам)</option>`;
    }
  }

  // onTick(elapsedSeconds, level 0..1) runs every animation frame while
  // recording; with maxSeconds the recording stops by itself at that point.
  function create({ onTick, onStop, maxSeconds } = {}) {
    let stream = null;
    let mediaRecorder = null;
    let audioCtx = null;
    let analyser = null;
    let rafId = null;
    let startTime = 0;
    let chunks = [];
    let disposed = false;

    function level() {
      if (!analyser) return 0;
      const data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128) / 128);
      return peak;
    }

    function tick() {
      const elapsed = (Date.now() - startTime) / 1000;
      if (onTick) onTick(elapsed, level());
      if (maxSeconds && elapsed >= maxSeconds) {
        stop();
        return;
      }
      rafId = requestAnimationFrame(tick);
    }

    function release() {
      cancelAnimationFrame(rafId);
      if (stream) stream.getTracks().forEach((t) => t.stop());
      stream = null;
      if (audioCtx) {
        audioCtx.close().catch(() => {});
        audioCtx = null;
        analyser = null;
      }
    }

    async function start(deviceId) {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      });
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      audioCtx.createMediaStreamSource(stream).connect(analyser);

      chunks = [];
      const mimeType = pickMimeType();
      mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaRecorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      });
      mediaRecorder.addEventListener("stop", () => {
        const durationSeconds = Math.min((Date.now() - startTime) / 1000, maxSeconds || Infinity);
        release();
        if (disposed) return;
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
        if (onStop) onStop({ blob, durationSeconds });
      });
      mediaRecorder.start();
      startTime = Date.now();
      rafId = requestAnimationFrame(tick);
    }

    function stop() {
      cancelAnimationFrame(rafId);
      if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
    }

    function dispose() {
      disposed = true;
      stop();
      release();
    }

    return {
      start,
      stop,
      dispose,
      get recording() {
        return !!mediaRecorder && mediaRecorder.state === "recording";
      },
    };
  }

  return { create, fillMics };
})();
