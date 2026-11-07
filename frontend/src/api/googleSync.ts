import { apiFetch, apiFetchWithCookies } from "./client";

export type GoogleCalendar = {
  id: string;
  name: string;
  selected: boolean;
  owner_user_ids: string[];
};

export type GoogleSyncStatus = {
  configured: boolean;
  connected: boolean;
  email: string | null;
  /** "ok" | "needs_reauth" | "error" (null while not connected) */
  status: "ok" | "needs_reauth" | "error" | null;
  last_synced_at: string | null;
  last_error: string | null;
  calendars: GoogleCalendar[];
};

export type GoogleCalendarSelection = { id: string; owner_user_ids: string[] };

const BASE = "/calendar/sync/google";

export function fetchGoogleStatus(): Promise<GoogleSyncStatus> {
  return apiFetch<GoogleSyncStatus>(`${BASE}/status`);
}

export function saveGoogleCalendars(calendars: GoogleCalendarSelection[]): Promise<GoogleSyncStatus> {
  return apiFetch<GoogleSyncStatus>(`${BASE}/calendars`, { method: "PUT", body: JSON.stringify({ calendars }) });
}

export function syncGoogleNow(): Promise<GoogleSyncStatus> {
  return apiFetch<GoogleSyncStatus>(`${BASE}/sync-now`, { method: "POST" });
}

export function disconnectGoogle(removeEvents: boolean): Promise<void> {
  return apiFetch<void>(`${BASE}?remove_events=${removeEvents ? "true" : "false"}`, { method: "DELETE" });
}

/** Starts the OAuth flow: the API sets its state cookie in this response, so the call must send credentials. */
export async function requestGoogleAuthorizationUrl(): Promise<string> {
  const { authorization_url } = await apiFetchWithCookies<{ authorization_url: string }>(`${BASE}/connect`, {
    method: "POST",
  });
  return authorization_url;
}
