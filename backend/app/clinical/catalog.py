"""Reference catalogues: specialties, facility capabilities, diagnostic tests,
and the symptom-concept -> specialty mapping used by care navigation.

These are seeded into the database (so a system administrator can edit them)
but are also available in-process so the engines never require a DB round-trip
for a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Facility capabilities
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CapabilityDef:
    code: str
    name: str
    category: str


CAPABILITIES: list[CapabilityDef] = [
    # Clinical services
    CapabilityDef("emergency", "Emergency Department", "clinical"),
    CapabilityDef("icu", "Intensive Care Unit", "clinical"),
    CapabilityDef("cardiology", "Cardiology Service", "clinical"),
    CapabilityDef("dermatology", "Dermatology Service", "clinical"),
    CapabilityDef("obstetrics", "Obstetrics & Maternity", "clinical"),
    CapabilityDef("paediatrics", "Paediatrics", "clinical"),
    CapabilityDef("neurology", "Neurology Service", "clinical"),
    CapabilityDef("orthopaedics", "Orthopaedics", "clinical"),
    CapabilityDef("psychiatry", "Psychiatry & Mental Health", "clinical"),
    CapabilityDef("ophthalmology", "Ophthalmology", "clinical"),
    CapabilityDef("ent", "ENT Service", "clinical"),
    CapabilityDef("respiratory", "Respiratory Medicine", "clinical"),
    CapabilityDef("endocrinology", "Endocrinology", "clinical"),
    CapabilityDef("gastroenterology", "Gastroenterology", "clinical"),
    CapabilityDef("general_surgery", "General Surgery", "clinical"),
    CapabilityDef("sexual_health", "Sexual Health Clinic", "clinical"),
    CapabilityDef("teleconsultation", "Teleconsultation", "clinical"),
    # Imaging
    CapabilityDef("xray", "X-Ray", "imaging"),
    CapabilityDef("chest_xray", "Chest X-Ray", "imaging"),
    CapabilityDef("ct_scan", "CT Scan", "imaging"),
    CapabilityDef("mri", "MRI", "imaging"),
    CapabilityDef("ultrasound", "Ultrasound", "imaging"),
    CapabilityDef("echocardiography", "Echocardiography", "imaging"),
    CapabilityDef("mammography", "Mammography", "imaging"),
    # Diagnostics
    CapabilityDef("laboratory", "Clinical Laboratory", "diagnostic"),
    CapabilityDef("blood_bank", "Blood Bank", "diagnostic"),
    CapabilityDef("microbiology", "Microbiology", "diagnostic"),
    CapabilityDef("histopathology", "Histopathology", "diagnostic"),
    CapabilityDef("skin_biopsy", "Skin Biopsy", "diagnostic"),
    CapabilityDef("ecg", "ECG", "diagnostic"),
    CapabilityDef("sti_testing", "Confidential STI Testing", "diagnostic"),
    CapabilityDef("pharmacy", "Pharmacy", "support"),
]

CAPABILITY_BY_CODE = {c.code: c for c in CAPABILITIES}


# --------------------------------------------------------------------------
# Specialties
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SpecialtyDef:
    code: str
    name: str
    name_si: str
    name_ta: str
    description: str
    capability: str
    sub_specialties: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


SPECIALTIES: list[SpecialtyDef] = [
    SpecialtyDef(
        "general_medicine", "General Medicine", "සාමාන්‍ය වෛද්‍ය", "பொது மருத்துவம்",
        "First-line assessment of undifferentiated adult medical problems.",
        "laboratory",
        ["Internal Medicine", "Infectious Disease", "Geriatric Medicine"],
        ["fever", "fatigue", "general", "checkup", "infection"],
    ),
    SpecialtyDef(
        "cardiology", "Cardiology", "හෘද රෝග", "இதயநோய் மருத்துவம்",
        "Heart and circulatory conditions.", "cardiology",
        ["Interventional Cardiology", "Electrophysiology", "Heart Failure"],
        ["chest pain", "heart", "palpitations", "blood pressure"],
    ),
    SpecialtyDef(
        "dermatology", "Dermatology", "සම් රෝග", "தோல் மருத்துவம்",
        "Skin, hair and nail conditions.", "dermatology",
        ["Dermato-surgery", "Paediatric Dermatology", "Dermato-oncology"],
        ["skin", "rash", "mole", "lesion", "hair", "acne"],
    ),
    SpecialtyDef(
        "paediatrics", "Paediatrics", "ළමා රෝග", "குழந்தை மருத்துவம்",
        "Medical care of infants, children and adolescents.", "paediatrics",
        ["Neonatology", "Paediatric Cardiology", "Paediatric Neurology"],
        ["child", "baby", "infant", "newborn", "vaccination"],
    ),
    SpecialtyDef(
        "obstetrics_gynaecology", "Obstetrics & Gynaecology", "ප්‍රසව හා නාරිවේද",
        "மகப்பேறு மருத்துவம்",
        "Pregnancy, childbirth and women's reproductive health.", "obstetrics",
        ["Maternal-Fetal Medicine", "Reproductive Medicine", "Gynae-oncology"],
        ["pregnancy", "antenatal", "postpartum", "menstrual", "delivery"],
    ),
    SpecialtyDef(
        "orthopaedics", "Orthopaedics", "අස්ථි ශල්‍ය", "எலும்பியல்",
        "Bones, joints, ligaments and musculoskeletal injury.", "orthopaedics",
        ["Sports Medicine", "Spine Surgery", "Joint Replacement"],
        ["fracture", "joint", "bone", "back pain", "sprain", "injury"],
    ),
    SpecialtyDef(
        "neurology", "Neurology", "ස්නායු රෝග", "நரம்பியல்",
        "Brain, spinal cord and nervous system disorders.", "neurology",
        ["Stroke Medicine", "Epilepsy", "Movement Disorders"],
        ["headache", "seizure", "stroke", "numbness", "weakness"],
    ),
    SpecialtyDef(
        "endocrinology", "Endocrinology", "අන්තරාසර්ග", "நாளமில்லா சுரப்பியல்",
        "Hormonal and metabolic conditions including diabetes and thyroid.",
        "endocrinology",
        ["Diabetology", "Thyroid Disorders", "Reproductive Endocrinology"],
        ["thyroid", "diabetes", "hormone", "weight", "fatigue"],
    ),
    SpecialtyDef(
        "ent", "ENT (Otolaryngology)", "කන් නාසය උගුර", "காது மூக்கு தொண்டை",
        "Ear, nose and throat conditions.", "ent",
        ["Otology", "Rhinology", "Head & Neck Surgery"],
        ["ear", "nose", "throat", "hearing", "sinus", "tonsil"],
    ),
    SpecialtyDef(
        "ophthalmology", "Ophthalmology", " නේත්‍ර", "கண் மருத்துவம்",
        "Eye and vision conditions.", "ophthalmology",
        ["Retina", "Cornea", "Glaucoma", "Paediatric Ophthalmology"],
        ["eye", "vision", "sight", "cataract", "glaucoma"],
    ),
    SpecialtyDef(
        "psychiatry", "Psychiatry", "මනෝ වෛද්‍ය", "மனநல மருத்துவம்",
        "Mental health, mood, anxiety and behavioural conditions.", "psychiatry",
        ["Perinatal Psychiatry", "Child & Adolescent", "Addiction Medicine"],
        ["depression", "anxiety", "mental", "sleep", "stress", "mood"],
    ),
    SpecialtyDef(
        "respiratory_medicine", "Respiratory Medicine", "ශ්වසන", "சுவாச மருத்துவம்",
        "Lung and breathing conditions.", "respiratory",
        ["Interventional Pulmonology", "Sleep Medicine", "TB Care"],
        ["cough", "breathing", "asthma", "lung", "pneumonia", "wheeze"],
    ),
    SpecialtyDef(
        "gastroenterology", "Gastroenterology", "ආමාශ", "இரைப்பை குடலியல்",
        "Digestive system, liver and bowel conditions.", "gastroenterology",
        ["Hepatology", "Endoscopy", "Inflammatory Bowel Disease"],
        ["stomach", "abdominal", "liver", "bowel", "digestion"],
    ),
    SpecialtyDef(
        "general_surgery", "General Surgery", "සාමාන්‍ය ශල්‍ය", "பொது அறுவை சிகிச்சை",
        "Surgical assessment and operative management.", "general_surgery",
        ["Laparoscopic Surgery", "Colorectal Surgery", "Breast Surgery"],
        ["surgery", "hernia", "appendix", "lump", "gallbladder"],
    ),
    SpecialtyDef(
        "emergency_medicine", "Emergency Medicine", "හදිසි ප්‍රතිකාර", "அவசர மருத்துவம்",
        "Immediate assessment and stabilisation of acute presentations.",
        "emergency",
        ["Trauma", "Resuscitation", "Toxicology"],
        ["emergency", "accident", "collapse", "urgent", "trauma"],
    ),
    SpecialtyDef(
        "sexual_health", "Sexual Health & GUM", "ලිංගික සෞඛ්‍ය", "பாலியல் சுகாதாரம்",
        "Confidential sexual health assessment, testing and treatment.",
        "sexual_health",
        ["Genitourinary Medicine", "HIV Medicine", "Contraception"],
        ["sti", "std", "sexual", "confidential", "testing", "hiv"],
    ),
    SpecialtyDef(
        "urology", "Urology", "මුත්‍රා", "சிறுநீரகவியல்",
        "Urinary tract and male reproductive conditions.", "general_surgery",
        ["Andrology", "Endourology", "Uro-oncology"],
        ["urine", "kidney", "bladder", "prostate", "urinary"],
    ),
]

SPECIALTY_BY_CODE = {s.code: s for s in SPECIALTIES}


# --------------------------------------------------------------------------
# Concept -> specialty routing
# --------------------------------------------------------------------------
# Each concept maps to (specialty_code, weight). Weights accumulate across the
# concepts present in the intake, and the highest total wins.
CONCEPT_SPECIALTY_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "chest_pain": [("cardiology", 3.0), ("general_medicine", 1.0)],
    "palpitations": [("cardiology", 2.5), ("endocrinology", 0.8)],
    "radiating_pain": [("cardiology", 2.0)],
    "shortness_of_breath": [("respiratory_medicine", 2.5), ("cardiology", 1.5)],
    "cough": [("respiratory_medicine", 2.5), ("general_medicine", 1.0)],
    "coughing_blood": [("respiratory_medicine", 3.0)],
    "wheezing": [("respiratory_medicine", 3.0)],
    "severe_headache": [("neurology", 2.5), ("general_medicine", 1.0)],
    "headache": [("neurology", 1.5), ("general_medicine", 1.2)],
    "seizure": [("neurology", 3.0)],
    "facial_droop": [("neurology", 3.5)],
    "arm_weakness": [("neurology", 3.0)],
    "speech_difficulty": [("neurology", 3.0)],
    "confusion": [("neurology", 1.5), ("general_medicine", 1.5)],
    "dizziness": [("general_medicine", 1.5), ("neurology", 1.2), ("ent", 1.0)],
    "loss_of_consciousness": [("emergency_medicine", 2.5), ("cardiology", 1.5)],
    "neck_stiffness": [("emergency_medicine", 2.0), ("neurology", 1.5)],
    "fever": [("general_medicine", 2.0)],
    "high_fever": [("general_medicine", 2.5)],
    "fatigue": [("general_medicine", 1.8), ("endocrinology", 1.5)],
    "weight_loss": [("general_medicine", 1.5), ("endocrinology", 1.5)],
    "weight_gain": [("endocrinology", 2.0)],
    "cold_intolerance": [("endocrinology", 2.5)],
    "hair_loss": [("dermatology", 2.0), ("endocrinology", 1.5)],
    "excessive_thirst": [("endocrinology", 2.5)],
    "frequent_urination": [("endocrinology", 1.8), ("urology", 1.8)],
    "abdominal_pain": [("gastroenterology", 2.0), ("general_medicine", 1.2)],
    "severe_abdominal_pain": [("general_surgery", 2.5), ("gastroenterology", 1.5)],
    "vomiting": [("gastroenterology", 1.5), ("general_medicine", 1.2)],
    "vomiting_blood": [("gastroenterology", 3.0)],
    "nausea": [("gastroenterology", 1.2), ("general_medicine", 1.0)],
    "diarrhoea": [("gastroenterology", 2.0), ("general_medicine", 1.2)],
    "blood_in_stool": [("gastroenterology", 3.0)],
    "skin_lesion": [("dermatology", 3.5)],
    "rash": [("dermatology", 3.0)],
    "persistent_skin_change": [("dermatology", 2.0)],
    "joint_pain": [("orthopaedics", 2.5)],
    "injury": [("orthopaedics", 2.5), ("emergency_medicine", 1.5)],
    "vaginal_bleeding": [("obstetrics_gynaecology", 3.5)],
    "reduced_fetal_movement": [("obstetrics_gynaecology", 4.0)],
    "leaking_fluid": [("obstetrics_gynaecology", 3.5)],
    "contractions": [("obstetrics_gynaecology", 3.5)],
    "swelling": [("general_medicine", 1.2), ("cardiology", 1.0)],
    "suicidal_ideation": [("psychiatry", 4.0)],
    "low_mood": [("psychiatry", 3.0)],
    "genital_symptoms": [("sexual_health", 4.0), ("dermatology", 1.0)],
    "eye_pain": [("ophthalmology", 3.5)],
    "blurred_vision": [("ophthalmology", 2.5), ("neurology", 1.2)],
    "hearing_loss": [("ent", 3.5)],
    "sore_throat": [("ent", 2.5), ("general_medicine", 1.5)],
    "child_not_feeding": [("paediatrics", 4.0)],
    "child_lethargy": [("paediatrics", 4.0)],
    "night_sweats": [("general_medicine", 1.5), ("respiratory_medicine", 1.2)],
}

# Extra capabilities implied by a concept, beyond those the red-flag rules add.
CONCEPT_CAPABILITIES: dict[str, list[str]] = {
    "cough": ["chest_xray"],
    "coughing_blood": ["chest_xray", "laboratory"],
    "shortness_of_breath": ["chest_xray"],
    "chest_pain": ["ecg"],
    "palpitations": ["ecg", "echocardiography"],
    "skin_lesion": ["skin_biopsy", "histopathology"],
    "persistent_skin_change": ["skin_biopsy"],
    "fatigue": ["laboratory"],
    "weight_loss": ["laboratory"],
    "cold_intolerance": ["laboratory"],
    "injury": ["xray"],
    "joint_pain": ["xray"],
    "vaginal_bleeding": ["ultrasound"],
    "reduced_fetal_movement": ["ultrasound"],
    "abdominal_pain": ["ultrasound"],
    "severe_abdominal_pain": ["ultrasound", "laboratory"],
    "genital_symptoms": ["sti_testing", "laboratory"],
    "excessive_thirst": ["laboratory"],
    "high_fever": ["laboratory"],
}


# --------------------------------------------------------------------------
# Diagnostic test catalogue
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TestDef:
    code: str
    name: str
    category: str
    required_capability: str
    price_lkr: float
    description: str = ""
    preparation: str | None = None


DIAGNOSTIC_TESTS: list[TestDef] = [
    TestDef("cbc", "Full Blood Count (FBC/CBC)", "laboratory", "laboratory", 950,
            "Measures red cells, white cells, haemoglobin and platelets."),
    TestDef("fbs", "Fasting Blood Sugar", "laboratory", "laboratory", 450,
            "Blood glucose after fasting.", "Fast for 8-10 hours before the test."),
    TestDef("hba1c", "HbA1c", "laboratory", "laboratory", 1900,
            "Average blood glucose over the past ~3 months."),
    TestDef("lipid_profile", "Lipid Profile", "laboratory", "laboratory", 1600,
            "Cholesterol and triglyceride levels.", "Fast for 10-12 hours."),
    TestDef("tsh", "Thyroid Stimulating Hormone (TSH)", "laboratory", "laboratory", 1400,
            "Screens thyroid function."),
    TestDef("thyroid_profile", "Thyroid Profile (TSH, FT3, FT4)", "laboratory",
            "laboratory", 3200, "Full assessment of thyroid function."),
    TestDef("lft", "Liver Function Test", "laboratory", "laboratory", 1800,
            "Assesses liver enzymes and function."),
    TestDef("rft", "Renal Function Test", "laboratory", "laboratory", 1700,
            "Assesses kidney function including creatinine."),
    TestDef("crp", "C-Reactive Protein", "laboratory", "laboratory", 1200,
            "Marker of inflammation or infection."),
    TestDef("iron_studies", "Iron Studies", "laboratory", "laboratory", 2600,
            "Serum iron, ferritin and transferrin saturation."),
    TestDef("urine_fr", "Urine Full Report", "laboratory", "laboratory", 550,
            "Screens for urinary infection and kidney issues."),
    TestDef("blood_culture", "Blood Culture", "laboratory", "microbiology", 3400,
            "Identifies bacteria in the bloodstream."),
    TestDef("sti_panel", "Confidential STI Screening Panel", "laboratory",
            "sti_testing", 6500,
            "Screens for common sexually transmitted infections."),
    TestDef("hiv_test", "HIV Screening Test", "laboratory", "sti_testing", 2200,
            "Confidential HIV screening."),
    TestDef("vdrl", "VDRL / Syphilis Screening", "laboratory", "sti_testing", 1500,
            "Screens for syphilis infection."),
    TestDef("chest_xray", "Chest X-Ray", "imaging", "chest_xray", 1800,
            "Images the lungs, heart and chest wall."),
    TestDef("xray_limb", "X-Ray (Limb / Joint)", "imaging", "xray", 1600,
            "Images bones for fracture or joint damage."),
    TestDef("ct_brain", "CT Brain", "imaging", "ct_scan", 14500,
            "Cross-sectional imaging of the brain."),
    TestDef("ct_chest", "CT Chest", "imaging", "ct_scan", 18000,
            "Detailed cross-sectional imaging of the chest."),
    TestDef("mri_spine", "MRI Spine", "imaging", "mri", 32000,
            "Detailed imaging of the spine and discs."),
    TestDef("usg_abdomen", "Ultrasound Abdomen", "imaging", "ultrasound", 3500,
            "Images abdominal organs.", "Fast for 6 hours where possible."),
    TestDef("usg_obstetric", "Obstetric Ultrasound Scan", "imaging", "ultrasound", 4200,
            "Assesses fetal growth, position and wellbeing."),
    TestDef("echo", "2D Echocardiogram", "imaging", "echocardiography", 7500,
            "Ultrasound assessment of heart structure and function."),
    TestDef("ecg", "ECG (Electrocardiogram)", "diagnostic", "ecg", 900,
            "Records the electrical activity of the heart."),
    TestDef("skin_biopsy", "Skin Biopsy", "procedure", "skin_biopsy", 8500,
            "Removes a small skin sample for laboratory analysis."),
    TestDef("histopathology", "Histopathology Reporting", "laboratory",
            "histopathology", 6800, "Microscopic examination of tissue samples."),
    TestDef("mammogram", "Mammogram", "imaging", "mammography", 6200,
            "Breast imaging for screening or assessment."),
]

TEST_BY_CODE = {t.code: t for t in DIAGNOSTIC_TESTS}

# Capability -> the tests it unlocks, used to convert required capabilities into
# concrete recommended tests.
CAPABILITY_TESTS: dict[str, list[str]] = {}
for _test in DIAGNOSTIC_TESTS:
    CAPABILITY_TESTS.setdefault(_test.required_capability, []).append(_test.code)
