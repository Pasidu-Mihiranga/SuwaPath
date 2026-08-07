"""Realistic Sri Lankan reference data for seeding.

All people and institutions here are fictional. Hospital names are invented and
do not refer to, or claim affiliation with, any real Sri Lankan hospital.
Doctor names are common Sinhala/Tamil/Muslim/Burgher name components combined
programmatically, so they read naturally without matching a real practitioner.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Geography — real coordinates so distance ranking behaves realistically
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Location:
    city: str
    district: str
    latitude: float
    longitude: float
    population_weight: float  # relative share of seeded patients


LOCATIONS: list[Location] = [
    Location("Colombo", "Colombo", 6.9271, 79.8612, 0.20),
    Location("Dehiwala", "Colombo", 6.8511, 79.8653, 0.09),
    Location("Nugegoda", "Colombo", 6.8649, 79.8997, 0.09),
    Location("Maharagama", "Colombo", 6.8480, 79.9265, 0.08),
    Location("Kaduwela", "Colombo", 6.9333, 79.9833, 0.06),
    Location("Moratuwa", "Colombo", 6.7730, 79.8816, 0.05),
    Location("Negombo", "Gampaha", 7.2083, 79.8358, 0.07),
    Location("Gampaha", "Gampaha", 7.0917, 79.9999, 0.05),
    Location("Kandy", "Kandy", 7.2906, 80.6337, 0.09),
    Location("Galle", "Galle", 6.0535, 80.2210, 0.07),
    Location("Matara", "Matara", 5.9549, 80.5550, 0.05),
    Location("Kurunegala", "Kurunegala", 7.4863, 80.3623, 0.05),
    Location("Jaffna", "Jaffna", 9.6615, 80.0255, 0.05),
]

LOCATION_BY_CITY = {loc.city: loc for loc in LOCATIONS}


# --------------------------------------------------------------------------
# Names (fictional combinations)
# --------------------------------------------------------------------------
SINHALA_FEMALE_FIRST = [
    "Dilini", "Tharushi", "Iresha", "Nimali", "Sanduni", "Chathurika", "Hiruni",
    "Amaya", "Kaveesha", "Nethmi", "Piumi", "Sewwandi", "Upeksha", "Malsha",
    "Rashmi", "Dulanjali", "Thilini", "Anjali", "Gayani", "Shalini", "Kumudu",
    "Nadeesha", "Imalsha", "Hasini", "Oshadi", "Wathsala", "Erandi", "Nipuni",
]
SINHALA_MALE_FIRST = [
    "Sampath", "Nuwan", "Kasun", "Chamara", "Dinesh", "Ruwan", "Lahiru",
    "Tharindu", "Suranga", "Prabath", "Isuru", "Janaka", "Sahan", "Kavinda",
    "Roshan", "Buddhika", "Dhanushka", "Malinda", "Asanka", "Charith",
    "Gayan", "Nalin", "Pasan", "Sanjeewa", "Thilak", "Udara", "Vimukthi",
]
SINHALA_SURNAMES = [
    "Perera", "Fernando", "De Silva", "Jayawardena", "Jayasinghe", "Bandara",
    "Rajapaksha", "Wickramasinghe", "Gunasekara", "Dissanayake", "Ekanayake",
    "Weerasekara", "Senanayake", "Karunaratne", "Amarasinghe", "Herath",
    "Ranasinghe", "Abeywickrama", "Wijesinghe", "Samarasinghe", "Kumarasiri",
    "Wickramaratne", "Liyanage", "Peiris", "Mendis", "Rathnayake",
]
TAMIL_FEMALE_FIRST = [
    "Priya", "Kavitha", "Nirmala", "Thulasi", "Anusha", "Mathura", "Vaishnavi",
    "Shalini", "Abirami", "Kalaivani", "Nithya", "Sujatha",
]
TAMIL_MALE_FIRST = [
    "Rajan", "Suresh", "Mahendran", "Kumaran", "Thevan", "Arun", "Vimal",
    "Sivakumar", "Bala", "Nirmalan", "Ravindran", "Yogeswaran",
]
TAMIL_SURNAMES = [
    "Rajendran", "Selvarajah", "Thevarajah", "Balasubramaniam", "Sivalingam",
    "Kandasamy", "Vamadevan", "Nadarajah", "Sathyamoorthy", "Kanagaratnam",
]
MUSLIM_FEMALE_FIRST = ["Fathima", "Aisha", "Zahra", "Nadhira", "Rizana", "Safra"]
MUSLIM_MALE_FIRST = ["Mohamed", "Ahamed", "Rizwan", "Faizal", "Naseer", "Imran"]
MUSLIM_SURNAMES = ["Hassan", "Careem", "Rahman", "Ismail", "Marikkar", "Nizam"]

BURGHER_SURNAMES = ["Van Rooyen", "Ludowyke", "Ohlmus", "Toussaint", "Speldewinde"]


# --------------------------------------------------------------------------
# Facilities (fictional)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class HospitalSeed:
    name: str
    city: str
    tier: str  # tertiary | secondary | community | maternity | diagnostic
    has_emergency: bool
    is_24_hours: bool
    beds: int
    icu_beds: int
    consultation_rooms: int
    capabilities: list[str]


# Capability bundles by tier, kept explicit so matching stays realistic.
TERTIARY_CAPS = [
    "emergency", "icu", "cardiology", "dermatology", "obstetrics", "paediatrics",
    "neurology", "orthopaedics", "psychiatry", "ophthalmology", "ent",
    "respiratory", "endocrinology", "gastroenterology", "general_surgery",
    "teleconsultation", "xray", "chest_xray", "ct_scan", "mri", "ultrasound",
    "echocardiography", "laboratory", "blood_bank", "microbiology",
    "histopathology", "skin_biopsy", "ecg", "pharmacy", "mammography",
]
SECONDARY_CAPS = [
    "emergency", "cardiology", "dermatology", "obstetrics", "paediatrics",
    "orthopaedics", "ent", "respiratory", "endocrinology", "general_surgery",
    "teleconsultation", "xray", "chest_xray", "ultrasound", "echocardiography",
    "laboratory", "ecg", "pharmacy", "microbiology",
]
COMMUNITY_CAPS = [
    "emergency", "paediatrics", "obstetrics", "teleconsultation", "xray",
    "chest_xray", "ultrasound", "laboratory", "ecg", "pharmacy",
]
MATERNITY_CAPS = [
    "emergency", "obstetrics", "paediatrics", "icu", "ultrasound", "laboratory",
    "blood_bank", "teleconsultation", "xray", "pharmacy",
]

HOSPITALS: list[HospitalSeed] = [
    HospitalSeed("LankaCare Central Hospital", "Colombo", "tertiary", True, True,
                 620, 48, 38, TERTIARY_CAPS),
    HospitalSeed("Serene Health Medical Centre", "Colombo", "tertiary", True, True,
                 410, 30, 28, TERTIARY_CAPS),
    HospitalSeed("Ceylon Medical Institute", "Colombo", "tertiary", True, True,
                 520, 36, 32, TERTIARY_CAPS),
    HospitalSeed("Muthumala General Hospital", "Dehiwala", "secondary", True, True,
                 240, 14, 18, SECONDARY_CAPS),
    HospitalSeed("Nugegoda Family Hospital", "Nugegoda", "secondary", True, False,
                 180, 8, 15, SECONDARY_CAPS),
    HospitalSeed("Maharagama Wellness Hospital", "Maharagama", "secondary", True, True,
                 210, 12, 16, SECONDARY_CAPS),
    HospitalSeed("Kaduwela Community Hospital", "Kaduwela", "community", True, False,
                 95, 4, 9, COMMUNITY_CAPS),
    HospitalSeed("Moratuwa Bay Hospital", "Moratuwa", "community", True, False,
                 110, 5, 10, COMMUNITY_CAPS),
    HospitalSeed("Negombo Coastal Hospital", "Negombo", "secondary", True, True,
                 195, 10, 14, SECONDARY_CAPS),
    HospitalSeed("Gampaha Mother & Baby Hospital", "Gampaha", "maternity", True, True,
                 130, 8, 11, MATERNITY_CAPS),
    HospitalSeed("HillCity Medical Centre", "Kandy", "tertiary", True, True,
                 380, 24, 26, TERTIARY_CAPS),
    HospitalSeed("Kandy Highland Clinic", "Kandy", "community", False, False,
                 70, 0, 8, COMMUNITY_CAPS),
    HospitalSeed("South Coast Specialist Hospital", "Galle", "tertiary", True, True,
                 300, 20, 22, TERTIARY_CAPS),
    HospitalSeed("Matara Southern Hospital", "Matara", "secondary", True, True,
                 165, 9, 13, SECONDARY_CAPS),
    HospitalSeed("Kurunegala Regional Hospital", "Kurunegala", "secondary", True, True,
                 220, 12, 17, SECONDARY_CAPS),
    HospitalSeed("Northern Star Hospital", "Jaffna", "secondary", True, True,
                 205, 11, 16, SECONDARY_CAPS),
]

DIAGNOSTIC_CAPS_FULL = [
    "laboratory", "microbiology", "xray", "chest_xray", "ultrasound", "ct_scan",
    "ecg", "echocardiography", "histopathology", "skin_biopsy", "mammography",
    "teleconsultation",
]
DIAGNOSTIC_CAPS_LAB = ["laboratory", "microbiology", "ecg", "teleconsultation"]
DIAGNOSTIC_CAPS_IMAGING = [
    "xray", "chest_xray", "ultrasound", "ct_scan", "mri", "echocardiography",
    "laboratory",
]
SEXUAL_HEALTH_CAPS = [
    "sexual_health", "sti_testing", "laboratory", "microbiology", "teleconsultation",
]

DIAGNOSTIC_CENTRES: list[HospitalSeed] = [
    HospitalSeed("PureLab Diagnostics — Colombo", "Colombo", "diagnostic", False, True,
                 0, 0, 6, DIAGNOSTIC_CAPS_FULL),
    HospitalSeed("Metro Imaging Centre", "Colombo", "diagnostic", False, False,
                 0, 0, 4, DIAGNOSTIC_CAPS_IMAGING),
    HospitalSeed("Discreet Care Sexual Health Clinic", "Colombo", "diagnostic", False,
                 False, 0, 0, 5, SEXUAL_HEALTH_CAPS),
    HospitalSeed("Nugegoda Path Labs", "Nugegoda", "diagnostic", False, False,
                 0, 0, 3, DIAGNOSTIC_CAPS_LAB),
    HospitalSeed("Dehiwala Scan Centre", "Dehiwala", "diagnostic", False, False,
                 0, 0, 3, DIAGNOSTIC_CAPS_IMAGING),
    HospitalSeed("Negombo Diagnostic Services", "Negombo", "diagnostic", False, False,
                 0, 0, 3, DIAGNOSTIC_CAPS_FULL),
    HospitalSeed("HillCity Laboratory Services", "Kandy", "diagnostic", False, False,
                 0, 0, 4, DIAGNOSTIC_CAPS_FULL),
    HospitalSeed("Southern Diagnostics — Galle", "Galle", "diagnostic", False, False,
                 0, 0, 3, DIAGNOSTIC_CAPS_FULL),
    HospitalSeed("Matara Health Screening Centre", "Matara", "diagnostic", False, False,
                 0, 0, 2, DIAGNOSTIC_CAPS_LAB),
    HospitalSeed("Northern Diagnostic Hub", "Jaffna", "diagnostic", False, False,
                 0, 0, 3, DIAGNOSTIC_CAPS_FULL),
]


# --------------------------------------------------------------------------
# Doctor distribution: how many of each specialty to create (70 total)
# --------------------------------------------------------------------------
SPECIALTY_DOCTOR_COUNTS: dict[str, int] = {
    "general_medicine": 12,
    "cardiology": 6,
    "dermatology": 5,
    "paediatrics": 6,
    "obstetrics_gynaecology": 7,
    "orthopaedics": 5,
    "neurology": 4,
    "endocrinology": 4,
    "ent": 3,
    "ophthalmology": 3,
    "psychiatry": 3,
    "respiratory_medicine": 4,
    "gastroenterology": 3,
    "general_surgery": 3,
    "emergency_medicine": 1,
    "sexual_health": 1,
}

QUALIFICATIONS = [
    ["MBBS", "MD (Colombo)"],
    ["MBBS", "MD", "MRCP (UK)"],
    ["MBBS", "MS (Surgery)"],
    ["MBBS", "MD", "FRCS (Edin)"],
    ["MBBS", "Dip. in Family Medicine"],
    ["MBBS", "MD", "FCCP"],
    ["MBBS", "MRCOG (UK)"],
    ["MBBS", "MD (Paediatrics)", "MRCPCH"],
]

CHIEF_COMPLAINTS = [
    "Persistent fatigue and dizziness",
    "Chest tightness on exertion",
    "Recurring headaches",
    "Skin rash not responding to cream",
    "Joint pain in both knees",
    "Persistent dry cough",
    "Shortness of breath when climbing stairs",
    "Abdominal pain after meals",
    "Blurred vision in the evenings",
    "Difficulty sleeping and low mood",
    "Sore throat and difficulty swallowing",
    "Follow-up: diabetes management",
    "Follow-up: thyroid review",
    "Antenatal check-up",
    "Postpartum review",
    "High blood pressure review",
    "Child with recurring fever",
    "Ear pain and reduced hearing",
    "Lower back pain after lifting",
    "Unexplained weight loss",
    "Palpitations at rest",
    "Swelling in both ankles",
]

CONSULTATION_ASSESSMENTS = [
    "Symptoms consistent with a benign self-limiting condition. Reassurance provided.",
    "Findings suggest suboptimal control of the existing chronic condition.",
    "Clinical picture consistent with a mild upper respiratory infection.",
    "Examination unremarkable. Baseline investigations requested.",
    "Presentation consistent with musculoskeletal strain.",
    "Requires further imaging before a definitive assessment.",
    "Well-controlled on current therapy. Continue and review.",
    "Referred for specialist opinion given persistent symptoms.",
]
