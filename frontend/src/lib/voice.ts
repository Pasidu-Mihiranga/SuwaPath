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

let voicesReady: Promise<SpeechSynthesisVoice[]> | null = null;

/** Browsers load voices asynchronously; the first speak() often sees an empty list. */
function loadVoices(): Promise<SpeechSynthesisVoice[]> {
  if (!speechSupported()) return Promise.resolve([]);

  const existing = window.speechSynthesis.getVoices();
  if (existing.length > 0) return Promise.resolve(existing);

  if (!voicesReady) {
    voicesReady = new Promise((resolve) => {
      const finish = () => {
        const voices = window.speechSynthesis.getVoices();
        if (voices.length > 0) {
          window.speechSynthesis.removeEventListener("voiceschanged", onChange);
          resolve(voices);
        }
      };
      const onChange = () => finish();
      window.speechSynthesis.addEventListener("voiceschanged", onChange);
      finish();
      window.setTimeout(finish, 300);
    });
  }
  return voicesReady;
}

function pickVoice(
  voices: SpeechSynthesisVoice[],
  language: string,
): SpeechSynthesisVoice | null {
  const tag = LANG_TAGS[language] ?? LANG_TAGS.en;
  return (
    voices.find((v) => v.lang === tag)
    ?? voices.find((v) => v.lang.startsWith(tag.split("-")[0]))
    ?? null
  );
}

/** Prefer the patient's language, but read Latin-script doctor notes in English. */
export function resolveSpeakLanguage(text: string, preferred?: string): string {
  const language = preferred ?? "en";
  if (language === "en") return "en";
  if (pickVoice(window.speechSynthesis.getVoices(), language)) return language;
  if (/^[\x20-\x7E\s]+$/.test(text.trim())) return "en";
  return language;
}

/**
 * Language tag for speech *recognition* (dictation).
 * si-LK / ta-LK are unreliable in desktop Chrome, so bilingual users get
 * English recognition while keeping their UI language.
 */
export function resolveRecognitionLanguage(preferred?: string): string {
  const language = preferred ?? "en";
  if (language === "si" || language === "ta") return "en";
  return language;
}

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

if (typeof window !== "undefined" && speechSupported()) {
  void loadVoices();
}

export interface Listener {
  stop: () => void;
}

function micErrorMessage(error: DOMException): string {
  if (error.name === "NotAllowedError") {
    return "Microphone access was blocked. Allow it in your browser settings.";
  }
  if (error.name === "NotFoundError") {
    return "No microphone was found on this device.";
  }
  if (error.name === "NotReadableError") {
    return "Your microphone is in use by another app. Close it and try again.";
  }
  return "Could not access your microphone.";
}

function recognitionErrorMessage(code: string): string {
  switch (code) {
    case "not-allowed":
      return "Microphone access was blocked. Allow it in your browser settings.";
    case "network":
      return "Voice input needs an internet connection. Chrome sends audio to Google to transcribe it.";
    case "service-not-allowed":
      return "Voice input only works on secure pages. Open SuwaPath in Chrome or Safari.";
    case "audio-capture":
      return "Could not capture audio. Check that a microphone is connected and allowed.";
    case "language-not-supported":
      return "Your language is not supported for voice input here. Try English or type instead.";
    default:
      return "Speech recognition failed. Use Chrome or Safari with microphone access, or type instead.";
  }
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
  /**
   * Each completed sentence, as it lands, rather than the whole session at
   * the end.
   *
   * `onFinal` is right for dictation: it fires once, when the patient stops,
   * and hands over a transcript they are about to review in an input box. It
   * is useless to a listener that is waiting to hear one particular sentence,
   * because "I can't breathe" would sit in the buffer until the session
   * closed. Both are delivered from the same recognition session so nothing
   * has to run two microphones.
   */
  onUtterance?: (text: string) => void;
  onStart?: () => void;
  onError?: (message: string) => void;
  onEnd?: () => void;
}): Listener | null {
  const Ctor = recognitionCtor();
  if (!Ctor) return null;

  const recognition = new Ctor();
  recognition.lang =
    LANG_TAGS[resolveRecognitionLanguage(options.language)] ?? LANG_TAGS.en;
  recognition.interimResults = true;
  // Keep the session open until the patient taps stop. Chrome still ends
  // sessions after silence, so `onend` restarts while the mic is active.
  recognition.continuous = true;
  recognition.maxAlternatives = 1;

  let sessionText = "";
  let active = true;
  let manualStop = false;
  let started = false;
  let noSpeechCount = 0;
  let stream: MediaStream | null = null;

  const releaseMic = () => {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
  };

  const finish = () => {
    if (!active) return;
    active = false;
    releaseMic();
    const text = sessionText.trim();
    if (text) options.onFinal(text);
    options.onEnd?.();
  };

  recognition.onresult = (event: any) => {
    noSpeechCount = 0;
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const transcript = result[0].transcript;
      if (result.isFinal) {
        sessionText += `${transcript} `;
        const utterance = transcript.trim();
        if (utterance) options.onUtterance?.(utterance);
      } else interim += transcript;
    }
    options.onPartial?.((sessionText + interim).trim());
  };

  recognition.onerror = (event: any) => {
    if (event.error === "aborted") return;
    if (event.error === "no-speech") {
      noSpeechCount += 1;
      if (noSpeechCount >= 2 && !sessionText.trim()) {
        options.onError?.(
          "Didn't catch anything. Speak closer to the mic, or type your message instead.",
        );
      }
      return;
    }
    options.onError?.(recognitionErrorMessage(event.error));
    manualStop = true;
    finish();
  };

  recognition.onend = () => {
    if (manualStop || !active) {
      finish();
      return;
    }
    // Chrome stops after silence even with continuous:true — restart until
    // the patient taps stop.
    try {
      recognition.start();
    } catch {
      finish();
    }
  };

  const startRecognition = () => {
    if (!active || started) return;
    try {
      recognition.start();
      started = true;
      options.onStart?.();
    } catch {
      options.onError?.("Could not start voice input. Try again or type instead.");
      manualStop = true;
      finish();
    }
  };

  if (navigator.mediaDevices?.getUserMedia) {
    void navigator.mediaDevices.getUserMedia({ audio: true })
      .then((mediaStream) => {
        if (!active) {
          mediaStream.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = mediaStream;
        startRecognition();
      })
      .catch((error: DOMException) => {
        options.onError?.(micErrorMessage(error));
        manualStop = true;
        finish();
      });
  } else {
    startRecognition();
  }

  return {
    stop: () => {
      manualStop = true;
      try {
        recognition.stop();
      } catch {
        finish();
      }
    },
  };
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
  return pickVoice(window.speechSynthesis.getVoices(), language);
}

export function speak(markdown: string, options: SpeakOptions | string = {}): void {
  // The original signature was speak(text, language). Existing callers pass a
  // language string, so both are accepted rather than breaking them.
  const opts: SpeakOptions =
    typeof options === "string" ? { language: options } : options;

  if (!speechSupported()) {
    opts.onUnavailable?.();
    return;
  }

  const text = stripMarkdown(markdown);
  if (!text) return;

  const language = resolveSpeakLanguage(text, opts.language ?? "en");

  void loadVoices().then((voices) => {
    const match = pickVoice(voices, language);
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
    utterance.onerror = () => {
      opts.onEnd?.();
      opts.onUnavailable?.();
    };
    // Not every engine emits boundary events. The avatar therefore treats
    // these as a bonus for accuracy, never as its only source of animation.
    utterance.onboundary = () => opts.onBoundary?.();

    window.speechSynthesis.speak(utterance);
  });
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
