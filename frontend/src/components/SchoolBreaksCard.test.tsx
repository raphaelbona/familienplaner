import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchoolBreak } from "../api/schoolBreaks";
import { localIsoDateOffset } from "../utils/date";
import SchoolBreaksCard from "./SchoolBreaksCard";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const MIA = { id: "c-mia", display_name: "Mia", avatar_key: "🦊", color_index: 0 };
const LEO = { id: "c-leo", display_name: "Leo", avatar_key: "🐯", color_index: 1 };

function brk(extra: Partial<SchoolBreak> & { id: string }): SchoolBreak {
  return {
    title: "Herbstferien",
    kind: "ferien",
    start_date: "2999-10-12",
    end_date: "2999-10-23",
    child_user_ids: [],
    source: "manual",
    ...extra,
  };
}

type Call = { method: string; path: string; body?: Record<string, unknown> };

function stubApi(initial: SchoolBreak[], opts: { fail?: { method: string; status: number; detail: string } } = {}) {
  let rows = [...initial];
  const calls: Call[] = [];
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    calls.push({ method, path, body });
    const respond = (data: unknown, status = 200) =>
      Promise.resolve({
        ok: status < 400,
        status,
        statusText: "",
        headers: new Headers(),
        json: async () => data,
      });
    if (path === "/families/children") return respond([MIA, LEO]);
    if (opts.fail && method === opts.fail.method && path.startsWith("/school-breaks")) {
      return respond({ detail: opts.fail.detail }, opts.fail.status);
    }
    if (path.startsWith("/school-breaks") && method === "GET") return respond(rows);
    if (path === "/school-breaks" && method === "POST") {
      const created = brk({ id: `new-${rows.length}`, source: "manual", ...(body as Partial<SchoolBreak>) });
      rows = [...rows, created];
      return respond(created, 201);
    }
    const match = /^\/school-breaks\/(.+)$/.exec(path);
    if (match && method === "PATCH") {
      rows = rows.map((r) => (r.id === match[1] ? { ...r, ...(body as Partial<SchoolBreak>) } : r));
      return respond(rows.find((r) => r.id === match[1]));
    }
    if (match && method === "DELETE") {
      rows = rows.filter((r) => r.id !== match[1]);
      return Promise.resolve({ ok: true, status: 204, statusText: "", headers: new Headers(), json: async () => undefined });
    }
    return Promise.reject(new Error(`Unknown endpoint: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

async function list() {
  return screen.findByRole("list", { name: "Ferien und freie Tage" });
}

describe("SchoolBreaksCard: Liste", () => {
  it("zeigt Titel, Datumsbereich, Art-Icon und Kinder-Pillen", async () => {
    stubApi([
      brk({ id: "b1", child_user_ids: [MIA.id] }),
      brk({ id: "b2", title: "Brückentag", kind: "schulfrei", start_date: "2999-11-02", end_date: "2999-11-02" }),
    ]);
    render(<SchoolBreaksCard canEdit={false} />);

    const rows = within(await list()).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Herbstferien")).toBeInTheDocument();
    expect(within(rows[0]).getByText("12.10. – 23.10.2999")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("img", { name: "Ferien" })).toBeInTheDocument();
    expect(rows[0].querySelector('[data-icon="beach"]')).not.toBeNull();
    await waitFor(() => expect(within(rows[0]).getByText("Mia")).toBeInTheDocument());
    expect(within(rows[0]).queryByText("Alle")).toBeNull();

    expect(within(rows[1]).getByText("02.11.2999")).toBeInTheDocument(); // single day
    expect(within(rows[1]).getByRole("img", { name: "Schulfrei" })).toBeInTheDocument();
    expect(rows[1].querySelector('[data-icon="school-off"]')).not.toBeNull();
    expect(within(rows[1]).getByText("Alle")).toBeInTheDocument(); // empty list = all
  });

  it("färbt die Kinder-Pille in der Personenfarbe des Kindes", async () => {
    stubApi([brk({ id: "b1", child_user_ids: [LEO.id] })]);
    render(<SchoolBreaksCard canEdit={false} />);

    const pill = await screen.findByText("Leo");
    // palette index 1 = #3f89a3
    await waitFor(() => expect((pill.closest(".break-pill") as HTMLElement).style.borderColor).toBe("rgb(63, 137, 163)"));
  });

  it("sortiert nach Beginn, vergangene danach und gedämpft", async () => {
    const past = localIsoDateOffset(-20);
    const pastEnd = localIsoDateOffset(-15);
    stubApi([
      brk({ id: "p", title: "Sommerferien", start_date: past, end_date: pastEnd }),
      brk({ id: "b", title: "Später", start_date: "2999-12-20", end_date: "2999-12-31" }),
      brk({ id: "a", title: "Früher", start_date: "2999-10-12", end_date: "2999-10-13" }),
    ]);
    render(<SchoolBreaksCard canEdit={false} />);

    const rows = within(await list()).getAllByRole("listitem");
    expect(rows.map((r) => within(r).getByText(/Früher|Später|Sommerferien/).textContent)).toEqual([
      "Früher",
      "Später",
      "Sommerferien",
    ]);
    expect(rows[2]).toHaveClass("break-row--past");
    expect(rows[0]).not.toHaveClass("break-row--past");
  });

  it("zeigt bei leerer Liste einen Hinweis", async () => {
    stubApi([]);
    render(<SchoolBreaksCard canEdit={false} />);
    expect(await screen.findByText("Keine Ferien eingetragen.")).toBeInTheDocument();
  });

  it("holt die Liste mit einem Zeitfenster", async () => {
    const { calls } = stubApi([]);
    render(<SchoolBreaksCard canEdit={false} />);
    await screen.findByText("Keine Ferien eingetragen.");

    const get = calls.find((c) => c.method === "GET" && c.path.startsWith("/school-breaks"))!;
    const params = new URL(`http://x${get.path}`).searchParams;
    expect(params.get("from")).toBe(localIsoDateOffset(-30));
    expect(params.get("to")).toBe(localIsoDateOffset(365));
  });

  it("zeigt einen Ladefehler im Fehlerstil", async () => {
    stubApi([], { fail: { method: "GET", status: 500, detail: "Server kaputt" } });
    render(<SchoolBreaksCard canEdit={false} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Server kaputt");
  });
});

describe("SchoolBreaksCard: ohne Berechtigung", () => {
  it("zeigt nur die Liste, ohne Hinzufügen, Bearbeiten und Löschen", async () => {
    stubApi([brk({ id: "b1" }), brk({ id: "g1", title: "Schulfrei", source: "google", start_date: "2999-11-02", end_date: "2999-11-02" })]);
    render(<SchoolBreaksCard canEdit={false} />);

    await list();
    expect(screen.queryByRole("button", { name: /Ferien hinzufügen/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /bearbeiten/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /löschen/ })).toBeNull();
    expect(screen.queryByText("Wird in Google geändert.")).toBeNull();
    expect(screen.getByText("Ferien und freie Tage")).toBeInTheDocument();
  });
});

describe("SchoolBreaksCard: Eltern", () => {
  it("Google-Einträge haben ein Google-Zeichen und Hinweis, aber keine Buttons", async () => {
    stubApi([brk({ id: "g1", title: "Herbstferien", source: "google" })]);
    render(<SchoolBreaksCard canEdit />);

    const row = (await within(await list()).findAllByRole("listitem"))[0];
    expect(within(row).getByRole("img", { name: "Aus Google" })).toBeInTheDocument();
    expect(within(row).getByText("Wird in Google geändert.")).toBeInTheDocument();
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("legt Ferien mit Name, Art, Von/Bis und gewählten Kindern an", async () => {
    const { calls } = stubApi([]);
    render(<SchoolBreaksCard canEdit />);
    await screen.findByText("Keine Ferien eingetragen.");

    fireEvent.click(screen.getByRole("button", { name: /Ferien hinzufügen/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Herbstferien" } });
    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "2999-10-12" } });
    expect(screen.getByLabelText("Bis")).toHaveValue("2999-10-12"); // Bis follows Von
    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "2999-10-23" } });
    fireEvent.click(await screen.findByRole("button", { name: /Mia/ }));
    expect(screen.getByText("Nur die gewählten Kinder")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")).toMatchObject({
      path: "/school-breaks",
      body: {
        title: "Herbstferien",
        kind: "ferien",
        start_date: "2999-10-12",
        end_date: "2999-10-23",
        child_user_ids: [MIA.id],
      },
    });
    expect(await within(await list()).findByText("Herbstferien")).toBeInTheDocument();
    expect(screen.queryByRole("form")).toBeNull(); // the form closed
  });

  it("keine gewählten Kinder bedeuten 'Alle Kinder' und senden eine leere Liste", async () => {
    const { calls } = stubApi([]);
    render(<SchoolBreaksCard canEdit />);
    await screen.findByText("Keine Ferien eingetragen.");

    fireEvent.click(screen.getByRole("button", { name: /Ferien hinzufügen/ }));
    expect(screen.getByText("Alle Kinder")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Frei" } });
    fireEvent.change(screen.getByLabelText("Art"), { target: { value: "schulfrei" } });
    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "2999-11-02" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      kind: "schulfrei",
      start_date: "2999-11-02",
      end_date: "2999-11-02",
      child_user_ids: [],
    });
  });

  it("prüft Name und Datum, bevor etwas gesendet wird", async () => {
    const { calls } = stubApi([]);
    render(<SchoolBreaksCard canEdit />);
    await screen.findByText("Keine Ferien eingetragen.");
    fireEvent.click(screen.getByRole("button", { name: /Ferien hinzufügen/ }));

    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bitte einen Namen eingeben.");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bitte Von und Bis wählen.");

    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "2999-10-12" } });
    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "2999-10-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das Ende darf nicht vor dem Beginn liegen.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("zeigt die Fehlermeldung des Servers im Formular und lässt es offen", async () => {
    stubApi([], { fail: { method: "POST", status: 404, detail: "Kind nicht gefunden" } });
    render(<SchoolBreaksCard canEdit />);
    await screen.findByText("Keine Ferien eingetragen.");
    fireEvent.click(screen.getByRole("button", { name: /Ferien hinzufügen/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ferien" } });
    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "2999-10-12" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Kind nicht gefunden")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Ferien");
  });

  it("bearbeitet einen Eintrag mit PATCH", async () => {
    const { calls } = stubApi([brk({ id: "b1", child_user_ids: [LEO.id] })]);
    render(<SchoolBreaksCard canEdit />);

    fireEvent.click(await screen.findByRole("button", { name: "Ferien bearbeiten: Herbstferien" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Herbstferien");
    expect(screen.getByLabelText("Von")).toHaveValue("2999-10-12");
    expect(await screen.findByRole("button", { name: /Leo/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Herbstferien 2999" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")).toMatchObject({
      path: "/school-breaks/b1",
      body: { title: "Herbstferien 2999", child_user_ids: [LEO.id] },
    });
    expect(await within(await list()).findByText("Herbstferien 2999")).toBeInTheDocument();
  });

  it("Löschen braucht zwei Schritte und kann abgebrochen werden", async () => {
    const { calls } = stubApi([brk({ id: "b1" })]);
    render(<SchoolBreaksCard canEdit />);

    fireEvent.click(await screen.findByRole("button", { name: "Ferien löschen: Herbstferien" }));
    expect(screen.getByText("Löschen?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(screen.queryByText("Löschen?")).toBeNull();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Ferien löschen: Herbstferien" }));
    fireEvent.click(screen.getByRole("button", { name: "Ja, löschen" }));

    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/school-breaks/b1");
    expect(await screen.findByText("Keine Ferien eingetragen.")).toBeInTheDocument();
  });

  it("zeigt einen Löschfehler des Servers", async () => {
    stubApi([brk({ id: "b1" })], { fail: { method: "DELETE", status: 409, detail: "Dieser Eintrag kommt aus Google und wird dort geändert." } });
    render(<SchoolBreaksCard canEdit />);

    fireEvent.click(await screen.findByRole("button", { name: "Ferien löschen: Herbstferien" }));
    fireEvent.click(screen.getByRole("button", { name: "Ja, löschen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Dieser Eintrag kommt aus Google und wird dort geändert.");
  });

  it("die Aktionen haben mindestens 44 px Trefferfläche (icon-btn)", async () => {
    stubApi([brk({ id: "b1" })]);
    render(<SchoolBreaksCard canEdit />);
    const edit = await screen.findByRole("button", { name: "Ferien bearbeiten: Herbstferien" });
    const del = screen.getByRole("button", { name: "Ferien löschen: Herbstferien" });
    expect(edit).toHaveClass("icon-btn");
    expect(del).toHaveClass("icon-btn", "secondary");
  });
});
