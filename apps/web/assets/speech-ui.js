import { chatApi } from "./chat-api.js?v=20260722-flow115";
import { chatState } from "./chat-controller.js?v=20260723-flow131";

function blobBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(String(reader.result || "").split(",").pop() || "");
    reader.readAsDataURL(blob);
  });
}

export class SpeechUI {
  constructor(api = chatApi) {
    this.api = api;
    this.recorder = null;
    this.stream = null;
    this.chunks = [];
    this.startedAt = 0;
    this.timer = null;
    this.audio = null;
    this.capabilityPromise = null;
  }

  init() { window.taroaiSpeech = this; this.capabilities(); }

  capabilities() {
    if (!this.capabilityPromise) {
      this.capabilityPromise = this.api.get("/api/speech/capabilities").catch(() => ({ reason: "Speech service is unavailable" }));
    }
    return this.capabilityPromise;
  }

  async toggleRecording(control) {
    if (this.recorder?.state === "recording") { this.recorder.stop(); return; }
    const capability = await this.capabilities();
    if (!capability.transcription) {
      window.taroaiChat?.network?.(`Voice input unavailable: ${capability.reason || "transcription is not configured"}`, "warning");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      window.taroaiChat?.network?.("Voice recording is not supported by this browser", "warning");
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      this.chunks = [];
      this.recorder = new MediaRecorder(this.stream);
      this.recorder.addEventListener("dataavailable", (event) => { if (event.data.size) this.chunks.push(event.data); });
      this.recorder.addEventListener("stop", () => this.finishRecording(control));
      this.recorder.start(250);
      this.startedAt = Date.now();
      control.classList.add("is-recording");
      this.renderRecorder(control);
      window.taroaiChat?.network?.("Recording voice…", "active");
    } catch (error) { window.taroaiChat?.network?.(`Microphone unavailable: ${error.message}`, "error"); }
  }

  renderRecorder(control) {
    const host = document.querySelector("[data-upload-list]");
    if (!host) return;
    let tray = host.querySelector("[data-speech-recorder]");
    if (!tray) {
      tray = document.createElement("div"); tray.className = "speech-recorder"; tray.dataset.speechRecorder = "true";
      tray.innerHTML = `<span class="speech-wave"><i></i><i></i><i></i><i></i><i></i></span><strong>Recording</strong><time>00:00</time><button type="button">Stop</button>`;
      tray.querySelector("button").addEventListener("click", () => { if (this.recorder?.state === "recording") this.recorder.stop(); });
      host.append(tray);
    }
    clearInterval(this.timer);
    this.timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - this.startedAt) / 1000);
      tray.querySelector("time").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    }, 500);
  }

  async finishRecording(control) {
    clearInterval(this.timer);
    control?.classList.remove("is-recording");
    this.stream?.getTracks().forEach((track) => track.stop());
    document.querySelector("[data-speech-recorder]")?.remove();
    const blob = new Blob(this.chunks, { type: this.recorder?.mimeType || "audio/webm" });
    if (!blob.size) return;
    window.taroaiChat?.network?.("Transcribing voice…", "loading");
    try {
      const audio_base64 = await blobBase64(blob);
      const result = await this.api.post("/api/speech/transcribe", { audio_base64, content_type: blob.type }, { scope: "speech-transcription" });
      const input = document.querySelector("#composer-input");
      const transcript = result.text || result.transcript || "";
      if (input && transcript) { input.value = `${input.value}${input.value ? " " : ""}${transcript}`; input.dispatchEvent(new Event("input", { bubbles: true })); input.focus(); }
      window.taroaiChat?.network?.("Transcript ready to edit", "success");
    } catch (error) { window.taroaiChat?.network?.(`Transcription failed: ${error.message}`, "error"); }
  }

  async summarizeMessage(message) {
    const capability = await this.capabilities();
    if (!capability.summarization) {
      window.taroaiChat?.network?.(`Summary unavailable: ${capability.reason || "summarization is not configured"}`, "warning");
      return;
    }
    try {
      const result = await this.api.post("/api/speech/summarize", { text: message.content || message.text }, { scope: "message-summarize" });
      window.taroaiChat?.renderInlineNotice?.("Summary", result.summary || result.text || "Summary created.", "success");
    } catch (error) { window.taroaiChat?.network?.(`Summary failed: ${error.message}`, "error"); }
  }

  async toggleReadAloud(message, control) {
    if (this.audio && !this.audio.paused) { this.audio.pause(); this.audio.currentTime = 0; control.textContent = "Read aloud"; return; }
    if (window.speechSynthesis?.speaking) { window.speechSynthesis.cancel(); control.textContent = "Read aloud"; return; }
    control.textContent = "Preparing audio…";
    const capability = await this.capabilities();
    if (!capability.text_to_speech) return this.speakInBrowser(message, control);
    try {
      const result = await this.api.post("/api/speech/synthesize", { text: message.content || message.text }, { scope: "speech-synthesis" });
      const source = result.audio_url || result.url || (result.audio_base64 ? `data:${result.content_type || "audio/mpeg"};base64,${result.audio_base64}` : null);
      if (!source) throw new Error("Speech service returned no audio");
      this.audio = new Audio(source); control.textContent = "Stop audio";
      this.audio.addEventListener("ended", () => { control.textContent = "Read aloud"; });
      await this.audio.play();
    } catch { this.speakInBrowser(message, control); }
  }

  speakInBrowser(message, control) {
    if (!window.speechSynthesis) { control.textContent = "Read aloud"; return; }
    const utterance = new SpeechSynthesisUtterance(message.content || message.text || "");
    utterance.onend = () => { control.textContent = "Read aloud"; };
    control.textContent = "Stop audio";
    window.speechSynthesis.speak(utterance);
  }
}

let singleton;
export function createSpeechUI() { if (!singleton) { singleton = new SpeechUI(); singleton.init(); } return singleton; }
