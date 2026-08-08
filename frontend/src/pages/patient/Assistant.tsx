/**
 * SuwaPath Assistant — agent chat with a live chain of thought.
 *
 * The status chips are driven by *real* Server-Sent Events from the agent
 * graph, not a timed animation: each chip appears when its node actually
 * completes, so parallel agents show up side by side and tool calls report
 * their true status (including consent denials).
 */

import { useEffect, useRef, useState } from "react";
import {
  AiNotice,
  Card,
  Chip,
  Empty,
  ErrorNote,
  Icon,
  PageHeader,
  type IconName,
} from "../../components/ui";
import { API_BASE, tokens } from "../../lib/api";
import { useAuth } from "../../lib/auth";

interface Turn {
  role: "user" | "assistant";
  content: string;
  routes?: string[];
  guard?: Record<string, any>;
  structured?: Record<string, any>;
}

interface TraceItem {
  id: string;
  kind: "stage" | "tool" | "routes";
  label: string;
  detail?: string;
  status: "running" | "ok" | "denied" | "error";
  ms?: number;
}

const NODE_LABEL: Record<string, { label: string; icon: IconName }> = {
  guard_input: { label: "Safety check", icon: "shield" },
  route: { label: "Understanding your question", icon: "ai" },
  clinical_agent: { label: "Clinical assistant", icon: "stethoscope" },
  admin_agent: { label: "Appointments assistant", icon: "calendar" },
  records_agent: { label: "Records assistant", icon: "description" },
  knowledge_agent: { label: "Health knowledge", icon: "lab" },
  direct_agent: { label: "Assistant", icon: "chat" },
  merge: { label: "Combining answers", icon: "link" },
  judge: { label: "Answer safety review", icon: "verified" },
};

const TOOL_LABEL: Record<string, string> = {
  appointments: "Read appointments",
  find_care: "Match providers",
  records: "Read reports",
  medications: "Read medications",
  knowledge: "Search health knowledge",
  recommendation: "Read recommendation",
};

const SUGGESTIONS = [
  "What are my upcoming appointments?",
  "Is my next appointment still on, and what did my blood test mean?",
  "What does low haemoglobin mean?",
  "Find me a dermatologist nearby",
];

export default function Assistant() {
  const { user } = useAuth();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, trace]);

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
    }
  }

  function handleEvent(event: any) {
    if (event.type === "routes") {
      setTrace((previous) => [
        ...previous,
        {
          id: `routes-${previous.length}`,
          kind: "routes",
          label: event.parallel
            ? `Running ${event.routes.length} assistants in parallel`
            : "Selected assistant",
          detail: event.routes.join(", "),
          status: "ok",
        },
      ]);
      return;
    }

    if (event.type === "stage") {
      const meta = NODE_LABEL[event.node];
      if (!meta) return;
      setTrace((previous) => [
        ...previous,
        {
          id: `${event.node}-${previous.length}`,
          kind: "stage",
          label: meta.label,
          detail: event.verdict && event.verdict !== "allow" ? event.verdict : undefined,
          status: event.verdict === "block" || event.verdict === "crisis" ? "denied" : "ok",
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
          kind: "tool",
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
      setSessionId(event.session_id);
      setTurns((previous) => [
        ...previous,
        {
          role: "assistant",
          content: event.answer,
          routes: event.routes,
          guard: event.guard,
          structured: event.structured,
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
    <div className="max-w-4xl mx-auto space-y-4">
      <PageHeader
        title="SuwaPath Assistant"
        subtitle="Ask about your symptoms, appointments, reports or general health."
      />

      <Card className="!p-0 overflow-hidden">
        <div className="h-[52vh] min-h-[340px] overflow-y-auto p-4 sm:p-5 space-y-4 bg-canvas">
          {turns.length === 0 && (
            <div className="text-center py-8">
              <span className="sp-icon-tile bg-brand-50 text-brand-700 !h-12 !w-12 mx-auto">
                <Icon name="ai" size={26} />
              </span>
              <p className="mt-3 font-medium text-ink-800">
                Hello {user?.full_name.split(" ")[0]} — what can I help with?
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => void send(suggestion)}
                    className="rounded-full border border-ink-200 bg-surface px-3.5 py-2 text-sm text-ink-700 hover:border-brand-400 hover:bg-brand-50 transition"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn, index) => (
            <div
              key={index}
              className={`flex ${turn.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div className="max-w-[85%] space-y-2">
                <div
                  className={`rounded-2xl px-4 py-2.5 whitespace-pre-wrap ${
                    turn.role === "user"
                      ? "bg-brand-600 text-white rounded-br-md"
                      : "bg-surface border border-line text-ink-800 rounded-bl-md"
                  }`}
                >
                  {turn.content}
                </div>

                {turn.role === "assistant" && (
                  <div className="flex flex-wrap gap-1.5">
                    {turn.routes?.map((route) => (
                      <Chip key={route} tone="info">
                        {NODE_LABEL[`${route}_agent`]?.label ?? route}
                      </Chip>
                    ))}
                    {turn.guard?.input === "crisis" && (
                      <Chip tone="danger" icon="emergency">
                        Crisis support
                      </Chip>
                    )}
                    {turn.guard?.output_verdict === "soften" && (
                      <Chip tone="warn" icon="shield">
                        Safety caveat added
                      </Chip>
                    )}
                  </div>
                )}

                {/* Structured results render as real UI, not as prose. */}
                {turn.structured?.admin?.doctors?.length > 0 && (
                  <div className="space-y-2">
                    {turn.structured.admin.doctors.slice(0, 3).map((doctor: any) => (
                      <div
                        key={doctor.doctor_id}
                        className="rounded-xl border border-line bg-surface p-3"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="font-semibold text-ink-900">{doctor.name}</p>
                            <p className="text-xs text-ink-500">
                              {doctor.specialty} · {doctor.hospital_name}
                            </p>
                          </div>
                          {doctor.distance_km != null && (
                            <span className="text-xs text-ink-500 shrink-0">
                              {doctor.distance_km} km
                            </span>
                          )}
                        </div>
                        {doctor.next_available && (
                          <p className="text-xs text-brand-700 font-medium mt-1">
                            Next: {doctor.next_available.date_label},{" "}
                            {doctor.next_available.label}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* Live chain of thought */}
          {trace.length > 0 && (
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-2xl rounded-bl-md border border-line bg-surface p-3.5 space-y-2">
                <p className="text-xs text-ink-500">Working…</p>
                {trace.map((item) => (
                  <div key={item.id} className="flex items-center gap-2 text-sm">
                    <span
                      className={`grid place-items-center h-5 w-5 rounded-md border shrink-0 ${
                        item.status === "denied"
                          ? "bg-warn-surface border-warn-border text-warn-text"
                          : item.status === "error"
                            ? "bg-danger-surface border-danger-border text-danger-text"
                            : "bg-ok-surface border-ok-border text-ok-text"
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
          )}

          {busy && trace.length === 0 && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3">
                <span className="flex gap-1">
                  {[0, 1, 2].map((dot) => (
                    <span
                      key={dot}
                      className="h-2 w-2 rounded-full bg-ink-300 animate-bounce"
                      style={{ animationDelay: `${dot * 120}ms` }}
                    />
                  ))}
                </span>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form
          className="border-t border-line p-3 flex gap-2 bg-surface"
          onSubmit={(event) => {
            event.preventDefault();
            void send(input);
          }}
        >
          <input
            className="sp-input"
            placeholder="Ask about symptoms, appointments or your reports…"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={busy}
          />
          <button className="sp-btn sp-btn-primary" disabled={busy || !input.trim()}>
            <Icon name="send" size={18} />
            <span className="hidden sm:inline">Send</span>
          </button>
        </form>
      </Card>

      <ErrorNote message={error} />

      {turns.length === 0 && (
        <Empty
          title="Your conversation is private"
          hint="Only the minimum information needed for each question is used, and never anything a person has not shared with you."
        />
      )}

      <AiNotice>
        The assistant helps you navigate care. It does not diagnose or
        prescribe, and urgency is always decided by SuwaPath's clinical rule
        engine — never by the language model.
      </AiNotice>
    </div>
  );
}
