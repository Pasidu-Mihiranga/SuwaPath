import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Card,
  Empty,
  Icon,
  Spinner,
  StatusChip,
  formatDate,
  formatDateTime,
  relativeDay,
} from "../../components/ui";
import { api } from "../../lib/api";

const TONE: Record<string, string> = {
  urgent: "bg-red-100 text-red-800",
  attention: "bg-orange-100 text-orange-800",
  programme: "bg-purple-100 text-purple-800",
  normal: "bg-green-100 text-green-800",
};

/* ------------------------------------------------------------ Dependents */

export function Dependents() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/guardian/dependents")
      .then((r) => setItems(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">My Dependents</h1>
        <p className="text-ink-500">
          You see only what each person has chosen to share with you.
        </p>
      </header>

      {items.length === 0 ? (
        <Empty
          title="No dependents linked"
          hint="A patient must add you as a guardian from their sharing settings."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {items.map((dependent) => (
            <Link
              key={dependent.patient_user_id}
              to={`/guardian/dependents/${dependent.patient_user_id}`}
              className="sp-card p-5 hover:shadow-lg transition"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="h-12 w-12 rounded-2xl bg-brand-100 text-brand-800 grid place-items-center font-bold">
                    {dependent.name.split(" ").map((p: string) => p[0]).slice(0, 2).join("")}
                  </div>
                  <div>
                    <p className="font-bold text-ink-900">{dependent.name}</p>
                    <p className="text-sm text-ink-500">
                      {dependent.relationship}
                      {dependent.age ? ` · ${dependent.age} yrs` : ""}
                    </p>
                  </div>
                </div>
                {dependent.unread_alerts > 0 && (
                  <span className="sp-chip bg-red-100 text-red-800">
                    {dependent.unread_alerts} alert
                    {dependent.unread_alerts > 1 ? "s" : ""}
                  </span>
                )}
              </div>

              {dependent.care_programme && (
                <span className="sp-chip bg-purple-100 text-purple-800 mt-3">
                  {dependent.care_programme}
                </span>
              )}

              <div className="mt-3 flex items-center justify-between">
                <span className={`sp-chip ${TONE[dependent.status_tone] ?? TONE.normal}`}>
                  {dependent.status_label}
                </span>
                <span className="text-xs text-ink-500">
                  {dependent.granted_permissions.length} permission
                  {dependent.granted_permissions.length === 1 ? "" : "s"}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------- Dependent view */

export function DependentDetail() {
  const { patientId } = useParams();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get(`/guardian/dependents/${patientId}`)
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, [patientId]);

  if (loading) return <Spinner />;
  if (!data) return <Empty title="Could not load this dependent." />;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/guardian" className="text-sm text-brand-700 hover:underline">
            ← All dependents
          </Link>
          <h1 className="text-2xl font-bold text-ink-900 mt-1">{data.name}</h1>
          <p className="text-ink-500">
            {data.relationship}
            {data.age ? ` · ${data.age} yrs` : ""}
            {data.city ? ` · ${data.city}` : ""}
          </p>
        </div>
        {data.can_book_appointments && (
          <span className="sp-chip bg-brand-100 text-brand-800">
            You can book appointments
          </span>
        )}
      </header>

      {/* Consent boundary made explicit rather than silently hiding sections */}
      {data.withheld_sections?.length > 0 && (
        <div className="rounded-2xl border border-ink-200 bg-ink-50 p-4">
          <p className="text-sm font-semibold text-ink-800">
            Some information is not shared with you
          </p>
          <p className="text-sm text-ink-600 mt-1">
            {data.name.split(" ")[0]} has not granted access to:{" "}
            {data.withheld_sections.map((s: string) => s.replace(/_/g, " ")).join(", ")}.
            Only they can change this.
          </p>
        </div>
      )}

      {data.maternal && (
        <Card title="Maternal care">
          <div className="grid sm:grid-cols-3 gap-4">
            <Metric
              label="Pregnancy week"
              value={data.maternal.pregnancy_week ?? "—"}
            />
            <Metric
              label="Expected delivery"
              value={formatDate(data.maternal.expected_delivery_date)}
            />
            <Metric
              label="Risk"
              value={data.maternal.is_high_risk ? "High risk" : "Standard"}
            />
          </div>
        </Card>
      )}

      {data.medications && (
        <Card title="Medications">
          {data.medications.length === 0 ? (
            <Empty title="No active medications" />
          ) : (
            <div className="space-y-3">
              {data.medications.map((medication: any) => (
                <div
                  key={medication.id}
                  className="rounded-xl border border-ink-100 p-3.5 flex flex-wrap items-center justify-between gap-3"
                >
                  <div>
                    <p className="font-semibold text-ink-900">
                      {medication.name} {medication.dosage}
                    </p>
                    <p className="text-sm text-ink-500">
                      {medication.frequency_label}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {medication.consecutive_missed >= 2 && (
                      <span className="sp-chip bg-red-100 text-red-800">
                        {medication.consecutive_missed} missed
                      </span>
                    )}
                    {medication.adherence_percent_14d != null && (
                      <span
                        className={`sp-chip ${
                          medication.adherence_percent_14d >= 80
                            ? "bg-green-100 text-green-800"
                            : "bg-orange-100 text-orange-800"
                        }`}
                      >
                        {medication.adherence_percent_14d}% adherence
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {data.appointments && (
        <Card title="Upcoming appointments">
          {data.appointments.length === 0 ? (
            <Empty title="No upcoming appointments" />
          ) : (
            <div className="space-y-3">
              {data.appointments.map((appointment: any) => (
                <div
                  key={appointment.id}
                  className="rounded-xl border border-ink-100 p-3.5"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold text-ink-900">
                        {appointment.doctor_name}
                      </p>
                      <p className="text-sm text-ink-500">
                        {appointment.specialty_name} · {appointment.hospital_name}
                      </p>
                    </div>
                    <StatusChip value={appointment.status} />
                  </div>
                  <p className="text-sm text-ink-700 mt-2">
                    {relativeDay(appointment.scheduled_start)} ·{" "}
                    {formatDateTime(appointment.scheduled_start)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {data.check_ins && (
        <Card title="Daily check-ins">
          {data.check_ins.length === 0 ? (
            <Empty title="No check-ins recorded" />
          ) : (
            <div className="flex gap-2 overflow-x-auto pb-2">
              {data.check_ins.map((entry: any, index: number) => (
                <div
                  key={index}
                  className={`shrink-0 rounded-xl border p-3 text-center min-w-[96px] ${
                    entry.triggered_alert
                      ? "border-red-300 bg-red-50"
                      : entry.wellbeing === "good"
                        ? "border-green-200 bg-green-50"
                        : "border-ink-200"
                  }`}
                >
                  <Icon
                    name={
                      entry.triggered_alert
                        ? "warning"
                        : entry.wellbeing === "good"
                          ? "circleCheck"
                          : "info"
                    }
                    size={20}
                    className={
                      entry.triggered_alert
                        ? "mx-auto text-danger-text"
                        : entry.wellbeing === "good"
                          ? "mx-auto text-ok-text"
                          : "mx-auto text-ink-400"
                    }
                  />
                  <p className="text-[11px] text-ink-600 mt-1">
                    {formatDate(entry.date)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {data.reports && (
        <Card title="Medical reports">
          {data.reports.length === 0 ? (
            <Empty title="No reports shared" />
          ) : (
            <div className="divide-y divide-ink-100">
              {data.reports.map((report: any) => (
                <div key={report.id} className="py-3">
                  <p className="font-medium text-ink-900">{report.file_name}</p>
                  <p className="text-xs text-ink-500">
                    {formatDateTime(report.uploaded_at)}
                  </p>
                  {report.summary && (
                    <p className="text-sm text-ink-600 mt-1">{report.summary}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: any }) {
  return (
    <div className="rounded-xl bg-ink-50 p-3.5">
      <p className="text-xs text-ink-500">{label}</p>
      <p className="text-lg font-bold text-ink-900 mt-0.5">{value}</p>
    </div>
  );
}

/* ---------------------------------------------------------------- Alerts */

export function Alerts() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    const { data } = await api.get("/guardian/alerts", { params: { limit: 60 } });
    setItems(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Alerts</h1>
        <p className="text-ink-500">
          Raised only for meaningful patterns, and only where consent allows.
        </p>
      </header>

      {items.length === 0 ? (
        <Empty title="No alerts" hint="You'll be notified if something needs attention." />
      ) : (
        <div className="space-y-3">
          {items.map((alert) => (
            <div
              key={alert.id}
              className={`sp-card p-5 border-l-4 ${
                alert.severity === "critical"
                  ? "border-l-red-500"
                  : alert.severity === "attention"
                    ? "border-l-orange-500"
                    : "border-l-brand-500"
              } ${alert.is_acknowledged ? "opacity-60" : ""}`}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="font-bold text-ink-900">{alert.title}</p>
                    <StatusChip value={alert.severity} />
                  </div>
                  <p className="text-sm text-ink-500 mt-0.5">
                    {alert.patient_name} · {formatDateTime(alert.created_at)}
                  </p>
                  <p className="text-sm text-ink-700 mt-2">{alert.detail}</p>
                  <p className="text-xs text-ink-400 mt-2">
                    Shared with you under: {alert.required_permission.replace(/_/g, " ")}
                  </p>
                </div>
                <div className="flex flex-col gap-2 shrink-0">
                  {!alert.is_acknowledged && (
                    <button
                      className="sp-btn sp-btn-secondary sp-btn-sm"
                      onClick={async () => {
                        await api.post(`/guardian/alerts/${alert.id}/acknowledge`);
                        await load();
                      }}
                    >
                      Acknowledge
                    </button>
                  )}
                  <Link
                    to={`/guardian/dependents/${alert.patient_user_id}`}
                    className="sp-btn sp-btn-primary sp-btn-sm"
                  >
                    Open
                  </Link>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
