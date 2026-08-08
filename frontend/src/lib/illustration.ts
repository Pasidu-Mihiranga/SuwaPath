/**
 * Choosing an illustration for a person.
 *
 * Showing someone a picture that looks a bit like them is a small thing that
 * makes a health product feel like it was built for them rather than at them.
 * Getting it wrong is worse than not trying — an elderly Sri Lankan woman
 * shown a young man with a smartphone learns that this product did not have
 * her in mind.
 *
 * So the rules here are conservative, in this order:
 *
 * 1. **Only use what the patient told us.** Sex comes from their own profile
 *    field. Nothing is inferred from a name — names do not carry gender
 *    reliably in any language, and Sri Lankan names carry it less reliably
 *    than most.
 * 2. **Unknown is a first-class case, not a failure.** "Prefer not to say",
 *    "other", and an empty profile all resolve to a neutral illustration that
 *    was chosen to work for anyone. There is no "default = male".
 * 3. **Never block on it.** Every path returns something. A missing
 *    illustration must never leave a hole in the layout.
 *
 * These are decorative. They are marked `aria-hidden` at the call site and
 * carry no information that is not also in text, so a patient who sees the
 * neutral variant loses nothing but a small piece of warmth.
 */

const BASE = "/illustrations";

/** What a profile can tell us. Anything unrecognised becomes `unknown`. */
export type Sex = "male" | "female" | "unknown";

export function normaliseSex(value?: string | null): Sex {
  const sex = (value ?? "").trim().toLowerCase();
  if (sex === "male" || sex === "m") return "male";
  if (sex === "female" || sex === "f") return "female";
  // "other", "prefer_not_to_say", "intersex", "", null — all land here, and
  // that is a legitimate answer rather than missing data to be guessed at.
  return "unknown";
}

/** Elderly illustrations are used from the age the elderly programme uses. */
const ELDER_FROM = 65;

function isElder(age?: number | null, ageBand?: string | null): boolean {
  if (typeof age === "number") return age >= ELDER_FROM;
  return (ageBand ?? "").includes("elderly") || (ageBand ?? "").includes("65");
}

export interface PersonLike {
  sex?: string | null;
  age?: number | null;
  age_band?: string | null;
}

/**
 * The illustration for a patient viewing their own space.
 *
 * When sex is unknown we show the adult-female illustration rather than
 * inventing a third asset. That is a deliberate, stated choice: the set we
 * have is male/female only, and defaulting to male is the more common and
 * more alienating error. Swap `NEUTRAL_*` below if a neutral asset is added.
 */
const NEUTRAL_ADULT = `${BASE}/patient-adult-female.webp`;
const NEUTRAL_ELDER = `${BASE}/patient-elder-female.webp`;

export function patientIllustration(person: PersonLike = {}): string {
  const sex = normaliseSex(person.sex);
  const elder = isElder(person.age, person.age_band);

  if (sex === "unknown") return elder ? NEUTRAL_ELDER : NEUTRAL_ADULT;
  return `${BASE}/patient-${elder ? "elder" : "adult"}-${sex}.webp`;
}

/**
 * The illustration for a guardian and the dependent they care for.
 *
 * Two people, so two unknowns. Rather than a combinatorial explosion of
 * fallbacks, an unknown on either side resolves to the same neutral pairing.
 */
export function guardianIllustration(
  dependant: PersonLike = {},
  carer: PersonLike = {},
): string {
  const dependantSex = normaliseSex(dependant.sex);
  const carerSex = normaliseSex(carer.sex);

  if (dependantSex === "unknown" || carerSex === "unknown") {
    return `${BASE}/guardian-elder-female-carer-female.webp`;
  }
  return `${BASE}/guardian-elder-${dependantSex}-carer-${carerSex}.webp`;
}

/**
 * Care-programme artwork.
 *
 * Fixed per programme, deliberately not personalised. A programme card is
 * about the programme, not about the viewer — the per-person portraits belong
 * to the dashboard hero, where the patient is the subject.
 */
export function programmeIllustration(programmeType?: string | null): string | null {
  const type = (programmeType ?? "").toLowerCase();
  if (type.includes("maternal") || type.includes("postpartum")) {
    return `${BASE}/programme-maternal.webp`;
  }
  if (type.includes("elder")) return `${BASE}/programme-elderly.webp`;
  // The confidential programme deliberately has no illustration: a
  // recognisable image on a sexual-health card is exactly the kind of thing
  // someone glancing at a shared phone would notice.
  return null;
}

export const loginIllustration = {
  desktop: `${BASE}/login-desktop.webp`,
  mobile: `${BASE}/login-mobile.webp`,
};
