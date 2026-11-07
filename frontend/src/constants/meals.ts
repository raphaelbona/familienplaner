export type MealType = "breakfast" | "lunch" | "dinner";

export const MEAL_TYPES: { value: MealType; label: string }[] = [
  { value: "breakfast", label: "Frühstück" },
  { value: "lunch", label: "Mittagessen" },
  { value: "dinner", label: "Abendessen" }
];

export const MEAL_LABELS: Record<string, string> = Object.fromEntries(MEAL_TYPES.map((m) => [m.value, m.label]));
