"""Sri Lanka's own disease burden and health system, as patient guidance.

The original corpus was 33 general passages — roughly four pages of text — and
almost none of it was about the things that actually send people to a Sri
Lankan hospital. A patient asking about dengue, leptospirosis, snakebite or
the OPD queue got nothing back, and the assistant answered from the model's
general knowledge with no grounding at all.

Scope and limits, stated plainly:

* This is **patient education**, not clinical protocol. It describes when to
  seek care and what to expect, never how to treat.
* No medicine and no dose appears anywhere in this file, matching the rule the
  rest of the platform follows.
* Content is paraphrased from WHO guidance and Sri Lankan public-health
  material (Epidemiology Unit and Ministry of Health campaigns, the National
  Dengue Control Unit, the Anti-Malaria and Anti-Leprosy campaigns, Suwa
  Seriya). It is written in plain language and deliberately conservative:
  where guidance varies, it points to a clinician rather than choosing.
* **Not clinician-reviewed.** Same caveat as the first-aid scripts, and the
  same fix: one clinician, one afternoon.

Seasonality matters here in a way it does not in the general corpus. Dengue
follows the two monsoons and leptospirosis follows flooding and paddy
cultivation, so several passages name the season — that is genuinely part of
the guidance, not decoration.
"""

from __future__ import annotations

from app.knowledge.corpus import KnowledgeDoc

WHO = "WHO"
MOH = "Sri Lanka Ministry of Health / Epidemiology Unit"

CORPUS_LK: list[KnowledgeDoc] = [
    # ---------------- Dengue: the single biggest seasonal burden ----------
    KnowledgeDoc(
        "lk-den-001", "Dengue fever: what it looks like", "dengue", "patient",
        "Dengue is spread by Aedes mosquitoes, which bite mostly in the early "
        "morning and late afternoon and breed in small collections of clean "
        "water around the house. It usually begins with sudden high fever, "
        "severe headache, pain behind the eyes, and marked muscle and joint "
        "pain, often with a rash after a few days. Most people recover at "
        "home with rest and plenty of fluids, but anyone with fever during a "
        "dengue season should have a blood count checked, because the illness "
        "is monitored by how the platelet count and haematocrit change over "
        "several days rather than by how bad the fever feels.",
        f"{MOH} / National Dengue Control Unit",
    ),
    KnowledgeDoc(
        "lk-den-002", "Dengue warning signs — go to hospital now", "dengue", "patient",
        "The dangerous phase of dengue often begins as the fever falls, "
        "usually between the third and seventh day, which is exactly when "
        "people assume they are getting better. Go to a hospital immediately "
        "if there is severe abdominal pain, vomiting that will not stop, "
        "bleeding from the gums or nose, blood in vomit or stool, black "
        "tarry stools, restlessness or unusual drowsiness, cold clammy skin, "
        "or a drop in the amount of urine passed. These are warning signs of "
        "plasma leakage and they need hospital assessment the same hour, not "
        "the next day.",
        f"{WHO} dengue guidelines / {MOH}",
    ),
    KnowledgeDoc(
        "lk-den-003", "Preventing dengue around the house", "dengue", "patient",
        "Aedes mosquitoes breed in clean water close to homes, so control is "
        "mostly about removing standing water rather than spraying. Once a "
        "week, empty and scrub water storage containers, discard or cover "
        "tyres, coconut shells, tins and plastic containers, clear roof "
        "gutters and check plant pot trays, refrigerator drip trays and "
        "ornamental water features. Mosquito breeding inspections are carried "
        "out by public health inspectors and premises with breeding sites can "
        "be issued notices. Use repellent and sleep under a net if someone in "
        "the house already has dengue, which stops the household becoming a "
        "source of further cases.",
        f"{MOH} / National Dengue Control Unit",
    ),

    # ---------------- Leptospirosis ---------------------------------------
    KnowledgeDoc(
        "lk-lep-001", "Leptospirosis (rat fever) after floods or paddy work",
        "leptospirosis", "patient",
        "Leptospirosis spreads through water or mud contaminated with the "
        "urine of rats and cattle, entering through cuts, broken skin or the "
        "eyes and mouth. In Sri Lanka it follows flooding and the paddy "
        "cultivation seasons, and it is an occupational risk for farmers, "
        "cleaners, fishermen and anyone wading through floodwater. It begins "
        "with sudden fever, severe headache, and characteristically severe "
        "muscle pain, especially in the calves, often with red eyes. Tell the "
        "doctor about any recent contact with floodwater, canals or paddy "
        "fields — it changes what they test for, and untreated leptospirosis "
        "can damage the kidneys and liver within days.",
        f"{MOH} / {WHO}",
    ),

    # ---------------- Snakebite -------------------------------------------
    KnowledgeDoc(
        "lk-snb-001", "Snakebite: what to do and what never to do",
        "snakebite", "patient",
        "Snakebite is a genuine rural emergency in Sri Lanka, and the "
        "difference between outcomes is usually how fast someone reaches a "
        "hospital with antivenom. Keep the person still and calm, because "
        "movement moves venom faster. Immobilise the bitten limb with a splint "
        "as you would a fracture, keep it at or below heart level, remove "
        "rings and bangles before swelling starts, and get to hospital "
        "immediately — call 1990 Suwa Seriya. Do not cut the wound, do not "
        "suck the venom, do not apply a tight tourniquet, do not wash the "
        "site vigorously, and do not waste time looking for a traditional "
        "healer first. If the snake can be photographed safely from a "
        "distance it helps identification, but never try to catch or kill it.",
        f"{WHO} snakebite envenoming guidance / {MOH}",
    ),

    # ---------------- Rabies ----------------------------------------------
    KnowledgeDoc(
        "lk-rab-001", "Dog and animal bites: rabies prevention", "rabies", "patient",
        "Rabies is present in Sri Lanka and is effectively always fatal once "
        "symptoms begin, but it is completely preventable if treatment starts "
        "in time. After any bite, scratch or lick on broken skin from a dog, "
        "cat, monkey, bat or mongoose, wash the wound with soap under running "
        "water for a full fifteen minutes — this alone removes a large part of "
        "the virus — then go to the nearest hospital the same day for "
        "post-exposure treatment. Do not wait to see whether the animal falls "
        "ill, and do not close the wound with stitches before it has been "
        "assessed. Treatment is available free at government hospitals.",
        f"{WHO} rabies guidance / {MOH}",
    ),

    # ---------------- Non-communicable disease ----------------------------
    KnowledgeDoc(
        "lk-ncd-001", "Diabetes: why screening matters before symptoms",
        "endocrine", "patient",
        "Diabetes is common in Sri Lanka and often silent for years before it "
        "is found. Symptoms worth acting on are passing urine frequently "
        "including at night, being unusually thirsty, losing weight without "
        "trying, tiredness, blurred vision, and cuts or infections that heal "
        "slowly. Because damage to the eyes, kidneys, nerves and heart begins "
        "before symptoms appear, screening is recommended from around age 35, "
        "or earlier for anyone with a parent or sibling with diabetes, a high "
        "body weight, high blood pressure, or a history of diabetes during "
        "pregnancy. A fasting blood sugar or HbA1c test at any government "
        "clinic is enough to start.",
        f"{WHO} / {MOH} NCD guidance",
    ),
    KnowledgeDoc(
        "lk-ncd-002", "High blood pressure: the silent one", "cardiac", "patient",
        "High blood pressure usually causes no symptoms at all, which is why "
        "it is found at a clinic rather than felt at home. It raises the risk "
        "of stroke, heart attack and kidney disease over years. Adults should "
        "have it checked at least once a year, and more often with a family "
        "history, excess weight, or a diet high in salt — including dried "
        "fish, salted snacks and processed food. Occasional headaches are not "
        "a reliable sign of blood pressure, so it should not be judged by how "
        "you feel. Sudden severe headache with visual change, weakness or "
        "confusion is a different matter and needs emergency assessment.",
        f"{WHO} hypertension guidance / {MOH}",
    ),
    KnowledgeDoc(
        "lk-ckd-001", "Chronic kidney disease and CKDu", "renal", "patient",
        "Chronic kidney disease develops slowly and is often silent until it "
        "is advanced. Sri Lanka also has a distinct form, chronic kidney "
        "disease of uncertain aetiology (CKDu), concentrated among farming "
        "communities in the North Central, North Western, Uva and Eastern "
        "provinces and not explained by diabetes or blood pressure. Anyone "
        "farming in those areas, and anyone with diabetes or high blood "
        "pressure anywhere, should have kidney function and urine protein "
        "checked periodically even while feeling well. Staying properly "
        "hydrated during field work, avoiding unnecessary painkillers, and "
        "attending screening clinics are the practical steps that matter.",
        f"{MOH} / {WHO}",
    ),

    # ---------------- Tuberculosis ----------------------------------------
    KnowledgeDoc(
        "lk-tb-001", "A cough lasting more than two weeks", "respiratory", "patient",
        "A cough that has lasted more than two weeks should be investigated "
        "for tuberculosis, particularly alongside fever in the evenings, night "
        "sweats, loss of appetite, weight loss, or coughing blood. TB is "
        "curable, and diagnosis and treatment are free at chest clinics under "
        "the National Programme for Tuberculosis Control. Treatment must be "
        "completed in full even after the cough settles, because stopping "
        "early is what produces drug-resistant disease. Household contacts, "
        "especially young children, should also be screened.",
        f"{WHO} / National Programme for Tuberculosis Control, Sri Lanka",
    ),

    # ---------------- Maternal --------------------------------------------
    KnowledgeDoc(
        "lk-mat-001", "The pregnancy record book and the field midwife",
        "maternal", "patient",
        "Antenatal care in Sri Lanka is delivered largely through the field "
        "Public Health Midwife (PHM) attached to the local MOH area, together "
        "with clinics at the nearest hospital. Every pregnant woman is given a "
        "pregnancy record book, which should be carried to every visit and to "
        "any hospital admission — it holds the scan dates, blood pressure "
        "readings, blood group and risk assessments a doctor needs quickly in "
        "an emergency. Registration with the PHM in the first trimester is "
        "what triggers home visits, iron and folate supply, and the schedule "
        "of clinic appointments. Care through this route is free.",
        f"{MOH} maternal and child health programme",
    ),
    KnowledgeDoc(
        "lk-mat-002", "Danger signs in pregnancy", "maternal", "patient",
        "Go to a hospital with maternity services immediately, at any stage of "
        "pregnancy, for bleeding from the vagina, severe or persistent "
        "headache, blurred vision or flashing lights, swelling of the face and "
        "hands appearing quickly, severe pain in the upper abdomen, fluid "
        "leaking from the vagina before labour, a noticeable reduction in the "
        "baby's movements, fever, or convulsions. Several of these together "
        "can indicate pre-eclampsia, which can worsen quickly for both mother "
        "and baby. Do not wait for the next clinic appointment, and take the "
        "pregnancy record book.",
        f"{WHO} antenatal care recommendations / {MOH}",
    ),

    # ---------------- Mental health ---------------------------------------
    KnowledgeDoc(
        "lk-mh-001", "Getting help for low mood or thoughts of self-harm",
        "mental_health", "patient",
        "Persistent low mood, loss of interest in things that used to matter, "
        "disturbed sleep and appetite, hopelessness, or thoughts of harming "
        "yourself are medical problems and they respond to treatment. Help is "
        "available and free: the National Mental Health Helpline is 1926, "
        "Sumithrayo offers confidential emotional support, and every base and "
        "teaching hospital has a psychiatry unit that can be reached through "
        "the OPD without a referral. If someone is in immediate danger, call "
        "1990 Suwa Seriya. Pesticide and medicine ingestion is a leading "
        "method of self-harm in Sri Lanka, so restricting access to these in "
        "the home is a concrete protective step for a household under strain.",
        f"{WHO} mental health guidance / {MOH}",
    ),

    # ---------------- Elderly ---------------------------------------------
    KnowledgeDoc(
        "lk-eld-001", "Falls in older adults", "elderly", "patient",
        "A fall in an older adult is worth a medical assessment even when "
        "nothing appears broken, because it often signals something treatable "
        "underneath — a drop in blood pressure on standing, an infection, poor "
        "vision, or a medicine that is no longer suiting them. Repeated falls "
        "carry a high risk of hip fracture and head injury. Practical "
        "reductions are good lighting on stairs and to the toilet at night, "
        "removing loose mats and cables, a non-slip mat in the bathroom, "
        "grab rails, well-fitting footwear, and an annual eye check. Any fall "
        "with a head strike, a blood-thinning medicine, confusion afterwards, "
        "or inability to bear weight needs the same-day emergency assessment.",
        f"{WHO} integrated care for older people / {MOH}",
    ),
    KnowledgeDoc(
        "lk-eld-002", "New confusion in an older person is an emergency sign",
        "elderly", "patient",
        "Confusion that comes on over hours or a few days in an older adult is "
        "not simply ageing or dementia progressing. It is usually delirium, "
        "and it usually has a treatable cause — most often a urinary or chest "
        "infection, dehydration, constipation, pain, or a recently changed "
        "medicine. It needs assessment the same day. Bring a list of every "
        "medicine being taken, including anything bought over the counter or "
        "from a traditional practitioner, because that list is frequently "
        "where the answer is.",
        f"{WHO} integrated care for older people",
    ),

    # ---------------- Navigating the health system ------------------------
    KnowledgeDoc(
        "lk-nav-001", "Government OPD, clinics and channelling explained",
        "navigation", "patient",
        "Sri Lanka has universal free care in government hospitals. The "
        "Outpatient Department (OPD) sees anyone without an appointment and is "
        "the usual entry point for a new problem; arriving early matters "
        "because it works as a queue. Specialist clinics inside government "
        "hospitals run on fixed days and normally need a referral from the OPD "
        "or a ward. Channelling centres are private and let you choose a named "
        "consultant at a set time for a fee, which is faster but not free. "
        "For anything the deterministic urgency check has flagged as an "
        "emergency, go to the nearest emergency department rather than booking "
        "anything — do not wait for a channelled appointment.",
        "Care navigation guidance",
    ),
    KnowledgeDoc(
        "lk-nav-002", "1990 Suwa Seriya and when to call an ambulance",
        "navigation", "patient",
        "1990 Suwa Seriya is the national pre-hospital ambulance service. It "
        "is free, covers the whole island, and does not require payment or "
        "insurance details. Call it for chest pain, difficulty breathing, "
        "signs of stroke, a fit, heavy bleeding, loss of consciousness, "
        "snakebite, serious injury, or any pregnancy emergency. Calling an "
        "ambulance is safer than driving the patient yourself, because "
        "treatment begins on the way and the crew take you to a hospital able "
        "to handle the problem rather than the nearest one. Do not drive "
        "yourself if you are the person with chest pain.",
        "Suwa Seriya Foundation / Care navigation guidance",
    ),
    KnowledgeDoc(
        "lk-nav-003", "What to bring to a hospital visit", "navigation", "patient",
        "Bring the clinic book or pregnancy record book, any previous reports "
        "and scan films, a list or the actual packets of every medicine being "
        "taken including Ayurvedic and over-the-counter preparations, the "
        "National Identity Card, and any insurance details for a private "
        "visit. For a child, bring the Child Health Development Record. "
        "Knowing when the problem started and what makes it better or worse is "
        "as useful to the doctor as any test result, so it is worth writing "
        "down before going.",
        "Care navigation guidance",
    ),

    # ---------------- Fever, the commonest presentation --------------------
    KnowledgeDoc(
        "lk-fev-001", "Fever in Sri Lanka: what it might be", "infection", "patient",
        "Fever is the commonest reason people attend an OPD here, and the "
        "likely causes are seasonal. During and after the monsoons dengue and "
        "leptospirosis both rise, and influenza circulates year round. A fever "
        "lasting more than two days, or any fever with severe headache, "
        "severe muscle pain especially in the calves, bleeding, a rash, "
        "breathlessness, reduced urine output, or drowsiness should be seen "
        "the same day and have a blood count done. Self-medicating a fever "
        "with painkillers bought over the counter can mask exactly the "
        "deterioration a doctor is watching for, and some of them worsen "
        "bleeding risk in dengue — which is why the choice should be a "
        "clinician's.",
        f"{MOH} / {WHO}",
    ),

    # ---------------- Child health ----------------------------------------
    KnowledgeDoc(
        "lk-chi-001", "When a young child needs to be seen urgently",
        "paediatric", "patient",
        "Take a child under five to hospital straight away if they are "
        "refusing to feed or drink, are unusually sleepy or difficult to wake, "
        "are floppy or unresponsive, have a fit, are breathing fast or "
        "struggling to breathe, have a rash that does not fade when pressed "
        "with a glass, are passing much less urine than usual, or have a fever "
        "in a baby under three months. Young children deteriorate faster than "
        "adults and recover faster once treated, so early assessment is worth "
        "far more than waiting to see. Bring the Child Health Development "
        "Record, which holds the growth chart and immunisation history.",
        f"{WHO} integrated management of childhood illness / {MOH}",
    ),
    KnowledgeDoc(
        "lk-chi-002", "Immunisation and the Child Health Development Record",
        "paediatric", "patient",
        "Sri Lanka's national immunisation programme is free and delivered "
        "through MOH clinics and field midwives, with very high coverage. The "
        "schedule is recorded in the Child Health Development Record, which "
        "also tracks growth and development and should be brought to every "
        "visit. If a dose has been missed the schedule can be caught up — a "
        "delay is not a reason to stop. Mild fever or soreness after a "
        "vaccine is expected; a child who is drowsy, floppy, has a fit or "
        "difficulty breathing after any vaccine should be seen immediately.",
        f"{MOH} Epidemiology Unit national immunisation programme",
    ),

    # ---------------- Anaemia and nutrition --------------------------------
    KnowledgeDoc(
        "lk-nut-001", "Anaemia, iron and thalassaemia", "haematology", "patient",
        "Anaemia is common in Sri Lanka, particularly in women of "
        "reproductive age, pregnant women and adolescents, and shows as "
        "tiredness, breathlessness on exertion, pallor and poor concentration. "
        "Most is due to iron deficiency and responds to treatment, but a low "
        "haemoglobin should always be investigated rather than simply "
        "supplemented, because it can be the first sign of blood loss from the "
        "gut or of an inherited condition. Thalassaemia trait is also present "
        "in Sri Lanka and matters before marriage and pregnancy: carrier "
        "screening is available and lets couples understand the risk to a "
        "child before it arises.",
        f"{WHO} / {MOH} nutrition and thalassaemia programmes",
    ),
]
