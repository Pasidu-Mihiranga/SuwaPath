/**
 * Account pages — Profile and Settings.
 *
 * Shared by all five roles rather than duplicated per role: the editable
 * fields come from `PATCH /auth/me`, which is role-agnostic. Patient-only
 * sections (clinical background, accessibility, guardians) render conditionally, so a
 * doctor or hospital admin sees a clean form without irrelevant medical fields.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Card,
  Chip,
  ErrorNote,
  Icon,
  Notice,
  PageHeader,
  Spinner,
} from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuth, type Role } from "../lib/auth";

const ROLE_LABEL: Record<Role, string> = {
  patient: "Patient",
  guardian: "Guardian",
  doctor: "Doctor",
  hospital_admin: "Hospital Administrator",
  system_admin: "System Administrator",
};

const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "si", label: "සිංහල (Sinhala)" },
  { code: "ta", label: "தமிழ் (Tamil)" },
];

/** Comma-separated text <-> string[], for list fields like allergies. */
function toList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function useProfileForm() {
  const { user, refreshUser } = useAuth();
  const [form, setForm] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .get("/auth/me")
      .then(({ data }) => {
        const p = data.patient_profile ?? {};
        setForm({
          full_name: data.full_name ?? "",
          phone: data.phone ?? "",
          preferred_language: data.preferred_language ?? "en",
          sex: p.sex ?? "",
          city: p.city ?? "",
          district: p.district ?? "",
          blood_group: p.blood_group ?? "",
          chronic_conditions: (p.chronic_conditions ?? []).join(", "),
          allergies: (p.allergies ?? []).join(", "),
          current_medications: (p.current_medications ?? []).join(", "),
          emergency_contact_name: p.emergency_contact_name ?? "",
          emergency_contact_phone: p.emergency_contact_phone ?? "",
          accessibility_large_text: Boolean(p.accessibility_large_text),
        });
      })
      .finally(() => setLoading(false));
  }, []);

  function set(key: string, value: any) {
    setForm((previous) => ({ ...previous, [key]: value }));
    setSaved(false);
  }

  async function save(keys: string[]) {
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, any> = {};
      for (const key of keys) {
        const value = form[key];
        if (value === undefined) continue;
        if (
          key === "chronic_conditions" ||
          key === "allergies" ||
          key === "current_medications"
        ) {
          payload[key] = toList(String(value));
        } else if (key === "sex") {
          payload[key] = value === "" ? null : value;
        } else {
          payload[key] = value;
        }
      }
      await api.patch("/auth/me", payload);
      await refreshUser();
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return { user, form, set, save, loading, saving, error, saved };
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-xs font-semibold text-ink-700 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-ink-500 mt-1">{hint}</span>}
    </label>
  );
}

function SaveBar({
  saving,
  saved,
  onSave,
}: {
  saving: boolean;
  saved: boolean;
  onSave: () => void;
}) {
  return (
    <div className="flex items-center gap-3 pt-1">
      <button className="sp-btn sp-btn-primary" onClick={onSave} disabled={saving}>
        {saving ? "Saving…" : "Save changes"}
      </button>
      {saved && (
        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-ok-text">
          <Icon name="circleCheck" size={16} />
          Saved successfully
        </span>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- Profile */

export function Profile() {
  const { user, form, set, save, loading, saving, error, saved } = useProfileForm();
  const [guardians, setGuardians] = useState<any[]>([]);

  useEffect(() => {
    if (user?.role === "patient") {
      api
        .get("/patients/me/guardians")
        .then(({ data }) => setGuardians(data))
        .catch(() => setGuardians([]));
    }
  }, [user?.role]);

  if (loading || !user) return <Spinner />;

  const isPatient = user.role === "patient";
  const profile = user.patient_profile;

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-12 w-full">
      <PageHeader title="My Profile" subtitle="Your personal, contact, and healthcare background." />

      {/* Identity summary */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4 min-w-0">
            <div className="sp-avatar h-16 w-16 text-xl bg-brand-100 text-brand-800 font-bold shrink-0">
              {user.full_name
                .split(" ")
                .map((part) => part[0])
                .slice(0, 2)
                .join("")}
            </div>
            <div className="min-w-0">
              <p className="text-xl font-bold text-ink-900 truncate">{user.full_name}</p>
              <p className="text-sm text-ink-500 truncate">{user.email}</p>
              <div className="flex flex-wrap gap-1.5 mt-2">
                <Chip tone="info">{ROLE_LABEL[user.role]}</Chip>
                {isPatient && profile?.age != null && (
                  <Chip tone="neutral">{profile.age} yrs</Chip>
                )}
                {user.doctor_profile?.specialty_name && (
                  <Chip tone="programme">{user.doctor_profile.specialty_name}</Chip>
                )}
              </div>
            </div>
          </div>
          <Link to="/patient/settings" className="sp-btn sp-btn-secondary sp-btn-sm shrink-0">
            <Icon name="history" size={15} />
            <span>Account Settings</span>
          </Link>
        </div>
      </Card>

      <ErrorNote message={error} />

      {/* Personal Details */}
      <Card title="Personal details">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Row label="Full name">
            <input
              className="sp-input"
              value={form.full_name}
              onChange={(event) => set("full_name", event.target.value)}
            />
          </Row>
          <Row label="Phone">
            <input
              className="sp-input"
              value={form.phone}
              onChange={(event) => set("phone", event.target.value)}
              placeholder="+94…"
            />
          </Row>
          <Row label="Email" hint="Email address is linked to your login identity.">
            <input className="sp-input bg-ink-50 text-ink-600" value={user.email} disabled />
          </Row>
          <Row label="Preferred language">
            <select
              className="sp-select"
              value={form.preferred_language}
              onChange={(event) => set("preferred_language", event.target.value)}
            >
              {LANGUAGES.map((language) => (
                <option key={language.code} value={language.code}>
                  {language.label}
                </option>
              ))}
            </select>
          </Row>
        </div>
        <div className="mt-5 pt-3 border-t border-ink-100/60">
          <SaveBar
            saving={saving}
            saved={saved}
            onSave={() => save(["full_name", "phone", "preferred_language"])}
          />
        </div>
      </Card>

      {/* Clinical Background (Patient Only) */}
      {isPatient && (
        <>
          <Card
            title="Clinical background"
            subtitle="Shown to clinicians in your pre-consultation summary."
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Row label="Sex" hint="Used for clinical context and tailored health pathways.">
                <select
                  className="sp-select"
                  value={form.sex}
                  onChange={(event) => set("sex", event.target.value)}
                >
                  <option value="">Prefer not to say</option>
                  <option value="female">Female</option>
                  <option value="male">Male</option>
                  <option value="other">Other</option>
                </select>
              </Row>
              <Row label="City / Region">
                <input
                  className="sp-input"
                  value={form.city}
                  onChange={(event) => set("city", event.target.value)}
                  placeholder="e.g. Colombo, Kandy, Galle"
                />
              </Row>
              <Row label="Blood group">
                <input
                  className="sp-input"
                  value={form.blood_group}
                  onChange={(event) => set("blood_group", event.target.value)}
                  placeholder="e.g. O+, A-, B+"
                />
              </Row>
              <Row label="Chronic conditions">
                <input
                  className="sp-input"
                  value={form.chronic_conditions}
                  onChange={(event) => set("chronic_conditions", event.target.value)}
                  placeholder="e.g. Diabetes, Hypertension"
                />
              </Row>
              <Row label="Allergies">
                <input
                  className="sp-input"
                  value={form.allergies}
                  onChange={(event) => set("allergies", event.target.value)}
                  placeholder="e.g. Penicillin, Peanuts"
                />
              </Row>
              <Row label="Current medications">
                <input
                  className="sp-input"
                  value={form.current_medications}
                  onChange={(event) => set("current_medications", event.target.value)}
                  placeholder="e.g. Metformin 500mg, Thyroxine"
                />
              </Row>
            </div>
            <div className="mt-4">
              <Notice tone="info" icon="info">
                Keeping allergies and medications up to date directly improves the accuracy and safety of your care recommendations.
              </Notice>
            </div>
            <div className="mt-5 pt-3 border-t border-ink-100/60">
              <SaveBar
                saving={saving}
                saved={saved}
                onSave={() =>
                  save([
                    "sex",
                    "city",
                    "blood_group",
                    "chronic_conditions",
                    "allergies",
                    "current_medications",
                  ])
                }
              />
            </div>
          </Card>

          {/* Linked Guardians & Family Carers (NEW: Prominently displayed on profile) */}
          <Card
            title="Linked Guardians & Carers"
            subtitle="Family members authorized to receive health updates or manage appointments."
            action={
              <Link
                to="/patient/sharing"
                className="text-xs font-semibold text-brand-700 hover:underline inline-flex items-center gap-1"
              >
                <span>Manage consent</span>
                <Icon name="arrowRight" size={13} />
              </Link>
            }
          >
            {guardians.length > 0 ? (
              <div className="space-y-3">
                {guardians.map((rel: any) => (
                  <div
                    key={rel.relationship_id}
                    className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border border-ink-100 p-3.5 bg-ink-50/40"
                  >
                    <div className="flex items-center gap-3">
                      <span className="sp-icon-tile bg-brand-50 text-brand-700 !h-9 !w-9 shrink-0">
                        <Icon name="privacy" size={18} />
                      </span>
                      <div className="min-w-0">
                        <p className="font-semibold text-sm text-ink-900">
                          {rel.guardian_name ?? rel.guardian_email}
                        </p>
                        <p className="text-xs text-ink-500">
                          {rel.relationship_label} · {rel.granted_permissions.length} active scope(s)
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 self-end sm:self-auto">
                      <Chip tone={rel.is_active ? "ok" : "neutral"}>
                        {rel.is_active ? "Connected" : "Inactive"}
                      </Chip>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-ink-200 p-4 text-center">
                <p className="text-sm font-medium text-ink-700">No guardian currently linked</p>
                <p className="text-xs text-ink-500 mt-1 max-w-md mx-auto">
                  You can grant permission to a family member or caregiver to monitor emergency alerts, check-ins, or manage appointments.
                </p>
                <Link
                  to="/patient/sharing"
                  className="sp-btn sp-btn-secondary sp-btn-sm inline-flex items-center gap-1.5 mt-3"
                >
                  <Icon name="privacy" size={14} />
                  <span>Add Guardian or Carer</span>
                </Link>
              </div>
            )}
          </Card>

          {/* Emergency Contact */}
          <Card title="Emergency contact">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Row label="Contact name">
                <input
                  className="sp-input"
                  value={form.emergency_contact_name}
                  onChange={(event) =>
                    set("emergency_contact_name", event.target.value)
                  }
                  placeholder="e.g. Sunil Fernando (Spouse)"
                />
              </Row>
              <Row label="Contact phone">
                <input
                  className="sp-input"
                  value={form.emergency_contact_phone}
                  onChange={(event) =>
                    set("emergency_contact_phone", event.target.value)
                  }
                  placeholder="+94 77…"
                />
              </Row>
            </div>
            <div className="mt-5 pt-3 border-t border-ink-100/60">
              <SaveBar
                saving={saving}
                saved={saved}
                onSave={() =>
                  save(["emergency_contact_name", "emergency_contact_phone"])
                }
              />
            </div>
          </Card>
        </>
      )}

      {user.doctor_profile && (
        <Card title="Practice details" subtitle="Managed by your hospital administrator.">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            {[
              ["Specialty", user.doctor_profile.specialty_name],
              ["Hospital", user.doctor_profile.hospital_name],
            ].map(([label, value]) => (
              <div key={label as string} className="p-3 bg-ink-50/50 rounded-xl border border-ink-100">
                <p className="text-xs text-ink-500">{label}</p>
                <p className="font-semibold text-ink-900 mt-0.5">{value ?? "—"}</p>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- Settings */

export function Settings() {
  const { user, form, set, save, loading, saving, error, saved } = useProfileForm();
  const [privacy, setPrivacy] = useState<any>(null);

  useEffect(() => {
    api
      .get("/patients/me/guardians")
      .then(({ data }) => setPrivacy(data))
      .catch(() => setPrivacy(null));
  }, []);

  if (loading || !user) return <Spinner />;
  const isPatient = user.role === "patient";

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-12 w-full">
      <PageHeader
        title="Settings"
        subtitle="Language, accessibility and privacy preferences."
      />

      <ErrorNote message={error} />

      <Card title="Language">
        <Row label="Interface and conversation language">
          <select
            className="sp-select"
            value={form.preferred_language}
            onChange={(event) => set("preferred_language", event.target.value)}
          >
            {LANGUAGES.map((language) => (
              <option key={language.code} value={language.code}>
                {language.label}
              </option>
            ))}
          </select>
        </Row>
        <p className="text-xs text-ink-500 mt-2">
          The symptom assistant replies in this language. Clinical warning
          detection works identically in all three.
        </p>
        <div className="mt-5 pt-3 border-t border-ink-100/60">
          <SaveBar
            saving={saving}
            saved={saved}
            onSave={() => save(["preferred_language"])}
          />
        </div>
      </Card>

      {isPatient && (
        <Card title="Accessibility">
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              className="h-5 w-5 mt-0.5 accent-brand-600 rounded"
              checked={Boolean(form.accessibility_large_text)}
              onChange={(event) =>
                set("accessibility_large_text", event.target.checked)
              }
            />
            <span>
              <span className="font-semibold text-ink-900">
                Larger text and buttons
              </span>
              <span className="block text-sm text-ink-500 mt-0.5">
                Increases type size and tap-target size across the whole app.
                Recommended for the elderly care pathway.
              </span>
            </span>
          </label>
          <div className="mt-5 pt-3 border-t border-ink-100/60">
            <SaveBar
              saving={saving}
              saved={saved}
              onSave={() => save(["accessibility_large_text"])}
            />
          </div>
        </Card>
      )}

      {isPatient && (
        <Card
          title="Privacy and sharing"
          subtitle="Who can see your health information."
        >
          {privacy && privacy.length > 0 ? (
            <div className="space-y-2">
              {privacy.map((relationship: any) => (
                <div
                  key={relationship.relationship_id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line p-3"
                >
                  <div>
                    <p className="font-semibold text-ink-900">
                      {relationship.guardian_name}
                    </p>
                    <p className="text-xs text-ink-500">
                      {relationship.relationship_label} ·{" "}
                      {relationship.granted_permissions.length} permission
                      {relationship.granted_permissions.length === 1 ? "" : "s"}
                    </p>
                  </div>
                  <Chip tone={relationship.is_active ? "ok" : "neutral"}>
                    {relationship.is_active ? "Active" : "Revoked"}
                  </Chip>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-ink-500">
              No one else currently has access to your health information.
            </p>
          )}
          <Link to="/patient/sharing" className="sp-btn sp-btn-secondary mt-4 inline-flex items-center gap-1.5">
            <Icon name="privacy" size={16} />
            <span>Manage sharing &amp; consent</span>
          </Link>
        </Card>
      )}

      <Card title="Account">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
          <div className="p-3 bg-ink-50/50 rounded-xl border border-ink-100">
            <p className="text-xs text-ink-500">Signed in as</p>
            <p className="font-semibold text-ink-900 mt-0.5">{user.email}</p>
          </div>
          <div className="p-3 bg-ink-50/50 rounded-xl border border-ink-100">
            <p className="text-xs text-ink-500">Role</p>
            <p className="font-semibold text-ink-900 mt-0.5">{ROLE_LABEL[user.role]}</p>
          </div>
        </div>
        <div className="mt-4">
          <Notice tone="info" icon="info">
            To change your email or password, contact SuwaPath support on 0112 123 456.
          </Notice>
        </div>
      </Card>
    </div>
  );
}
