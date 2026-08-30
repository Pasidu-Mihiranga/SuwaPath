"""Multilingual clinical term lexicon.

Maps a canonical concept (e.g. ``chest_pain``) to the surface forms patients
actually type, in English, Sinhala and Tamil.  The red-flag engine and the
navigation engine both match against these concepts rather than raw strings, so
a Sinhala-speaking patient triggers exactly the same deterministic rules as an
English-speaking one.

Sinhala/Tamil entries include both native script and common Latin
transliterations, because patients frequently type romanised text.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Concept -> surface forms
# --------------------------------------------------------------------------
LEXICON: dict[str, list[str]] = {
    # --- Cardiorespiratory ---
    "chest_pain": [
        "chest pain", "chest discomfort", "chest tightness", "tight chest",
        "pain in my chest", "pain in chest", "chest pressure", "chest heaviness",
        "angina", "crushing chest", "පපුවේ කැක්කුම", "පපුව රිදෙනවා", "papuwe kakkuma",
        "papuwa ridenawa", "மார்பு வலி", "maarbu vali",
    ],
    "shortness_of_breath": [
        "shortness of breath", "short of breath", "breathless", "breathlessness",
        "difficulty breathing", "hard to breathe", "cant breathe", "can't breathe",
        "trouble breathing", "gasping", "sob", "dyspnea", "dyspnoea",
        # Spoken, not typed. Dictation expands contractions — a patient who
        # would type "can't breathe" is transcribed as "cannot breathe", which
        # matched nothing and cost the concept that half the emergency rules
        # depend on.
        "cannot breathe", "cannot breath", "unable to breathe", "can not breathe",
        "struggling to breathe", "struggling for breath", "fighting for breath",
        "gasping for air", "cant get air", "can't get air", "cannot get air",
        "barely breathe", "can barely breathe", "hardly breathe",
        "හුස්ම ගැනීමේ අපහසුතාව", "husma ganna amaruyi", "husma", "மூச்சுத் திணறல்",
        "moochu thinaral",
    ],
    "palpitations": [
        "palpitations", "heart racing", "racing heart", "fast heartbeat",
        "irregular heartbeat", "heart pounding", "හෘද ස්පන්දනය", "hadawatha wedi",
    ],
    "sweating": [
        "sweating", "cold sweat", "clammy", "profuse sweating", "diaphoresis",
        "sweaty", "දහඩිය", "dahadiya", "வியர்வை", "viyarvai",
    ],
    "radiating_pain": [
        "pain spreads", "spreading to arm", "spreading to my arm", "spreading to left arm",
        "spreading to my left arm", "spreading to the left arm", "radiating to my arm",
        "radiating to left arm", "radiating to my left arm", "radiating to arm", "radiating",
        "pain in left arm", "pain to jaw", "pain going to my arm", "shoulder and arm pain",
        # Said aloud, radiation is described as direction of travel rather
        # than with the word "radiating": "the pain is going down my left
        # arm". This concept never fires a rule on its own — it only upgrades
        # chest pain to the heart-attack pattern — so broad phrasing here
        # cannot cause a dispatch by itself.
        "going down my arm", "going down my left arm", "down my left arm",
        "down the left arm", "going into my jaw", "into my jaw", "up to my jaw",
        "pain in my left arm", "pain in my jaw", "pain in my shoulder and arm",
        "spreads to my arm", "moving to my arm",
        "අත දක්වා පැතිරෙන", "atha dakwa",
    ],
    "cough": [
        "cough", "coughing", "dry cough", "productive cough", "phlegm",
        "කැස්ස", "kassa", "இருமல்", "irumal",
    ],
    "coughing_blood": [
        "coughing blood", "coughing up blood", "cough up blood", "coughed up blood",
        "blood in sputum", "blood in phlegm", "haemoptysis", "hemoptysis",
        "blood when i cough", "ලේ කැස්ස", "le kassa",
    ],
    "wheezing": ["wheezing", "wheeze", "whistling breath", "asthma attack"],

    # --- Neurological ---
    "severe_headache": [
        "severe headache", "worst headache", "thunderclap headache",
        "terrible headache", "unbearable headache", "intense headache",
        "දරුණු හිසරදය", "hisaradaya", "දැඩි හිසරදය", "kadumai thalaivali",
        "கடுமையான தலைவலி",
    ],
    "headache": [
        "headache", "head pain", "හිසරදය", "hisaradaya", "தலைவலி", "thalaivali",
    ],
    "blurred_vision": [
        "blurred vision", "blurry vision", "vision problems", "double vision",
        "cant see clearly", "can't see properly", "loss of vision", "vision loss",
        "පෙනීම බොඳ", "penima bo", "மங்கலான பார்வை",
    ],
    "dizziness": [
        "dizzy", "dizziness", "light headed", "lightheaded", "vertigo",
        "feel faint", "spinning", "කරකැවිල්ල", "karakavilla", "தலைச்சுற்றல்",
        "thalaichuttral",
    ],
    "loss_of_consciousness": [
        "fainted", "passed out", "unconscious", "blacked out", "loss of consciousness",
        "collapsed", "සිහිය නැති", "sihiya nathi",
    ],
    "seizure": [
        "seizure", "fit", "convulsion", "convulsions", "epileptic",
        "ෆිට් එකක්", "වලිප්පුව", "valippuwa",
    ],
    "facial_droop": [
        "face drooping", "facial droop", "face is drooping", "one side of face",
        "mouth drooping", "crooked smile", "මුහුණ ඇද", "muhuna adha",
    ],
    "arm_weakness": [
        "arm weakness", "cant lift my arm", "weakness on one side",
        "one side weak", "left side weak", "right side weak", "hemiplegia",
        "numbness one side",
        # Spoken phrasing. Dictation expands the contraction, and a bystander
        # reports the limb before the deficit: "her arm has gone weak", not
        # "arm weakness". Stroke treatment is measured in minutes, so these
        # are the phrasings least affordable to miss.
        "cannot lift my arm", "cannot lift her arm", "cannot lift his arm",
        "cannot move my arm", "cannot move her arm", "cannot move his arm",
        "cannot raise", "arm has gone weak", "arm is weak", "arm feels weak",
        "arm went weak", "weak on one side", "numbness on one side",
        "one arm is weak", "cant move my arm", "can't move my arm",
        "අත දුර්වල", "atha durwala",
    ],
    "speech_difficulty": [
        "slurred speech", "cant speak", "can't speak", "speech difficulty",
        "trouble speaking", "confused speech",
        # As above: "cannot speak" is what the microphone hears, and the
        # subject-first form "her speech is slurred" is how it is reported.
        "cannot speak", "can not speak", "cannot talk", "cant talk",
        "can't talk", "speech is slurred", "speech has gone", "slurring",
        "words are slurred", "not making any sense when",
        "කතා කරන්න බැරි", "katha karanna bari",
    ],
    "neck_stiffness": [
        "stiff neck", "neck stiffness", "cant bend my neck", "neck rigid",
        "ගෙල තද", "gela tha",
    ],
    "confusion": [
        "confused", "confusion", "disoriented", "not making sense", "altered mental",
        "delirium",
    ],

    # --- Constitutional ---
    "fever": [
        "fever", "high temperature", "feverish", "pyrexia", "temperature",
        "උණ", "una", "காய்ச்சல்", "kaaichal",
    ],
    "high_fever": [
        "high fever", "very high fever", "104", "103", "40 degrees", "39 degrees",
        "දැඩි උණ", "wedi una",
    ],
    "fatigue": [
        "fatigue", "tired", "tiredness", "exhausted", "weakness", "no energy",
        "lethargy", "වෙහෙස", "wehesa", "සෙනෙහස නැති", "சோர்வு", "sorvu",
    ],
    "weight_loss": [
        "weight loss", "losing weight", "lost weight", "unintentional weight loss",
        "බර අඩුවීම", "bara aduwima",
    ],
    "night_sweats": ["night sweats", "sweating at night"],
    "cold_intolerance": ["cold intolerance", "always cold", "feel cold", "sensitive to cold"],

    # --- Abdominal / GI ---
    "severe_abdominal_pain": [
        "severe abdominal pain", "severe stomach pain", "unbearable stomach pain",
        "intense abdominal pain", "severe belly pain", "දරුණු බඩ කැක්කුම",
        "bada kakkuma darunu",
    ],
    "abdominal_pain": [
        "abdominal pain", "stomach pain", "belly pain", "tummy pain", "stomach ache",
        "බඩ කැක්කුම", "bada kakkuma", "வயிற்று வலி", "vayitru vali",
    ],
    "vomiting": [
        "vomiting", "throwing up", "vomit", "nausea and vomiting",
        "වමනය", "wamanaya", "வாந்தி", "vaanthi",
    ],
    "vomiting_blood": [
        "vomiting blood", "blood in vomit", "haematemesis", "hematemesis",
        "vomited blood", "vomiting large amounts of blood",
        "vomiting large amounts of bright red blood", "throwing up blood",
        "ලේ වමනය", "le wamanaya",
    ],
    "nausea": ["nausea", "nauseous", "feel sick", "queasy", "ඔක්කාරය", "okkaraya"],
    "diarrhoea": [
        "diarrhoea", "diarrhea", "loose motions", "loose stools", "watery stools",
        "බඩ බුරුල", "bada burula", "வயிற்றுப்போக்கு",
    ],
    "blood_in_stool": [
        "blood in stool", "bloody stool", "black stool", "melaena", "melena",
        "rectal bleeding", "ලේ මළපහ", "le malapaha",
    ],

    # --- Bleeding / trauma ---
    "severe_bleeding": [
        "severe bleeding", "heavy bleeding", "bleeding a lot", "wont stop bleeding",
        "won't stop bleeding", "uncontrolled bleeding", "haemorrhage", "hemorrhage",
        # Said aloud, the subject comes first: "the bleeding won't stop", not
        # "won't stop bleeding". Only the cessation phrasings are listed —
        # "bleeding" alone is deliberately not a concept, because a shaving cut
        # is bleeding and this rule dispatches an ambulance.
        "bleeding will not stop", "bleeding wont stop", "bleeding won't stop",
        "bleeding does not stop", "bleeding doesnt stop", "bleeding is not stopping",
        "bleeding keeps coming", "soaked through", "losing a lot of blood",
        # The adverb also follows the verb when spoken: "I am bleeding
        # heavily", not "heavy bleeding". This is the phrasing a postpartum
        # haemorrhage is reported in, and that rule has no other way in.
        "bleeding heavily", "bleeding badly", "bleeding very badly",
        "blood everywhere",
        "දැඩි ලේ ගැලීම", "le galima",
    ],
    "injury": [
        "injury", "accident", "fell down", "fall", "trauma", "hit", "wound",
        "fracture", "broken bone", "twisted", "sprain", "තුවාල", "thuwala",
    ],
    "joint_pain": [
        "joint pain", "knee pain", "shoulder pain", "back pain", "ankle pain",
        "hip pain", "swollen joint", "සන්ධි කැක්කුම", "sandhi kakkuma", "மூட்டு வலி",
    ],

    # --- Maternal ---
    "vaginal_bleeding": [
        "vaginal bleeding", "bleeding down there", "spotting", "bleeding from vagina",
        "pregnancy bleeding", "යෝනි ලේ ගැලීම", "ලේ යනවා",
    ],
    "reduced_fetal_movement": [
        "baby not moving", "reduced fetal movement", "less baby movement",
        "baby movements decreased", "no kicks", "baby stopped moving",
        # Longer forms deliberately listed separately rather than relying on
        # "baby not moving" as a substring: the words people actually say put
        # something between "baby" and "not", and the index matches literally.
        "baby is not moving", "baby isnt moving", "baby is not kicking",
        "baby not kicking", "baby has not moved", "baby hasnt moved",
        # Phrased as a denial, which is the signal rather than something to
        # discard. Listed as whole surface forms so the negation window sees
        # nothing before them and cannot cancel the concept they establish.
        "not felt the baby", "have not felt the baby", "havent felt the baby",
        "no baby movement", "no fetal movement", "no movement from the baby",
        "දරුවා චලනය අඩු", "daruwa chalanaya adu",
    ],
    "leaking_fluid": [
        "water broke", "waters broke", "leaking fluid", "fluid leaking",
        "amniotic fluid", "දිය පිටවීම",
    ],
    "swelling": [
        "swelling", "swollen feet", "swollen hands", "swollen face", "puffiness",
        "throat tightening", "throat is tightening", "throat tightening up", "throat is tightening up",
        "throat swelling", "swelling in throat", "swelling of throat",
        "lip swelling", "tongue swelling", "swollen tongue", "swollen lips",
        "oedema", "edema", "ඉදිමීම", "idimima", "வீக்கம்",
    ],
    "contractions": ["contractions", "labour pain", "labor pains", "regular pains"],

    # --- Mental health ---
    "suicidal_ideation": [
        "want to die", "kill myself", "end my life", "suicidal", "suicide",
        "no reason to live", "harm myself", "self harm", "දිවි නසා",
    ],
    "low_mood": [
        "depressed", "depression", "sad all the time", "hopeless", "low mood",
        "crying a lot", "no interest", "anxious", "anxiety", "panic",
    ],

    # --- Dermatology ---
    "skin_lesion": [
        "skin lesion", "mole", "spot on skin", "growth on skin", "lump on skin",
        "patch on skin", "skin patch", "ulcer", "sore that wont heal",
        "සමේ තුවාලයක්", "same lapaya",
    ],
    "rash": [
        "rash", "skin rash", "red spots", "itchy skin", "hives", "eczema",
        "සමේ රතු", "kurulu", "தோல் வெடிப்பு",
    ],
    "persistent_skin_change": [
        "not healing", "wont heal", "won't heal", "getting bigger", "changing colour",
        "changing color", "for months", "persistent",
    ],

    # --- Sexual health ---
    "genital_symptoms": [
        "genital sore", "genital ulcer", "discharge", "burning when urinating",
        "painful urination", "genital itching", "sti", "std", "genital rash",
        "unprotected sex", "sexual exposure", "genital warts", "penile discharge",
        "vaginal discharge",
    ],

    # --- Ophthalmology / ENT ---
    "eye_pain": [
        "eye pain", "red eye", "eye redness", "painful eye", "eye discharge",
        "ඇස් කැක්කුම", "as kakkuma", "கண் வலி",
    ],
    "hearing_loss": ["hearing loss", "cant hear", "ear pain", "ear discharge", "tinnitus"],
    "sore_throat": ["sore throat", "throat pain", "difficulty swallowing", "tonsils"],

    # --- Endocrine / urinary ---
    "excessive_thirst": ["excessive thirst", "very thirsty", "drinking a lot of water"],
    "frequent_urination": [
        "frequent urination", "urinating often", "passing urine frequently",
        "waking up to urinate",
    ],
    "hair_loss": ["hair fall", "hair loss", "losing hair", "කොණ්ඩේ", "konde weteneva"],
    "weight_gain": ["weight gain", "gaining weight", "put on weight"],

    # --- Paediatric ---
    "child_not_feeding": [
        "not feeding", "refusing feeds", "not drinking", "baby not eating",
        "not breastfeeding",
    ],
    "child_lethargy": ["baby is floppy", "very sleepy baby", "unresponsive baby"],
}

# Concepts that indicate the patient explicitly denied a symptom.
NEGATION_MARKERS = (
    "no ", "not ", "without ", "denies ", "never ", "none", "n't ", "nope",
    "නැහැ", "නෑ", "naha", "nathi", "இல்லை",
)

# Words that end the reach of a preceding denial. "I have no chest pain, but
# my shoulder aches" denies the chest pain and asserts the shoulder — without
# these, the denial swallowed both. Commas would be the natural marker but
# `normalise` has already removed them, so these are the ones left standing.
#
# " and " is here for the spoken path specifically. People say two findings in
# one breath — "she cannot breathe and her face is swelling up" — and the
# "not " inside "cannot" sat in the lookback window for *swelling*, so the
# second finding was discarded and the airway rule that needs both never
# fired. A conjunction joins two clauses; it does not carry a denial into the
# one after it.
#
# The cost is the rarer "no fever and vomiting", where a single denial was
# meant to cover both and the second is now read as asserted. That is
# over-triage on an unusual phrasing, traded against under-triage on a common
# one. Denials that repeat the marker — "no fever and no vomiting" — are
# unaffected, because the marker after the conjunction is the one that governs.
CLAUSE_RESETS = (
    " but ", " however ", " just ", " only ", " though ", " although ",
    " except ", " otherwise ", " apart from ", " besides ", " and ",
    " නමුත් ", " namuth ", " ஆனால் ",
)

# Words that invert a denial rather than carrying it, because what is being
# denied is the symptom *ending*. "The bleeding will not stop" put "not " in
# the lookback window and so discarded `severe_bleeding` — under-triage on
# possibly the single sentence a frightened person is most likely to say out
# loud, and the one the bleeding first-aid script exists for.
#
# Matched against the word directly after the negation marker, not anywhere in
# the window. That distinction is the whole safety of it: "no more bleeding"
# stays a denial because "more" is not a cessation verb, while "won't stop
# bleeding" does not.
NEGATION_BLOCKERS = (
    "stop", "stops", "stopping", "stopped",
    "settle", "settles", "settling", "settled",
    "ease", "eases", "easing", "eased",
    "improve", "improves", "improving", "improved",
    "getting better", "go away", "goes away", "going away", "gone away",
    "let up", "letting up", "calm down", "calming down",
)

_CONCEPT_INDEX: list[tuple[str, str]] = sorted(
    ((term.lower(), concept) for concept, terms in LEXICON.items() for term in terms),
    key=lambda pair: len(pair[0]),
    reverse=True,  # longest surface form wins, so "severe headache" beats "headache"
)


def normalise(text: str) -> str:
    """Lowercase, strip accents from Latin text and collapse whitespace.

    Sinhala and Tamil code points are preserved: NFC keeps their combining
    marks intact, which NFKD would incorrectly split.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = text.lower()
    text = re.sub(r"[^\w\s඀-෿஀-௿'/-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_negated(haystack: str, position: int) -> bool:
    """True when a negation marker governs the concept at `position`.

    A plain 28-character lookback is not enough, because a denial of one
    symptom runs straight on into the next one: in "no fever, no vomiting,
    just a mild headache" the window before *headache* still contains "no ",
    so the headache the patient plainly asserted was being discarded. The
    failure direction is under-triage, which is the one that matters.

    Punctuation cannot be used to find the clause boundary — `normalise`
    replaces commas with spaces before this ever runs — so the boundary is
    detected from the words that survive. A contrast word resets the negation
    only when it appears *after* the last negation marker in the window, so
    "no pain but no fever either" still correctly negates fever.
    """
    window = haystack[max(0, position - 28) : position]

    last_negation = max(
        (window.rfind(marker) for marker in NEGATION_MARKERS),
        default=-1,
    )
    if last_negation == -1:
        return False

    tail = window[last_negation:]
    if any(reset in tail for reset in CLAUSE_RESETS):
        return False

    # Drop the marker itself and look at what it actually governs.
    words = tail.split(maxsplit=1)
    governed = words[1] if len(words) > 1 else ""
    if any(governed.startswith(blocker) for blocker in NEGATION_BLOCKERS):
        return False

    return True


def extract_concepts(text: str) -> tuple[set[str], set[str]]:
    """Return (asserted concepts, negated concepts) found in free text."""
    haystack = normalise(text)
    if not haystack:
        return set(), set()

    asserted: set[str] = set()
    negated: set[str] = set()
    consumed: list[tuple[int, int]] = []

    for term, concept in _CONCEPT_INDEX:
        start = haystack.find(term)
        while start != -1:
            # Skip if this span was already claimed by a longer term.
            if any(s <= start < e for s, e in consumed):
                start = haystack.find(term, start + 1)
                continue
            consumed.append((start, start + len(term)))
            if _is_negated(haystack, start):
                negated.add(concept)
            else:
                asserted.add(concept)
            break
        # Only the first non-consumed occurrence per term matters.

    # Second pass: body-part/symptom co-occurrence for phrasings the literal
    # index cannot cover.
    tokens = haystack.split()
    for concept in _proximity_concepts(tokens, asserted | negated):
        if _proximity_is_negated(haystack, tokens, concept):
            negated.add(concept)
        else:
            asserted.add(concept)

    # An explicit assertion anywhere outranks a negation elsewhere.
    return asserted, negated - asserted


def _proximity_is_negated(haystack: str, tokens: list[str], concept: str) -> bool:
    """Check for negation before the body-part token that produced `concept`.

    Every mention is checked, not just the first. The original took the first
    occurrence anywhere in the string, so a denial attached to one symptom
    silently negated a different one mentioned later. One un-negated mention
    is enough to treat the concept as asserted, which is the same precedence
    `extract_concepts` already applies to direct matches — and it errs toward
    hearing a symptom rather than discarding it.
    """
    part_terms = next(
        (parts for parts, _, c in PROXIMITY_RULES if c == concept), set()
    )
    mentioned = False
    for token in part_terms:
        index = haystack.find(token)
        while index != -1:
            mentioned = True
            if not _is_negated(haystack, index):
                return False
            index = haystack.find(token, index + 1)
    return mentioned


def concept_label(concept: str) -> str:
    """Human-readable label for a concept id."""
    return concept.replace("_", " ").title()


# --------------------------------------------------------------------------
# Proximity matching
# --------------------------------------------------------------------------
# Patients rarely type the canonical phrase. "my eye is red and painful" never
# contains the literal "eye pain", so a body-part word appearing close to a
# symptom word also resolves to a concept. Applied only to concepts the direct
# pass missed, so explicit phrases always win.
PROXIMITY_RULES: list[tuple[set[str], set[str], str]] = [
    (
        {"eye", "eyes", "ඇස", "ඇස්", "கண்"},
        {"red", "pain", "painful", "hurt", "hurts", "sore", "burning", "itchy",
         "swollen", "discharge", "watering"},
        "eye_pain",
    ),
    (
        {"ear", "ears", "කන", "காது"},
        {"pain", "painful", "hurt", "hurts", "blocked", "discharge", "ringing"},
        "hearing_loss",
    ),
    (
        {"throat", "උගුර", "தொண்டை"},
        {"pain", "painful", "sore", "hurts", "scratchy", "swollen"},
        "sore_throat",
    ),
    (
        {"chest", "පපුව", "පපුවේ", "மார்பு"},
        {"pain", "painful", "hurts", "hurting", "ache", "aching", "sore",
         "tight", "tightness", "heavy", "heaviness",
         "pressure", "discomfort", "burning", "crushing", "squeezing"},
        "chest_pain",
    ),
    (
        {"stomach", "abdomen", "belly", "tummy", "බඩ", "வயிறு"},
        {"pain", "painful", "hurts", "ache", "aching", "cramping", "cramps"},
        "abdominal_pain",
    ),
    (
        {"head", "හිස", "தலை"},
        {"pain", "painful", "hurts", "ache", "aching", "pounding", "throbbing"},
        "headache",
    ),
    (
        {"skin", "සම", "தோல்"},
        {"lump", "patch", "spot", "growth", "lesion", "sore", "bump", "mark"},
        "skin_lesion",
    ),
    (
        {"joint", "knee", "shoulder", "elbow", "ankle", "hip", "wrist", "back"},
        {"pain", "painful", "hurts", "ache", "aching", "swollen", "stiff", "stiffness"},
        "joint_pain",
    ),
    (
        {"breath", "breathing", "husma", "හුස්ම"},
        {"difficult", "difficulty", "hard", "trouble", "short", "cant", "can't",
         "problem", "issue", "amaru", "apahasu"},
        "shortness_of_breath",
    ),
]

# Words apart within which a body part and a symptom are treated as related.
_PROXIMITY_WINDOW = 4


def _proximity_concepts(tokens: list[str], already_found: set[str]) -> set[str]:
    found: set[str] = set()
    for part_terms, symptom_terms, concept in PROXIMITY_RULES:
        if concept in already_found:
            continue
        part_positions = [i for i, t in enumerate(tokens) if t in part_terms]
        if not part_positions:
            continue
        symptom_positions = [i for i, t in enumerate(tokens) if t in symptom_terms]
        if any(
            abs(p - s) <= _PROXIMITY_WINDOW
            for p in part_positions
            for s in symptom_positions
        ):
            found.add(concept)
    return found
