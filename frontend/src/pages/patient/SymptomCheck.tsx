import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AiNotice,
  Card,
  Confidence,
  Icon,
  ErrorNote,
  EscalationBanner,
  UrgencyBadge,
} from "../../components/ui";
import { api, errorMessage } from "../../lib/api";

interface Turn {
  role: "patient" | "assistant";
  content: string;
}

const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "si", label: "සිංහල" },
  { code: "ta", label: "தமிழ்" },
];

const STARTERS: Record<string, string[]> = {
  en: [
    "I have pain in my chest and I feel dizzy",
    "I have had a persistent cough for three weeks",
    "There is a skin patch on my arm that won't heal",
  ],
  si: ["මට පපුවේ කැක්කුම සහ හුස්ම ගැනීමේ අපහසුතාව තියෙනවා"],
  ta: ["எனக்கு காய்ச்சல் மற்றும் தலைவலி உள்ளது"],
};

export default function SymptomCheck() {
  const navigate = useNavigate();
  const [language, setLanguage] = useState("en");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const [trace, setTrace] = useState<any[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, result]);

  function applyTurn(data: any) {
    if (data.assistant_message) {
      setTurns((previous) => [
        ...previous,
        { role: "assistant", content: data.assistant_message },
      ]);
    }
    if (data.orchestration_trace?.length) setTrace(data.orchestration_trace);
    if (data.is_complete && data.recommendation) {
      setResult({
        recommendation: data.recommendation,
        redFlags: data.red_flags,
        intake: data.intake,
      });
    }
  }

  async function send(message: string) {
    const text = message.trim();
    if (!text || busy) return;

    setBusy(true);
    setError(null);
    setInput("");
    setTurns((previous) => [...previous, { role: "patient", content: text }]);

    try {
      if (!sessionId) {
        const { data } = await api.post("/symptoms/sessions", {
          language,
          initial_message: text,
        });
        setSessionId(data.session_id);
        applyTurn(data);
      } else {
        const { data } = await api.post(
          `/symptoms/sessions/${sessionId}/messages`,
          { message: text },
        );
        applyTurn(data);
      }
    } catch (err) {
      setError(errorMessage(err, "Could not send that message."));
    } finally {
      setBusy(false);
    }
  }

  function restart() {
    setSessionId(null);
    setTurns([]);
    setResult(null);
    setTrace([]);
    setError(null);
  }

  const recommendation = result?.recommendation;
  const redFlags = result?.redFlags;

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink-900">Symptom Check</h1>
          <p className="text-ink-500">
            Describe what you are experiencing. I'll ask a few questions.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="sp-select py-2 w-auto"
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
            disabled={Boolean(sessionId)}
          >
            {LANGUAGES.map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.label}
              </option>
            ))}
          </select>
          {sessionId && (
            <button className="sp-btn sp-btn-secondary sp-btn-sm" onClick={restart}>
              Start over
            </button>
          )}
        </div>
      </header>

      <Card className="!p-0 overflow-hidden">
        <div className="h-[46vh] min-h-[320px] overflow-y-auto p-5 space-y-4 bg-ink-50/40">
          {turns.length === 0 && (
            <div className="text-center py-10">
              <Icon name="chat" size={40} className="mx-auto text-brand-500" />
              <p className="mt-3 font-medium text-ink-800">
                Tell me what health concern brings you here today.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {(STARTERS[language] ?? STARTERS.en).map((starter) => (
                  <button
                    key={starter}
                    onClick={() => void send(starter)}
                    className="rounded-full border border-ink-200 bg-white px-3.5 py-2 text-sm text-ink-700 hover:border-brand-400 hover:bg-brand-50 transition"
                  >
                    {starter}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn, index) => (
            <div
              key={index}
              className={`flex ${turn.role === "patient" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-2.5 ${
                  turn.role === "patient"
                    ? "bg-brand-600 text-white rounded-br-md"
                    : "bg-white border border-ink-100 text-ink-800 rounded-bl-md"
                }`}
              >
                {turn.content}
              </div>
            </div>
          ))}

          {busy && (
            <div className="flex justify-start">
              <div className="bg-white border border-ink-100 rounded-2xl rounded-bl-md px-4 py-3">
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
          className="border-t border-ink-100 p-3 flex gap-2 bg-white"
          onSubmit={(event) => {
            event.preventDefault();
            void send(input);
          }}
        >
          <input
            className="sp-input"
            placeholder={result ? "Symptom check complete" : "Type your answer…"}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={busy || Boolean(result)}
          />
          <button className="sp-btn sp-btn-primary" disabled={busy || Boolean(result) || !input.trim()}>
            Send
          </button>
        </form>
      </Card>

      <ErrorNote message={error} />

      {redFlags && (
        <EscalationBanner
          urgency={redFlags.urgency}
          message={redFlags.escalation_message}
        />
      )}

      {result && (
        <>
          {/* Structured intake — stored separately from the raw conversation */}
          <Card title="Your Structured Health Summary">
            <div className="grid sm:grid-cols-2 gap-4 text-sm">
              <Field label="Chief complaint" value={result.intake?.chief_complaint} />
              <Field label="Duration" value={result.intake?.duration_text} />
              <Field
                label="Severity"
                value={result.intake?.severity ? `${result.intake.severity} / 10` : null}
              />
              <Field
                label="Symptoms"
                value={result.intake?.symptoms?.join(", ")}
              />
              <Field
                label="Relevant history"
                value={result.intake?.relevant_history?.join(", ")}
              />
              <Field
                label="Medication"
                value={result.intake?.medications?.join(", ")}
              />
              <Field
                label="Allergies"
                value={result.intake?.allergies?.join(", ")}
              />
              <Field
                label="Explicitly denied"
                value={result.intake?.negative_findings?.join(", ")}
              />
            </div>

            {redFlags?.triggered_rules?.length > 0 && (
              <div className="mt-5 rounded-xl border border-red-200 bg-red-50 p-4">
                <p className="font-semibold text-red-900 text-sm">
                  Clinical warning patterns detected
                </p>
                <ul className="mt-2 space-y-2">
                  {redFlags.triggered_rules.map((rule: any) => (
                    <li key={rule.rule_id} className="text-sm">
                      <p className="font-medium text-ink-900">
                        {rule.label}{" "}
                        <span className="text-xs font-normal text-ink-500">
                          ({rule.rule_id})
                        </span>
                      </p>
                      <p className="text-ink-600">{rule.rationale}</p>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-red-800 mt-3">
                  Detected by SuwaPath's clinician-defined rule engine, not by
                  the language model.
                </p>
              </div>
            )}
          </Card>

          {/* Recommendation */}
          <Card title="Care Recommendation">
            <div className="grid sm:grid-cols-3 gap-4">
              <div className="sm:col-span-2 space-y-3">
                <div className="flex items-center gap-3">
                  <div>
                    <p className="text-xs text-ink-500">Recommended specialty</p>
                    <p className="text-lg font-bold text-ink-900">
                      {recommendation.specialty_name ?? recommendation.specialty_code}
                    </p>
                  </div>
                  <UrgencyBadge urgency={recommendation.urgency} />
                </div>
                <div>
                  <p className="text-xs font-semibold text-ink-700">Reason</p>
                  <p className="text-sm text-ink-600 mt-0.5">
                    {recommendation.reason}
                  </p>
                </div>
                <div className="rounded-xl bg-brand-50 border border-brand-200 p-3">
                  <p className="text-xs font-semibold text-brand-900">
                    Suggested next action
                  </p>
                  <p className="text-sm text-ink-700 mt-0.5">
                    {recommendation.suggested_next_action}
                  </p>
                </div>
              </div>
              <div className="space-y-3">
                <div>
                  <p className="text-xs text-ink-500 mb-1">Confidence</p>
                  <Confidence value={recommendation.confidence} />
                </div>
                {recommendation.recommended_tests?.length > 0 && (
                  <div>
                    <p className="text-xs text-ink-500 mb-1">
                      Tests that may be needed
                    </p>
                    <ul className="space-y-1">
                      {recommendation.recommended_tests.map((test: any) => (
                        <li key={test.code} className="text-sm text-ink-700">
                          • {test.name}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>

            <button
              className="sp-btn sp-btn-primary w-full mt-5"
              onClick={() =>
                navigate(
                  `/patient/find-care?recommendation=${
                    recommendation.recommendation_id ?? recommendation.id ?? ""
                  }`,
                )
              }
            >
              See matching doctors & hospitals →
            </button>
            <AiNotice>
              SuwaPath provides care navigation and screening support. A
              qualified clinician confirms all diagnoses and treatment.
            </AiNotice>
          </Card>

          {/* Orchestration transparency */}
          {trace.length > 0 && (
            <details className="sp-card p-4">
              <summary className="cursor-pointer text-sm font-semibold text-ink-700">
                How this was processed ({trace.length} steps)
              </summary>
              <ol className="mt-3 space-y-2">
                {trace.map((step, index) => (
                  <li key={index} className="flex items-start gap-3 text-sm">
                    <span className="sp-chip bg-ink-100 text-ink-700 shrink-0">
                      {index + 1}
                    </span>
                    <div>
                      <p className="font-medium text-ink-800">
                        {String(step.node).replace(/_/g, " ")}
                      </p>
                      <p className="text-xs text-ink-500">
                        {step.source ?? ""}
                        {step.rationale ? ` — ${step.rationale}` : ""}
                        {step.rules_fired?.length
                          ? ` — rules: ${step.rules_fired.join(", ")}`
                          : ""}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <p className="text-xs text-ink-500">{label}</p>
      <p className="text-ink-900 font-medium mt-0.5">{value || "Not reported"}</p>
    </div>
  );
}
