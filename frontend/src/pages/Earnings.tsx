import { useEffect, useState } from "react";
import { apiFetch, decodeRoleFromToken, getToken } from "../api/client";

type EarningsLedgerEntry = {
  id: string;
  amount_chf: number;
  status: "pending_approval" | "approved" | "paid_out";
  created_at: string;
};

type EarningsBalance = {
  user_id: string;
  pending_chf: number;
  approved_chf: number;
  paid_out_chf: number;
};

type Child = {
  id: string;
  display_name: string;
  avatar_key: string | null;
};

const STATUS_LABELS: Record<EarningsLedgerEntry["status"], string> = {
  pending_approval: "wartet auf Freigabe",
  approved: "freigegeben",
  paid_out: "ausgezahlt",
};

const STATUS_ICONS: Record<EarningsLedgerEntry["status"], string> = {
  pending_approval: "⏳",
  approved: "✅",
  paid_out: "💰",
};

const STATUS_CHIP_CLASS: Record<EarningsLedgerEntry["status"], string> = {
  pending_approval: "",
  approved: "leaf",
  paid_out: "sky",
};

function BalanceCards({ balance }: { balance: EarningsBalance }) {
  return (
    <>
      <div className="card">
        <div className="icon-chip" aria-hidden="true">
          ⏳
        </div>
        <div className="card-body">
          <div className="card-title">Wartet auf Freigabe</div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--accent)", marginTop: 6 }}>
            CHF {balance.pending_chf.toFixed(2)}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="icon-chip leaf" aria-hidden="true">
          ✅
        </div>
        <div className="card-body">
          <div className="card-title">Freigegeben</div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--accent)", marginTop: 6 }}>
            CHF {balance.approved_chf.toFixed(2)}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="icon-chip sky" aria-hidden="true">
          💰
        </div>
        <div className="card-body">
          <div className="card-title">Ausgezahlt</div>
          <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--accent)", marginTop: 6 }}>
            CHF {balance.paid_out_chf.toFixed(2)}
          </div>
        </div>
      </div>
    </>
  );
}

function ParentPayoutSection() {
  const [children, setChildren] = useState<Child[]>([]);
  const [selectedChildId, setSelectedChildId] = useState("");
  const [childBalance, setChildBalance] = useState<EarningsBalance | null>(null);
  const [childLedger, setChildLedger] = useState<EarningsLedgerEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [payingOutId, setPayingOutId] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<Child[]>("/families/children")
      .then((data) => {
        setChildren(data);
        if (data.length > 0) setSelectedChildId(data[0].id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Kinder konnten nicht geladen werden"));
  }, []);

  function loadChildEarnings(childId: string) {
    Promise.all([
      apiFetch<EarningsBalance>(`/earnings/balance?user_id=${childId}`),
      apiFetch<EarningsLedgerEntry[]>(`/earnings/ledger?user_id=${childId}`),
    ])
      .then(([balanceData, ledgerData]) => {
        setChildBalance(balanceData);
        setChildLedger(ledgerData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Verdienst konnte nicht geladen werden"));
  }

  useEffect(() => {
    if (selectedChildId) loadChildEarnings(selectedChildId);
  }, [selectedChildId]);

  async function handlePayOut(entryId: string) {
    setError(null);
    setPayingOutId(entryId);
    try {
      await apiFetch(`/earnings/${entryId}/pay-out`, { method: "POST" });
      loadChildEarnings(selectedChildId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Konnte nicht ausgezahlt werden");
    } finally {
      setPayingOutId(null);
    }
  }

  if (children.length === 0) {
    return null;
  }

  return (
    <>
      <h2 style={{ marginTop: 28, marginBottom: 14, fontSize: "16px" }}>Verdienst der Kinder verwalten</h2>

      <div className="form-card" style={{ maxWidth: "none", marginBottom: 22 }}>
        <label htmlFor="child-select">Kind</label>
        <select id="child-select" value={selectedChildId} onChange={(e) => setSelectedChildId(e.target.value)}>
          {children.map((child) => (
            <option key={child.id} value={child.id}>
              {child.avatar_key || "🧒"} {child.display_name}
            </option>
          ))}
        </select>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {childBalance && <BalanceCards balance={childBalance} />}

      {childLedger.length === 0 ? (
        <p className="empty-state">Noch keine Verdienste registriert.</p>
      ) : (
        childLedger.map((entry) => (
          <div key={entry.id} className="card">
            <div className={`icon-chip ${STATUS_CHIP_CLASS[entry.status]}`} aria-hidden="true">
              {STATUS_ICONS[entry.status]}
            </div>
            <div className="card-body">
              <div className="card-title">CHF {entry.amount_chf.toFixed(2)}</div>
              <div className="card-meta">
                {STATUS_LABELS[entry.status]} · {new Date(entry.created_at).toLocaleDateString("de-CH")}
              </div>
              {entry.status === "approved" && (
                <button
                  style={{ marginTop: 10 }}
                  disabled={payingOutId === entry.id}
                  onClick={() => handlePayOut(entry.id)}
                >
                  {payingOutId === entry.id ? "Wird ausgezahlt…" : "Auszahlen"}
                </button>
              )}
            </div>
          </div>
        ))
      )}
    </>
  );
}

export default function Earnings() {
  const role = decodeRoleFromToken(getToken() ?? "");
  const isParent = role === "parent_admin" || role === "parent_member";
  const isChild = role === "child";

  const [ledger, setLedger] = useState<EarningsLedgerEntry[]>([]);
  const [balance, setBalance] = useState<EarningsBalance | null>(null);
  const [error, setError] = useState<string | null>(null);

  function loadData() {
    Promise.all([
      apiFetch<EarningsLedgerEntry[]>("/earnings/ledger"),
      apiFetch<EarningsBalance>("/earnings/balance"),
    ])
      .then(([ledgerData, balanceData]) => {
        setLedger(ledgerData);
        setBalance(balanceData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Konnte nicht geladen werden"));
  }

  useEffect(loadData, []);

  return (
    <>
      <h1>{isChild ? "Mein Taschengeld" : "Verdienst"}</h1>
      <p className="subtitle">
        {isChild ? "So viel Taschengeld hast du dir schon verdient." : "Übersicht deiner Verdienste in CHF."}
      </p>

      {error && <div className="error-banner">{error}</div>}

      {/* Balance cards */}
      {balance && <BalanceCards balance={balance} />}

      {/* Ledger entries */}
      <h2 style={{ marginTop: 28, marginBottom: 14, fontSize: "16px" }}>Einträge</h2>
      {ledger.length === 0 ? (
        <p className="empty-state">Noch keine Verdienste registriert.</p>
      ) : (
        ledger.map((entry) => (
          <div key={entry.id} className="card">
            <div className={`icon-chip ${STATUS_CHIP_CLASS[entry.status]}`} aria-hidden="true">
              {STATUS_ICONS[entry.status]}
            </div>
            <div className="card-body">
              <div className="card-title">CHF {entry.amount_chf.toFixed(2)}</div>
              <div className="card-meta">
                {STATUS_LABELS[entry.status]} · {new Date(entry.created_at).toLocaleDateString("de-CH")}
              </div>
            </div>
          </div>
        ))
      )}

      {isParent && <ParentPayoutSection />}
    </>
  );
}
