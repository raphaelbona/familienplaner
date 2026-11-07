/** "⭐ 4,3 (3)" with German decimal comma. */
export function formatRating(average: number, count: number): string {
  return `⭐ ${average.toFixed(1).replace(".", ",")} (${count})`;
}

export function isHttpsUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}

export function isParentRole(role: string | null): boolean {
  return role === "parent_admin" || role === "parent_member";
}
