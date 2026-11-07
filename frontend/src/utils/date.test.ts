import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { localIsoDateOffset, todayIsoDate, tomorrowIsoDate } from "./date";

beforeEach(() => {
  vi.stubEnv("TZ", "Europe/Zurich");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("date helpers (Europe/Zurich)", () => {
  it("the test environment really uses a Zurich-like offset", () => {
    expect(new Date(2026, 6, 15, 12).getTimezoneOffset()).toBe(-120);
  });

  it("gives the local date shortly after local midnight (UTC is still yesterday)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 15, 0, 30)); // 00:30 local == 22:30 UTC the day before
    expect(new Date().toISOString().slice(0, 10)).toBe("2026-07-14");
    expect(todayIsoDate()).toBe("2026-07-15");
    expect(tomorrowIsoDate()).toBe("2026-07-16");
  });

  it("gives the local date shortly before local midnight", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 0, 31, 23, 45));
    expect(todayIsoDate()).toBe("2026-01-31");
    expect(tomorrowIsoDate()).toBe("2026-02-01");
  });

  it("offsets across month boundaries and backwards", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 2, 1, 0, 10));
    expect(localIsoDateOffset(-14)).toBe("2026-02-15");
    expect(localIsoDateOffset(6)).toBe("2026-03-07");
  });
});
