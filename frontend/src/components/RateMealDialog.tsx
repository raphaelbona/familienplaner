import { useEffect, useRef, useState } from "react";
import { apiFetch, decodeUserIdFromToken, getToken } from "../api/client";
import { FamilyMember, fetchFamilyMembers } from "../api/members";
import Dialog from "./Dialog";
import StarRating from "./StarRating";

export type RatableEntry = {
  recipe_id: string;
  recipe_name: string;
  date: string;
};

type RateMealDialogProps = {
  entry: RatableEntry;
  isParent: boolean;
  onClose: () => void;
  onRated: () => void;
};

export default function RateMealDialog({ entry, isParent, onClose, onRated }: RateMealDialogProps) {
  const myId = decodeUserIdFromToken(getToken() ?? "");
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [raterId, setRaterId] = useState<string | null>(myId);
  const [stars, setStars] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!isParent) return;
    fetchFamilyMembers()
      .then(setMembers)
      .catch((err) => setError(err instanceof Error ? err.message : "Familie konnte nicht geladen werden"));
  }, [isParent]);

  // After saving, the focused save button gets disabled; keep keyboard focus inside the dialog.
  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice]);

  const raterName = members.find((m) => m.id === raterId)?.display_name;

  async function handleSave() {
    if (stars === 0) return;
    setError(null);
    setNotice(null);
    setSaving(true);
    try {
      await apiFetch(`/recipes/${entry.recipe_id}/ratings`, {
        method: "POST",
        body: JSON.stringify({
          stars,
          cooked_on: entry.date,
          ...(isParent && raterId ? { user_id: raterId } : {})
        })
      });
      setNotice(isParent ? `Bewertung${raterName ? ` von ${raterName}` : ""} gespeichert.` : "Danke für deine Bewertung!");
      setStars(0);
      onRated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bewertung konnte nicht gespeichert werden");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog title={`${entry.recipe_name} bewerten`} onClose={onClose}>
      {isParent && members.length > 0 && (
        <>
          <label htmlFor="rater-select">Wer bewertet?</label>
          <select
            id="rater-select"
            value={raterId ?? ""}
            onChange={(e) => {
              setRaterId(e.target.value);
              setNotice(null);
            }}
          >
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
          </select>
        </>
      )}

      <p className="card-meta" style={{ marginBottom: 8 }}>
        Wie hat es geschmeckt?
      </p>
      <StarRating value={stars} onChange={setStars} large={!isParent} label="Sterne vergeben" />

      {error && (
        <div className="error-banner" role="alert" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}
      {notice && (
        <p ref={noticeRef} tabIndex={-1} role="status" className="dialog-notice">
          {notice}
        </p>
      )}

      <div className="dialog-actions">
        <button type="button" onClick={handleSave} disabled={stars === 0 || saving} className={isParent ? "" : "btn-large"}>
          {saving ? "Wird gespeichert…" : "Bewertung speichern"}
        </button>
        <button type="button" className="secondary" onClick={onClose}>
          Fertig
        </button>
      </div>
    </Dialog>
  );
}
