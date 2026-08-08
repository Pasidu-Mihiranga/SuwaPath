"""PHI boundary — what may cross from SuwaPath into a third-party LLM.

Why this module exists
----------------------
Masking alone is not a privacy architecture. Redacting a name to "█████"
either destroys the utility the model needs, or leaks anyway through
quasi-identifiers: a 34-year-old pregnant woman in Nugegoda with a rare
condition is identifiable without her name ever appearing.

So masking is the *last* layer here, not the first. The controls, in order of
how much they actually protect:

  1. Minimisation      the agent never receives a record — only the smallest
                       field set a given route needs (``ROUTE_FIELDS``).
  2. Local-only paths  whole capabilities never call an LLM at all
                       (``LOCAL_ONLY_CAPABILITIES``).
  3. Pseudonymisation  direct identifiers become stable opaque tokens before
                       egress and are rehydrated on the way back, so the model
                       can still reason ("PERSON_A7 reports…") without holding
                       an identity.
  4. Egress guard      a pre-flight scan blocks the call outright if an
                       unexpected identifier survived the steps above.
  5. Audit             every crossing is recorded: route, data classes, byte
                       count, and whether it was blocked.

A deliberate non-goal: this does not try to make a free-tier hosted LLM safe
for real patient data. See ``egress_policy_note()`` — that is a deployment
decision, and the honest answer is documented rather than hidden.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable


class DataClass(StrEnum):
    """How sensitive a field is, which decides how it may cross the boundary."""

    DIRECT_IDENTIFIER = "direct_identifier"   # name, email, phone, NIC
    QUASI_IDENTIFIER = "quasi_identifier"     # DOB, exact address, coordinates
    CLINICAL = "clinical"                     # symptoms, diagnoses, results
    OPERATIONAL = "operational"               # appointment times, specialties
    NON_SENSITIVE = "non_sensitive"           # public catalogue data


# Fields that must never be sent verbatim. Pseudonymised or dropped.
DIRECT_IDENTIFIER_FIELDS = frozenset({
    "full_name", "name", "email", "phone", "patient_name",
    "emergency_contact_name", "emergency_contact_phone",
    "guardian_name", "patient_name_on_report", "slmc_registration_no",
    "nic", "passport_no", "address", "recovery_code",
})

# Fields that re-identify in combination. Coarsened, never sent raw.
QUASI_IDENTIFIER_FIELDS = frozenset({
    "date_of_birth", "latitude", "longitude", "registration_no",
})

# Capabilities that never call a hosted LLM. This is the strongest control in
# the file: the data simply has nowhere to leak to.
#
#   red_flag       urgency is decided by the deterministic rule engine
#   image_analysis CV inference runs locally (ONNX / baseline adapter)
#   ocr_extraction document text is parsed locally by PyMuPDF + Tesseract
#   confidential   sexual-health sessions are structurally isolated (rule 7)
LOCAL_ONLY_CAPABILITIES = frozenset({
    "red_flag", "image_analysis", "ocr_extraction", "confidential",
})

# The minimum field set each agent route is allowed to see. Anything not
# listed is stripped before the prompt is built — an allowlist, so a new
# field added to a model is private by default rather than leaking silently.
ROUTE_FIELDS: dict[str, frozenset[str]] = {
    "consult": frozenset({
        "age_band", "sex", "is_pregnant", "pregnancy_week", "chronic_conditions",
        "allergies", "current_medications", "symptoms", "duration_text",
        "severity", "chief_complaint", "negative_findings",
        # Durable facts the patient told the assistant in earlier
        # conversations. Same class as the fields above — clinical, never
        # identifying — and carried under the same allowlist discipline.
        "remembered",
    }),
    "admin": frozenset({
        "age_band", "city", "preferred_language", "specialty_code",
        "appointment_time", "visit_type", "status", "doctor_name",
        "hospital_name", "urgency",
    }),
    "records": frozenset({
        "age_band", "sex", "report_title", "collection_date", "test_name",
        "result", "unit", "reference_range", "flag", "finding_label",
        "confidence", "modality",
    }),
    "knowledge": frozenset({
        "age_band", "sex", "is_pregnant", "question",
    }),
    # Web search leaves our infrastructure entirely and is logged by a third
    # party indefinitely, so it is the most restricted route of all: the
    # search topic and nothing else.
    "web": frozenset({"question"}),
    "direct": frozenset({
        "age_band", "preferred_language",
    }),
    # Retained so a stale caller using the old route name still gets the same
    # allowlist rather than silently falling back to "direct".
    "clinical": frozenset({
        "age_band", "sex", "is_pregnant", "pregnancy_week", "chronic_conditions",
        "allergies", "current_medications", "symptoms", "duration_text",
        "severity", "chief_complaint", "negative_findings",
        # Durable facts the patient told the assistant in earlier
        # conversations. Same class as the fields above — clinical, never
        # identifying — and carried under the same allowlist discipline.
        "remembered",
    }),
}

# Patterns used by the egress guard. Deliberately broad — a false positive
# blocks a call and logs it, which is the safe direction to fail.
_EGRESS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("phone_lk", re.compile(r"\+?94[\s-]?\d{2}[\s-]?\d{7}|\b0\d{2}[\s-]?\d{7}\b")),
    ("nic_lk", re.compile(r"\b\d{9}[vVxX]\b|\b\d{12}\b")),
    ("coordinates", re.compile(r"\b[-+]?\d{1,2}\.\d{4,}\s*,\s*[-+]?\d{1,3}\.\d{4,}\b")),
    ("recovery_code", re.compile(r"\bSUWA-[A-Z0-9]{4}-[A-Z0-9]{4}\b")),
]


class EgressBlocked(RuntimeError):
    """Raised when content fails the pre-flight scan and must not be sent."""


@dataclass
class EgressReport:
    """What crossed the boundary on one LLM call — the audit unit."""

    route: str
    allowed: bool
    data_classes: list[str] = field(default_factory=list)
    pseudonymised: int = 0
    stripped_fields: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    payload_chars: int = 0

    def to_audit(self) -> dict:
        return {
            "route": self.route,
            "allowed": self.allowed,
            "data_classes": self.data_classes,
            "pseudonymised_count": self.pseudonymised,
            "stripped_fields": self.stripped_fields,
            "violations": self.violations,
            "payload_chars": self.payload_chars,
        }


class Pseudonymiser:
    """Deterministic, reversible-in-process identifier tokenisation.

    Deterministic so the same person is the same token across a conversation
    (the model can reason about "PERSON_A7" consistently), and salted per
    session so tokens cannot be correlated across sessions or users.

    The mapping lives only in memory for the lifetime of one request/session;
    it is never persisted, so a stolen database yields no re-identification.
    """

    def __init__(self, salt: str) -> None:
        self._salt = salt
        self._forward: dict[str, str] = {}
        self._reverse: dict[str, str] = {}

    def token_for(self, value: str, kind: str = "PERSON") -> str:
        value = (value or "").strip()
        if not value:
            return ""
        if value in self._forward:
            return self._forward[value]

        digest = hashlib.sha256(f"{self._salt}:{value}".encode()).hexdigest()
        token = f"{kind}_{digest[:6].upper()}"
        self._forward[value] = token
        self._reverse[token] = value
        return token

    def rehydrate(self, text: str) -> str:
        """Put real values back into a model response before showing the user."""
        for token, value in self._reverse.items():
            text = text.replace(token, value)
        return text

    @property
    def count(self) -> int:
        return len(self._forward)


def age_band(age: int | None) -> str | None:
    """Coarsen an exact age into a band.

    Exact age plus city plus condition is a well-known re-identification
    vector; a band keeps the clinical usefulness (paediatric vs elderly
    thresholds still resolve) while removing the precision.
    """
    if age is None:
        return None
    if age < 1:
        return "infant"
    if age <= 12:
        return "child"
    if age <= 17:
        return "adolescent"
    if age <= 39:
        return "adult_18_39"
    if age <= 64:
        return "adult_40_64"
    return "elderly_65_plus"


class PHIBoundary:
    """The single gate every outbound LLM payload passes through."""

    def __init__(self, *, session_salt: str, route: str) -> None:
        self.route = route
        self.pseudonymiser = Pseudonymiser(session_salt)
        self.report = EgressReport(route=route, allowed=True)

    # -- 1. minimisation ---------------------------------------------------
    def minimise(self, context: dict[str, Any]) -> dict[str, Any]:
        """Keep only the fields this route is allowed to see."""
        allowed = ROUTE_FIELDS.get(self.route, ROUTE_FIELDS["direct"])
        kept: dict[str, Any] = {}

        for key, value in (context or {}).items():
            if key == "age":
                # Never pass exact age; substitute the band.
                banded = age_band(value)
                if banded and "age_band" in allowed:
                    kept["age_band"] = banded
                    self._note(DataClass.QUASI_IDENTIFIER)
                continue

            if key in allowed:
                kept[key] = value
                self._note(_classify(key))
            else:
                self.report.stripped_fields.append(key)

        return kept

    # -- 2. pseudonymisation ----------------------------------------------
    def pseudonymise_text(self, text: str, names: Iterable[str] = ()) -> str:
        """Replace known identifiers in free text with stable tokens."""
        out = text or ""
        for name in names:
            if name and name.strip():
                out = out.replace(name, self.pseudonymiser.token_for(name))
        self.report.pseudonymised = self.pseudonymiser.count
        return out

    # -- 3. egress guard ---------------------------------------------------
    def guard(self, payload: str) -> str:
        """Final pre-flight scan. Raises rather than sending on a hit."""
        self.report.payload_chars = len(payload or "")
        violations = [
            label for label, pattern in _EGRESS_PATTERNS if pattern.search(payload or "")
        ]
        if violations:
            self.report.allowed = False
            self.report.violations = violations
            raise EgressBlocked(
                "Outbound payload contained identifiers that must not leave "
                f"SuwaPath: {', '.join(violations)}."
            )
        return payload

    def rehydrate(self, response: str) -> str:
        return self.pseudonymiser.rehydrate(response or "")

    def _note(self, data_class: DataClass) -> None:
        if str(data_class) not in self.report.data_classes:
            self.report.data_classes.append(str(data_class))


def _classify(field_name: str) -> DataClass:
    if field_name in DIRECT_IDENTIFIER_FIELDS:
        return DataClass.DIRECT_IDENTIFIER
    if field_name in QUASI_IDENTIFIER_FIELDS:
        return DataClass.QUASI_IDENTIFIER
    if field_name in {
        "symptoms", "chief_complaint", "chronic_conditions", "allergies",
        "current_medications", "severity", "flag", "result", "finding_label",
        "negative_findings", "is_pregnant", "pregnancy_week",
    }:
        return DataClass.CLINICAL
    if field_name in {
        "appointment_time", "visit_type", "status", "specialty_code", "urgency",
    }:
        return DataClass.OPERATIONAL
    return DataClass.NON_SENSITIVE


def is_local_only(capability: str) -> bool:
    """True when a capability must never call a hosted model."""
    return capability in LOCAL_ONLY_CAPABILITIES


def egress_policy_note(gemini_free_tier: bool = True) -> dict:
    """The deployment caveat, stated explicitly rather than buried.

    Google's free Gemini tier permits use of submitted content to improve
    their products; the paid tier carries a zero-retention commitment. That
    makes free-tier + real patient data indefensible regardless of how good
    the controls in this module are — the controls reduce what is sent, they
    do not change what the receiver may do with it.

    SuwaPath's demo dataset is entirely synthetic, so this is safe here. The
    note is surfaced in the admin console so it is a conscious decision at
    deployment time rather than an accident.
    """
    return {
        "tier": "free" if gemini_free_tier else "paid",
        "retention": (
            "Content may be used to improve the provider's products."
            if gemini_free_tier
            else "Zero-retention; content is not used for training."
        ),
        "safe_for_real_phi": not gemini_free_tier,
        "current_data": "synthetic",
        "guidance": (
            "Before processing real patient data, move to a zero-retention "
            "paid tier or a self-hosted model. The PHI boundary reduces what "
            "is disclosed; it cannot constrain the receiver."
        ),
    }
