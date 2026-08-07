"""End-to-end verification of the seven required demo scenarios (spec §29).

Runs against a live API. Start the server and seed the database first:

    uvicorn app.main:app --port 8000
    python -m app.seed.seeder --reset
    python tests/test_scenarios.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"
PASSWORD = "Demo@1234"
SAMPLES = Path(__file__).resolve().parents[1] / "storage" / "samples"

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        PASSED.append(label)
        print(f"    PASS  {label}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}" + (f" — {detail}" if detail else ""))
    return condition


def login(client: httpx.Client, email: str) -> dict:
    response = client.post(
        f"{API}/auth/login", json={"email": email, "password": PASSWORD}
    )
    response.raise_for_status()
    return response.json()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def banner(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# --------------------------------------------------------------------------
def scenario_a(client: httpx.Client) -> None:
    banner("SCENARIO A — Symptom navigation to doctor's queue")

    patient = login(client, "patient@suwapath.lk")
    headers = auth(patient["access_token"])
    check("Patient logs in", bool(patient["access_token"]), patient["user"]["full_name"])

    session = client.post(
        f"{API}/symptoms/sessions",
        json={"language": "en", "initial_message": "I have pain in my chest and I feel dizzy"},
        headers=headers,
    ).json()
    session_id = session["session_id"]
    check("Symptom session starts", bool(session_id))
    check(
        "AI asks a follow-up rather than recommending immediately",
        not session.get("is_complete") and bool(session.get("assistant_message")),
        session.get("assistant_message", "")[:70],
    )

    replies = [
        "It started about two hours ago, quite suddenly",
        "The pain is about 8 out of 10 and it spreads to my left arm",
        "Yes I am sweating a lot and feel short of breath",
        "I have high blood pressure and take amlodipine",
        "No known allergies",
        "No other symptoms",
    ]
    result = session
    for reply in replies:
        if result.get("is_complete"):
            break
        result = client.post(
            f"{API}/symptoms/sessions/{session_id}/messages",
            json={"message": reply},
            headers=headers,
        ).json()

    check("Conversation completes and structures intake", bool(result.get("intake")))
    red_flags = result.get("red_flags") or {}
    check(
        "Deterministic engine flags EMERGENCY",
        red_flags.get("urgency") == "emergency",
        f"rules={[r['rule_id'] for r in red_flags.get('triggered_rules', [])]}",
    )

    recommendation = result.get("recommendation") or {}
    check(
        "Specialty recommended with an explanation",
        recommendation.get("specialty_code") == "cardiology" and bool(recommendation.get("reason")),
        recommendation.get("specialty_code", ""),
    )
    trace = [t.get("node") for t in result.get("orchestration_trace", [])]
    check(
        "Orchestration traverses the clinical spine",
        {"red_flag_assessment", "care_navigation"}.issubset(set(trace)),
        " -> ".join(trace),
    )

    recommendation_id = recommendation.get("recommendation_id") or recommendation.get("id")
    doctors = client.get(
        f"{API}/providers/doctors",
        params={"recommendation_id": recommendation_id, "limit": 5},
        headers=headers,
    ).json()
    check("Matching doctors returned", doctors["count"] > 0, f"{doctors['count']} doctors")
    top = doctors["results"][0]
    check("Each result explains why it matched", bool(top["explanation"]), top["explanation"][:80])
    check(
        "Emergency routes to an emergency-capable facility",
        doctors["criteria"]["requires_emergency"],
    )

    hospitals = client.get(
        f"{API}/providers/hospitals",
        params={"recommendation_id": recommendation_id, "limit": 3},
        headers=headers,
    ).json()
    check("Matching hospitals returned", hospitals["count"] > 0, f"{hospitals['count']} facilities")

    # Book the first doctor with an available slot.
    booking = None
    for candidate in doctors["results"]:
        slot = candidate.get("next_available")
        if not slot:
            continue
        response = client.post(
            f"{API}/appointments",
            json={
                "doctor_id": candidate["doctor_id"],
                "scheduled_start": slot["start"],
                "visit_type": "physical",
                "reason": "Chest pain with breathlessness",
                "recommendation_id": recommendation_id,
            },
            headers=headers,
        )
        if response.status_code == 201:
            booking = response.json()
            booked_doctor = candidate
            break

    check("Appointment booked", booking is not None)
    if not booking:
        return

    doctor_user = login(client, "doctor@suwapath.lk")
    doctor_headers = auth(doctor_user["access_token"])

    # The booked doctor may not be the demo doctor, so verify via that doctor's
    # own account where possible; otherwise confirm the appointment is visible
    # to the doctor who owns it.
    queue_visible = False
    summary_ok = False
    appointment_day = booking["scheduled_start"][:10]

    doctor_list = client.get(
        f"{API}/appointments", params={"scope": "upcoming", "limit": 200}, headers=doctor_headers
    )
    # Confirm from the patient side that the booking is queued for the doctor.
    queue = client.get(
        f"{API}/doctor/queue", params={"day": appointment_day}, headers=doctor_headers
    )
    if queue.status_code == 200:
        entries = queue.json()["queue"]
        queue_visible = any(e["appointment_id"] == booking["id"] for e in entries)

    if not queue_visible:
        # Booked with a different doctor: verify the shared-record property by
        # booking the demo doctor directly.
        demo_doctor_id = doctor_user["user"]["doctor_profile"]["id"]
        slots = client.get(
            f"{API}/providers/doctors/{demo_doctor_id}/slots",
            params={"days": 21},
            headers=headers,
        ).json()
        first_slot = next(
            (s for day in slots["days"] for s in day["slots"]), None
        )
        if first_slot:
            second = client.post(
                f"{API}/appointments",
                json={
                    "doctor_id": demo_doctor_id,
                    "scheduled_start": first_slot["start"],
                    "visit_type": "physical",
                    "reason": "Chest pain with breathlessness",
                    "recommendation_id": recommendation_id,
                },
                headers=headers,
            )
            if second.status_code == 201:
                booking = second.json()
                appointment_day = booking["scheduled_start"][:10]
                entries = client.get(
                    f"{API}/doctor/queue",
                    params={"day": appointment_day},
                    headers=doctor_headers,
                ).json()["queue"]
                queue_visible = any(e["appointment_id"] == booking["id"] for e in entries)

    check("Doctor immediately sees the patient in their queue", queue_visible)

    summary = client.get(
        f"{API}/doctor/patients/{patient['user']['id']}/pre-consultation",
        headers=doctor_headers,
    )
    if summary.status_code == 200:
        data = summary.json()
        summary_ok = bool(data.get("structured_intake")) and bool(
            data.get("original_conversation")
        )
        check(
            "Pre-consultation summary available",
            summary_ok,
            f"complaint='{(data.get('structured_intake') or {}).get('chief_complaint')}'",
        )
        check(
            "Original patient answers retained alongside the AI summary",
            len(data.get("original_conversation", [])) > 0,
            f"{len(data.get('original_conversation', []))} raw turns",
        )
        check(
            "Red flags carried into the summary",
            (data.get("red_flags") or {}).get("urgency") == "emergency",
        )
    else:
        check("Pre-consultation summary available", False, summary.text[:100])


def scenario_b(client: httpx.Client) -> None:
    banner("SCENARIO B — Medical report understanding")

    patient = login(client, "patient@suwapath.lk")
    headers = auth(patient["access_token"])

    path = SAMPLES / "cbc_report.pdf"
    if not path.exists():
        check("Sample report exists", False, str(path))
        return

    with path.open("rb") as handle:
        response = client.post(
            f"{API}/documents",
            files={"file": (path.name, handle, "application/pdf")},
            data={"document_type": "lab_report"},
            headers=headers,
            timeout=90,
        )
    check("Report uploads and processes", response.status_code == 201, response.text[:120])
    if response.status_code != 201:
        return

    document = response.json()
    extracted = document["extracted"]
    check("OCR extracts table rows", len(extracted["values"]) >= 5, f"{len(extracted['values'])} rows")

    haemoglobin = next(
        (v for v in extracted["values"] if "haemoglobin" in v["test_name"].lower()), None
    )
    check(
        "Reference range read from the report itself",
        haemoglobin is not None and haemoglobin["reference_source"] == "report",
        f"{haemoglobin['result']} vs {haemoglobin['reference_range']}" if haemoglobin else "",
    )
    check(
        "Low haemoglobin flagged against that range",
        haemoglobin is not None and haemoglobin["flag"] in ("low", "critical"),
    )
    check(
        "Plain-language explanation produced",
        bool(extracted["plain_language_explanation"]),
        extracted["plain_language_explanation"][:90],
    )

    recommendation = document["recommendation"]
    check(
        "Report result feeds care navigation",
        recommendation is not None and bool(recommendation["specialty_code"]),
        recommendation["specialty_code"] if recommendation else "",
    )

    doctors = client.get(
        f"{API}/providers/doctors",
        params={"recommendation_id": recommendation["id"], "limit": 3},
        headers=headers,
    ).json()
    check("Matching doctors reachable from the report", doctors["count"] > 0)


def scenario_c(client: httpx.Client) -> None:
    banner("SCENARIO C — Medical image screening")

    patient = login(client, "patient@suwapath.lk")
    headers = auth(patient["access_token"])

    path = SAMPLES / "chest_xray_pneumonia.png"
    if not path.exists():
        check("Sample chest X-ray exists", False, str(path))
        return

    with path.open("rb") as handle:
        response = client.post(
            f"{API}/images",
            files={"file": (path.name, handle, "image/png")},
            data={"modality": "chest_xray"},
            headers=headers,
            timeout=90,
        )
    check("Image uploads and screens", response.status_code == 201, response.text[:120])
    if response.status_code != 201:
        return

    image = response.json()
    analysis = image["analysis"]
    check("Model returns a finding", bool(analysis["finding_label"]), analysis["finding_label"])
    check("Confidence reported", 0.0 < analysis["confidence"] <= 1.0, str(analysis["confidence"]))
    check("Result labelled as screening support, not diagnosis", bool(analysis["disclaimer"]))
    check("Visual explanation produced", analysis["has_visual_explanation"])

    if analysis["heatmap_url"]:
        heatmap = client.get(f"{BASE}{analysis['heatmap_url']}", headers=headers)
        check("Heatmap is retrievable", heatmap.status_code == 200, f"{len(heatmap.content)} bytes")

    recommendation = image["recommendation"]
    check(
        "Image finding feeds care navigation",
        recommendation is not None
        and recommendation["specialty_code"] == "respiratory_medicine",
        recommendation["specialty_code"] if recommendation else "",
    )

    doctors = client.get(
        f"{API}/providers/doctors",
        params={"recommendation_id": recommendation["id"], "limit": 3},
        headers=headers,
    ).json()
    check("Patient can continue to booking", doctors["count"] > 0, f"{doctors['count']} doctors")

    # Modality validation must reject a non-radiograph.
    bad = SAMPLES / "not_an_xray.png"
    if bad.exists():
        with bad.open("rb") as handle:
            rejected = client.post(
                f"{API}/images",
                files={"file": (bad.name, handle, "image/png")},
                data={"modality": "chest_xray"},
                headers=headers,
                timeout=60,
            )
        check("Non-radiograph rejected by validation", rejected.status_code == 422)


def scenario_d(client: httpx.Client) -> None:
    banner("SCENARIO D — Maternal care and danger-sign escalation")

    mother = login(client, "maternal@suwapath.lk")
    headers = auth(mother["access_token"])

    dashboard = client.get(f"{API}/care/maternal", headers=headers)
    check("Maternal dashboard loads", dashboard.status_code == 200, dashboard.text[:100])
    if dashboard.status_code != 200:
        return
    data = dashboard.json()
    check(
        "Pregnancy week shown",
        data["pregnancy_week"] is not None,
        f"week {data['pregnancy_week']}",
    )

    check_in = client.post(
        f"{API}/care/check-ins",
        json={
            "check_in_type": "maternal",
            "wellbeing": "not_great",
            "responses": {
                "severe_headache": True,
                "blurred_vision": True,
                "vaginal_bleeding": False,
                "reduced_fetal_movement": False,
            },
        },
        headers=headers,
    ).json()

    check("Danger sign triggers an alert", check_in["triggered_alert"])
    check(
        "Escalation is EMERGENCY for pre-eclampsia pattern",
        check_in["urgency"] == "emergency",
        f"rules={[r['rule_id'] for r in check_in['triggered_rules']]}",
    )
    check("Patient receives escalation guidance", bool(check_in["escalation_message"]))

    guardian = login(client, "guardian@suwapath.lk")
    guardian_headers = auth(guardian["access_token"])
    alerts = client.get(
        f"{API}/guardian/alerts",
        params={"patient_user_id": mother["user"]["id"]},
        headers=guardian_headers,
    ).json()
    check(
        "Guardian receives the alert (consent granted for emergency alerts)",
        any(a["alert_type"] == "danger_sign_reported" for a in alerts),
        f"{len(alerts)} alerts",
    )

    # The maternal dependent has NOT granted reports access.
    detail = client.get(
        f"{API}/guardian/dependents/{mother['user']['id']}", headers=guardian_headers
    ).json()
    check(
        "Non-consented section withheld from guardian",
        "reports" in detail["withheld_sections"],
        f"withheld={detail['withheld_sections']}",
    )


def scenario_e(client: httpx.Client) -> None:
    banner("SCENARIO E — Elderly care, missed medication, guardian action")

    elderly = login(client, "elderly@suwapath.lk")
    headers = auth(elderly["access_token"])

    medications = client.get(f"{API}/care/medications", headers=headers).json()
    check("Medication schedule present", len(medications) > 0, f"{len(medications)} medications")

    missed = [m for m in medications if m["consecutive_missed"] >= 2]
    check(
        "Repeated missed doses detected in the data",
        len(missed) > 0,
        f"{missed[0]['name']} x{missed[0]['consecutive_missed']}" if missed else "",
    )

    detection = client.post(f"{API}/care/medications/detect-missed", headers=headers).json()
    check(
        "Pattern detection raises guardian alerts",
        len(detection["alerts"]) > 0,
        f"{detection['alerts']}" if detection["alerts"] else "",
    )

    guardian = login(client, "guardian@suwapath.lk")
    guardian_headers = auth(guardian["access_token"])

    dependents = client.get(f"{API}/guardian/dependents", headers=guardian_headers).json()
    check("Guardian sees dependents", len(dependents) >= 2, f"{len(dependents)} dependents")

    alerts = client.get(
        f"{API}/guardian/alerts",
        params={"patient_user_id": elderly["user"]["id"]},
        headers=guardian_headers,
    ).json()
    check(
        "Guardian receives the missed-medication alert",
        any(a["alert_type"] == "missed_medication_pattern" for a in alerts),
    )

    detail = client.get(
        f"{API}/guardian/dependents/{elderly['user']['id']}", headers=guardian_headers
    ).json()
    check("Guardian opens dependent detail", "medications" in detail)

    # Guardian books a follow-up on the dependent's behalf.
    doctors = client.get(
        f"{API}/providers/doctors",
        params={"specialty_code": "general_medicine", "limit": 5},
        headers=guardian_headers,
    ).json()
    booked = False
    for candidate in doctors.get("results", []):
        slot = candidate.get("next_available")
        if not slot:
            continue
        response = client.post(
            f"{API}/appointments",
            json={
                "doctor_id": candidate["doctor_id"],
                "scheduled_start": slot["start"],
                "visit_type": "physical",
                "reason": "Follow-up: missed blood pressure medication",
                "patient_user_id": elderly["user"]["id"],
            },
            headers=guardian_headers,
        )
        if response.status_code == 201:
            booked = True
            break
    check("Guardian books a follow-up for the dependent", booked)


def scenario_f(client: httpx.Client) -> None:
    banner("SCENARIO F — Confidential sexual health")

    started = client.post(
        f"{API}/confidential/sessions",
        json={"language": "en", "approximate_city": "Colombo",
              "latitude": 6.9271, "longitude": 79.8612},
    ).json()
    session_id = started["session_id"]
    recovery_code = started["recovery_code"]

    check("Private session starts without an account", bool(session_id))
    check("Recovery code issued", bool(recovery_code), recovery_code)
    check("Structured questions provided", len(started["questions"]) >= 5)

    answered = client.post(
        f"{API}/confidential/sessions/{session_id}/answers",
        json={
            "answers": {
                "concern_type": "I had a possible exposure",
                "symptoms": ["Burning when passing urine"],
                "exposure_type": "Vaginal",
                "time_since_exposure": "3-14 days",
                "protection_used": "No",
                "previous_testing": "Never",
                "pregnancy_concern": "No",
            }
        },
    ).json()
    check("Testing guidance produced", bool(answered["testing_guidance"]),
          answered["testing_guidance"][:90])
    check("Tests recommended", len(answered["recommended_tests"]) > 0,
          ", ".join(t["name"] for t in answered["recommended_tests"][:3]))
    check("Specialty suggested", answered["suggested_specialty_code"] == "sexual_health")

    facilities = client.get(f"{API}/confidential/sessions/{session_id}/facilities").json()
    check("Suitable confidential facilities listed", facilities["count"] > 0,
          f"{facilities['count']} facilities")

    resumed = client.post(
        f"{API}/confidential/sessions/resume", json={"recovery_code": recovery_code}
    )
    check("Session resumable with the recovery code", resumed.status_code == 200)

    deleted = client.delete(f"{API}/confidential/sessions/{session_id}").json()
    check("Session deletable", deleted["deleted"])

    gone = client.get(f"{API}/confidential/sessions/{session_id}")
    check("Deleted session no longer retrievable", gone.status_code == 404)

    reuse = client.post(
        f"{API}/confidential/sessions/resume", json={"recovery_code": recovery_code}
    )
    check("Recovery code invalid after deletion", reuse.status_code == 404)


def scenario_g(client: httpx.Client) -> None:
    banner("SCENARIO G — Hospital intelligence")

    admin = login(client, "hospital@suwapath.lk")
    headers = auth(admin["access_token"])

    dashboard = client.get(f"{API}/hospital/dashboard", headers=headers, timeout=120).json()
    kpis = dashboard["kpis"]
    check("Dashboard loads with real seeded data", kpis["appointments_booked"] > 0,
          f"{kpis['appointments_booked']} appointments this month")
    check("No-show rate computed", kpis["no_show_rate"] > 0, f"{kpis['no_show_rate']}%")
    check("Intake volume computed", kpis["total_intakes"] >= 0)
    check("Average intake-to-consult computed",
          kpis["avg_intake_to_consult_days"] is not None,
          f"{kpis['avg_intake_to_consult_days']} days")

    forecast = client.get(
        f"{API}/hospital/forecast", params={"horizon_days": 7}, headers=headers, timeout=120
    ).json()
    check("7-day forecast generated", len(forecast["daily"]) > 0,
          f"{len(forecast['daily'])} specialty-days")

    by_specialty = forecast["by_specialty"]["specialties"]
    check("Specialty demand ranked", len(by_specialty) > 0)
    warnings = forecast["by_specialty"]["warnings"]
    check(
        "High-demand capacity warning appears from seeded capacity",
        len(warnings) > 0,
        "; ".join(
            f"{w['specialty_code']} {w['predicted_total']}>{w['capacity_total']}"
            for w in warnings[:3]
        ),
    )

    no_show = client.get(f"{API}/hospital/no-show", headers=headers, timeout=180).json()
    check("No-show predictions produced", no_show["total_upcoming"] > 0,
          f"{no_show['total_upcoming']} upcoming, bands={no_show['by_risk_band']}")
    check(
        "Risk bands populated",
        no_show["by_risk_band"]["high"] + no_show["by_risk_band"]["medium"] > 0,
    )
    if no_show["top_risk"]:
        check("Risk explained by contributing factors",
              len(no_show["top_risk"][0]["contributing_factors"]) > 0,
              str([f["label"] for f in no_show["top_risk"][0]["contributing_factors"]]))

    capacity = client.get(f"{API}/hospital/capacity", headers=headers, timeout=120).json()
    check("Capacity view available", len(capacity["specialties"]) > 0)


def rbac_checks(client: httpx.Client) -> None:
    banner("RBAC — cross-role access boundaries")

    patient = login(client, "patient@suwapath.lk")
    doctor = login(client, "doctor@suwapath.lk")
    guardian = login(client, "guardian@suwapath.lk")
    hospital_admin = login(client, "hospital@suwapath.lk")

    # Rule 8: doctors cannot browse arbitrary patient records.
    other = client.get(
        f"{API}/doctor/patients/{guardian['user']['id']}/pre-consultation",
        headers=auth(doctor["access_token"]),
    )
    check("Doctor blocked from an unrelated patient record", other.status_code == 403)

    # Rule 9: hospital admins have no clinical conversation access.
    clinical = client.get(
        f"{API}/doctor/queue", headers=auth(hospital_admin["access_token"])
    )
    check("Hospital admin blocked from the clinical queue", clinical.status_code == 403)

    # Patients cannot reach hospital analytics.
    analytics = client.get(
        f"{API}/hospital/dashboard", headers=auth(patient["access_token"])
    )
    check("Patient blocked from hospital analytics", analytics.status_code == 403)

    # Unauthenticated access is rejected.
    anonymous = client.get(f"{API}/patients/me/dashboard")
    check("Unauthenticated request rejected", anonymous.status_code in (401, 403))

    # Guardian cannot read a non-dependent's record.
    not_dependent = client.get(
        f"{API}/guardian/dependents/{patient['user']['id']}",
        headers=auth(guardian["access_token"]),
    )
    check("Guardian blocked from a non-dependent", not_dependent.status_code == 403)


def main() -> int:
    with httpx.Client(timeout=60) as client:
        try:
            client.get(f"{BASE}/health").raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"API not reachable at {BASE}: {exc}")
            return 1

        scenario_a(client)
        scenario_b(client)
        scenario_c(client)
        scenario_d(client)
        scenario_e(client)
        scenario_f(client)
        scenario_g(client)
        rbac_checks(client)

    banner("RESULTS")
    print(f"  Passed: {len(PASSED)}")
    print(f"  Failed: {len(FAILED)}")
    if FAILED:
        print("\n  Failing checks:")
        for label in FAILED:
            print(f"    - {label}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
