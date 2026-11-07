import { useMemo, useState } from "react";
import { ICON_GROUPS } from "../constants/taskRewardIcons";

type IconPickerProps = {
  value: string | null;
  onChange: (icon: string) => void;
};

function normalize(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/ß/g, "ss")
    // "Hände" and "haende" must find each other: fold umlaut digraphs on both sides
    .replace(/ae/g, "a")
    .replace(/oe/g, "o")
    .replace(/ue/g, "u");
}

/** A searchable, grouped grid of selectable emoji icons for task templates and rewards. */
export default function IconPicker({ value, onChange }: IconPickerProps) {
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const words = normalize(query).split(/\s+/).filter(Boolean);
    if (words.length === 0) return ICON_GROUPS;
    return ICON_GROUPS.map((group) => ({
      ...group,
      icons: group.icons.filter((icon) => {
        const haystack = normalize(`${icon.k} ${group.label}`);
        return words.every((word) => haystack.includes(word));
      }),
    })).filter((group) => group.icons.length > 0);
  }, [query]);

  return (
    <div style={{ marginBottom: 12 }}>
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.preventDefault();
        }}
        aria-label="Symbol suchen"
        placeholder="Symbol suchen, z. B. Abwaschen"
        style={{ marginBottom: 8 }}
      />
      <div
        style={{
          maxHeight: 280,
          overflowY: "auto",
          padding: 8,
          border: "1.5px solid var(--border)",
          borderRadius: "var(--radius-md)",
          background: "var(--surface)",
        }}
      >
        {groups.length === 0 && <div className="card-meta">Kein Symbol gefunden.</div>}
        {groups.map((group) => (
          <section key={group.label} aria-label={group.label} style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-muted)", margin: "2px 0 6px" }}>{group.label}</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(44px, 1fr))", gap: 8 }}>
              {group.icons.map((icon) => {
                const selected = value === icon.e;
                return (
                  <button
                    key={icon.e}
                    type="button"
                    onClick={() => onChange(icon.e)}
                    aria-pressed={selected}
                    aria-label={icon.k.split(" ").slice(0, 2).join(" ")}
                    title={icon.k.split(" ").slice(0, 3).join(", ")}
                    style={{
                      width: 44,
                      height: 44,
                      padding: 0,
                      minHeight: "auto",
                      fontSize: 22,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      borderRadius: "var(--radius-md)",
                      border: `1.5px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                      background: selected ? "var(--accent-soft)" : "var(--surface)",
                    }}
                  >
                    {icon.e}
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
