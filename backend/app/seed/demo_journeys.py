"""Give the demo accounts something to show.

The seeder builds a hospital system: patients, doctors, appointments,
medications, check-ins. What it does not build is the part of a patient's
record that only exists because they *used the product* — a symptom check and
the recommendation it produced, an uploaded lab report with its extracted
values, an X-ray with its screening result.

So a freshly seeded deployment signs a reviewer into the flagship demo account
and shows them six empty cards: no recommendation, no reports, no screenings,
no recent activity. Everything works; nothing has happened yet.

This closes that gap by **using the product**. It signs in over HTTP and does
what a patient would do, so every row it creates is a row the application
itself created, through its own validation, its own OCR, its own red-flag
engine and its own navigation logic.

Writing those rows directly from the seeder was the obvious alternative and is
worse in a specific way: it would let the demo show a recommendation shaped
differently from anything the running system can actually produce, and it
would drift silently the first time the real pipeline changed. Demo data that
the product could not have generated is a lie about the product.

Usage — the API must be running:

    python -m app.seed.demo_journeys
    python -m app.seed.demo_journeys --base-url http://127.0.0.1:8000

Idempotent enough to re-run: it checks whether the account already has a
recommendation and a report, and skips the work if so.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import httpx

from app.core.config import settings

DEFAULT_BASE = "http://127.0.0.1:8000"
PASSWORD = "Demo@1234"
SAMPLES = settings.storage_dir / "samples"

# One conversation each, written so the red-flag engine and the navigator
# reach a real conclusion rather than stalling in history-taking.
JOURNEYS: dict[str, dict] = {
    "patient@suwapath.lk": {
        "symptoms": [
            "I have had a cough for two weeks and it hurts when I breathe in",
            "There is a fever in the evenings and I feel short of breath climbing stairs",
            "No blood in the cough. I have not travelled recently.",
            "I am not taking any medication for it",
        ],
        "report": "cbc_report.pdf",
        "image": "chest_xray_pneumonia.png",
        # The seeder enrols this account in nothing, so its Care Programmes
        # page is empty. Enrol through the API like a patient would, which
        # also exercises the eligibility gate: her profile records no
        # pregnancy, so this is a `confirm` verdict and needs the
        # acknowledgement plus a due date.
        "programme": {
            "programme_code": "maternal_care",
            "acknowledged": True,
            "weeks_pregnant": 19,
            "profile": {"is_pregnant": True},
        },
    },
    "maternal@suwapath.lk": {
        "symptoms": [
            "I am 28 weeks pregnant and my ankles have been swelling",
            "I get headaches in the afternoon and sometimes see spots",
            "No bleeding and the baby is moving normally",
            "My blood pressure was 138 over 88 at the clinic last week",
        ],
        "report": "thyroid_profile.pdf",
        "image": None,
    },
    "elderly@suwapath.lk": {
        "symptoms": [
            "I feel dizzy when I stand up and I am more tired than usual",
            "It has been about ten days. No chest pain.",
            "I take medication for blood pressure and cholesterol",
            "No falls, but I hold the furniture when I walk",
        ],
        "report": "lipid_glucose.pdf",
        "image": None,
    },
}


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def sign_in(client: httpx.Client, email: str) -> dict[str, str] | None:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    if response.status_code != 200:
        log(f"{email}: cannot sign in ({response.status_code}) — is the database seeded?")
        return None
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def already_populated(client: httpx.Client, headers: dict) -> bool:
    dashboard = client.get("/patients/me/dashboard", headers=headers)
    if dashboard.status_code != 200:
        return False
    data = dashboard.json()
    return bool(data.get("current_recommendation")) and bool(data.get("recent_report"))


def run_symptom_check(client: httpx.Client, headers: dict, turns: list[str]) -> bool:
    """Hold a full consultation so the assessment actually completes.

    The first message opens the session; the rest answer the history-taking
    questions. The engine decides when it has enough, so the loop stops on
    `is_complete` rather than sending every scripted line.
    """
    # The history-taking loop asks up to four questions before it assesses.
    # Scripted answers cover the clinically meaningful ones; the fillers stop
    # the session stalling one question short of a conclusion, which is what
    # happened on the first run — a report-derived recommendation appeared but
    # no symptom-derived one did.
    opening, *replies = [*turns, "No other symptoms", "Nothing else to add"]
    response = client.post(
        "/symptoms/sessions",
        json={"language": "en", "initial_message": opening},
        headers=headers,
        timeout=120,
    )
    if response.status_code not in (200, 201):
        log(f"could not start a symptom session ({response.status_code})")
        return False

    result = response.json()
    session_id = result.get("session_id")
    if not session_id:
        log("symptom session returned no id")
        return False

    for reply in replies:
        if result.get("is_complete"):
            break
        turn = client.post(
            f"/symptoms/sessions/{session_id}/messages",
            json={"message": reply},
            headers=headers,
            timeout=120,
        )
        if turn.status_code != 200:
            log(f"symptom turn rejected ({turn.status_code})")
            return False
        result = turn.json()

    return bool(result.get("recommendation") or result.get("is_complete"))


def enrol_programme(client: httpx.Client, headers: dict, spec: dict) -> str | None:
    """Join a care programme, first making the profile say why it applies.

    Order matters. The programme's own eligibility check reads the profile, so
    the profile is corrected first and the enrolment is then a plain
    `eligible` one. Setting `acknowledged` as well is deliberate belt and
    braces: it keeps this working if the demo profile ever changes underneath.
    """
    code = spec["programme_code"]
    existing = client.get("/care/enrollments", headers=headers)
    if existing.status_code == 200:
        if any(e.get("programme_code") == code for e in existing.json()):
            return None

    body = {"programme_code": code, "acknowledged": spec.get("acknowledged", False)}

    weeks = spec.get("weeks_pregnant")
    if weeks is not None:
        # A due date the dashboard can count down from: 40 weeks from
        # conception, so `weeks` behind means 40 − weeks ahead.
        edd = date.today() + timedelta(weeks=40 - weeks)
        body["expected_delivery_date"] = edd.isoformat()

    profile_patch = dict(spec.get("profile") or {})
    if profile_patch:
        if weeks is not None:
            profile_patch.setdefault("expected_delivery_date", body["expected_delivery_date"])
        patched = client.patch("/auth/me", json=profile_patch, headers=headers)
        if patched.status_code != 200:
            log(f"profile update rejected ({patched.status_code}): {patched.text[:100]}")

    response = client.post("/care/enrollments", json=body, headers=headers)
    if response.status_code not in (200, 201):
        log(f"{code} enrolment rejected ({response.status_code}): {response.text[:120]}")
        return None
    return code


def upload(client: httpx.Client, headers: dict, name: str, endpoint: str, data: dict) -> bool:
    path = SAMPLES / name
    if not path.is_file():
        log(f"sample missing: {path}")
        return False
    mime = "application/pdf" if path.suffix == ".pdf" else "image/png"
    with path.open("rb") as handle:
        response = client.post(
            endpoint,
            files={"file": (path.name, handle, mime)},
            data=data,
            headers=headers,
            timeout=180,
        )
    if response.status_code != 201:
        log(f"{name} rejected ({response.status_code}): {response.text[:100]}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument(
        "--force", action="store_true", help="Run even if the account looks populated"
    )
    args = parser.parse_args()

    client = httpx.Client(base_url=f"{args.base_url}/api/v1", timeout=180.0)

    try:
        client.get("/../health", timeout=10)
    except httpx.HTTPError:
        print(
            f"No API at {args.base_url}. Start it first — this script drives the "
            "real endpoints on purpose, so the demo data is data the product made."
        )
        return 1

    print("Building demo journeys")
    for email, journey in JOURNEYS.items():
        headers = sign_in(client, email)
        if headers is None:
            continue

        done = []
        # Enrolment is checked on its own rather than under the populated
        # gate: an account can have reports and still be in no programme,
        # which is exactly the state the seeder leaves this one in.
        if journey.get("programme") and enrol_programme(
            client, headers, journey["programme"]
        ):
            done.append("care programme")

        if not args.force and already_populated(client, headers):
            log(f"{email}: {', '.join(done) if done else 'already populated'}, skipping rest")
            continue

        if run_symptom_check(client, headers, journey["symptoms"]):
            done.append("symptom check")
        if journey["report"] and upload(
            client, headers, journey["report"], "/documents",
            {"document_type": "lab_report"},
        ):
            done.append("report")
        if journey["image"] and upload(
            client, headers, journey["image"], "/images",
            {"modality": "chest_xray"},
        ):
            done.append("screening")

        log(f"{email}: {', '.join(done) if done else 'nothing created'}")

    client.close()
    print("Done. Sign in as any demo account to see a populated dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
