/**
 * Account pages — Profile and Settings.
 *
 * Shared by all five roles rather than duplicated per role: the editable
 * fields come from `PATCH /auth/me`, which is role-agnostic. Patient-only
 * sections (clinical background, accessibility) render conditionally, so a
 * doctor or hospital admin sees a clean form without irrelevant medical fields.
 */

import { useEffect, useState, type ReactNode } from "react";
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
          // "Prefer not to say" is the empty option; send null rather than ""
          // so the enum validator does not reject it.
          payload[key] = value === "" ? null : value;
        } else {
          payload[key] = value;
        }
      }
      await api.patch("/auth/me", payload);
      await refreshUser();
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err, "Could not save your changes."));
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
    <div>
      <label className="sp-field">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-ink-500">{hint}</p>}
    </div>
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
        <span className="inline-flex items-center gap-1.5 text-sm text-ok-text">
          <Icon name="circleCheck" size={16} />
          Saved
        </span>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- Profile */

export function Profile() {
  const { user, form, set, save, loading, saving, error, saved } = useProfileForm();
  if (loading || !user) return <Spinner />;

  const isPatient = user.role === "patient";
  const profile = user.patient_profile;

  return (
    <div className="space-y-5 max-w-3xl">
      <PageHeader title="My Profile" subtitle="Your account and contact details." />

      {/* Identity summary */}
      <Card>
        <div className="flex flex-wrap items-center gap-4">
          <div className="sp-avatar h-16 w-16 text-xl">
            {user.full_name
              .split(" ")
              .map((part) => part[0])
              .slice(0, 2)
              .join("")}
          </div>
          <div className="min-w-0">
            <p className="text-lg font-bold text-ink-900">{user.full_name}</p>
            <p className="text-sm text-ink-500">{user.email}</p>
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
      </Card>

      <ErrorNote message={error} />

      <Card title="Personal details">
        <div className="grid sm:grid-cols-2 gap-4">
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
          <Row label="Email">
            {/* Email is the login identity — changing it is an auth flow, not
                a profile edit, so it is shown read-only here. */}
            <input className="sp-input" value={user.email} disabled />
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
        <div className="mt-4">
          <SaveBar
            saving={saving}
            saved={saved}
            onSave={() => save(["full_name", "phone", "preferred_language"])}
          />
        </div>
      </Card>

      {isPatient && (
        <>
          <Card
            title="Clinical background"
            subtitle="Shown to clinicians in your pre-consultation summary."
          >
            <div className="grid sm:grid-cols-2 gap-4">
              {/* Optional, and it stays optional. Leaving it unset is a real
                  answer, not missing data — see lib/illustration.ts. */}
              <Row label="Sex" hint="Optional. Used for clinical context and artwork.">
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
              <Row label="City">
                <input
                  className="sp-input"
                  value={form.city}
                  onChange={(event) => set("city", event.target.value)}
                />
              </Row>
              <Row label="Blood group">
                <input
                  className="sp-input"
                  value={form.blood_group}
                  onChange={(event) => set("blood_group", event.target.value)}
                  placeholder="O+"
                />
              </Row>
              <Row label="Chronic conditions">
                <input
                  className="sp-input"
                  value={form.chronic_conditions}
                  onChange={(event) => set("chronic_conditions", event.target.value)}
                  placeholder="Comma separated"
                />
              </Row>
              <Row label="Allergies">
                <input
                  className="sp-input"
                  value={form.allergies}
                  onChange={(event) => set("allergies", event.target.value)}
                  placeholder="Comma separated"
                />
              </Row>
              <Row label="Current medications">
                <input
                  className="sp-input"
                  value={form.current_medications}
                  onChange={(event) => set("current_medications", event.target.value)}
                  placeholder="Comma separated"
                />
              </Row>
            </div>
            <div className="mt-4">
              <Notice tone="info" icon="info">
              Keeping allergies and medications current directly improves the
              safety of every recommendation SuwaPath makes.
              </Notice>
            </div>
            <div className="mt-4">
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

          <Card title="Emergency contact">
            <div className="grid sm:grid-cols-2 gap-4">
              <Row label="Contact name">
                <input
                  className="sp-input"
                  value={form.emergency_contact_name}
                  onChange={(event) =>
                    set("emergency_contact_name", event.target.value)
                  }
                />
              </Row>
              <Row label="Contact phone">
                <input
                  className="sp-input"
                  value={form.emergency_contact_phone}
                  onChange={(event) =>
                    set("emergency_contact_phone", event.target.value)
                  }
                />
              </Row>
            </div>
            <div className="mt-4">
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
          <div className="grid sm:grid-cols-2 gap-4 text-sm">
            {[
              ["Specialty", user.doctor_profile.specialty_name],
              ["Hospital", user.doctor_profile.hospital_name],
            ].map(([label, value]) => (
              <div key={label as string}>
                <p className="text-xs text-ink-500">{label}</p>
                <p className="font-medium text-ink-900">{value ?? "—"}</p>
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
    // Guardians a patient has shared with — the most consequential privacy
    // setting a patient has, so it is surfaced here as well as on its own page.
    api
      .get("/patients/me/guardians")
      .then(({ data }) => setPrivacy(data))
      .catch(() => setPrivacy(null));
  }, []);

  if (loading || !user) return <Spinner />;
  const isPatient = user.role === "patient";

  return (
    <div className="space-y-5 max-w-3xl">
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
        <div className="mt-4">
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
              className="h-5 w-5 mt-0.5 accent-brand-600"
              checked={Boolean(form.accessibility_large_text)}
              onChange={(event) =>
                set("accessibility_large_text", event.target.checked)
              }
            />
            <span>
              <span className="font-medium text-ink-900">
                Larger text and buttons
              </span>
              <span className="block text-sm text-ink-500">
                Increases type size and tap-target size across the whole app.
                Recommended for the elderly care pathway.
              </span>
            </span>
          </label>
          <div className="mt-4">
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
                    <p className="font-medium text-ink-900">
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
          <a href="/patient/sharing" className="sp-btn sp-btn-secondary mt-4">
            <Icon name="lock" size={16} />
            Manage sharing &amp; consent
          </a>
        </Card>
      )}

      <Card title="Account">
        <div className="grid sm:grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-xs text-ink-500">Signed in as</p>
            <p className="font-medium text-ink-900">{user.email}</p>
          </div>
          <div>
            <p className="text-xs text-ink-500">Role</p>
            <p className="font-medium text-ink-900">{ROLE_LABEL[user.role]}</p>
          </div>
        </div>
        <div className="mt-4">
              <Notice tone="info" icon="info">
          To change your email or password, contact SuwaPath support on
          0112 123 456.
          </Notice>
        </div>
      </Card>
    </div>
  );
}
