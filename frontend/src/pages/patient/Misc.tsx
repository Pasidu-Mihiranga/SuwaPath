import { useEffect, useState } from "react";
import {
  Card,
  Empty,
  Icon,
  type IconName,
  ErrorNote,
  Spinner,
  StatusChip,
  UrgencyBadge,
  formatDate,
  formatDateTime,
  relativeDay,
} from "../../components/ui";
import { ProposalInbox } from "../../components/Proposals";
import DoctorAvatar from "../../components/Avatar/DoctorAvatar";
import { api, errorMessage } from "../../lib/api";
import { useAuth } from "../../lib/auth";

/* --------------------------------------------------------- Appointments */

export function Appointments() {
  const [scope, setScope] = useState<"upcoming" | "past">("upcoming");
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    const { data } = await api.get("/appointments", { params: { scope } });
    setItems(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, [scope]);

  async function cancel(id: string) {
    setError(null);
    try {
      await api.patch(`/appointments/${id}/status`, {
        status: "cancelled",
        reason: "Cancelled by patient",
      });
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not cancel that appointment."));
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-ink-900">Appointments</h1>
        <div className="flex gap-1 rounded-xl bg-ink-100 p-1">
          {(["upcoming", "past"] as const).map((key) => (
            <button
              key={key}
              onClick={() => setScope(key)}
              className={`rounded-lg px-4 py-1.5 text-sm font-semibold capitalize transition ${
                scope === key ? "bg-white text-ink-900 shadow-sm" : "text-ink-500"
              }`}
            >
              {key}
            </button>
          ))}
        </div>
      </header>

      <ErrorNote message={error} />

      {loading ? (
        <Spinner />
      ) : items.length === 0 ? (
        <Empty title={`No ${scope} appointments`} />
      ) : (
        <div className="grid gap-3 sm:gap-4 lg:grid-cols-2">
          {items.map((appointment) => (
            <div key={appointment.id} className="sp-card p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-bold text-ink-900">
                    {appointment.doctor?.name}
                  </p>
                  <p className="text-sm text-ink-600">
                    {appointment.doctor?.specialty_name}
                  </p>
                  <p className="text-sm text-ink-500">
                    {appointment.hospital?.name}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1.5">
                  <StatusChip value={appointment.status} />
                  <UrgencyBadge urgency={appointment.urgency} />
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between rounded-xl bg-ink-50 p-3">
                <div>
                  <p className="text-xs text-ink-500">
                    {relativeDay(appointment.scheduled_start)}
                  </p>
                  <p className="font-semibold text-ink-900">
                    {formatDateTime(appointment.scheduled_start)}
                  </p>
                </div>
                <span className="sp-chip bg-brand-100 text-brand-800">
                  {appointment.visit_type === "teleconsultation"
                    ? "Teleconsultation"
                    : "In-person"}
                </span>
              </div>

              {appointment.reason && (
                <p className="mt-3 text-sm text-ink-600">{appointment.reason}</p>
              )}

              {scope === "upcoming" &&
                ["pending", "confirmed"].includes(appointment.status) && (
                  <div className="mt-4 flex gap-2">
                    {appointment.teleconsultation_url && (
                      <a
                        className="sp-btn sp-btn-primary sp-btn-sm flex-1"
                        href={appointment.teleconsultation_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Join teleconsultation
                      </a>
                    )}
                    <button
                      className="sp-btn sp-btn-secondary sp-btn-sm flex-1"
                      onClick={() => void cancel(appointment.id)}
                    >
                      Cancel
                    </button>
                  </div>
                )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- History */

export function History() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/patients/me/history")
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-ink-900">Medical History</h1>

      <Card title="Previous consultations">
        {data?.consultations?.length ? (
          <div className="space-y-3">
            {data.consultations.map((consultation: any) => (
              <div
                key={consultation.id}
                className="rounded-xl border border-ink-100 p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold text-ink-900">
                      {consultation.doctor_name}
                    </p>
                    <p className="text-sm text-ink-500">
                      {consultation.specialty_name} · {formatDate(consultation.date)}
                    </p>
                  </div>
                  <StatusChip value={consultation.status} />
                </div>
                {consultation.presenting_complaint && (
                  <p className="text-sm text-ink-700 mt-2">
                    <span className="text-ink-500">Complaint: </span>
                    {consultation.presenting_complaint}
                  </p>
                )}
                {consultation.assessment && (
                  <p className="text-sm text-ink-700 mt-1">
                    <span className="text-ink-500">Assessment: </span>
                    {consultation.assessment}
                  </p>
                )}
                {consultation.treatment_plan && (
                  <p className="text-sm text-ink-700 mt-1">
                    <span className="text-ink-500">Plan: </span>
                    {consultation.treatment_plan}
                  </p>
                )}
                {consultation.follow_up_required && (
                  <span className="sp-chip sp-chip-programme mt-2">
                    Follow-up {formatDate(consultation.follow_up_date)}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <Empty title="No consultations recorded yet" />
        )}
      </Card>

      <Card title="Symptom checks">
        {data?.symptom_checks?.length ? (
          <div className="divide-y divide-ink-100">
            {data.symptom_checks.map((check: any) => (
              <div key={check.id} className="py-3 flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-ink-900 truncate">
                    {check.chief_complaint}
                  </p>
                  <p className="text-xs text-ink-500">{formatDate(check.date)}</p>
                </div>
                <UrgencyBadge urgency={check.urgency} />
              </div>
            ))}
          </div>
        ) : (
          <Empty title="No symptom checks yet" />
        )}
      </Card>
    </div>
  );
}

/* -------------------------------------------------------------- Sharing */

const PERMISSION_LABELS: Record<string, string> = {
  appointments: "Appointments",
  medications: "Medications",
  reminders: "Reminders",
  wellbeing: "Wellbeing & check-ins",
  care_programme: "Care programme progress",
  emergency_alerts: "Emergency alerts",
  reports: "Medical reports",
  full_medical: "Full medical information",
};

export function Sharing() {
  const [guardians, setGuardians] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  async function load() {
    const { data } = await api.get("/patients/me/guardians");
    setGuardians(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  async function toggle(relationship: any, permission: string) {
    const current: string[] = relationship.granted_permissions;
    const next = current.includes(permission)
      ? current.filter((p) => p !== permission)
      : [...current, permission];

    setSaving(relationship.relationship_id);
    setError(null);
    try {
      await api.put(
        `/patients/me/guardians/${relationship.relationship_id}/permissions`,
        { permissions: next },
      );
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not update sharing settings."));
    } finally {
      setSaving(null);
    }
  }

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold text-ink-900">Sharing & Consent</h1>
        <p className="text-ink-500">
          You decide exactly what each family member or carer can see. Nothing is
          shared unless you turn it on.
        </p>
      </header>

      <ErrorNote message={error} />

      {guardians.length === 0 ? (
        <Empty
          title="No guardians linked"
          hint="You can give a family member limited access to help with your care."
        />
      ) : (
        guardians.map((relationship) => (
          <Card
            key={relationship.relationship_id}
            title={relationship.guardian_name}
            subtitle={`${relationship.relationship_label} · ${relationship.guardian_email}`}
            action={
              relationship.is_active ? (
                <span className="sp-chip sp-chip-ok">Active</span>
              ) : (
                <span className="sp-chip sp-chip-neutral">Revoked</span>
              )
            }
          >
            <div className="grid sm:grid-cols-2 gap-2.5">
              {Object.entries(PERMISSION_LABELS).map(([key, label]) => {
                const granted = relationship.granted_permissions.includes(key);
                return (
                  <button
                    key={key}
                    onClick={() => void toggle(relationship, key)}
                    disabled={saving === relationship.relationship_id}
                    className={`flex items-center justify-between rounded-xl border px-3.5 py-3 text-sm transition ${
                      granted
                        ? "border-brand-400 bg-brand-50 text-brand-900"
                        : "border-ink-200 text-ink-600 hover:border-ink-300"
                    }`}
                  >
                    <span>{label}</span>
                    <span
                      className={`h-5 w-9 rounded-full transition relative ${
                        granted ? "bg-brand-600" : "bg-ink-200"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                          granted ? "left-[18px]" : "left-0.5"
                        }`}
                      />
                    </span>
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-ink-500 mt-3">
              {relationship.can_book_appointments
                ? "This person can book appointments on your behalf."
                : "This person cannot book appointments on your behalf."}
            </p>
          </Card>
        ))
      )}
    </div>
  );
}

/* -------------------------------------------------------- Notifications */

const CATEGORY_ICON: Record<string, IconName> = {
  emergency: "emergency",
  appointment: "calendar",
  medication: "medication",
  care_programme: "favorite",
  report: "description",
  doctor_message: "message",
  hospital_alert: "hospital",
  guardian_alert: "group",
  follow_up: "refresh",
};

export function Notifications() {
  const { user } = useAuth();
  const language = user?.preferred_language ?? "en";
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    const { data } = await api.get("/notifications", { params: { limit: 60 } });
    setData(data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  if (loading) return <Spinner />;

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink-900">Notifications</h1>
          <p className="text-ink-500">{data.unread_count} unread</p>
        </div>
        {data.unread_count > 0 && (
          <button
            className="sp-btn sp-btn-secondary sp-btn-sm"
            onClick={async () => {
              await api.post("/notifications/read-all");
              await load();
            }}
          >
            Mark all read
          </button>
        )}
      </header>

      {/* A message from the care team is the one notification a patient may
          genuinely be unable to read for themselves, and the one where being
          missed matters most. It is lifted out of the list and offered aloud
          rather than sitting as the fourth row of a scrollable log.

          The avatar reads the doctor's own words. Nothing is generated. */}
      {(() => {
        const fromDoctor = (data.notifications ?? []).find(
          (n: any) => n.category === "doctor_message" && !n.is_read,
        );
        if (!fromDoctor) return null;
        const body = [fromDoctor.title, fromDoctor.body].filter(Boolean).join(". ");
        return (
          <DoctorAvatar
            variant="doctor"
            message={body}
            // The notification payload carries no sender name, so the
            // component's neutral heading stands rather than inventing one.
            language={language}
            onDismiss={async () => {
              await api.post(`/notifications/${fromDoctor.id}/read`);
              await load();
            }}
          />
        );
      })()}

      {/* Anything the system prepared on its own sits above the log of things
          that already happened — it is the only part of this page that still
          needs a decision. */}
      <ProposalInbox />

      {data.notifications.length === 0 ? (
        <Empty title="No notifications" />
      ) : (
        <Card className="!p-0">
          <div className="divide-y divide-ink-100">
            {data.notifications.map((notification: any) => (
              <button
                key={notification.id}
                onClick={async () => {
                  if (!notification.is_read) {
                    await api.post(`/notifications/${notification.id}/read`);
                    await load();
                  }
                }}
                className={`w-full text-left flex gap-4 p-4 transition hover:bg-ink-50 ${
                  notification.is_read ? "" : "bg-brand-50/40"
                }`}
              >
                <span className="sp-icon-tile bg-ink-100 text-ink-600 !h-9 !w-9">
                  <Icon
                    name={CATEGORY_ICON[notification.category] ?? "notifications"}
                    size={18}
                  />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-semibold text-ink-900">
                      {notification.title}
                    </p>
                    {!notification.is_read && (
                      <span className="h-2 w-2 rounded-full bg-brand-500 mt-1.5 shrink-0" />
                    )}
                  </div>
                  <p className="text-sm text-ink-600 mt-0.5">{notification.body}</p>
                  <p className="text-xs text-ink-400 mt-1">
                    {formatDateTime(notification.created_at)}
                  </p>
                </div>
                {notification.priority === "critical" && (
                  <span className="sp-chip sp-chip-danger self-start">
                    Critical
                  </span>
                )}
              </button>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
