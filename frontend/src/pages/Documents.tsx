import { FormEvent, useEffect, useState } from "react";
import { apiFetch, apiFetchBlob, decodeRoleFromToken, getFamilyId, getToken } from "../api/client";

type ChildAccount = {
  id: string;
  display_name: string;
};

type ExtractedEvent = {
  id: string;
  title: string;
  event_date: string;
  event_time: string | null;
  person_hint: string | null;
  resolved_child_user_id: string | null;
  status: string;
  calendar_event_id: string | null;
};

type ParentDocument = {
  id: string;
  original_filename: string;
  content_type: string;
  status: string;
  created_at: string;
  events: ExtractedEvent[];
};

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<ParentDocument[]>([]);
  const [childAccounts, setChildAccounts] = useState<ChildAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isParent, setIsParent] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [processingEventId, setProcessingEventId] = useState<string | null>(null);

  function loadDocuments() {
    apiFetch<ParentDocument[]>("/documents")
      .then(setDocuments)
      .catch((err) => setError(err instanceof Error ? err.message : "Dokumente konnten nicht geladen werden"));
  }

  function loadChildren() {
    const familyId = getFamilyId();
    if (!familyId) return;

    apiFetch<ChildAccount[]>(`/families/${familyId}/child-accounts`)
      .then(setChildAccounts)
      .catch((err) => setError(err instanceof Error ? err.message : "Kinder konnten nicht geladen werden"));
  }

  useEffect(() => {
    const token = getToken();
    const role = decodeRoleFromToken(token || "");
    const isParentRole = role === "parent_admin" || role === "parent_member";
    setIsParent(isParentRole);

    loadDocuments();
    if (isParentRole) {
      loadChildren();
    }
  }, []);

  async function handleUpload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();

    if (!selectedFile) {
      setError("Bitte wählen Sie eine Datei aus");
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append("file", selectedFile);

      await apiFetch("/documents/upload", {
        method: "POST",
        body: formData,
      });

      setSelectedFile(null);
      setError(null);
      loadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload fehlgeschlagen");
    } finally {
      setUploading(false);
    }
  }

  async function handleDownloadFile(documentId: string, originalFilename: string) {
    try {
      const blob = await apiFetchBlob(`/documents/${documentId}/file`);
      const objectUrl = URL.createObjectURL(blob);

      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = originalFilename;
      link.click();

      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download fehlgeschlagen");
    }
  }

  async function handleResolveEvent(
    documentId: string,
    eventId: string,
    childUserId: string | null
  ) {
    setProcessingEventId(eventId);
    setError(null);

    try {
      await apiFetch(`/documents/${documentId}/events/${eventId}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          child_user_id: childUserId,
        }),
      });

      loadDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auflösung fehlgeschlagen");
    } finally {
      setProcessingEventId(null);
    }
  }

  const getStatusBadgeText = (status: string) => {
    if (status === "needs_clarification") return "Klärung nötig";
    if (status === "processed") return "Verarbeitet";
    return status;
  };

  const getStatusBadgeStyle = (status: string) => {
    if (status === "needs_clarification") {
      return { color: "var(--accent)", fontWeight: 600, fontSize: "12px" };
    }
    if (status === "processed") {
      return { color: "#4caf50", fontWeight: 600, fontSize: "12px" };
    }
    return {};
  };

  return (
    <>
      <h1>Briefe</h1>
      <p className="subtitle">Schulbriefe hochladen und Termine extrahieren.</p>

      {error && <div className="error-banner">{error}</div>}

      {isParent && (
        <div className="form-card">
          <h2 style={{ marginTop: 0, marginBottom: 20, fontSize: "18px" }}>Brief hochladen</h2>
          <form onSubmit={handleUpload}>
            <label htmlFor="file">Bild auswählen</label>
            <input
              id="file"
              type="file"
              accept="image/*"
              onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
              required
            />
            <button type="submit" disabled={uploading}>
              {uploading ? "Wird hochgeladen…" : "Hochladen"}
            </button>
          </form>
        </div>
      )}

      {documents.length === 0 ? (
        <p className="empty-state">Noch keine Dokumente vorhanden.</p>
      ) : (
        documents.map((doc) => (
          <div key={doc.id} className="card" style={{ marginBottom: "16px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
              <div style={{ flex: 1 }}>
                <div className="card-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "20px" }} aria-hidden="true">
                    📄
                  </span>
                  {doc.original_filename}
                </div>
                <div
                  className="card-meta"
                  style={{ display: "flex", gap: "12px", alignItems: "center", marginTop: "8px" }}
                >
                  <span>{new Date(doc.created_at).toLocaleDateString("de-CH")}</span>
                  <span style={getStatusBadgeStyle(doc.status)}>{getStatusBadgeText(doc.status)}</span>
                </div>
              </div>
              <button
                onClick={() => handleDownloadFile(doc.id, doc.original_filename)}
                style={{
                  padding: "8px 12px",
                  fontSize: "13px",
                  backgroundColor: "transparent",
                  border: "1px solid var(--text-secondary)",
                  borderRadius: "6px",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                  marginLeft: "12px",
                }}
              >
                Ansehen
              </button>
            </div>

            {/* Pending clarification events */}
            {doc.events.some((e) => e.status === "pending_clarification") && (
              <div style={{ marginTop: "16px", paddingTop: "16px", borderTop: "1px solid var(--border-color)" }}>
                <div style={{ fontSize: "13px", fontWeight: 600, marginBottom: "12px", color: "var(--accent)" }}>
                  Ereignisse zur Zuordnung:
                </div>
                {doc.events
                  .filter((e) => e.status === "pending_clarification")
                  .map((event) => (
                    <div
                      key={event.id}
                      style={{
                        padding: "12px",
                        backgroundColor: "var(--surface-hover)",
                        borderRadius: "6px",
                        marginBottom: "8px",
                      }}
                    >
                      <div style={{ fontWeight: 500, marginBottom: "8px" }}>{event.title}</div>
                      <div style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "10px" }}>
                        {event.event_date}
                        {event.event_time && ` um ${event.event_time}`}
                        {event.person_hint && ` (Hinweis: ${event.person_hint})`}
                      </div>
                      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                        <select
                          onChange={(e) => {
                            if (e.target.value) {
                              handleResolveEvent(doc.id, event.id, e.target.value);
                              e.target.value = "";
                            }
                          }}
                          disabled={processingEventId === event.id}
                          style={{
                            padding: "6px 8px",
                            fontSize: "13px",
                            borderRadius: "4px",
                            border: "1px solid var(--border-color)",
                          }}
                        >
                          <option value="">Kind wählen…</option>
                          {childAccounts.map((child) => (
                            <option key={child.id} value={child.id}>
                              {child.display_name}
                            </option>
                          ))}
                        </select>
                        <button
                          onClick={() => handleResolveEvent(doc.id, event.id, null)}
                          disabled={processingEventId === event.id}
                          style={{
                            padding: "6px 12px",
                            fontSize: "13px",
                            backgroundColor: "transparent",
                            border: "1px solid var(--border-color)",
                            borderRadius: "4px",
                            cursor: "pointer",
                          }}
                        >
                          Nicht zuordnen
                        </button>
                      </div>
                    </div>
                  ))}
              </div>
            )}

            {/* Confirmed events (read-only display) */}
            {doc.events.some((e) => e.status === "confirmed") && (
              <div style={{ marginTop: "16px", paddingTop: "16px", borderTop: "1px solid var(--border-color)" }}>
                <div style={{ fontSize: "13px", fontWeight: 600, marginBottom: "8px", color: "#4caf50" }}>
                  Zugeordnete Ereignisse:
                </div>
                {doc.events
                  .filter((e) => e.status === "confirmed")
                  .map((event) => (
                    <div key={event.id} style={{ fontSize: "13px", marginBottom: "6px" }}>
                      ✓ {event.title} ({event.event_date})
                    </div>
                  ))}
              </div>
            )}
          </div>
        ))
      )}
    </>
  );
}
