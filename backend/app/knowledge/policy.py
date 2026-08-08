"""How SuwaPath itself works — the answers to questions about the product.

Separate from the clinical corpus on purpose. "Who can see my reports?" is not
a medical question, and letting it retrieve from clinical guidance produces
confidently wrong answers about consent.

These entries are the long-form versions. The short, high-frequency ones
(greetings, "what is SuwaPath") are answered straight from
``app/agent/cag.py`` without retrieval at all.
"""

from __future__ import annotations

POLICY_DOCS: list[dict] = [
    {
        "id": "kb-pol-001",
        "title": "Who can see your medical records",
        "topic": "privacy",
        "text": (
            "Nobody can see your records unless you have granted them access. "
            "Guardian access is deny-by-default: adding a guardian grants "
            "nothing on its own, and each category — appointments, reports, "
            "medications, wellbeing, full medical — is a separate permission "
            "you switch on individually. A guardian who has not been granted "
            "a category cannot see it, and the system tells them it is "
            "withheld rather than pretending it does not exist. Doctors "
            "cannot browse patients: a doctor may only open your record if "
            "there is an appointment or a referral linking them to you. "
            "Hospital administrators see operational data such as appointment "
            "volumes and no-show rates, never clinical conversations. You can "
            "review and withdraw any permission at any time under Sharing and "
            "Consent, and every access is written to an audit log."
        ),
    },
    {
        "id": "kb-pol-002",
        "title": "What information is sent to an AI model",
        "topic": "privacy",
        "text": (
            "Very little, and never anything that identifies you. Before any "
            "request leaves the system it passes a privacy boundary that does "
            "four things. First, some work never leaves at all: urgency "
            "assessment, reading uploaded reports, screening images, and "
            "anything in a private conversation are computed locally. Second, "
            "each type of question has a fixed list of fields it is allowed "
            "to use; a question about symptoms sees your age band, sex, "
            "pregnancy status and the symptoms themselves, and cannot see "
            "your name, email, NIC or location because those are not on the "
            "list. Your exact age is replaced with a band such as 18 to 39. "
            "Third, any names that remain are replaced with placeholders. "
            "Fourth, the finished request is scanned for identifiers, and if "
            "one is found the request is blocked and recorded rather than "
            "sent."
        ),
    },
    {
        "id": "kb-pol-003",
        "title": "Private conversations and the PIN",
        "topic": "privacy",
        "text": (
            "A private chat is for anything you would not want appearing in a "
            "list on a shared phone — sexual health, an unplanned pregnancy, "
            "mental health. In a private chat the messages are never written "
            "to the database at all. Not encrypted, not hidden: never "
            "written. The conversation does not appear in your history, and "
            "its title is never taken from what you said. To reopen it you "
            "enter the six-digit PIN you chose when you started it. Five "
            "incorrect attempts delete the conversation rather than locking "
            "it. Everything disappears automatically after twelve hours. If "
            "you forget the PIN the conversation cannot be recovered by "
            "anyone, including SuwaPath staff — that is the point of it."
        ),
    },
    {
        "id": "kb-pol-004",
        "title": "What the assistant will and will not do",
        "topic": "safety",
        "text": (
            "The assistant helps you understand a symptom and get to the right "
            "care. It does not diagnose you and it does not prescribe. It will "
            "not tell you which medicine to take, what dose to take, or how to "
            "give yourself an injection, because those depend on an "
            "examination and on everything else you take. For a medicine "
            "question, any pharmacist will help free of charge. How urgent "
            "your situation is is never decided by the AI: a separate rule "
            "engine assesses that from your own words, and the AI cannot "
            "raise or lower it. The assistant is designed to say when it is "
            "unsure, and you should treat everything it says as a starting "
            "point for a clinician to confirm."
        ),
    },
    {
        "id": "kb-pol-005",
        "title": "Booking, changing and cancelling appointments",
        "topic": "appointments",
        "text": (
            "You can book from the chat by describing what you need, or from "
            "Doctors and Hospitals. Results show the doctor's specialty, "
            "their facility, the distance from you, the consultation fee and "
            "their next free slot, and each result explains why it was "
            "suggested. If a recommendation requires a particular capability "
            "such as a skin biopsy or an MRI, only facilities that provide it "
            "are offered. An appointment moves through pending, confirmed, "
            "checked in, in consultation and completed. You may cancel while "
            "it is pending or confirmed. Cancelling early helps: the slot goes "
            "back into availability for someone else, and repeated no-shows "
            "affect scheduling for everyone."
        ),
    },
    {
        "id": "kb-pol-006",
        "title": "Uploading reports and scans",
        "topic": "records",
        "text": (
            "You can upload a lab report as a PDF or a photograph. The system "
            "reads the text, keeps the table structure so each test stays with "
            "its own result, and flags values against the reference range "
            "printed on your own report rather than a generic range, because "
            "laboratories differ. Extracted values are always shown next to "
            "what was actually on the page so you can check them. Reading "
            "happens entirely on the SuwaPath server; report contents are not "
            "sent to an external AI service. For X-rays, screening produces a "
            "confidence score and a heatmap showing which region influenced "
            "it. Screening is not a diagnosis, and a radiologist or physician "
            "must review the original image."
        ),
    },
    {
        "id": "kb-pol-007",
        "title": "Care programmes and who can join",
        "topic": "programmes",
        "text": (
            "Care programmes add structured follow-up on top of ordinary "
            "appointments. The maternal programme tracks pregnancy weeks, "
            "scheduled checks and danger-sign check-ins, and continues into "
            "the postpartum period. The elderly programme tracks medications "
            "and adherence, and raises alerts to a consenting guardian when a "
            "pattern of missed doses or concerning check-ins appears. Any "
            "patient can enrol themselves from Care Programmes; a guardian can "
            "enrol a dependent only where the dependent has granted that. "
            "Enrolling does not change who can see your records — programme "
            "data follows the same consent rules as everything else. The "
            "confidential sexual-health pathway is deliberately not a "
            "programme you join, because joining would create a visible "
            "record; use a private chat instead."
        ),
    },
    {
        "id": "kb-pol-008",
        "title": "Languages and how they are handled",
        "topic": "platform",
        "text": (
            "You can use SuwaPath in English, Sinhala or Tamil, and you can "
            "type in whichever you prefer regardless of the interface "
            "language. Symptom matching does not work by translating your "
            "words into English first. Around ninety clinical concepts are "
            "mapped directly to the forms patients actually type in all three "
            "languages, including romanised Sinhala and Tamil, so a Sinhala "
            "description of chest pain and breathlessness triggers exactly the "
            "same emergency rule as the English one. Nothing about how "
            "urgently you are treated depends on which language you chose."
        ),
    },
    {
        "id": "kb-pol-009",
        "title": "Emergency numbers in Sri Lanka",
        "topic": "emergency",
        "text": (
            "Suwa Seriya ambulance is 1990 and is free nationwide. Police "
            "emergency is 110. The National Mental Health Helpline is 1926 and "
            "operates twenty-four hours. Sumithrayo offers emotional support "
            "on 1333. Go directly to the nearest emergency department, without "
            "waiting for advice from any app, for chest pain, difficulty "
            "breathing, heavy bleeding, sudden weakness or drooping on one "
            "side of the face, a first seizure, or a serious injury. Where "
            "possible call an ambulance rather than travelling by private "
            "vehicle, because treatment can begin on the way."
        ),
    },
    {
        "id": "kb-pol-010",
        "title": "Costs and what SuwaPath charges",
        "topic": "platform",
        "text": (
            "SuwaPath does not charge patients to search, to understand a "
            "report or to talk to the assistant. Consultation fees are set by "
            "the doctor or facility and are shown before you book, so there "
            "is no surprise at the counter. Diagnostic test prices vary "
            "considerably between facilities for the same test, and where a "
            "test is suggested the price range across facilities is shown so "
            "you can choose. Government hospital outpatient departments remain "
            "free, and are listed alongside private options rather than "
            "beneath them."
        ),
    },
]
