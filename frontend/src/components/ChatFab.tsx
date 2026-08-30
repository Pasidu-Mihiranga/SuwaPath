/**
 * Floating chat assistant shortcut.
 *
 * A fixed FAB in the bottom-right corner that opens a compact chat popup,
 * giving patients quick access to the assistant without navigating away from
 * whatever page they are on. Hidden on the Assistant page itself (it would
 * be redundant).
 *
 * Features integrated:
 * - Streaming assistant response
 * - Chat vs. Private mode toggle pill + 6-digit PIN prompt
 * - Attachment picker (+) for report & photo uploads
 * - Voice dictation (mic) for dictating messages
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import Markdown from "./Markdown";
import Icon, { type IconName } from "./Icon";
import { Card } from "./ui";
import { API_BASE, api, errorMessage, tokens } from "../lib/api";
import { useAuth } from "../lib/auth";
import * as voice from "../lib/voice";

/* ------------------------------------------------------------------ */

interface Turn {
  role: "user" | "assistant";
  content: string;
}

const QUICK_PROMPTS = [
  "I have a headache",
  "Explain my latest report",
  "Find a doctor nearby",
];

type AttachMode = "photo" | "camera" | "file";

function acceptFor(mode: AttachMode): string {
  if (mode === "photo" || mode === "camera") return "image/jpeg,image/png";
  return "application/pdf,image/jpeg,image/png";
}

const ATTACH_OPTIONS: {
  mode: AttachMode;
  icon: IconName;
  label: string;
  hint: string;
  mobileOnly?: boolean;
}[] = [
  {
    mode: "camera",
    icon: "camera",
    label: "Take a photo",
    hint: "Photograph a report or a rash",
    mobileOnly: true,
  },
  { mode: "photo", icon: "image", label: "Photo library", hint: "A scan or a photo you already have" },
  { mode: "file", icon: "description", label: "Upload a file", hint: "PDF lab report, or an image" },
];

function AttachMenu({
  disabled,
  disabledReason,
  onPick,
}: {
  disabled: boolean;
  disabledReason?: string;
  onPick: (mode: AttachMode) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    function onEsc(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const isTouch =
    typeof window !== "undefined" &&
    window.matchMedia?.("(pointer: coarse)").matches;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-label={disabledReason ?? "Add an attachment"}
        aria-expanded={open}
        title={disabledReason ?? "Add an attachment"}
        className="grid h-9 w-9 place-items-center rounded-full border border-line bg-surface text-ink-600 transition hover:border-brand-400 hover:text-brand-700 disabled:opacity-40"
      >
        <Icon name="add" size={17} />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute bottom-11 left-0 z-50 w-56 overflow-hidden rounded-2xl border border-line bg-surface shadow-xl"
        >
          {ATTACH_OPTIONS.filter((o) => !o.mobileOnly || isTouch).map((option) => (
            <button
              key={option.mode}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onPick(option.mode);
              }}
              className="flex w-full items-start gap-2.5 px-3 py-2 text-left transition hover:bg-canvas"
            >
              <span className="sp-icon-tile mt-0.5 shrink-0 bg-brand-50 text-brand-700 !h-7 !w-7">
                <Icon name={option.icon} size={15} />
              </span>
              <span className="min-w-0">
                <span className="block text-xs font-medium text-ink-900">
                  {option.label}
                </span>
                <span className="block text-[10px] text-ink-500">{option.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function PinDialog({
  pin,
  setPin,
  onCancel,
  onConfirm,
}: {
  pin: string;
  setPin: (v: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-ink-900/50 p-4">
      <Card className="w-full max-w-xs p-4">
        <div className="flex items-start gap-3">
          <span className="sp-icon-tile bg-programme-surface text-programme-text shrink-0">
            <Icon name="lock" size={18} />
          </span>
          <div className="min-w-0">
            <h2 className="font-semibold text-ink-900 text-sm">Start a private chat</h2>
            <p className="mt-1 text-xs text-ink-600">
              Nothing is saved to history. Enter your 6-digit private PIN.
            </p>
          </div>
        </div>

        <input
          className="sp-input mt-3 text-center text-base tracking-[0.3em] !h-9"
          inputMode="numeric"
          autoComplete="off"
          maxLength={6}
          placeholder="••••••"
          value={pin}
          onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
        />

        <div className="mt-3 flex gap-2">
          <button
            onClick={onCancel}
            className="sp-btn sp-btn-ghost flex-1 justify-center !py-1.5 text-xs"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="sp-btn sp-btn-primary flex-1 justify-center !py-1.5 text-xs"
          >
            Start
          </button>
        </div>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */

export default function ChatFab() {
  const { user } = useAuth();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Private mode states
  const [isPrivate, setIsPrivate] = useState(false);
  const [pinPrompt, setPinPrompt] = useState(false);
  const [pin, setPin] = useState("");

  // Voice & Upload states
  const [uploading, setUploading] = useState(false);
  const [listening, setListening] = useState(false);
  const [startingMic, setStartingMic] = useState(false);
  const [attachMode, setAttachMode] = useState<AttachMode>("file");

  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const listenerRef = useRef<voice.Listener | null>(null);

  const canListen = voice.listeningSupported();

  // Only show for patients and not on the assistant page.
  const isPatient = user?.role === "patient";
  const isAssistantPage = location.pathname.includes("/assistant");
  const visible = isPatient && !isAssistantPage;

  // Auto-scroll to bottom when new messages arrive.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  // Listen for toggle-chat-fab event from bottom nav bar.
  useEffect(() => {
    function handleToggle() {
      setOpen((prev) => !prev);
    }
    window.addEventListener("toggle-chat-fab", handleToggle);
    return () => window.removeEventListener("toggle-chat-fab", handleToggle);
  }, []);

  // Focus the input when the popup opens.
  useEffect(() => {
    if (open) {
      window.setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  useEffect(() => () => {
    listenerRef.current?.stop();
    voice.stopSpeaking();
  }, []);

  function reset(privateMode: boolean) {
    setTurns([]);
    setSessionId(null);
    setError(null);
    setIsPrivate(privateMode);
  }

  async function startPrivate() {
    if (!/^\d{6}$/.test(pin)) {
      setError("Please choose a 6-digit PIN.");
      return;
    }
    try {
      const { data } = await api.post("/agent/sessions", { private: true, pin });
      reset(true);
      setSessionId(data.id);
      setPinPrompt(false);
      setPin("");
    } catch (err) {
      setError(errorMessage(err, "Could not start a private chat."));
    }
  }

  function toggleListening() {
    if (listening) {
      listenerRef.current?.stop();
      setListening(false);
      listenerRef.current = null;
      return;
    }
    setError(null);
    setStartingMic(true);
    const handle = voice.listen({
      language: user?.preferred_language ?? "en",
      onStart: () => {
        setStartingMic(false);
        setListening(true);
      },
      onPartial: setInput,
      onFinal: setInput,
      onError: (message) => {
        setError(message);
        setListening(false);
        setStartingMic(false);
        listenerRef.current = null;
      },
      onEnd: () => {
        setListening(false);
        setStartingMic(false);
        listenerRef.current = null;
      },
    });
    if (!handle) {
      setStartingMic(false);
      setError("Voice input not supported in your browser.");
      return;
    }
    listenerRef.current = handle;
  }

  async function uploadFile(file: File) {
    if (isPrivate) {
      setError("Uploads are disabled in private chat.");
      return;
    }

    const isImage = file.type.startsWith("image/");
    const endpoint = isImage ? "/images" : "/documents";
    const form = new FormData();
    form.append("file", file);

    setUploading(true);
    setError(null);
    setTurns((prev) => [
      ...prev,
      { role: "user", content: `Uploaded **${file.name}**` },
    ]);

    try {
      const { data } = await api.post(endpoint, form);
      const summary =
        data.explanation ??
        data.summary ??
        data.analysis?.explanation ??
        "Saved. Ask me anything about it.";

      setTurns((prev) => [
        ...prev,
        { role: "assistant", content: summary },
      ]);
    } catch (err) {
      setError(errorMessage(err, "File could not be processed."));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const send = useCallback(
    async (message: string) => {
      const text = message.trim();
      if (!text || busy) return;

      setBusy(true);
      setError(null);
      setInput("");
      setTurns((prev) => [...prev, { role: "user", content: text }]);

      try {
        const response = await fetch(`${API_BASE}/api/v1/agent/stream`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${tokens.access}`,
          },
          body: JSON.stringify({ message: text, session_id: sessionId }),
        });

        if (!response.ok || !response.body) {
          throw new Error(`Request failed (${response.status})`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";

          for (const frame of frames) {
            const line = frame
              .split("\n")
              .find((l) => l.startsWith("data: "));
            if (!line) continue;
            const event = JSON.parse(line.slice(6));

            if (event.type === "session") {
              setSessionId(event.session_id);
            }
            if (event.type === "final") {
              setTurns((prev) => [
                ...prev,
                { role: "assistant", content: event.answer },
              ]);
            }
            if (event.type === "error") {
              setError("The assistant could not complete that request.");
            }
          }
        }
      } catch {
        setError("Could not reach the assistant.");
      } finally {
        setBusy(false);
      }
    },
    [busy, sessionId],
  );

  if (!visible) return null;

  return (
    <>
      {/* ── Backdrop ── */}
      {open && (
        <div
          className="fixed inset-0 z-[50] bg-ink-900/10 backdrop-blur-[1px] transition-opacity"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* ── Chat popup ── */}
      {open && (
        <div
          className="
            fixed z-[60] inset-0 w-full h-full rounded-none border-0 bg-surface
            sm:inset-auto sm:bottom-20 sm:right-4 sm:w-[24rem] sm:max-w-[calc(100vw-2rem)] sm:h-[min(34rem,calc(100vh-7rem))] sm:rounded-2xl sm:border sm:border-line
            lg:bottom-24 lg:right-8
            flex flex-col
            shadow-2xl ring-1 ring-ink-900/5
            animate-[fabSlideUp_200ms_ease-out]
            overflow-hidden
          "
        >
          {/* Header */}
          <div className="flex items-center gap-2 border-b border-line bg-brand-600 px-4 py-3 shrink-0">
            <span className="grid h-8 w-8 place-items-center rounded-full bg-white/20">
              <Icon name="ai" size={18} className="text-white" />
            </span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-white truncate">
                SuwaPath Assistant
              </p>
              <p className="text-[11px] text-white/75">Ask anything</p>
            </div>
            {isPrivate && (
              <span className="flex items-center gap-1 rounded-full bg-white/20 px-2 py-0.5 text-[11px] font-medium text-white">
                <Icon name="lock" size={11} />
                Private
              </span>
            )}
            <Link
              to={sessionId ? `/patient/assistant?session=${sessionId}` : "/patient/assistant"}
              title="Open full assistant page"
              className="grid h-8 w-8 place-items-center rounded-full text-white/80 transition hover:bg-white/20 hover:text-white"
              onClick={() => setOpen(false)}
            >
              <Icon name="arrowRight" size={16} />
            </Link>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close chat"
              className="grid h-8 w-8 place-items-center rounded-full text-white/80 transition hover:bg-white/20 hover:text-white"
            >
              <Icon name="close" size={18} />
            </button>
          </div>

          {/* Private Mode Notice */}
          {isPrivate && (
            <div className="flex items-center gap-2 border-b border-programme-border bg-programme-surface px-3 py-1.5 shrink-0">
              <Icon name="lock" size={13} className="text-programme-text shrink-0" />
              <p className="text-[11px] text-programme-text truncate">
                Private chat mode active — nothing saved to history.
              </p>
            </div>
          )}

          {/* Messages */}
          <div className="flex-1 min-h-0 overflow-y-auto bg-canvas px-3.5 py-3.5">
            {turns.length === 0 && !busy && !uploading && (
              <div className="text-center py-4">
                <span className="inline-grid h-10 w-10 place-items-center rounded-full bg-brand-50 text-brand-600">
                  <Icon name="ai" size={22} />
                </span>
                <p className="mt-2 text-sm font-medium text-ink-800">
                  How can I help you?
                </p>
                <p className="mt-0.5 text-xs text-ink-500">
                  Describe a symptom, upload a report, or ask a question
                </p>
                <div className="mt-3.5 flex flex-col gap-1.5">
                  {QUICK_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      onClick={() => void send(prompt)}
                      className="rounded-lg border border-ink-150 bg-surface px-3 py-1.5 text-left text-xs text-ink-700 transition hover:border-brand-300 hover:bg-brand-50"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.map((turn, i) => (
              <div
                key={i}
                className={`mb-3 flex ${turn.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-3 py-2 text-xs sm:text-sm leading-relaxed ${
                    turn.role === "user"
                      ? "rounded-br-md bg-brand-600 text-white"
                      : "rounded-bl-md border border-line bg-surface text-ink-800"
                  }`}
                >
                  {turn.role === "assistant" ? (
                    <Markdown content={turn.content} />
                  ) : (
                    turn.content
                  )}
                </div>
              </div>
            ))}

            {(busy || uploading) && (
              <div className="flex justify-start mb-3">
                <div className="rounded-2xl rounded-bl-md border border-line bg-surface px-3.5 py-2.5">
                  <span className="flex gap-1 items-center">
                    {uploading && <span className="text-xs text-ink-500 mr-1">Processing report…</span>}
                    {[0, 1, 2].map((dot) => (
                      <span
                        key={dot}
                        className="h-2 w-2 animate-bounce rounded-full bg-ink-300"
                        style={{ animationDelay: `${dot * 120}ms` }}
                      />
                    ))}
                  </span>
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Error Note */}
          {error && (
            <div className="border-t border-danger-border bg-danger-surface px-3 py-1.5 shrink-0">
              <p className="text-xs text-danger-text">{error}</p>
            </div>
          )}

          {/* Composer Controls & Form */}
          <div className="border-t border-line bg-surface px-3 py-2.5 shrink-0">
            {/* Chat vs Private Toggle Pill */}
            <div className="mb-2 flex items-center justify-between">
              <div className="inline-flex rounded-full border border-line bg-canvas p-0.5">
                {([false, true] as const).map((mode) => (
                  <button
                    key={String(mode)}
                    type="button"
                    aria-pressed={isPrivate === mode}
                    onClick={() => {
                      if (isPrivate === mode) return;
                      if (mode) setPinPrompt(true);
                      else reset(false);
                    }}
                    className={`flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium transition ${
                      isPrivate === mode
                        ? mode
                          ? "bg-programme-surface text-programme-text shadow-xs"
                          : "bg-surface text-ink-900 shadow-xs"
                        : "text-ink-500 hover:text-ink-800"
                    }`}
                  >
                    {mode && <Icon name="lock" size={11} />}
                    {mode ? "Private" : "Chat"}
                  </button>
                ))}
              </div>
            </div>

            {/* Input Form with +, mic, input, send */}
            <form
              className="flex items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                void send(input);
              }}
            >
              <input
                ref={fileRef}
                type="file"
                accept={acceptFor(attachMode)}
                capture={attachMode === "camera" ? "environment" : undefined}
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void uploadFile(file);
                }}
              />
              <AttachMenu
                disabled={busy || uploading || isPrivate}
                disabledReason={isPrivate ? "Uploads disabled in private chat" : undefined}
                onPick={(mode) => {
                  setAttachMode(mode);
                  window.setTimeout(() => fileRef.current?.click(), 0);
                }}
              />

              {canListen && (
                <button
                  type="button"
                  onClick={toggleListening}
                  disabled={busy || uploading || startingMic}
                  aria-label={
                    listening
                      ? "Stop dictating"
                      : startingMic
                        ? "Starting microphone"
                        : "Dictate a message"
                  }
                  title={
                    listening
                      ? "Stop dictating"
                      : startingMic
                        ? "Starting microphone…"
                        : "Dictate a message"
                  }
                  className={`grid h-9 w-9 shrink-0 place-items-center rounded-full border transition ${
                    listening || startingMic
                      ? "animate-pulse border-danger-border bg-danger-surface text-danger-text"
                      : "border-line bg-surface text-ink-600 hover:border-brand-400 hover:text-brand-700"
                  }`}
                >
                  <Icon name="mic" size={16} />
                </button>
              )}

              <input
                ref={inputRef}
                className="sp-input flex-1 min-w-0 !h-9 !rounded-full !text-xs sm:!text-sm"
                placeholder={
                  startingMic
                    ? "Starting microphone…"
                    : listening
                      ? "Speak now — tap mic to stop"
                      : isPrivate
                        ? "Private chat mode…"
                        : "Describe a symptom or report…"
                }
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={busy || uploading}
              />

              <button
                aria-label="Send"
                disabled={busy || uploading || !input.trim()}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-600 text-white transition hover:bg-brand-700 disabled:opacity-40"
              >
                <Icon name="send" size={15} />
              </button>
            </form>
          </div>

          {/* Footer link */}
          <div className="border-t border-line bg-surface px-3 py-1.5 text-center shrink-0">
            <Link
              to={sessionId ? `/patient/assistant?session=${sessionId}` : "/patient/assistant"}
              onClick={() => setOpen(false)}
              className="text-xs font-medium text-brand-600 hover:underline inline-flex items-center gap-1"
            >
              Open full assistant page
              <Icon name="arrowRight" size={13} />
            </Link>
          </div>
        </div>
      )}

      {/* ── PIN Dialog for Private Chat ── */}
      {pinPrompt && (
        <PinDialog
          pin={pin}
          setPin={setPin}
          onCancel={() => {
            setPinPrompt(false);
            setPin("");
          }}
          onConfirm={() => void startPrivate()}
        />
      )}

      {/* ── Desktop FAB button ── */}
      <button
        onClick={() => setOpen(!open)}
        aria-label={open ? "Close assistant" : "Open assistant"}
        className={`
          hidden lg:grid fixed z-[60] h-14 w-14 place-items-center rounded-full shadow-xl
          transition-all duration-200 hover:scale-105 active:scale-95
          ${open
            ? "bg-ink-800 text-white bottom-6 right-8"
            : "bg-brand-600 text-white bottom-8 right-8"
          }
        `}
        title="Chat with SuwaPath Assistant"
      >
        <Icon name={open ? "close" : "chat"} size={22} />
        {!open && turns.length > 0 && (
          <span className="absolute -top-0.5 -right-0.5 h-3.5 w-3.5 rounded-full bg-danger-text border-2 border-white" />
        )}
      </button>
    </>
  );
}
