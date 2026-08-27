/**
 * Voice input and output via the browser's own Web Speech API.
 *
 * Chosen because it costs nothing, needs no key, and works offline on some
 * platforms — which matters for a product that must run on a free tier. The
 * trade-off is real and worth stating: recognition quality for Sinhala and
 * Tamil is noticeably worse than for English, and Firefox does not implement
 * recognition at all.
 *
 * So this degrades rather than fails. `speechSupported()` and
 * `listeningSupported()` are checked before any voice affordance is rendered;
 * where unsupported, the UI simply does not offer the button. A microphone
 * icon that does nothing is worse than no microphone icon.
 *
 * **Nothing here sends audio to SuwaPath.** Recognition happens in the
 * browser (on some platforms via the vendor's own service — Chrome uses
 * Google's), and only the resulting text enters the normal chat pipeline,
 * where it passes the same guardrails and PHI boundary as typed text. For a
 * medical product that distinction matters: we never hold a voice recording,
 * so there is no voiceprint to leak.
 *
 * A LiveKit-backed path would replace `listen()` only. The rest of the
 * application consumes text and does not care where it came from.
 */

const LANG_TAGS: Record<string, string> = {
  en: "en-US",
  si: "si-LK",
  ta: "ta-LK",
};

type SpeechRecognitionCtor = new () => any;

function recognitionCtor(): SpeechRecognitionCtor | null {
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function listeningSupported(): boolean {
  return recognitionCtor() !== null;
}

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export interface Listener {
  stop: () => void;
}

/**
 * Start dictation. Returns a handle, or null if unsupported.
 *
 * Interim results are surfaced so the input box fills in as the patient
 * speaks — without that, a long sentence looks like the app has frozen.
 */
export function listen(options: {
  language?: string;
  onPartial?: (text: string) => void;
  onFinal: (text: string) => void;
  onError?: (message: string) => void;
  onEnd?: () => void;
}): Listener | null {
  const Ctor = recognitionCtor();
  if (!Ctor) return null;

  const recognition = new Ctor();
  recognition.lang = LANG_TAGS[options.language ?? "en"] ?? LANG_TAGS.en;
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;

  let finalText = "";

  recognition.onresult = (event: any) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      if (result.isFinal) finalText += result[0].transcript;
      else interim += result[0].transcript;
    }
    options.onPartial?.((finalText + interim).trim());
  };

  recognition.onerror = (event: any) => {
    // `no-speech` and `aborted` are ordinary outcomes of someone changing
    // their mind, not failures worth showing an error for.
    if (event.error === "no-speech" || event.error === "aborted") return;
    options.onError?.(
      event.error === "not-allowed"
        ? "Microphone access was blocked. Allow it in your browser settings."
        : "Speech recognition failed. You can type instead.",
    );
  };

  recognition.onend = () => {
    const text = finalText.trim();
    if (text) options.onFinal(text);
    options.onEnd?.();
  };

  try {
    recognition.start();
  } catch {
    return null;
  }

  return { stop: () => recognition.stop() };
}

/**
 * Read an assistant reply aloud.
 *
 * Markdown is stripped first — a synthesiser reading "asterisk asterisk What
 * I'd do asterisk asterisk" is unusable, and the structural markers carry no
 * meaning out loud.
 */
export interface SpeakOptions {
  language?: string;
  /** Fired at each word boundary — drives the avatar's mouth. */
  onBoundary?: () => void;
  onStart?: () => void;
  onEnd?: () => void;
  /**
   * Called instead of speaking when no voice for `language` is installed.
   * Sinhala and Tamil voices are genuinely rare, and reading Sinhala aloud
   * with an English voice produces confident nonsense — worse than silence
   * for someone relying on it because they cannot read the screen.
   */
  onUnavailable?: () => void;
}

/** Is there a voice installed that can actually pronounce this language? */
export function voiceFor(language: string): SpeechSynthesisVoice | null {
  if (!speechSupported()) return null;
  const tag = LANG_TAGS[language] ?? LANG_TAGS.en;
  const voices = window.speechSynthesis.getVoices();
  return (
    voices.find((v) => v.lang === tag)
    ?? voices.find((v) => v.lang.startsWith(tag.split("-")[0]))
    ?? null
  );
}

export function speak(markdown: string, options: SpeakOptions | string = {}): void {
  // The original signature was speak(text, language). Existing callers pass a
  // language string, so both are accepted rather than breaking them.
  const opts: SpeakOptions =
    typeof options === "string" ? { language: options } : options;
  const language = opts.language ?? "en";

  if (!speechSupported()) {
    opts.onUnavailable?.();
    return;
  }

  const text = stripMarkdown(markdown);
  if (!text) return;

  const match = voiceFor(language);
  if (!match && language !== "en") {
    opts.onUnavailable?.();
    return;
  }

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = LANG_TAGS[language] ?? LANG_TAGS.en;
  utterance.rate = 0.97;
  if (match) utterance.voice = match;

  utterance.onstart = () => opts.onStart?.();
  utterance.onend = () => opts.onEnd?.();
  // Not every engine emits boundary events. The avatar therefore treats
  // these as a bonus for accuracy, never as its only source of animation.
  utterance.onboundary = () => opts.onBoundary?.();

  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking(): void {
  if (speechSupported()) window.speechSynthesis.cancel();
}

/** Reduce our markdown subset to something worth hearing. */
export function stripMarkdown(markdown: string): string {
  return (markdown || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/_([^_\n]+)_/g, "$1")
    .replace(/^\s*#{1,6}\s*/gm, "")
    // List markers become a pause rather than being read as punctuation.
    .replace(/^\s*[-*•]\s+/gm, ". ")
    .replace(/^\s*\d+[.)]\s+/gm, ". ")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/\s*\n\s*/g, ". ")
    .replace(/\.{2,}/g, ".")
    .replace(/\s{2,}/g, " ")
    .trim();
}
