const PERSON_PALETTE = ["#e2793d", "#3f89a3", "#a35f97", "#5f8f61", "#c9a227", "#5f6fa3"];

/** A stable color per family member, cycling through a warm palette by position
 * in the (stably ordered) children list — so siblings never share a color. */
export function colorForPerson(index: number): string {
  return PERSON_PALETTE[index % PERSON_PALETTE.length];
}
