/**
 * What to do while help is on the way.
 *
 * Every line here is hand-written and keyed to a specific red-flag rule id.
 * None of it is generated, paraphrased at runtime, or passed through a model.
 * That is the whole safety argument for the avatar: it can only ever say the
 * deterministic escalation message the rule engine produced, one of these
 * reviewed scripts, or text a doctor typed. An avatar that reads out model
 * output with a human face and a calm voice would be the single most
 * dangerous surface in the product.
 *
 * Scope rules these scripts follow, deliberately:
 *   - No medicine is ever named, not even aspirin. The rest of the platform
 *     refuses to name drugs or doses; a spoken channel does not get an
 *     exemption because it feels more helpful.
 *   - Nothing here requires equipment or training.
 *   - Nothing here delays calling for help. Step one is always the call.
 *   - Nothing contradicts the urgency the rule engine already decided.
 *
 * NOT YET CLINICALLY REVIEWED. These follow standard bystander first-aid
 * guidance, but they have not been signed off by a clinician, and they must be
 * before this is used with real patients.
 */

/** Sri Lanka's national ambulance service — free, nationwide. */
export const AMBULANCE = "1990 Suwa Seriya";

export interface FirstAidScript {
  /** Red-flag rule ids this applies to. */
  rules: string[];
  title: string;
  steps: string[];
  /** Things a well-meaning bystander commonly does that make it worse. */
  avoid?: string[];
}

export const FIRST_AID: FirstAidScript[] = [
  {
    rules: ["RF-CARD-001", "RF-CARD-002"],
    title: "While you wait — chest pain",
    steps: [
      `Call ${AMBULANCE} now.`,
      "Stop what you are doing and sit down. Rest your back against something.",
      "Loosen anything tight around your neck or waist.",
      "Stay still and try to breathe slowly. Someone should stay with you.",
    ],
    avoid: [
      "Do not drive yourself.",
      "Do not walk it off or wait to see if it passes.",
    ],
  },
  {
    rules: ["RF-CARD-003"],
    title: "While you wait — someone has collapsed",
    steps: [
      `Call ${AMBULANCE} now.`,
      "Check whether they are breathing normally.",
      "If they are breathing, roll them onto their side and tilt the head back a little so the airway stays open.",
      "Stay with them and keep watching their breathing until help arrives.",
    ],
    avoid: ["Do not put a pillow under their head.", "Do not leave them alone."],
  },
  {
    rules: ["RF-NEURO-001"],
    title: "While you wait — signs of a stroke",
    steps: [
      `Call ${AMBULANCE} now and say you think it is a stroke.`,
      "Note the time the symptoms started. Treatment depends on it.",
      "Help them sit or lie down with their head slightly raised.",
      "Stay with them and keep them calm.",
    ],
    avoid: [
      "Do not give food or drink, including water — swallowing may not be safe.",
      "Do not wait to see if it improves.",
    ],
  },
  {
    rules: ["RF-NEURO-003"],
    title: "While you wait — a sudden, worst-ever headache",
    steps: [
      `Call ${AMBULANCE} now and say the headache came on suddenly.`,
      "Note the exact time it started, and whether it reached its worst within seconds or minutes.",
      "Lie still somewhere dark and quiet, with the head slightly raised.",
      "Someone should stay and keep watching how awake and alert you are.",
    ],
    avoid: [
      "Do not drive yourself.",
      "Do not wait to see whether it eases off.",
      "Do not eat or drink in case you become drowsy.",
    ],
  },
  {
    rules: ["RF-NEURO-002"],
    title: "While you wait — fever with a stiff neck",
    steps: [
      `Call ${AMBULANCE} now and say there is fever with a stiff neck.`,
      "Note the time the fever and the neck stiffness started.",
      "Keep them lying down somewhere dim and quiet — bright light often hurts.",
      "Stay with them and keep checking that they are awake and answering you.",
      "If the ambulance is delayed, go straight to the nearest emergency department.",
    ],
    avoid: [
      "Do not wait to see whether the fever comes down.",
      "Do not give food or drink if they are drowsy.",
    ],
  },
  {
    rules: ["RF-NEURO-004", "RF-PAED-001"],
    title: "While you wait — a fit or convulsion",
    steps: [
      `Call ${AMBULANCE} now.`,
      "Move anything hard or sharp out of the way.",
      "Put something soft under the head.",
      "Note the time it started and how long it lasts.",
      "When the movements stop, roll them gently onto their side.",
    ],
    avoid: [
      "Do not hold them down.",
      "Do not put anything in their mouth.",
    ],
  },
  {
    rules: ["RF-BLEED-001"],
    title: "While you wait — heavy bleeding",
    steps: [
      `Call ${AMBULANCE} now.`,
      "Press firmly on the wound with a clean cloth and keep pressing.",
      "If you can, raise the injured part above the level of the heart.",
      "If the cloth soaks through, add another on top.",
    ],
    avoid: [
      "Do not lift the cloth to check — that restarts the bleeding.",
      "Do not tie anything tightly around a limb.",
    ],
  },
  {
    rules: ["RF-RESP-001"],
    title: "While you wait — difficulty breathing",
    steps: [
      `Call ${AMBULANCE} now.`,
      "Help them sit upright, leaning slightly forward.",
      "Loosen tight clothing around the neck and chest.",
      "Open a window or door for fresh air.",
      "If they have an inhaler or emergency medicine they were prescribed for this, help them use their own.",
    ],
    avoid: ["Do not make them lie flat."],
  },
  {
    rules: ["RF-MAT-001", "RF-MAT-002", "RF-MAT-003", "RF-MAT-004", "RF-MAT-005"],
    title: "While you wait — pregnancy or after birth",
    steps: [
      `Call ${AMBULANCE} now and say how many weeks pregnant, or how long since the birth.`,
      "Lie down on your left side.",
      "Take your maternal record book with you if it is nearby.",
      "Someone should stay with you until help arrives.",
    ],
    avoid: ["Do not travel alone."],
  },
  {
    rules: ["RF-GI-001"],
    title: "While you wait — bleeding from the stomach or bowel",
    steps: [
      `Call ${AMBULANCE} now.`,
      "Lie down and keep still.",
      "If you have vomited, keep a sample to show the doctor if you can do so easily.",
    ],
    avoid: ["Do not eat or drink anything."],
  },
  {
    rules: ["RF-MH-001"],
    title: "You do not have to handle this alone",
    steps: [
      "You can call 1926 for the National Mental Health Helpline, any time.",
      "If you are in immediate danger, call 1990 Suwa Seriya.",
      "If someone is with you, tell them how you are feeling right now.",
      "Move away from anything you could use to hurt yourself.",
    ],
  },
];

const BY_RULE = new Map<string, FirstAidScript>();
for (const script of FIRST_AID) {
  for (const rule of script.rules) BY_RULE.set(rule, script);
}

/**
 * The script for the most serious rule that fired, or null.
 *
 * Returns null rather than a generic fallback when nothing matches. There is
 * no safe generic first aid, and inventing reassuring filler for an
 * unrecognised emergency is exactly the failure this module exists to avoid —
 * the escalation message alone is the right output in that case.
 */
export function scriptForRules(ruleIds: string[]): FirstAidScript | null {
  for (const id of ruleIds) {
    const found = BY_RULE.get(id);
    if (found) return found;
  }
  return null;
}

/** The script rendered as one continuous passage for text-to-speech. */
export function spokenForm(script: FirstAidScript): string {
  const parts = [script.title, ...script.steps];
  if (script.avoid?.length) parts.push("Important.", ...script.avoid);
  return parts.join(" ");
}
