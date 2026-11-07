import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiFetchWithCookies } from "./client";

afterEach(() => vi.unstubAllGlobals());

function stubFetch() {
  const fetchMock = vi.fn(() =>
    Promise.resolve({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => ({ ok: true }) })
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("apiFetchWithCookies", () => {
  it("sendet und speichert Cookies (credentials: include), apiFetch bleibt ohne", async () => {
    const fetchMock = stubFetch();

    await apiFetchWithCookies("/calendar/sync/google/connect", { method: "POST" });
    await apiFetch("/calendar/events");

    const [withCookies, plain] = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(withCookies[1].credentials).toBe("include");
    expect(withCookies[1].method).toBe("POST");
    expect(plain[1].credentials).toBeUndefined();
  });
});
