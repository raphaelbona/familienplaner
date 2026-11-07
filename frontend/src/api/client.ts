const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const TOKEN_STORAGE_KEY = "familienplaner_token";
const FAMILY_ID_STORAGE_KEY = "familienplaner_family_id";
const DEVICE_TOKEN_STORAGE_KEY = "familienplaner_device_token";
const DEVICE_ID_STORAGE_KEY = "familienplaner_device_id";

/** Detail the backend sends with a 401 when a device is unknown, revoked, expired or of another family. */
export const DEVICE_UNAUTHORIZED_MESSAGE = "Gerät nicht freigegeben";

/** An API error that keeps the HTTP status, so callers can tell "device not approved" from "wrong PIN". */
export class ApiError extends Error {
  status: number;
  /** Seconds from the Retry-After header (429 responses), if present. */
  retryAfterSeconds?: number;
  constructor(message: string, status: number, retryAfterSeconds?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export function getFamilyId(): string | null {
  return localStorage.getItem(FAMILY_ID_STORAGE_KEY);
}

export function setFamilyId(familyId: string): void {
  localStorage.setItem(FAMILY_ID_STORAGE_KEY, familyId);
}

export function clearFamilyId(): void {
  localStorage.removeItem(FAMILY_ID_STORAGE_KEY);
}

// The device token proves that a parent approved this device for the children's login. It is deliberately NOT
// removed on logout (clearToken): only "Freigabe aufheben" or a 401 from the server removes it.
export function getDeviceToken(): string | null {
  return localStorage.getItem(DEVICE_TOKEN_STORAGE_KEY);
}

export function isDevicePaired(): boolean {
  return Boolean(getDeviceToken());
}

export function setDeviceToken(token: string, deviceId?: string): void {
  localStorage.setItem(DEVICE_TOKEN_STORAGE_KEY, token);
  if (deviceId) localStorage.setItem(DEVICE_ID_STORAGE_KEY, deviceId);
}

export function getDeviceId(): string | null {
  return localStorage.getItem(DEVICE_ID_STORAGE_KEY);
}

export function clearDeviceToken(): void {
  localStorage.removeItem(DEVICE_TOKEN_STORAGE_KEY);
  localStorage.removeItem(DEVICE_ID_STORAGE_KEY);
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    // JWT format: header.payload.signature; payload is base64url encoded
    const parts = token.split(".");
    if (parts.length !== 3) return null;

    // Convert base64url to base64: replace - with +, _ with /, then add padding
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    return JSON.parse(atob(padded));
  } catch {
    return null;
  }
}

export function decodeFamilyIdFromToken(token: string): string | null {
  const payload = decodeJwtPayload(token);
  return (payload?.family_id as string) ?? null;
}

export function decodeRoleFromToken(token: string): string | null {
  const payload = decodeJwtPayload(token);
  return (payload?.role as string) ?? null;
}

export function decodeUserIdFromToken(token: string): string | null {
  const payload = decodeJwtPayload(token);
  return (payload?.sub as string) ?? null;
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  // FormData bodies (file uploads) must NOT get an explicit Content-Type: the
  // browser sets multipart/form-data with the correct boundary itself.
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const retryAfter = Number(response.headers?.get?.("Retry-After"));
    throw new ApiError(
      body.detail ?? `Anfrage fehlgeschlagen (${response.status})`,
      response.status,
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/**
 * Like apiFetch, but the browser also stores and sends cookies for the API origin (credentials: "include").
 * Needed where the API sets a cookie in its response (the Google OAuth state cookie): in production the frontend
 * (app.*) and the API (api.*) are different origins of the same site, and without credentials the browser
 * ignores Set-Cookie on such a cross-origin fetch. Only use it for calls that really need cookies.
 */
export function apiFetchWithCookies<T>(path: string, options: RequestInit = {}): Promise<T> {
  return apiFetch<T>(path, { ...options, credentials: "include" });
}

export async function apiFetchBlob(path: string, options: RequestInit = {}): Promise<Blob> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? `Anfrage fehlgeschlagen (${response.status})`);
  }
  return response.blob();
}
