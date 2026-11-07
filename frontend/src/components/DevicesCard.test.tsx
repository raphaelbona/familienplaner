import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DevicesCard from "./DevicesCard";

function tokenFor(role: string) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role, sub: "u1" }));
  return `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`;
}

interface Device {
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  revoked: boolean;
}

function stubApi(devices: Device[]) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    let body: unknown = devices;
    let status = 200;
    if (method === "POST") {
      const label = JSON.parse(String(init?.body)).label;
      body = { id: "new1", label, device_token: "secret-token", expires_at: "2027-01-01T00:00:00Z" };
      status = 201;
      devices.unshift({
        id: "new1",
        label,
        created_at: "2026-09-20T00:00:00Z",
        last_used_at: null,
        expires_at: "2027-01-01T00:00:00Z",
        revoked: false,
      });
    } else if (method === "DELETE") {
      const id = String(url).split("/").pop();
      devices.forEach((d) => {
        if (d.id === id) d.revoked = true;
      });
      body = undefined;
      status = 204;
    }
    return Promise.resolve({ ok: true, status, json: async () => body });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsWith(fetchMock: ReturnType<typeof stubApi>, method: string) {
  return fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === method);
}

const existing: Device = {
  id: "d1",
  label: "Küchen-Tablet",
  created_at: "2026-09-01T00:00:00Z",
  last_used_at: "2026-09-10T12:00:00Z",
  expires_at: "2027-09-01T00:00:00Z",
  revoked: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("DevicesCard", () => {
  it("erklaert den Datenschutz-Grund und zeigt, dass dieses Geraet nicht freigegeben ist", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    stubApi([]);
    render(<DevicesCard />);

    expect(await screen.findByText(/Aus Datenschutzgründen/)).toBeInTheDocument();
    expect(screen.getByText(/nicht für Kinder freigegeben/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Bezeichnung/)).toHaveValue("Tablet");
  });

  it("gibt dieses Geraet frei, speichert das Token und zeigt eine Erfolgsmeldung", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    const fetchMock = stubApi([]);
    render(<DevicesCard />);

    const input = await screen.findByLabelText(/Bezeichnung/);
    fireEvent.change(input, { target: { value: "Wohnzimmer" } });
    fireEvent.click(screen.getByRole("button", { name: "Dieses Gerät für Kinder freigeben" }));

    expect(await screen.findByText(/jetzt für Kinder freigegeben/)).toBeInTheDocument();
    expect(localStorage.getItem("familienplaner_device_token")).toBe("secret-token");
    expect(localStorage.getItem("familienplaner_device_id")).toBe("new1");
    expect(localStorage.getItem("familienplaner_family_id")).toBe("fam1");
    const post = callsWith(fetchMock, "POST")[0];
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ label: "Wohnzimmer" });
    expect(screen.getByRole("button", { name: "Freigabe für dieses Gerät aufheben" })).toBeInTheDocument();
  });

  it("hebt die Freigabe fuer dieses Geraet auf und entfernt das Token lokal", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    localStorage.setItem("familienplaner_device_token", "tok");
    localStorage.setItem("familienplaner_device_id", "d1");
    const fetchMock = stubApi([{ ...existing }]);
    render(<DevicesCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Freigabe für dieses Gerät aufheben" }));

    await waitFor(() => expect(localStorage.getItem("familienplaner_device_token")).toBeNull());
    expect(localStorage.getItem("familienplaner_device_id")).toBeNull();
    expect(callsWith(fetchMock, "DELETE").some((c) => String(c[0]).endsWith("/families/devices/d1"))).toBe(true);
    expect(await screen.findByRole("button", { name: "Dieses Gerät für Kinder freigeben" })).toBeInTheDocument();
  });

  it("listet freigegebene Geraete mit letzter Nutzung und widerruft ein anderes Geraet", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    const fetchMock = stubApi([{ ...existing }]);
    render(<DevicesCard />);

    expect(await screen.findByText("Küchen-Tablet")).toBeInTheDocument();
    expect(screen.getByText(/Zuletzt benutzt: .*2026/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Widerrufen" }));

    await waitFor(() => expect(screen.queryByText("Küchen-Tablet")).not.toBeInTheDocument());
    expect(callsWith(fetchMock, "DELETE")).toHaveLength(1);
    // this device was never paired, so nothing local changes
    expect(localStorage.getItem("familienplaner_device_token")).toBeNull();
  });
});
