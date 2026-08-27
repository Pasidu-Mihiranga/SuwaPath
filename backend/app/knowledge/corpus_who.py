"""General WHO-aligned patient guidance — the topics any health assistant meets.

The Sri Lanka corpus covers what is locally distinctive; this covers what is
universal, and it exists because a judge or a patient will ask about ordinary
things — antibiotics, back pain, a burn, breastfeeding, smoking — and grounded
answers are better than a model improvising from memory.

Same rules as the rest of the knowledge base:

* **Patient education, not clinical protocol.** When to seek care and what to
  expect. Never how to treat.
* **No medicine and no dose appears anywhere.** Where a drug class is
  unavoidable (antibiotics, painkillers) it is discussed as a category and the
  choice is always a clinician's.
* Written to be retrieved as one idea per passage, since the index chunks and
  scores each on its own.
* Paraphrased from WHO patient-facing guidance. **Not clinician-reviewed.**

Deliberately excluded: anything a patient could act on in a way that replaces
seeing a doctor. There is no "how to manage your asthma attack at home" here,
because the honest version of that passage is "see a doctor and follow the
plan they gave you", and the dishonest version is dangerous.
"""

from __future__ import annotations

from app.knowledge.corpus import KnowledgeDoc

WHO = "WHO"

CORPUS_WHO: list[KnowledgeDoc] = [
    # ---------------- Antimicrobial resistance -----------------------------
    KnowledgeDoc(
        "who-amr-001", "Antibiotics: why they are not for every illness",
        "infection", "patient",
        "Antibiotics work against bacteria and do nothing at all against "
        "viruses, so they will not help a common cold, most sore throats, flu "
        "or ordinary viral fever. Taking them when they are not needed still "
        "carries the side effects and helps bacteria become resistant, which "
        "means the antibiotic may not work when it is genuinely needed later. "
        "Never take antibiotics left over from a previous illness or given to "
        "someone else, and never stop a prescribed course early because you "
        "feel better. If a doctor decides an infection is bacterial they will "
        "choose the antibiotic; that decision depends on the site of "
        "infection, local resistance patterns and your allergies.",
        f"{WHO} antimicrobial resistance guidance",
    ),

    # ---------------- Pain and musculoskeletal -----------------------------
    KnowledgeDoc(
        "who-msk-001", "Low back pain: what usually helps and what worries a doctor",
        "musculoskeletal", "patient",
        "Most low back pain is mechanical, improves within weeks, and does not "
        "indicate damage. Staying gently active is better than bed rest, which "
        "tends to prolong it. What changes the picture entirely is back pain "
        "with any of these: numbness around the groin or inner thighs, loss of "
        "bladder or bowel control, weakness in a leg, pain after a significant "
        "fall, fever, unexplained weight loss, or a history of cancer. Those "
        "need urgent assessment rather than rest, because they point to nerve "
        "compression or a cause other than the muscles.",
        f"{WHO} musculoskeletal conditions guidance",
    ),
    KnowledgeDoc(
        "who-hea-001", "Headache: the ordinary kinds and the ones that are not",
        "neurological", "patient",
        "Most headaches are tension-type or migraine. Tension headache is a "
        "dull band-like pressure; migraine is often one-sided and throbbing "
        "with nausea and dislike of light and noise, and can follow a visual "
        "aura. Both are treatable and neither is dangerous in itself. Seek "
        "care urgently for a headache that reaches its worst within seconds, "
        "the worst headache of your life, headache with fever and a stiff "
        "neck, with new weakness, confusion or visual loss, after a head "
        "injury, or one that is steadily worsening over days and is worse in "
        "the morning or on coughing. Frequent use of painkillers for headache "
        "can itself cause daily headache, which is worth raising with a doctor.",
        f"{WHO} / general neurological patient education",
    ),

    # ---------------- Injuries and first contact ---------------------------
    KnowledgeDoc(
        "who-inj-001", "Burns: immediate care and when to go to hospital",
        "injury", "patient",
        "Cool a burn under cool running water for twenty minutes, as soon as "
        "possible and up to three hours after the injury — this genuinely "
        "reduces the depth of damage. Remove rings, watches and clothing near "
        "the area before swelling starts, unless material is stuck to the "
        "skin. Cover loosely with clean cling film or a clean cloth. Do not "
        "use ice, butter, oil, toothpaste or ash; do not burst blisters. Go to "
        "hospital for any burn larger than the palm of the hand, any burn on "
        "the face, hands, feet, genitals or across a joint, any burn that "
        "looks white or leathery or is painless, any electrical or chemical "
        "burn, and any burn in a young child or older adult.",
        f"{WHO} burns guidance",
    ),
    KnowledgeDoc(
        "who-inj-002", "Cuts and wound care", "injury", "patient",
        "Press firmly with a clean cloth to stop bleeding, then rinse the "
        "wound under clean running water and cover it. Seek medical care if "
        "bleeding does not stop after ten minutes of firm pressure, if the "
        "wound is deep, gaping or on the face, if something is embedded in "
        "it, if it was caused by an animal or human bite, if it happened in "
        "soil, mud or dirty water, or if the person's tetanus vaccination is "
        "not up to date. Signs of infection appearing over the following days "
        "— spreading redness, increasing pain, swelling, pus, or fever — need "
        "review rather than waiting.",
        f"{WHO} wound care guidance",
    ),

    # ---------------- Gastrointestinal -------------------------------------
    KnowledgeDoc(
        "who-gi-001", "Diarrhoea and dehydration", "gastrointestinal", "patient",
        "Most acute diarrhoea settles on its own, and the thing that actually "
        "matters is fluid replacement rather than stopping the diarrhoea. Oral "
        "rehydration salts are the standard treatment and are widely "
        "available; continue eating and, for infants, continue breastfeeding "
        "throughout. Seek care for blood in the stool, persistent vomiting, "
        "fever, diarrhoea lasting more than three days, or any sign of "
        "dehydration — sunken eyes, very little urine, drowsiness, or in a "
        "small child crying without tears. Anti-diarrhoeal medicines are not "
        "suitable for everyone, particularly children and anyone with blood "
        "in the stool, so that is a decision for a clinician.",
        f"{WHO} diarrhoeal disease guidance",
    ),
    KnowledgeDoc(
        "who-gi-002", "Food and water safety", "gastrointestinal", "patient",
        "Most food-borne illness is preventable with a few habits: wash hands "
        "with soap before preparing food and after using the toilet, keep raw "
        "meat and fish separate from food that will not be cooked again, cook "
        "food thoroughly, keep cooked food out of the danger zone between "
        "room temperature and refrigeration rather than leaving it standing, "
        "and use safe water for drinking and for washing anything eaten raw. "
        "During flooding or after a supply interruption, boil drinking water "
        "or use a reliable treatment method.",
        f"{WHO} five keys to safer food",
    ),

    # ---------------- Respiratory ------------------------------------------
    KnowledgeDoc(
        "who-res-001", "Asthma: what good control looks like", "respiratory", "patient",
        "Asthma is well controlled when you can sleep through the night, take "
        "part in normal activity and exercise, and rarely need your reliever "
        "inhaler. Needing a reliever several times a week, waking at night "
        "with symptoms, or increasing use over weeks all mean the condition "
        "is not controlled and the plan needs reviewing — not that you should "
        "simply use more reliever. Seek emergency care for breathlessness "
        "that makes it hard to speak in full sentences, lips or fingers "
        "turning blue, or a reliever that is not working as it usually does. "
        "Inhaler technique is worth checking at every review, because a large "
        "share of poor control is technique rather than the medicine.",
        f"{WHO} chronic respiratory disease guidance",
    ),

    # ---------------- Prevention and lifestyle -----------------------------
    KnowledgeDoc(
        "who-pre-001", "Tobacco, betel and alcohol", "prevention", "patient",
        "Smoking and smokeless tobacco both cause cancer, heart and lung "
        "disease, and betel quid with tobacco is strongly associated with oral "
        "cancer — a persistent mouth ulcer, a white or red patch, or "
        "difficulty opening the mouth should be examined without delay. There "
        "is no safe level of tobacco use, and stopping brings measurable "
        "benefit within weeks at any age. For alcohol, the risk of liver "
        "disease, several cancers and injury rises with the amount consumed, "
        "and there is no amount established as protective. Support to stop is "
        "available through clinics and is more effective than willpower alone.",
        f"{WHO} tobacco and alcohol guidance",
    ),
    KnowledgeDoc(
        "who-pre-002", "Physical activity and weight", "prevention", "patient",
        "Adults benefit from at least 150 minutes a week of moderate activity — "
        "brisk walking counts — plus muscle-strengthening twice a week. Some "
        "activity is substantially better than none, and it need not be done "
        "in long sessions. Regular activity lowers blood pressure and blood "
        "sugar, improves sleep and mood, and reduces falls in older adults. "
        "Anyone with heart disease, uncontrolled blood pressure, or who "
        "becomes breathless or has chest pain on exertion should get advice "
        "before starting a new exercise programme rather than pushing through.",
        f"{WHO} physical activity guidelines",
    ),
    KnowledgeDoc(
        "who-pre-003", "Salt, sugar and everyday diet", "prevention", "patient",
        "Reducing salt lowers blood pressure and stroke risk, and most salt "
        "comes from processed and preserved foods — dried fish, salted snacks, "
        "sauces, instant noodles and papadam — rather than the salt shaker. "
        "Sugary drinks are a concentrated source of calories with no "
        "satiety, and cutting them is one of the more effective single "
        "changes. A diet built around vegetables, fruit, whole grains and "
        "pulses, with fish and limited red and processed meat, supports blood "
        "pressure, blood sugar and weight together. Changes that are small and "
        "sustained outperform restrictive diets that are abandoned.",
        f"{WHO} healthy diet guidance",
    ),
    KnowledgeDoc(
        "who-pre-004", "Sleep and its effect on health", "prevention", "patient",
        "Most adults need seven to nine hours of sleep. Persistent poor sleep "
        "worsens blood pressure, blood sugar control, mood and concentration, "
        "and is a common and treatable problem rather than something to "
        "endure. Regular sleep and waking times, daylight in the morning, "
        "limiting caffeine after midday, and keeping screens out of the last "
        "hour help most people. Insomnia lasting more than a few weeks, loud "
        "snoring with pauses in breathing, or severe daytime sleepiness should "
        "be assessed — the last two can indicate sleep apnoea, which is "
        "treatable and carries cardiovascular risk if it is not.",
        f"{WHO} / general sleep health education",
    ),

    # ---------------- Allergy ----------------------------------------------
    KnowledgeDoc(
        "who-all-001", "Allergic reactions and anaphylaxis", "allergy", "patient",
        "Mild allergic reactions cause itching, a rash or hives, and localised "
        "swelling. Anaphylaxis is different and is an emergency: swelling of "
        "the lips, tongue or throat, difficulty breathing or noisy breathing, "
        "a tight chest, feeling faint or collapsing, or sudden severe "
        "abdominal symptoms after an exposure. Call an ambulance immediately, "
        "help the person lie flat with their legs raised unless breathing is "
        "easier sitting up, and if they carry an adrenaline auto-injector "
        "prescribed for them, help them use their own. Anyone who has had "
        "anaphylaxis needs specialist review and a written emergency plan, and "
        "should always tell clinicians about the allergy before any treatment.",
        f"{WHO} / allergy patient education",
    ),

    # ---------------- Maternal, newborn, reproductive ----------------------
    KnowledgeDoc(
        "who-mnh-001", "Breastfeeding in the early weeks", "maternal", "patient",
        "Exclusive breastfeeding is recommended for the first six months, with "
        "continued breastfeeding alongside other foods afterwards. Feeding "
        "frequently, including at night, is normal and is what establishes "
        "supply. Signs that feeding is going well are regular wet nappies, "
        "steady weight gain after the first days, and a baby who settles after "
        "feeds. Seek help early for painful or damaged nipples, a hard painful "
        "area in the breast with fever, a baby who is difficult to wake for "
        "feeds, or concern about supply — most of these are fixable with "
        "positioning support from a midwife rather than by stopping.",
        f"{WHO} infant and young child feeding guidance",
    ),
    KnowledgeDoc(
        "who-mnh-002", "Family planning and contraception", "sexual_health", "patient",
        "Several reliable contraceptive methods are available free through "
        "government clinics, and they differ in how they are used, how "
        "reversible they are, and how they suit different medical histories — "
        "blood pressure, migraine with aura, smoking, breastfeeding and "
        "clotting history all affect which are appropriate. That makes it a "
        "conversation with a clinician rather than a choice from a list. "
        "Emergency contraception exists and is more effective the sooner it is "
        "taken after unprotected sex. Only condoms also protect against "
        "sexually transmitted infections, so they are worth using alongside "
        "another method where that risk exists.",
        f"{WHO} family planning guidance",
    ),

    # ---------------- Cancer awareness -------------------------------------
    KnowledgeDoc(
        "who-can-001", "Symptoms worth having checked without delay",
        "oncology", "patient",
        "Most of these turn out to be something ordinary, and the reason to "
        "act is that the ones that are not are far more treatable when found "
        "early. Have a doctor look at: a lump anywhere, particularly in the "
        "breast; a mole changing in size, shape or colour, or one that bleeds "
        "or will not heal; a cough or hoarseness lasting more than three "
        "weeks; blood in the urine, stool or when coughing; a persistent "
        "change in bowel habit; difficulty swallowing; unexplained weight "
        "loss; a mouth ulcer or white patch that has not healed in three "
        "weeks; and any bleeding after menopause or between periods. Screening "
        "programmes exist for some cancers and are worth attending when "
        "invited even with no symptoms.",
        f"{WHO} cancer early detection guidance",
    ),

    # ---------------- Eyes, ears, teeth ------------------------------------
    KnowledgeDoc(
        "who-eye-001", "Eye symptoms that need seeing quickly", "ophthalmology", "patient",
        "Sudden loss of vision in one or both eyes, a curtain or shadow coming "
        "across the vision, a sudden shower of new floaters or flashing "
        "lights, a painful red eye with reduced vision or sensitivity to "
        "light, or double vision that has just started should be seen the same "
        "day. Gradual blurring, difficulty reading, and dry irritated eyes are "
        "not emergencies but are worth a routine eye test. Anyone with "
        "diabetes needs regular retinal screening regardless of whether their "
        "sight seems fine, because diabetic damage is silent until it is "
        "advanced.",
        f"{WHO} eye care guidance",
    ),
    KnowledgeDoc(
        "who-den-001", "Dental pain and oral health", "dental", "patient",
        "Toothache is usually decay or gum disease and needs a dentist rather "
        "than repeated painkillers, which only postpone it. Facial swelling "
        "with a toothache, fever, difficulty swallowing or opening the mouth "
        "indicates a spreading dental infection and needs urgent care the same "
        "day. Day to day, brushing twice with fluoride toothpaste, cleaning "
        "between the teeth, and limiting how often sugary food and drink are "
        "consumed matter more than the total amount. A mouth ulcer, white or "
        "red patch lasting more than three weeks should be examined, "
        "especially for anyone who chews betel or uses tobacco.",
        f"{WHO} oral health guidance",
    ),

    # ---------------- Understanding the assistant itself -------------------
    KnowledgeDoc(
        "who-ai-001", "What a health assistant can and cannot do", "general", "patient",
        "A digital health assistant can help you describe symptoms clearly, "
        "point you to the right kind of care and how soon, explain what a test "
        "or report means in plain language, and handle appointments and "
        "reminders. It cannot examine you, and examination is how a great deal "
        "of medicine is actually decided — so it cannot diagnose, and it "
        "cannot prescribe. Treat what it tells you as a starting point that "
        "helps you have a better conversation with a clinician, not as a "
        "substitute for one. If it advises emergency care, act on that "
        "immediately rather than seeking a second opinion from it.",
        f"{WHO} ethics and governance of artificial intelligence for health",
    ),
]
