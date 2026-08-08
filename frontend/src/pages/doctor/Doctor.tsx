import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Icon from "../../components/Icon";
import {
  AiNotice,
  Avatar,
  Card,
  DataTable,
  Confidence,
  Empty,
  ErrorNote,
  Spinner,
  Stat,
  StatusChip,
  UrgencyBadge,
  formatDate,
  formatDateTime,
  formatTime,
} from "../../components/ui";
import { api, errorMessage } from "../../lib/api";
import { chartColors } from "../../styles/theme";

const URGENCY_RANK: Record<string, number> = {
  emergency: 0,
  urgent: 1,
  routine: 2,
  self_care: 3,
};

/* ------------------------------------------------------------- Dashboard */

export function DoctorDashboard() {
  const [data, setData] = useState<any>(null);
  const [queue, setQueue] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.get("/doctor/dashboard"), api.get("/doctor/queue")])
      .then(([dashboard, live]) => {
        setData(dashboard.data);
        setQueue(live.data);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;
  if (!data) return <Empty title="Could not load your dashboard." />;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">
          Good day, {data.doctor.name.replace("Dr. ", "Dr. ")}
        </h1>
        <p className="text-ink-500">
          {data.doctor.specialty_name} · {data.doctor.hospital_name}
        </p>
      </header>

      <div className="grid gap-3 sm:gap-4 grid-cols-2 lg:grid-cols-4">
        <Stat label="Today's patients" value={data.todays_patients} icon="group" />
        <Stat
          label="Urgent cases"
          value={data.urgent_cases}
          tone="danger"
          icon="emergency"
          hint={data.urgent_cases > 0 ? "Require priority review" : "None today"}
        />
        <Stat label="Follow-ups due" value={data.follow_ups_due} tone="programme" icon="refresh" />
        <Stat
          label="Reports to review"
          value={data.reports_pending_review}
          tone="warn"
          icon="description"
        />
      </div>

      <div className="grid lg:grid-cols-3 gap-4 lg:gap-6">
        <Card
          title="Live Patient Queue"
          subtitle="Urgent cases first"
          className="lg:col-span-2"
          action={
            <Link to="/doctor/queue" className="text-sm font-semibold text-brand-700 hover:underline">
              View all
            </Link>
          }
        >
          <QueueTable entries={queue?.queue?.slice(0, 6) ?? []} />
        </Card>

        <Card title="Clinical workload">
          <div className="text-center py-2">
            <div className="relative inline-grid place-items-center">
              <svg width="120" height="120" className="-rotate-90">
                <circle cx="60" cy="60" r="52" fill="none" stroke={chartColors.grid} strokeWidth="12" />
                <circle
                  cx="60"
                  cy="60"
                  r="52"
                  fill="none"
                  stroke={chartColors.brand}
                  strokeWidth="12"
                  strokeLinecap="round"
                  strokeDasharray={`${(data.workload.percent_complete / 100) * 327} 327`}
                />
              </svg>
              <span className="absolute text-2xl font-bold text-ink-900">
                {data.workload.percent_complete}%
              </span>
            </div>
          </div>
          <dl className="space-y-2 mt-3">
            {[
              ["Completed", data.workload.completed],
              ["In progress", data.workload.in_progress],
              ["Remaining", data.workload.remaining],
            ].map(([label, value]) => (
              <div key={label as string} className="flex justify-between text-sm">
                <dt className="text-ink-500">{label}</dt>
                <dd className="font-semibold text-ink-900">{value as number}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- Queue */

export function Queue() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/doctor/queue")
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink-900">Live Patient Queue</h1>
          <p className="text-ink-500">{formatDate(data?.date)}</p>
        </div>
        <div className="flex gap-2 flex-wrap">
          {Object.entries(data?.counts ?? {}).map(([key, value]) => (
            <span key={key} className="sp-chip bg-ink-100 text-ink-700">
              {key.replace(/_/g, " ")}: {value as number}
            </span>
          ))}
        </div>
      </header>

      <Card className="!p-0 overflow-hidden">
        <QueueTable entries={data?.queue ?? []} />
      </Card>
    </div>
  );
}

function QueueTable({ entries }: { entries: any[] }) {
  const sorted = [...entries].sort(
    (a, b) => (URGENCY_RANK[a.urgency] ?? 9) - (URGENCY_RANK[b.urgency] ?? 9),
  );

  return (
    <DataTable
      rows={sorted}
      keyOf={(entry) => entry.appointment_id}
      empty={<Empty title="No patients scheduled" hint="Your queue is clear." icon="queue" />}
      columns={[
        {
          key: "patient",
          header: "Patient",
          primary: true,
          render: (entry) => (
            <div className="flex items-center gap-2.5">
              <Avatar name={entry.patient_name} size={32} />
              <div className="min-w-0">
                <p className="font-semibold text-ink-900 truncate">
                  {entry.patient_name}
                </p>
                <p className="text-xs text-ink-500">
                  {entry.age ?? "—"} · {entry.sex?.[0]?.toUpperCase() ?? "—"}
                </p>
              </div>
            </div>
          ),
        },
        {
          key: "complaint",
          header: "Chief complaint",
          render: (entry) => (
            <span className="block max-w-[240px] truncate">
              {entry.chief_complaint ?? "—"}
            </span>
          ),
        },
        {
          key: "urgency",
          header: "Urgency",
          render: (entry) => <UrgencyBadge urgency={entry.urgency} />,
        },
        {
          key: "time",
          header: "Time",
          render: (entry) => formatTime(entry.scheduled_start),
        },
        {
          key: "status",
          header: "Status",
          render: (entry) => <StatusChip value={entry.status} />,
        },
        {
          key: "action",
          header: "",
          hideOnMobile: false,
          render: (entry) => (
            <Link
              to={`/doctor/patients/${entry.patient_user_id}?appointment=${entry.appointment_id}`}
              className="sp-btn sp-btn-primary sp-btn-sm"
            >
              Open
            </Link>
          ),
        },
      ]}
    />
  );
}

/* -------------------------------------------------- Pre-consultation view */

export function PatientDetail() {
  const { patientId } = useParams();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    api
      .get(`/doctor/patients/${patientId}/pre-consultation`)
      .then((r) => setData(r.data))
      .catch((err) => setError(errorMessage(err, "Could not open this record.")))
      .finally(() => setLoading(false));
  }, [patientId]);

  if (loading) return <Spinner />;
  if (error) return <ErrorNote message={error} />;
  if (!data) return <Empty title="Record unavailable." />;

  const intake = data.structured_intake;
  const redFlags = data.red_flags;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/doctor/queue" className="inline-flex items-center gap-1 text-sm text-brand-700 hover:underline">
            <Icon name="chevronLeft" size={16} />
            Back to queue
          </Link>
          <h1 className="text-2xl font-bold text-ink-900 mt-1">
            {data.patient.name}
          </h1>
          <p className="text-ink-500">
            {data.patient.age} yrs · {data.patient.sex} ·{" "}
            {data.patient.blood_group ?? "Blood group unknown"} ·{" "}
            {data.patient.city}
          </p>
        </div>
        {redFlags?.urgency && <UrgencyBadge urgency={redFlags.urgency} />}
      </header>

      {redFlags?.triggered_rules?.length > 0 && (
        <div className="rounded-2xl border border-danger-border bg-danger-surface p-4">
          <p className="font-bold text-danger-text">Red flags identified</p>
          <ul className="mt-2 space-y-2">
            {redFlags.triggered_rules.map((rule: any) => (
              <li key={rule.rule_id} className="text-sm">
                <p className="font-semibold text-ink-900">
                  {rule.label}{" "}
                  <span className="font-normal text-ink-500">({rule.rule_id})</span>
                </p>
                <p className="text-ink-600">{rule.rationale}</p>
              </li>
            ))}
          </ul>
          <p className="text-xs text-danger-text mt-2">
            Rule engine {redFlags.rule_engine_version} — deterministic, not model output.
          </p>
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-5">
        <Card
          title="Pre-consultation Summary"
          subtitle={
            intake
              ? `AI-compiled from patient self-report · ${formatDateTime(intake.recorded_at)}`
              : undefined
          }
          className="lg:col-span-2"
          action={
            data.original_conversation?.length > 0 && (
              <button
                className="sp-btn sp-btn-secondary sp-btn-sm"
                onClick={() => setShowRaw((previous) => !previous)}
              >
                {showRaw ? "Hide" : "View"} original answers
              </button>
            )
          }
        >
          {!intake ? (
            <Empty title="No structured intake recorded for this patient." />
          ) : (
            <>
              <div className="grid sm:grid-cols-2 gap-4 text-sm">
                <Field label="Chief complaint" value={intake.chief_complaint} />
                <Field label="Duration" value={intake.duration} />
                <Field
                  label="Severity"
                  value={intake.severity ? `${intake.severity}/10` : null}
                />
                <Field label="Symptoms" value={intake.symptoms?.join(", ")} />
                <Field
                  label="Associated symptoms"
                  value={intake.associated_symptoms?.join(", ")}
                />
                <Field
                  label="Relevant history"
                  value={intake.relevant_history?.join(", ")}
                />
                <Field label="Medication" value={intake.medications?.join(", ")} />
                <Field label="Allergies" value={intake.allergies?.join(", ")} />
                <Field
                  label="Explicitly denied"
                  value={intake.negative_findings?.join(", ")}
                />
              </div>
              <AiNotice>
                AI-extracted structure ({intake.extraction_source},{" "}
                {Math.round(intake.extraction_confidence * 100)}% confidence).
                Please review and confirm during consultation — the original
                patient answers are always available.
              </AiNotice>

              {showRaw && (
                <div className="mt-4 rounded-xl bg-ink-50 border border-ink-100 p-4">
                  <p className="text-sm font-semibold text-ink-800 mb-2">
                    Original patient conversation
                  </p>
                  <div className="space-y-2 max-h-72 overflow-y-auto">
                    {data.original_conversation.map((turn: any) => (
                      <div
                        key={turn.sequence}
                        className={`text-sm rounded-lg px-3 py-2 ${
                          turn.role === "patient"
                            ? "bg-brand-50 text-ink-800"
                            : "bg-white text-ink-600 border border-ink-100"
                        }`}
                      >
                        <span className="text-[11px] uppercase tracking-wide text-ink-400">
                          {turn.role}
                        </span>
                        <p>{turn.content}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </Card>

        <div className="space-y-5">
          {data.ai_recommendation && (
            <Card title="AI-suggested routing">
              <p className="text-xs text-ink-500">Specialty</p>
              <p className="font-bold text-ink-900">
                {data.ai_recommendation.specialty_name ??
                  data.ai_recommendation.specialty_code}
              </p>
              <div className="mt-2">
                <Confidence value={data.ai_recommendation.confidence} />
              </div>
              <p className="text-sm text-ink-600 mt-3">
                {data.ai_recommendation.reason}
              </p>
              {data.ai_recommendation.recommended_tests?.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs text-ink-500 mb-1">Suggested tests</p>
                  <ul className="text-sm text-ink-700 space-y-0.5">
                    {data.ai_recommendation.recommended_tests.map((test: any) => (
                      <li key={test.code}>• {test.name}</li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          )}

          <Card title="Background">
            <div className="space-y-3 text-sm">
              <Field
                label="Chronic conditions"
                value={data.profile_history.chronic_conditions?.join(", ")}
              />
              <Field
                label="Allergies"
                value={data.profile_history.allergies?.join(", ")}
              />
              <div>
                <p className="text-xs text-ink-500">Current medication</p>
                {data.current_medications?.length ? (
                  <ul className="mt-1 space-y-1">
                    {data.current_medications.map((medication: any, index: number) => (
                      <li key={index} className="text-ink-900">
                        {medication.name} {medication.dosage}
                        <span className="text-ink-500"> — {medication.frequency}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-ink-900 font-medium">None recorded</p>
                )}
              </div>
            </div>
          </Card>
        </div>
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="Uploaded reports">
          {data.reports?.length ? (
            <div className="space-y-3">
              {data.reports.map((report: any) => (
                <div key={report.id} className="rounded-xl border border-ink-100 p-3.5">
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium text-ink-900">{report.file_name}</p>
                    <span className="sp-chip bg-ink-100 text-ink-700">
                      {report.document_type.replace(/_/g, " ")}
                    </span>
                  </div>
                  <p className="text-xs text-ink-500 mt-0.5">
                    {formatDateTime(report.uploaded_at)}
                  </p>
                  {report.summary && (
                    <p className="text-sm text-ink-600 mt-1.5">{report.summary}</p>
                  )}
                  {report.key_findings?.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {report.key_findings.slice(0, 5).map((finding: any, index: number) => (
                        <span key={index} className="sp-chip sp-chip-warn">
                          {finding.test}: {finding.result}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No reports uploaded" />
          )}
        </Card>

        <Card title="Image screening">
          {data.image_screening?.length ? (
            <div className="space-y-3">
              {data.image_screening.map((image: any) => (
                <div key={image.id} className="rounded-xl border border-ink-100 p-3.5">
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium text-ink-900">{image.finding_label}</p>
                    <Confidence value={image.confidence} />
                  </div>
                  <p className="text-xs text-ink-500 mt-0.5">
                    {image.modality.replace(/_/g, " ")} ·{" "}
                    {formatDateTime(image.uploaded_at)} · {image.model_name}
                  </p>
                  {image.is_uncertain && (
                    <span className="sp-chip sp-chip-warn mt-2">
                      Uncertain result
                    </span>
                  )}
                </div>
              ))}
              <AiNotice>
                AI assistance only. Please correlate clinically with the original
                images.
              </AiNotice>
            </div>
          ) : (
            <Empty title="No image screening" />
          )}
        </Card>
      </div>

      {data.previous_consultations?.length > 0 && (
        <Card title="Previous consultations">
          <div className="space-y-3">
            {data.previous_consultations.map((consultation: any) => (
              <div key={consultation.id} className="rounded-xl border border-ink-100 p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-ink-900">
                    {consultation.specialty ?? "Consultation"}
                  </p>
                  <p className="text-xs text-ink-500">
                    {formatDate(consultation.date)}
                  </p>
                </div>
                {consultation.assessment && (
                  <p className="text-sm text-ink-600 mt-1">{consultation.assessment}</p>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <p className="text-xs text-ink-500">{label}</p>
      <p className="text-ink-900 font-medium mt-0.5">{value || "Not recorded"}</p>
    </div>
  );
}

/* ------------------------------------------------------ Doctor: patients */

export function MyPatients() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/doctor/patients")
      .then((r) => setItems(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">My Patients</h1>
        <p className="text-ink-500">
          Patients booked with you or referred to you.
        </p>
      </header>

      {items.length === 0 ? (
        <Empty title="No patients yet" />
      ) : (
        <Card className="!p-0">
          <div className="divide-y divide-ink-100">
            {items.map((patient) => (
              <Link
                key={patient.user_id}
                to={`/doctor/patients/${patient.user_id}`}
                className="flex items-center gap-4 p-4 hover:bg-ink-50 transition"
              >
                <div className="h-10 w-10 rounded-xl bg-brand-100 text-brand-800 grid place-items-center font-bold text-sm">
                  {patient.name.split(" ").map((p: string) => p[0]).slice(0, 2).join("")}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-ink-900">{patient.name}</p>
                  <p className="text-xs text-ink-500">
                    {patient.age ?? "—"} yrs · {patient.sex ?? "—"}
                  </p>
                </div>
                <p className="text-sm text-ink-500">
                  Last seen {formatDate(patient.last_appointment)}
                </p>
              </Link>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/* -------------------------------------------------- Doctor: appointments */

export function DoctorAppointments() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/appointments", { params: { scope: "upcoming", limit: 100 } })
      .then((r) => setItems(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-ink-900">Upcoming Appointments</h1>
      {items.length === 0 ? (
        <Empty title="No upcoming appointments" />
      ) : (
        <Card className="!p-0 overflow-hidden">
          <div className="sp-table-wrap">
            <table className="w-full text-sm min-w-[620px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-ink-100">
                  <th className="py-2.5 px-4 font-medium">Patient</th>
                  <th className="py-2.5 px-4 font-medium">When</th>
                  <th className="py-2.5 px-4 font-medium">Type</th>
                  <th className="py-2.5 px-4 font-medium">Urgency</th>
                  <th className="py-2.5 px-4 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-50">
                {items.map((appointment) => (
                  <tr key={appointment.id} className="hover:bg-ink-50">
                    <td className="py-3 px-4 font-medium text-ink-900">
                      {appointment.patient?.name ?? "—"}
                    </td>
                    <td className="py-3 px-4 text-ink-700">
                      {formatDateTime(appointment.scheduled_start)}
                    </td>
                    <td className="py-3 px-4 text-ink-600">
                      {appointment.visit_type.replace(/_/g, " ")}
                    </td>
                    <td className="py-3 px-4">
                      <UrgencyBadge urgency={appointment.urgency} />
                    </td>
                    <td className="py-3 px-4">
                      <StatusChip value={appointment.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
