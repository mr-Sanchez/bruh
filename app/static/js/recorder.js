// Microphone capture shared by every spoken activity («Говорение», picture
// description, a lesson task, shadowing): MediaRecorder + a level meter.
//   Recorder.fillMics(select)          - list the microphones in a <select>
//   Recorder.create({ onTick, onStop, maxSeconds })
//     .start(deviceId)  - ask for the mic and start; throws if access fails
//     .stop()           - finish; onStop({ blob, durationSeconds }) follows
//     .dispose()        - stop everything without calling onStop
//   Recorder.findSilences(blob)        - quiet spans of a recording, for pauses
// Duration and silences are measured here, in the browser, on purpose: the
// server never decodes audio.
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

  // Quiet spans of a recording as [[start, end], ...] seconds, or null when
  // the audio cannot be decoded or has too little contrast to tell speech from
  // silence. Loudness is taken per 20 ms frame; the line between quiet and
  // speech sits 30% of the way from the room's noise (10th percentile) to
  // speech (95th), so it adapts to the mic. A blip under 0.2 s (a click, a
  // breath) does not break a silence; spans under 0.3 s are left out.
  async function findSilences(blob) {
    const FRAME = 0.02;
    try {
      const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      const audio = await new Offline(1, 1, 16000).decodeAudioData(await blob.arrayBuffer());
      const samples = audio.getChannelData(0);
      const hop = Math.round(audio.sampleRate * FRAME);
      const frames = Math.floor(samples.length / hop);
      if (frames < 50) return null;
      const db = new Float32Array(frames);
      for (let f = 0; f < frames; f++) {
        let sum = 0;
        for (let i = f * hop; i < (f + 1) * hop; i++) sum += samples[i] * samples[i];
        db[f] = 10 * Math.log10(sum / hop + 1e-10);
      }
      const sorted = Float32Array.from(db).sort();
      const floor = sorted[Math.floor(frames * 0.1)];
      const speech = sorted[Math.floor(frames * 0.95)];
      if (speech - floor < 12) return null;
      const line = floor + 0.3 * (speech - floor);
      const spans = [];
      for (let f = 0; f < frames; ) {
        if (db[f] >= line) {
          f++;
          continue;
        }
        const start = f;
        while (f < frames && db[f] < line) f++;
        const last = spans[spans.length - 1];
        if (last && start * FRAME - last[1] <= 0.2) last[1] = f * FRAME;
        else spans.push([start * FRAME, f * FRAME]);
      }
      return spans
        .filter(([a, b]) => b - a >= 0.3)
        .slice(0, 400)
        .map(([a, b]) => [Math.round(a * 100) / 100, Math.round(b * 100) / 100]);
    } catch (err) {
      return null;
    }
  }

  return { create, fillMics, findSilences };
})();
