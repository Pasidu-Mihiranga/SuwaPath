"""Curated health knowledge collection for semantic retrieval.

Content is paraphrased general patient guidance aligned with WHO and standard
public-health education material, written in plain language. It is deliberately
*general*: patient-specific information stays in PostgreSQL and is never mixed
into this collection (spec §25).

Each entry is retrieved by meaning, then passed to the LLM as grounding context
before it explains anything to a patient.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeDoc:
    id: str
    title: str
    topic: str
    audience: str  # patient | guardian | clinician
    text: str
    source: str


CORPUS: list[KnowledgeDoc] = [
    # ---------------- Emergency recognition ----------------
    KnowledgeDoc(
        "kb-emg-001", "Recognising a possible heart attack", "cardiac", "patient",
        "Chest pain or pressure that lasts more than a few minutes, especially "
        "when it happens with breathlessness, sweating, nausea, or pain "
        "spreading to the arm, jaw, neck or back, may be a heart attack. This "
        "is an emergency. Call an ambulance rather than travelling by private "
        "vehicle, because treatment can begin on the way. Symptoms in women, "
        "older adults and people with diabetes can be less typical and may "
        "present mainly as fatigue, breathlessness or indigestion-like "
        "discomfort.",
        "WHO cardiovascular disease patient guidance",
    ),
    KnowledgeDoc(
        "kb-emg-002", "Stroke warning signs and the FAST check", "neurological", "patient",
        "Stroke is a medical emergency where blood flow to part of the brain is "
        "interrupted. The FAST check helps recognise it: Face drooping on one "
        "side, Arm weakness on one side, Speech that is slurred or confused, "
        "and Time to call emergency services immediately. Other signs include "
        "sudden severe headache, sudden loss of vision, and sudden loss of "
        "balance or coordination. Treatment is time-critical: the sooner a "
        "person reaches a hospital with brain imaging, the more treatment "
        "options remain available.",
        "WHO stroke awareness guidance",
    ),
    KnowledgeDoc(
        "kb-emg-003", "When breathing difficulty is an emergency", "respiratory", "patient",
        "Seek emergency care for breathing difficulty when a person cannot "
        "speak in full sentences, has blue lips or fingertips, is drowsy or "
        "confused, or when breathing difficulty comes on suddenly with facial "
        "or throat swelling, which may indicate a severe allergic reaction. "
        "In young children, fast breathing, chest indrawing and inability to "
        "feed are danger signs requiring urgent assessment.",
        "WHO acute respiratory care guidance",
    ),

    # ---------------- Symptom education ----------------
    KnowledgeDoc(
        "kb-sym-001", "Understanding fever", "general", "patient",
        "Fever is a rise in body temperature and is usually a sign that the "
        "body is responding to an infection. Most fevers in otherwise healthy "
        "adults settle within a few days. Seek medical assessment if fever "
        "lasts more than three days, rises above 39C repeatedly, or occurs "
        "with neck stiffness, a rash that does not fade under pressure, "
        "confusion, difficulty breathing, or reduced urine output. In infants "
        "under three months, any fever needs same-day medical assessment. "
        "In dengue-endemic areas, fever with severe body aches warrants a "
        "blood count and medical review.",
        "WHO fever management guidance",
    ),
    KnowledgeDoc(
        "kb-sym-002", "Persistent cough: what it can mean", "respiratory", "patient",
        "A cough lasting more than three weeks is described as persistent and "
        "should be assessed. Common causes include post-infectious irritation, "
        "asthma, acid reflux, smoking and certain blood-pressure medicines. "
        "A cough lasting more than two weeks with fever, night sweats, weight "
        "loss or coughing up blood requires prompt evaluation including a "
        "chest X-ray, as these can be features of tuberculosis or other "
        "serious lung conditions.",
        "WHO respiratory health education",
    ),
    KnowledgeDoc(
        "kb-sym-003", "Fatigue and tiredness", "general", "patient",
        "Persistent tiredness that does not improve with rest can have many "
        "causes, including anaemia, thyroid problems, diabetes, poor sleep, "
        "depression, and chronic infections. Because the causes overlap, "
        "initial assessment usually includes a full blood count, blood sugar "
        "and thyroid function tests. Fatigue combined with breathlessness, "
        "chest pain, unexplained weight loss or night sweats needs earlier "
        "medical review.",
        "General symptom education",
    ),
    KnowledgeDoc(
        "kb-sym-004", "Headache: common types and warning signs", "neurological", "patient",
        "Most headaches are tension-type or migraine and are not dangerous. "
        "Warning signs that need urgent assessment include a sudden severe "
        "headache that peaks within seconds, headache with fever and neck "
        "stiffness, headache with new weakness, confusion or visual loss, "
        "headache after a head injury, and a headache that is persistently "
        "worse in the morning or on coughing.",
        "General neurological patient education",
    ),
    KnowledgeDoc(
        "kb-sym-005", "Skin lesions that need review", "dermatology", "patient",
        "Most skin lumps and patches are harmless. A skin lesion should be "
        "assessed by a dermatologist if it changes in size, shape or colour, "
        "has an irregular border, bleeds or itches persistently, or does not "
        "heal within four weeks. Diagnosis sometimes requires a small skin "
        "biopsy, where a sample is examined under a microscope, so it helps "
        "to attend a facility that can perform biopsy and histopathology.",
        "Dermatology patient education",
    ),
    KnowledgeDoc(
        "kb-sym-006", "Abdominal pain: when to seek urgent care", "gastrointestinal", "patient",
        "Seek urgent assessment for abdominal pain that is severe and sudden, "
        "pain with a rigid or very tender abdomen, pain with persistent "
        "vomiting, vomiting blood, black or bloody stools, or pain with fever "
        "and inability to pass stool or wind. Abdominal pain in pregnancy, or "
        "in an older adult with heart disease, should always be assessed "
        "promptly.",
        "General gastroenterology education",
    ),

    # ---------------- Laboratory interpretation ----------------
    KnowledgeDoc(
        "kb-lab-001", "Understanding haemoglobin and anaemia", "laboratory", "patient",
        "Haemoglobin is the protein in red blood cells that carries oxygen. A "
        "result below the reference range printed on the report indicates "
        "anaemia. Anaemia has many causes, including iron deficiency, blood "
        "loss, chronic disease, vitamin B12 or folate deficiency, and "
        "inherited conditions such as thalassaemia. Because the causes differ "
        "in treatment, a low haemoglobin is usually investigated with iron "
        "studies and a review of symptoms and diet rather than treated with "
        "supplements alone.",
        "Laboratory result patient education",
    ),
    KnowledgeDoc(
        "kb-lab-002", "Understanding thyroid function tests", "laboratory", "patient",
        "TSH is produced by the pituitary gland and tells the thyroid how much "
        "hormone to make. A high TSH with a low or low-normal free T4 usually "
        "indicates an underactive thyroid (hypothyroidism), which can cause "
        "tiredness, weight gain, cold intolerance, dry skin and hair thinning. "
        "A low TSH with raised free T4 suggests an overactive thyroid. "
        "Antibody tests such as anti-TPO help identify an autoimmune cause. "
        "Thyroid results are interpreted together with symptoms and any "
        "current thyroid medication.",
        "Endocrinology patient education",
    ),
    KnowledgeDoc(
        "kb-lab-003", "Understanding blood sugar and HbA1c", "laboratory", "patient",
        "Fasting blood sugar reflects glucose after not eating for 8 hours, "
        "while HbA1c reflects average blood glucose over roughly the previous "
        "three months. Higher-than-target values suggest that glucose control "
        "needs review. Because a single reading can be affected by illness, "
        "diet and timing, diagnosis and treatment changes are based on "
        "repeated results interpreted alongside symptoms.",
        "Diabetes patient education",
    ),
    KnowledgeDoc(
        "kb-lab-004", "Understanding a full blood count", "laboratory", "patient",
        "A full blood count measures red cells, white cells and platelets. Low "
        "haemoglobin indicates anaemia. A raised white cell count often "
        "suggests infection or inflammation, while a low count can reduce the "
        "ability to fight infection. Low platelets can increase bleeding risk, "
        "which is why they are monitored closely in dengue and in some "
        "medication regimens. Results should always be compared against the "
        "reference range printed on the report, as ranges differ between "
        "laboratories and by age and sex.",
        "Laboratory result patient education",
    ),

    # ---------------- Maternal health ----------------
    KnowledgeDoc(
        "kb-mat-001", "Danger signs during pregnancy", "maternal", "patient",
        "Seek care immediately during pregnancy for vaginal bleeding, severe "
        "or persistent headache, blurred vision or seeing flashing lights, "
        "severe upper abdominal pain, sudden swelling of the face or hands, "
        "fever, fluid leaking from the vagina, convulsions, or a noticeable "
        "reduction in the baby's movements. Several of these together can "
        "indicate pre-eclampsia, a condition that can worsen quickly and "
        "affects both mother and baby.",
        "WHO antenatal care guidance",
    ),
    KnowledgeDoc(
        "kb-mat-002", "Monitoring baby's movements", "maternal", "patient",
        "From around 28 weeks of pregnancy, most women notice a regular "
        "pattern of fetal movement. A reduction in the usual pattern of "
        "movements should be reported the same day, as it can be an early sign "
        "that the baby needs assessment. Do not wait until the next day or use "
        "cold drinks or other home methods to trigger movement instead of "
        "seeking review. Assessment usually involves listening to the baby's "
        "heartbeat and sometimes an ultrasound scan.",
        "WHO antenatal care guidance",
    ),
    KnowledgeDoc(
        "kb-mat-003", "Antenatal visit schedule and tests", "maternal", "patient",
        "Routine antenatal care includes regular checks of blood pressure, "
        "weight, urine and the baby's growth. Common investigations include a "
        "full blood count, blood group and antibody screening, blood sugar "
        "testing around 24 to 28 weeks, and ultrasound scans in the first and "
        "second trimesters. Iron and folic acid supplementation is routinely "
        "recommended. Attending scheduled visits allows problems such as "
        "anaemia, raised blood pressure and restricted growth to be found "
        "early when they are easier to manage.",
        "WHO antenatal care recommendations",
    ),
    KnowledgeDoc(
        "kb-mat-004", "Postpartum warning signs", "maternal", "patient",
        "After delivery, seek urgent care for heavy bleeding that soaks a pad "
        "within an hour, foul-smelling vaginal discharge, fever or chills, "
        "severe headache with visual changes, calf pain or swelling, "
        "breathing difficulty, or severe abdominal pain. These can indicate "
        "postpartum haemorrhage, infection, blood clots or high blood "
        "pressure, all of which are treatable when identified early.",
        "WHO postnatal care guidance",
    ),
    KnowledgeDoc(
        "kb-mat-005", "Postpartum mental health", "maternal", "patient",
        "Feeling tearful or overwhelmed in the first two weeks after birth is "
        "common and usually settles. Persistent low mood, loss of interest, "
        "difficulty bonding with the baby, severe anxiety, or thoughts of "
        "harming yourself or the baby beyond two weeks may indicate postnatal "
        "depression, which is common and treatable. Thoughts of self-harm "
        "need immediate professional support. Screening tools such as the "
        "Edinburgh Postnatal Depression Scale help identify who would benefit "
        "from further assessment.",
        "WHO perinatal mental health guidance",
    ),
    KnowledgeDoc(
        "kb-mat-006", "Newborn danger signs", "maternal", "patient",
        "Take a newborn for immediate assessment if the baby is feeding "
        "poorly or refusing feeds, is unusually sleepy or difficult to wake, "
        "has fast or difficult breathing or chest indrawing, has a fever or "
        "feels unusually cold, has convulsions, develops yellow skin in the "
        "first 24 hours or deep yellow colouring, or has redness or discharge "
        "around the umbilical stump. Newborns can deteriorate quickly, so "
        "early review is important.",
        "WHO newborn care guidance",
    ),

    # ---------------- Elderly care ----------------
    KnowledgeDoc(
        "kb-eld-001", "Medication safety for older adults", "elderly", "patient",
        "Taking several medicines at once increases the chance of side effects "
        "and interactions. Keep an up-to-date list of all medicines including "
        "over-the-counter and herbal products, and bring it to every "
        "appointment. Do not stop a prescribed medicine without advice, "
        "particularly blood-pressure, heart or diabetes medicines, as stopping "
        "suddenly can be harmful. Missing doses of blood-pressure medication "
        "repeatedly can lead to poor control and raises the risk of stroke.",
        "WHO integrated care for older people",
    ),
    KnowledgeDoc(
        "kb-eld-002", "Falls prevention", "elderly", "patient",
        "Falls are a leading cause of injury in older adults. Risk is reduced "
        "by keeping walkways clear, using adequate lighting, wearing "
        "well-fitting footwear, installing grab rails in bathrooms, reviewing "
        "medicines that cause drowsiness or dizziness, checking vision "
        "regularly, and maintaining strength and balance activity. Any fall "
        "with a head injury, loss of consciousness, or inability to bear "
        "weight should be assessed promptly.",
        "WHO integrated care for older people",
    ),
    KnowledgeDoc(
        "kb-eld-003", "New confusion in an older adult", "elderly", "guardian",
        "A sudden change in alertness, attention or behaviour in an older "
        "adult, developing over hours or days, is called delirium and is a "
        "medical warning sign rather than a normal part of ageing. Common "
        "treatable causes include urinary or chest infection, dehydration, "
        "constipation, pain, and medication side effects. It needs same-day "
        "medical assessment.",
        "WHO integrated care for older people",
    ),
    KnowledgeDoc(
        "kb-eld-004", "Understanding blood pressure readings", "elderly", "patient",
        "Blood pressure is written as systolic over diastolic, for example "
        "130/80 mmHg. Readings vary through the day, so a single high reading "
        "does not by itself mean a problem. Consistently raised readings "
        "should be reviewed by a clinician. A reading of 180/120 mmHg or "
        "above, especially with chest pain, breathlessness, severe headache, "
        "visual change or weakness, needs immediate medical attention.",
        "WHO hypertension patient guidance",
    ),

    # ---------------- Sexual health ----------------
    KnowledgeDoc(
        "kb-sh-001", "When to consider STI testing", "sexual_health", "patient",
        "Consider testing after unprotected sex with a new partner, if a "
        "partner has been diagnosed with an infection, or if you notice "
        "unusual discharge, burning on passing urine, genital sores, ulcers, "
        "warts, itching, or lower abdominal pain. Many sexually transmitted "
        "infections cause no symptoms at all, so testing is worthwhile even "
        "when you feel well. Testing is confidential and most infections are "
        "curable or manageable when found early.",
        "WHO sexual and reproductive health guidance",
    ),
    KnowledgeDoc(
        "kb-sh-002", "Timing of STI tests after possible exposure", "sexual_health", "patient",
        "Tests need enough time after exposure to become reliable, a period "
        "called the window period. Chlamydia and gonorrhoea testing is "
        "generally reliable about two weeks after exposure. HIV testing with "
        "modern combined antigen/antibody tests is generally reliable around "
        "four to six weeks, with a confirmatory test at twelve weeks. "
        "Syphilis testing is usually reliable after about four to six weeks. "
        "If exposure was very recent and HIV risk is considered high, "
        "post-exposure prophylaxis may be an option but must be started "
        "within 72 hours, so seek advice immediately rather than waiting.",
        "WHO STI testing guidance",
    ),
    KnowledgeDoc(
        "kb-sh-003", "Confidentiality in sexual health services", "sexual_health", "patient",
        "Sexual health services are confidential. Information about testing "
        "and treatment is not shared with family members or employers without "
        "consent. Many services allow attendance without a referral and some "
        "support anonymous or pseudonymous testing. Concerns about privacy "
        "are a common reason people delay testing, but delay increases the "
        "chance of complications and onward transmission.",
        "WHO sexual and reproductive health guidance",
    ),

    # ---------------- Imaging and screening ----------------
    KnowledgeDoc(
        "kb-img-001", "What a chest X-ray can and cannot show", "imaging", "patient",
        "A chest X-ray produces an image of the lungs, heart and chest wall "
        "and is often the first test for cough, fever, chest pain or "
        "breathlessness. It can show patterns suggesting pneumonia, fluid, "
        "collapsed lung, or an enlarged heart. It cannot by itself confirm a "
        "diagnosis: findings are interpreted together with symptoms, "
        "examination and blood tests. Some conditions are not visible on a "
        "plain X-ray and need a CT scan.",
        "Radiology patient education",
    ),
    KnowledgeDoc(
        "kb-img-002", "Understanding pneumonia", "respiratory", "patient",
        "Pneumonia is an infection that inflames the air sacs of the lungs, "
        "often causing cough, fever, chest pain and breathlessness. It is "
        "usually suspected from symptoms and examination and supported by a "
        "chest X-ray and blood tests. Most cases in otherwise healthy adults "
        "are treated with antibiotics at home, but pneumonia can be serious "
        "in young children, older adults, pregnant women and people with "
        "chronic conditions or weakened immunity, who may need hospital care.",
        "WHO pneumonia patient guidance",
    ),
    KnowledgeDoc(
        "kb-img-003", "How AI screening support should be used", "imaging", "patient",
        "Computer-assisted image analysis can highlight patterns that may "
        "warrant closer attention, but it is screening support and not a "
        "diagnosis. A result indicating a possible finding means the image "
        "should be reviewed by a qualified clinician alongside symptoms and "
        "history. A result indicating no abnormality detected does not rule "
        "out disease, particularly if symptoms persist. Always discuss "
        "results with a doctor before drawing conclusions or changing "
        "treatment.",
        "WHO ethics and governance of AI for health",
    ),

    # ---------------- Care navigation ----------------
    KnowledgeDoc(
        "kb-nav-001", "Choosing the right specialty", "navigation", "patient",
        "Choosing an appropriate specialty avoids delay and repeated "
        "appointments. General medicine is a sound starting point for "
        "undifferentiated symptoms such as tiredness, fever or general "
        "unwellness. Skin problems go to dermatology, heart symptoms to "
        "cardiology, bone and joint injuries to orthopaedics, pregnancy care "
        "to obstetrics, eye complaints to ophthalmology, and children to "
        "paediatrics. When symptoms suggest an emergency, care level matters "
        "more than specialty: go to a facility with an emergency department.",
        "Care navigation guidance",
    ),
    KnowledgeDoc(
        "kb-nav-002", "Why facility capability matters", "navigation", "patient",
        "Booking with the right specialist is only useful if the facility can "
        "also provide the tests that specialist will need. For example, a "
        "persistent skin lesion may require a biopsy and laboratory analysis, "
        "and a persistent cough may require a chest X-ray. Choosing a facility "
        "that offers both the consultation and the likely investigation "
        "reduces repeat visits and shortens time to diagnosis.",
        "Care navigation guidance",
    ),
    KnowledgeDoc(
        "kb-nav-003", "Preparing for a consultation", "navigation", "patient",
        "Consultations are more useful when you bring a clear history: when "
        "symptoms started, how they have changed, what makes them better or "
        "worse, all current medicines and doses, known allergies, previous "
        "relevant test results and reports, and any specific concerns or "
        "questions. Sharing a structured summary in advance means less time is "
        "spent reconstructing basic history and more on assessment.",
        "Care navigation guidance",
    ),
    KnowledgeDoc(
        "kb-nav-004", "Teleconsultation: when it is suitable", "navigation", "patient",
        "Teleconsultation works well for follow-up reviews, discussing test "
        "results, medication adjustments, prescription renewals, mental-health "
        "support and advice on whether an in-person visit is needed. It is not "
        "suitable when physical examination is essential, for suspected "
        "emergencies, for significant injuries, or when urgent tests or "
        "imaging are required.",
        "Care navigation guidance",
    ),
]

CORPUS_BY_ID = {doc.id: doc for doc in CORPUS}
