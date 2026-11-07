import { describe, expect, it } from "vitest";
import { computeWallZoom } from "./wallZoom";

describe("computeWallZoom", () => {
  it("lässt normale Tablet- und Handybreiten unverändert", () => {
    expect(computeWallZoom(375)).toBe(1);
    expect(computeWallZoom(1024)).toBe(1);
    expect(computeWallZoom(1280)).toBe(1);
  });

  it("skaliert sehr breite Kiosk-Viewports auf eine ca. 1280px breite Oberfläche", () => {
    expect(computeWallZoom(1920)).toBe(1.5);
    expect(computeWallZoom(2400)).toBe(1.88);
    expect(computeWallZoom(2560)).toBe(2);
  });

  it("begrenzt den Faktor nach oben und fängt ungültige Werte ab", () => {
    expect(computeWallZoom(6000)).toBe(2.25);
    expect(computeWallZoom(Number.NaN)).toBe(1);
    expect(computeWallZoom(0)).toBe(1);
  });
});
