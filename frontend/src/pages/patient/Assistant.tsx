/**
 * SuwaPath Assistant — the centre of the product.
 *
 * Everything a patient does starts here: describing a symptom, understanding a
 * report, finding a doctor, asking a general question. The separate symptom
 * checker and confidential advisor were three half-conversations that could
 * not see each other; this is one conversation that can do all three.
 *
 * Three things are surfaced that a chat UI usually hides, because in a medical
 * product they are part of the answer:
 *
 * - **How it was produced.** Cache hit, which model, how long it took. If an
 *   answer came from a reviewed cached response rather than a model, the
 *   patient can see that.
 * - **What it did.** The chain-of-thought chips are real SSE node events, not
 *   an animation — including consent denials, shown as a boundary rather than
 *   an error.
 * - **Whether it is private.** Private mode is visibly different, not a
 *   setting buried in a menu.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "../../components/Markdown";
import ProviderDeck, { type ProviderCard } from "../../components/ProviderDeck";
import {
  Card,
  Chip,
  ErrorNote,
  Icon,
  UrgencyBadge,
  relativeDay,
  type IconName,
} from "../../components/ui";
import { API_BASE, api, errorMessage, tokens } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import * as voice from "../../lib/voice";

interface Turn {
  role: "user" | "assistant";
  content: string;
  routes?: string[];
  guard?: Record<string, any>;
  structured?: Record<string, any>;
  citations?: any[];
  consult?: Record<string, any>;
  cache?: { hit?: boolean; key?: string; score?: number };
  latencyMs?: number;
  provider?: string;
}

interface SessionSummary {
  id: string;
  title: string;
  is_private: boolean;
  message_count: number;
  last_message_at: string | null;
}

interface TraceItem {
  id: string;
  label: string;
  detail?: string;
  status: "ok" | "denied" | "error";
  ms?: number;
}

const NODE_LABEL: Record<string, { label: string; icon: IconName }> = {
  guard_input: { label: "Safety check", icon: "shield" },
  cache_lookup: { label: "Checking known answers", icon: "bolt" },
  route: { label: "Understanding your question", icon: "ai" },
  consult_agent: { label: "Clinical reasoning", icon: "stethoscope" },
  admin_agent: { label: "Appointments", icon: "calendar" },
  records_agent: { label: "Your records", icon: "description" },
  knowledge_agent: { label: "Health knowledge", icon: "lab" },
  web_agent: { label: "Current sources", icon: "search" },
  direct_agent: { label: "Assistant", icon: "chat" },
  merge: { label: "Combining answers", icon: "link" },
  fulfil: { label: "Finding who can help", icon: "hospital" },
  judge: { label: "Answer safety review", icon: "verified" },
};

const ROUTE_LABEL: Record<string, string> = {
  consult: "Clinical reasoning",
  admin: "Appointments",
  records: "Your records",
  knowledge: "Health knowledge",
  web: "Current sources",
  direct: "General",
};

const TOOL_LABEL: Record<string, string> = {
  appointments: "Read appointments",
  find_care: "Match providers",
  records: "Read reports",
  medications: "Read medications",
  knowledge: "Search health knowledge",
  recommendation: "Read recommendation",
  web_search: "Search reputable sources",
  directory: "Search the provider directory",
};

const SUGGESTIONS = [
  "I've had a headache for three days",
  "Explain my latest blood test",
  "Find me a dermatologist nearby",
  "Is there a dengue outbreak right now?",
];

export default function Assistant() {
  const { user } = useAuth();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isPrivate, setIsPrivate] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [pinPrompt, setPinPrompt] = useState(false);
  const [pin, setPin] = useState("");
  // Reopening a private chat needs its code as well as its PIN: private
  // sessions are deliberately absent from history, so there is nothing to
  // click on and the code is the only handle the user has.
  const [resumePrompt, setResumePrompt] = useState(false);
  const [resumeCode, setResumeCode] = useState("");
  const [resumePin, setResumePin] = useState("");
  const [booking, setBooking] = useState<ProviderCard | null>(null);
  const [uploading, setUploading] = useState(false);
  const [listening, setListening] = useState(false);
  const [railOpen, setRailOpen] = useState(true);
  const [attachMode, setAttachMode] = useState<AttachMode>("file");
  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const listenerRef = useRef<voice.Listener | null>(null);

  // Resolved once: a microphone button that does nothing is worse than none.
  const canListen = voice.listeningSupported();
  const canSpeak = voice.speechSupported();

  const loadSessions = useCallback(async () => {
    try {
      const { data } = await api.get("/agent/sessions");
      setSessions(data.sessions ?? []);
    } catch {
      /* history is a convenience; a failure here must not block chatting */
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, trace]);

  function reset(privateMode: boolean) {
    setTurns([]);
    setTrace([]);
    setSessionId(null);
    setError(null);
    setIsPrivate(privateMode);
    setShowHistory(false);
  }

  async function openSession(id: string) {
    try {
      const { data } = await api.get(`/agent/sessions/${id}`);
      setTurns(
        (data.messages ?? []).map((m: any) => ({
          role: m.role,
          content: m.content,
          routes: m.meta?.routes,
          latencyMs: m.meta?.latency_ms,
          provider: m.meta?.provider,
          cache: { hit: m.meta?.cache_hit },
        })),
      );
      setSessionId(id);
      setIsPrivate(false);
      setShowHistory(false);
      setError(null);
    } catch (err) {
      setError(errorMessage(err, "That conversation could not be opened."));
    }
  }

  async function deleteSession(id: string) {
    try {
      await api.delete(`/agent/sessions/${id}`);
      if (id === sessionId) reset(false);
      void loadSessions();
    } catch (err) {
      setError(errorMessage(err, "Could not delete that conversation."));
    }
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

  async function resumePrivate() {
    const code = resumeCode.trim();
    if (!code || !/^\d{6}$/.test(resumePin)) {
      setError("Enter the chat code and its 6-digit PIN.");
      return;
    }
    try {
      const { data } = await api.post("/agent/sessions/resume", {
        session_id: code,
        pin: resumePin,
      });
      setTurns(
        (data.messages ?? []).map((m: any) => ({
          role: m.role,
          content: m.content,
        })),
      );
      setSessionId(data.id);
      setIsPrivate(true);
      setShowHistory(false);
      setResumePrompt(false);
      setResumeCode("");
      setResumePin("");
      setError(null);
    } catch (err) {
      // The server counts failed attempts and destroys the chat after too
      // many, so its message is the one that matters — surface it verbatim.
      setError(errorMessage(err, "That chat could not be reopened."));
      setResumePin("");
    }
  }

  async function send(message: string) {
    const text = message.trim();
    if (!text || busy) return;

    setBusy(true);
    setError(null);
    setInput("");
    setTrace([]);
    setTurns((previous) => [...previous, { role: "user", content: text }]);

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

      // SSE frames are separated by a blank line; a frame may straddle chunks.
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          handleEvent(JSON.parse(line.slice(6)));
        }
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "The assistant is unavailable.",
      );
    } finally {
      setBusy(false);
      void loadSessions();
    }
  }

  /**
   * Dictate instead of typing.
   *
   * The transcript lands in the input box rather than sending immediately —
   * speech recognition mishears, and in a medical context an unreviewed
   * "I have chest pain" that was actually "I have chest strain" is worth one
   * extra tap to prevent.
   */
  function toggleListening() {
    if (listening) {
      listenerRef.current?.stop();
      return;
    }
    setError(null);
    const handle = voice.listen({
      language: user?.preferred_language ?? "en",
      onPartial: setInput,
      onFinal: setInput,
      onError: setError,
      onEnd: () => {
        setListening(false);
        listenerRef.current = null;
      },
    });
    if (!handle) {
      setError("Your browser does not support voice input. Please type instead.");
      return;
    }
    listenerRef.current = handle;
    setListening(true);
  }

  useEffect(() => () => {
    listenerRef.current?.stop();
    voice.stopSpeaking();
  }, []);

  /**
   * Upload a report or scan without leaving the conversation.
   *
   * The file goes to the same endpoint the Reports page uses — OCR, flagging
   * against the report's own reference ranges, and care routing all happen
   * server-side exactly as before. What is different is only where the
   * patient is standing: the extracted findings come straight back as the
   * next assistant turn, so they can ask "what does the ferritin mean?"
   * immediately instead of navigating to another page and losing context.
   *
   * Private sessions do not accept uploads. A document is durable by nature
   * and would outlive a conversation that promises to leave no trace.
   */
  async function uploadFile(file: File) {
    if (isPrivate) {
      setError(
        "Uploads are turned off in a private chat, because a saved document " +
          "would outlast the conversation. Start a normal chat to upload.",
      );
      return;
    }

    const isImage = file.type.startsWith("image/");
    const endpoint = isImage ? "/images" : "/documents";
    const form = new FormData();
    form.append("file", file);

    setUploading(true);
    setError(null);
    setTurns((previous) => [
      ...previous,
      { role: "user", content: `Uploaded **${file.name}**` },
    ]);

    try {
      const { data } = await api.post(endpoint, form);
      const summary =
        data.explanation ??
        data.summary ??
        data.analysis?.explanation ??
        "I've saved that. Ask me anything about it.";

      setTurns((previous) => [
        ...previous,
        {
          role: "assistant",
          content: summary,
          routes: ["records"],
          structured: { records: data },
        },
      ]);
    } catch (err) {
      setError(errorMessage(err, "That file could not be processed."));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  /**
   * Picking a card continues the conversation rather than navigating away.
   *
   * A doctor goes straight to the booking sheet, because that is the whole
   * point of surfacing them. A facility or a test has no single obvious next
   * step, so it becomes a follow-up question and the assistant answers in
   * context — the patient never loses their place.
   */
  function pickProvider(provider: ProviderCard) {
    if (provider.kind === "doctor" && provider.doctor_id) {
      setBooking(provider);
      return;
    }
    if (provider.kind === "hospital") {
      void send(`Tell me more about ${provider.name} and how to get seen there.`);
      return;
    }
    void send(`Where should I get the ${provider.name}, and how do I prepare?`);
  }

  function handleEvent(event: any) {
    if (event.type === "session") {
      setSessionId(event.session_id);
      setIsPrivate(Boolean(event.private));
      return;
    }

    if (event.type === "routes") {
      if (!event.routes?.length) return;
      setTrace((previous) => [
        ...previous,
        {
          id: `routes-${previous.length}`,
          label: event.parallel
            ? `Running ${event.routes.length} assistants in parallel`
            : ROUTE_LABEL[event.routes[0]] ?? event.routes[0],
          detail: event.parallel
            ? event.routes.map((r: string) => ROUTE_LABEL[r] ?? r).join(", ")
            : undefined,
          status: "ok",
        },
      ]);
      return;
    }

    if (event.type === "stage") {
      const meta = NODE_LABEL[event.node];
      if (!meta) return;
      // A cache miss is an internal detail, not a step worth narrating.
      if (event.node === "cache_lookup" && event.hit === false) return;
      setTrace((previous) => [
        ...previous,
        {
          id: `${event.node}-${previous.length}`,
          label: meta.label,
          detail:
            event.verdict && event.verdict !== "allow"
              ? event.verdict
              : event.hit
                ? "known answer"
                : undefined,
          status:
            event.verdict === "block" || event.verdict === "crisis"
              ? "denied"
              : "ok",
          ms: event.ms,
        },
      ]);
      return;
    }

    if (event.type === "tool") {
      setTrace((previous) => [
        ...previous,
        {
          id: `${event.tool}-${previous.length}`,
          label: TOOL_LABEL[event.tool] ?? event.tool,
          status:
            event.status === "denied"
              ? "denied"
              : event.status === "error"
                ? "error"
                : "ok",
          ms: event.ms,
          detail: event.status === "denied" ? "Not shared with you" : undefined,
        },
      ]);
      return;
    }

    if (event.type === "final") {
      setTurns((previous) => [
        ...previous,
        {
          role: "assistant",
          content: event.answer,
          routes: event.routes,
          guard: event.guard,
          structured: event.structured,
          citations: event.citations,
          consult: event.consult,
          cache: event.cache,
          latencyMs: event.latency_ms,
          provider: event.provider,
        },
      ]);
      setTrace([]);
      return;
    }

    if (event.type === "error") {
      setError("The assistant could not complete that request.");
    }
  }

  return (
    <div className="flex h-full min-h-0">
      {/* ---------------- History rail ----------------
          Desktop: collapsible in place. Mobile: a slide-over, because a
          permanently visible rail costs a third of a phone screen. */}
      {showHistory && (
        <div
          className="fixed inset-0 z-30 bg-ink-900/40 lg:hidden"
          onClick={() => setShowHistory(false)}
          aria-hidden
        />
      )}
      <aside
        className={`
          fixed inset-y-0 left-0 z-40 w-72 shrink-0 border-r border-line bg-surface
          transition-transform duration-200 lg:static lg:z-auto lg:translate-x-0
          ${showHistory ? "translate-x-0" : "-translate-x-full"}
          ${railOpen ? "lg:w-64" : "lg:w-0 lg:overflow-hidden lg:border-r-0"}
        `}
      >
        <div className="flex h-full flex-col p-3">
          <div className="flex items-center gap-2">
            <button
              onClick={() => reset(false)}
              className="sp-btn sp-btn-primary flex-1 justify-center"
            >
              <Icon name="add" size={16} />
              New chat
            </button>
            <button
              onClick={() => setShowHistory(false)}
              aria-label="Close history"
              className="sp-btn sp-btn-ghost !px-2 lg:hidden"
            >
              <Icon name="close" size={18} />
            </button>
          </div>
          <button
            onClick={() => setPinPrompt(true)}
            className="sp-btn sp-btn-ghost sp-btn-block mt-2 justify-center"
          >
            <Icon name="lock" size={16} />
            Private chat
          </button>
          {/* Private chats are absent from the list below, so this is the only
              way back into one. */}
          <button
            onClick={() => setResumePrompt(true)}
            className="sp-btn sp-btn-ghost sp-btn-block mt-1 justify-center text-sm"
          >
            Resume a private chat
          </button>

          <p className="mb-1.5 mt-4 px-1 text-xs font-semibold uppercase tracking-wide text-ink-400">
            Recent
          </p>
          <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
            {sessions.length === 0 && (
              <p className="px-1 py-2 text-sm text-ink-400">
                Your conversations will appear here.
              </p>
            )}
            {sessions.map((session) => (
              <div
                key={session.id}
                className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm transition ${
                  session.id === sessionId
                    ? "bg-brand-50 text-brand-800"
                    : "text-ink-700 hover:bg-canvas"
                }`}
              >
                <button
                  onClick={() => void openSession(session.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <span className="block truncate">{session.title}</span>
                  <span className="block text-xs text-ink-400">
                    {relativeDay(session.last_message_at)}
                  </span>
                </button>
                <button
                  onClick={() => void deleteSession(session.id)}
                  aria-label="Delete conversation"
                  className="text-ink-400 opacity-0 transition hover:text-danger-text focus:opacity-100 group-hover:opacity-100"
                >
                  <Icon name="delete" size={15} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </aside>

      {/* ---------------- Conversation ---------------- */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2.5 sm:px-4">
          <button
            onClick={() =>
              window.innerWidth >= 1024
                ? setRailOpen((v) => !v)
                : setShowHistory(true)
            }
            aria-label={railOpen ? "Hide conversations" : "Show conversations"}
            title={railOpen ? "Hide conversations" : "Show conversations"}
            className="sp-btn sp-btn-ghost shrink-0 !px-2"
          >
            <Icon name={railOpen ? "sidebarClose" : "sidebarOpen"} size={19} />
          </button>
          <div className="min-w-0">
            <h1 className="sp-heading truncate text-base">SuwaPath Assistant</h1>
            <p className="hidden truncate text-xs text-ink-500 sm:block">
              Symptoms, reports, appointments — all in one conversation.
            </p>
          </div>
          {isPrivate && (
            <span className="ml-auto flex shrink-0 items-center gap-1 rounded-full bg-programme-surface px-2.5 py-1 text-xs font-medium text-programme-text">
              <Icon name="lock" size={13} />
              Private
            </span>
          )}
        </div>

        {isPrivate && (
          <div className="flex flex-wrap items-start gap-2.5 border-b border-programme-border bg-programme-surface px-4 py-2">
            <Icon
              name="lock"
              size={15}
              className="mt-0.5 shrink-0 text-programme-text"
            />
            <p className="text-xs text-programme-text">
              Nothing here is saved to your history. It disappears after 12
              hours, and reopening it needs the code below with your PIN —
              neither can be recovered.
            </p>
            {/* The code has to be visible somewhere. A private chat is absent
                from history by design, so without it there is no route back
                into the conversation and the PIN alone is not enough. */}
            {sessionId && (
              <div className="flex w-full items-center gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-programme-text">
                  Chat code
                </span>
                <code className="select-all rounded bg-surface/80 px-2 py-1 text-xs text-ink-800">
                  {sessionId}
                </code>
                <button
                  type="button"
                  onClick={() => void navigator.clipboard?.writeText(sessionId)}
                  className="sp-btn sp-btn-ghost sp-btn-sm"
                >
                  Copy
                </button>
              </div>
            )}
          </div>
        )}

        {/* The only scrolling region on the page. */}
        <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
          <div className="mx-auto w-full max-w-3xl space-y-4 px-3 py-5 sm:px-5">
            {turns.length === 0 && (
              <Welcome name={user?.full_name} onPick={send} />
            )}

            {turns.map((turn, index) => (
              <TurnBubble
                key={index}
                turn={turn}
                onPick={pickProvider}
                canSpeak={canSpeak}
                language={user?.preferred_language ?? "en"}
              />
            ))}

            {trace.length > 0 && <Thinking items={trace} />}
            {busy && trace.length === 0 && <Dots />}
            <div ref={endRef} />
          </div>
        </div>

        <div className="border-t border-line bg-surface px-3 py-3 sm:px-5">
          <div className="mx-auto w-full max-w-3xl">
            <ErrorNote message={error} />
            <form
              className="flex items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void send(input);
              }}
            >
              <input
                ref={fileRef}
                type="file"
                accept={acceptFor(attachMode)}
                capture={attachMode === "camera" ? "environment" : undefined}
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void uploadFile(file);
                }}
              />
              <AttachMenu
                disabled={busy || uploading || isPrivate}
                disabledReason={
                  isPrivate ? "Uploads are off in a private chat" : undefined
                }
                onPick={(mode) => {
                  setAttachMode(mode);
                  // Let `accept`/`capture` land before the picker opens.
                  window.setTimeout(() => fileRef.current?.click(), 0);
                }}
              />
              {canListen && (
                <button
                  type="button"
                  onClick={toggleListening}
                  disabled={busy || uploading}
                  aria-label={listening ? "Stop dictating" : "Dictate a message"}
                  aria-pressed={listening}
                  title={listening ? "Stop dictating" : "Dictate a message"}
                  className={`grid h-10 w-10 shrink-0 place-items-center rounded-full border transition ${
                    listening
                      ? "animate-pulse border-danger-border bg-danger-surface text-danger-text"
                      : "border-line bg-surface text-ink-600 hover:border-brand-400 hover:text-brand-700"
                  }`}
                >
                  <Icon name="mic" size={18} />
                </button>
              )}
              <input
                className="sp-input !rounded-full"
                placeholder={
                  listening
                    ? "Listening…"
                    : isPrivate
                      ? "Private chat — ask anything…"
                      : "Describe a symptom, or ask about your reports…"
                }
                value={input}
                onChange={(event) => setInput(event.target.value)}
                disabled={busy || uploading}
              />
              <button
                aria-label="Send"
                className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand-600 text-white transition hover:bg-brand-700 disabled:opacity-40"
                disabled={busy || uploading || !input.trim()}
              >
                <Icon name="send" size={17} />
              </button>
            </form>
            <p className="mt-2 text-center text-[11px] leading-snug text-ink-400">
              SuwaPath helps you navigate care. It does not diagnose or
              prescribe, and urgency is decided by the clinical rule engine —
              never by a language model.
            </p>
          </div>
        </div>
      </div>

      {pinPrompt && (
        <PinDialog
          pin={pin}
          setPin={setPin}
          onCancel={() => {
            setPinPrompt(false);
            setPin("");
          }}
          onConfirm={startPrivate}
        />
      )}

      {resumePrompt && (
        <ResumeDialog
          code={resumeCode}
          setCode={setResumeCode}
          pin={resumePin}
          setPin={setResumePin}
          onCancel={() => {
            setResumePrompt(false);
            setResumeCode("");
            setResumePin("");
          }}
          onConfirm={() => void resumePrivate()}
        />
      )}

      {booking && (
        <BookingSheet
          provider={booking}
          onClose={() => setBooking(null)}
          onBooked={(message) => {
            setBooking(null);
            setTurns((previous) => [
              ...previous,
              { role: "assistant", content: message, routes: ["admin"] },
            ]);
          }}
          onError={setError}
        />
      )}
    </div>
  );
}

/**
 * Confirm and book, in place.
 *
 * Only slots the server offered are bookable — the sheet never lets the
 * patient type a time. That keeps the one invariant that matters here: a
 * booking always corresponds to a slot the availability service actually
 * generated, at the doctor's own slot length.
 */
function BookingSheet({
  provider,
  onClose,
  onBooked,
  onError,
}: {
  provider: ProviderCard;
  onClose: () => void;
  onBooked: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [slots, setSlots] = useState<any[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get(
          `/providers/doctors/${provider.doctor_id}/availability`,
        );
        if (cancelled) return;
        const available = data.slots ?? data.results ?? [];
        setSlots(available);
        setChosen(available[0]?.start ?? provider.next_available?.start ?? null);
      } catch {
        if (cancelled) return;
        // Fall back to the single slot the card already showed.
        const fallback = provider.next_available ? [provider.next_available] : [];
        setSlots(fallback);
        setChosen(provider.next_available?.start ?? null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [provider]);

  async function confirm() {
    if (!chosen || !provider.doctor_id) return;
    setSaving(true);
    try {
      await api.post("/appointments", {
        doctor_id: provider.doctor_id,
        scheduled_start: chosen,
        visit_type: "physical",
        reason: reason.trim() || null,
      });
      const when = slots?.find((s) => s.start === chosen);
      onBooked(
        `**Booked.** You're seeing ${provider.name}` +
          (provider.hospital_name ? ` at ${provider.hospital_name}` : "") +
          (when ? ` on ${when.date_label}, ${when.label}` : "") +
          ".\n\nIt's in **Appointments** now, and the clinic can see it too. " +
          "Bring any recent reports with you.",
      );
    } catch (err) {
      onError(errorMessage(err, "That slot could not be booked."));
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink-900/50 p-4">
      <Card className="w-full max-w-md">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-semibold text-ink-900">Book {provider.name}</h2>
            <p className="text-sm text-ink-500">
              {provider.specialty}
              {provider.hospital_name ? ` · ${provider.hospital_name}` : ""}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 text-ink-400 hover:text-ink-700"
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        {provider.fee_lkr != null && (
          <p className="mt-2 text-sm text-ink-600">
            Consultation fee{" "}
            <strong className="font-semibold text-ink-900">
              LKR {Math.round(provider.fee_lkr).toLocaleString("en-LK")}
            </strong>
          </p>
        )}

        <p className="mt-4 mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-400">
          Choose a time
        </p>
        {slots === null ? (
          <p className="py-3 text-sm text-ink-400">Checking availability…</p>
        ) : slots.length === 0 ? (
          <p className="py-3 text-sm text-ink-500">
            No free slots are published for this doctor right now.
          </p>
        ) : (
          <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
            {slots.slice(0, 12).map((slot: any) => (
              <button
                key={slot.start}
                onClick={() => setChosen(slot.start)}
                className={`rounded-lg border px-2.5 py-1.5 text-sm transition ${
                  chosen === slot.start
                    ? "border-brand-500 bg-brand-50 text-brand-800"
                    : "border-line bg-surface text-ink-700 hover:border-brand-300"
                }`}
              >
                {slot.date_label}, {slot.label}
              </button>
            ))}
          </div>
        )}

        <label className="mt-4 block text-xs font-semibold uppercase tracking-wide text-ink-400">
          Reason (optional)
        </label>
        <input
          className="sp-input mt-1.5"
          placeholder="What would you like to discuss?"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />

        <div className="mt-4 flex gap-2">
          <button onClick={onClose} className="sp-btn sp-btn-ghost flex-1 justify-center">
            Cancel
          </button>
          <button
            onClick={confirm}
            disabled={!chosen || saving}
            className="sp-btn sp-btn-primary flex-1 justify-center"
          >
            {saving ? "Booking…" : "Confirm"}
          </button>
        </div>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */

/**
 * Attachment picker.
 *
 * One `+` opening a small menu, rather than three buttons crowding the
 * composer. The three modes differ only in what they hand the file input:
 * `accept` narrows the picker, and `capture` asks a phone for the camera
 * directly instead of the gallery.
 */
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

  // Camera capture is meaningless on a desktop without one, and the browser
  // silently falls back to a file picker — so it is only offered on coarse
  // pointers, where it actually opens a camera.
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
        className="grid h-10 w-10 place-items-center rounded-full border border-line bg-surface text-ink-600 transition hover:border-brand-400 hover:text-brand-700 disabled:opacity-40"
      >
        <Icon name="add" size={19} />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute bottom-12 left-0 z-30 w-60 overflow-hidden rounded-2xl border border-line bg-surface shadow-lg"
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
              className="flex w-full items-start gap-3 px-3 py-2.5 text-left transition hover:bg-canvas"
            >
              <span className="sp-icon-tile mt-0.5 shrink-0 bg-brand-50 text-brand-700">
                <Icon name={option.icon} size={17} />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-ink-900">
                  {option.label}
                </span>
                <span className="block text-xs text-ink-500">{option.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Welcome({
  name,
  onPick,
}: {
  name?: string;
  onPick: (text: string) => void;
}) {
  return (
    <div className="py-8 text-center">
      <span className="sp-icon-tile mx-auto bg-brand-50 text-brand-700 !h-12 !w-12">
        <Icon name="ai" size={26} />
      </span>
      <p className="mt-3 font-medium text-ink-800">
        Hello {name?.split(" ")[0]} — what can I help with?
      </p>
      <p className="mx-auto mt-1 max-w-md text-sm text-ink-500">
        Tell me what's bothering you in your own words. I'll ask a few questions
        the way a doctor would before pointing you anywhere.
      </p>
      <div className="mt-5 flex flex-wrap justify-center gap-2">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            onClick={() => onPick(suggestion)}
            className="rounded-full border border-ink-200 bg-surface px-3.5 py-2 text-sm text-ink-700 transition hover:border-brand-400 hover:bg-brand-50"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Merge the two sources of provider suggestions into one deck.
 *
 * `find_care` ranks by distance and live availability from a structured
 * recommendation; `directory` answers the fuzzy questions ("speaks Tamil",
 * "open Sunday") by semantic search. They overlap, so matches are keyed by
 * identity and the availability-bearing entry wins — a card that can show a
 * real next free slot is strictly more useful than one that cannot.
 */
function providerCards(structured?: Record<string, any>): ProviderCard[] {
  const admin = structured?.admin ?? {};
  const byKey = new Map<string, ProviderCard>();

  const put = (card: ProviderCard, preferExisting = false) => {
    const key = `${card.kind}:${card.doctor_id ?? card.hospital_id ?? card.test_id ?? card.name}`;
    if (preferExisting && byKey.has(key)) return;
    byKey.set(key, { ...byKey.get(key), ...card });
  };

  for (const doctor of admin.doctors ?? []) {
    put({ kind: "doctor", ...doctor });
  }
  for (const provider of admin.providers ?? []) {
    // Directory hits must not overwrite a `next_available` we already have.
    const key = `${provider.kind}:${provider.doctor_id ?? provider.hospital_id ?? provider.test_id ?? provider.name}`;
    const existing = byKey.get(key);
    byKey.set(key, existing ? { ...provider, ...existing } : provider);
  }
  for (const facility of admin.facilities ?? []) {
    put({ kind: "hospital", ...facility }, true);
  }

  return [...byKey.values()].filter((c) => c.name);
}

function deckTitle(cards: ProviderCard[]): string {
  const kinds = new Set(cards.map((c) => c.kind));
  if (kinds.size === 1) {
    const noun =
      [...kinds][0] === "doctor"
        ? "doctor"
        : [...kinds][0] === "hospital"
          ? "facility"
          : "test";
    return `${cards.length} ${noun}${cards.length > 1 ? "s" : ""} — swipe to compare`;
  }
  return `${cards.length} suggestions — swipe to compare`;
}

function TurnBubble({
  turn,
  onPick,
  canSpeak,
  language,
}: {
  turn: Turn;
  onPick: (provider: ProviderCard) => void;
  canSpeak: boolean;
  language: string;
}) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-white">
          {turn.content}
        </div>
      </div>
    );
  }

  const cards = providerCards(turn.structured);
  const tests = turn.structured?.consult?.consult?.tests ?? [];
  const urgency = turn.consult?.urgency;

  return (
    <div className="flex justify-start">
      <div className="min-w-0 max-w-[92%] space-y-2">
        <div className="rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3 text-ink-800">
          <Markdown content={turn.content} />
        </div>

        {/* Provenance: how this answer was produced. */}
        <div className="flex flex-wrap items-center gap-1.5 px-1">
          {turn.cache?.hit && (
            <Chip tone="ok" icon="bolt">
              Known answer
            </Chip>
          )}
          {turn.routes?.map((route) => (
            <Chip key={route} tone="info">
              {ROUTE_LABEL[route] ?? route}
            </Chip>
          ))}
          {urgency && urgency !== "routine" && <UrgencyBadge urgency={urgency} />}
          {turn.guard?.output_verdict === "soften" && (
            <Chip tone="warn" icon="shield">
              Caveat added
            </Chip>
          )}
          {turn.guard?.input === "crisis" && (
            <Chip tone="danger" icon="emergency">
              Crisis support
            </Chip>
          )}
          {turn.latencyMs != null && (
            <span className="text-xs text-ink-400">
              {(turn.latencyMs / 1000).toFixed(1)}s
              {turn.provider && turn.provider !== "passthrough"
                ? ` · ${turn.provider}`
                : ""}
            </span>
          )}
          {canSpeak && (
            <button
              type="button"
              onClick={() => voice.speak(turn.content, language)}
              aria-label="Read this answer aloud"
              title="Read aloud"
              className="text-ink-400 transition hover:text-brand-700"
            >
              <Icon name="volumeUp" size={14} />
            </button>
          )}
        </div>

        {/* Structured results render as UI, not as prose. */}
        {tests.length > 0 && (
          <div className="rounded-xl border border-line bg-surface p-3">
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-400">
              Tests worth asking about
            </p>
            <ul className="space-y-1 text-sm text-ink-700">
              {tests.slice(0, 4).map((test: any, i: number) => (
                <li key={i} className="flex gap-2">
                  <Icon
                    name="lab"
                    size={15}
                    className="mt-0.5 shrink-0 text-brand-600"
                  />
                  <span>{test.name}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {cards.length > 0 && (
          <ProviderDeck providers={cards} onSelect={onPick} title={deckTitle(cards)} />
        )}

        {turn.citations && turn.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-1">
            {turn.citations.slice(0, 4).map((citation: any, i: number) =>
              citation.url ? (
                <a
                  key={i}
                  href={citation.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-ink-600 transition hover:border-brand-400"
                >
                  {citation.source ?? citation.title}
                </a>
              ) : (
                <span
                  key={i}
                  className="rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-ink-500"
                >
                  {citation.title}
                </span>
              ),
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Thinking({ items }: { items: TraceItem[] }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-2 rounded-2xl rounded-bl-md border border-line bg-surface p-3.5">
        <p className="text-xs text-ink-500">Working…</p>
        {items.map((item) => (
          <div key={item.id} className="flex items-center gap-2 text-sm">
            <span
              className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border ${
                item.status === "denied"
                  ? "border-warn-border bg-warn-surface text-warn-text"
                  : item.status === "error"
                    ? "border-danger-border bg-danger-surface text-danger-text"
                    : "border-ok-border bg-ok-surface text-ok-text"
              }`}
            >
              <Icon
                name={
                  item.status === "denied"
                    ? "lock"
                    : item.status === "error"
                      ? "error"
                      : "check"
                }
                size={12}
              />
            </span>
            <span className="text-ink-700">{item.label}</span>
            {item.detail && (
              <span className="text-xs text-ink-400">· {item.detail}</span>
            )}
            {item.ms != null && item.ms > 0 && (
              <span className="text-xs text-ink-400">{item.ms}ms</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function Dots() {
  return (
    <div className="flex justify-start">
      <div className="rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3">
        <span className="flex gap-1">
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
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink-900/50 p-4">
      <Card className="w-full max-w-sm">
        <div className="flex items-start gap-3">
          <span className="sp-icon-tile bg-programme-surface text-programme-text">
            <Icon name="lock" size={20} />
          </span>
          <div className="min-w-0">
            <h2 className="font-semibold text-ink-900">Start a private chat</h2>
            <p className="mt-1 text-sm text-ink-600">
              Nothing you say is written to your history. Choose a 6-digit PIN —
              it is the only way to reopen this conversation, and it cannot be
              recovered.
            </p>
          </div>
        </div>

        <input
          className="sp-input mt-4 text-center text-lg tracking-[0.4em]"
          inputMode="numeric"
          autoComplete="off"
          maxLength={6}
          placeholder="••••••"
          value={pin}
          onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
        />

        <div className="mt-4 flex gap-2">
          <button
            onClick={onCancel}
            className="sp-btn sp-btn-ghost flex-1 justify-center"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={pin.length !== 6}
            className="sp-btn sp-btn-primary flex-1 justify-center"
          >
            Start
          </button>
        </div>
      </Card>
    </div>
  );
}

/**
 * Reopening a private chat.
 *
 * Takes the code as well as the PIN. The PIN alone cannot identify a
 * conversation — private sessions are excluded from history precisely so that
 * nothing points at them — so the code is what names the chat and the PIN is
 * what proves it is yours.
 *
 * Wrong PINs are counted server-side and the conversation is destroyed after
 * too many, so the warning below is a statement of fact, not a deterrent.
 */
function ResumeDialog({
  code,
  setCode,
  pin,
  setPin,
  onCancel,
  onConfirm,
}: {
  code: string;
  setCode: (v: string) => void;
  pin: string;
  setPin: (v: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink-900/50 p-4">
      <Card className="w-full max-w-sm">
        <div className="flex items-start gap-3">
          <span className="sp-icon-tile bg-programme-surface text-programme-text">
            <Icon name="lock" size={20} />
          </span>
          <div className="min-w-0">
            <h2 className="font-semibold text-ink-900">Resume a private chat</h2>
            <p className="mt-1 text-sm text-ink-600">
              Enter the chat code you saved and its PIN. Too many wrong PINs
              will delete the conversation.
            </p>
          </div>
        </div>

        <label className="sp-field mt-4">Chat code</label>
        <input
          className="sp-input"
          autoComplete="off"
          spellCheck={false}
          placeholder="Paste the code you saved"
          value={code}
          onChange={(event) => setCode(event.target.value.trim())}
        />

        <label className="sp-field mt-3">PIN</label>
        <input
          className="sp-input text-center text-lg tracking-[0.4em]"
          inputMode="numeric"
          autoComplete="off"
          maxLength={6}
          placeholder="••••••"
          value={pin}
          onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
        />

        <div className="mt-4 flex gap-2">
          <button
            onClick={onCancel}
            className="sp-btn sp-btn-ghost flex-1 justify-center"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={!code.trim() || pin.length !== 6}
            className="sp-btn sp-btn-primary flex-1 justify-center"
          >
            Reopen
          </button>
        </div>
      </Card>
    </div>
  );
}
