import { FormEvent, useEffect, useState } from "react";
import { apiFetch, decodeRoleFromToken, getToken } from "../api/client";
import IconPicker from "../components/IconPicker";

type Reward = {
  id: string;
  title: string;
  points_cost: number;
  icon: string | null;
};

type PointsBalance = {
  user_id: string;
  balance: number;
};

const REWARD_IDEAS = [
  { title: "20 Minuten Extra-Vorlesen", icon: "📚" },
  { title: "Spiel aussuchen", icon: "🎲" },
  { title: "Backen mit Mama/Papa", icon: "🧁" },
  { title: "Filmabend", icon: "🎬" },
  { title: "1:1-Zeit", icon: "💛" },
  { title: "Gemeinsamer Ausflug", icon: "🚲" },
];

export default function Rewards() {
  const role = decodeRoleFromToken(getToken() ?? "");
  const isParent = role === "parent_admin" || role === "parent_member";

  const [rewards, setRewards] = useState<Reward[]>([]);
  const [balance, setBalance] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [newRewardTitle, setNewRewardTitle] = useState("");
  const [newRewardCost, setNewRewardCost] = useState("");
  const [newRewardIcon, setNewRewardIcon] = useState<string | null>(null);
  const [creatingReward, setCreatingReward] = useState(false);
  const [redeemingId, setRedeemingId] = useState<string | null>(null);
  const [celebrate, setCelebrate] = useState(false);

  function loadData() {
    Promise.all([
      apiFetch<Reward[]>("/rewards"),
      apiFetch<PointsBalance>("/points/balance"),
    ])
      .then(([rewardsData, balanceData]) => {
        setRewards(rewardsData);
        setBalance(balanceData.balance);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Konnte nicht geladen werden"));
  }

  useEffect(loadData, []);

  async function handleCreateReward(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setCreatingReward(true);
    try {
      const cost = parseInt(newRewardCost, 10);
      if (isNaN(cost) || cost <= 0) {
        setError("Punkte müssen größer als 0 sein");
        setCreatingReward(false);
        return;
      }
      await apiFetch("/rewards", {
        method: "POST",
        body: JSON.stringify({
          title: newRewardTitle,
          points_cost: cost,
          icon: newRewardIcon || undefined,
        }),
      });
      setNewRewardTitle("");
      setNewRewardCost("");
      setNewRewardIcon(null);
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Konnte nicht erstellt werden");
    } finally {
      setCreatingReward(false);
    }
  }

  async function handleRedeem(rewardId: string) {
    setError(null);
    setRedeemingId(rewardId);
    try {
      await apiFetch(`/rewards/${rewardId}/redeem`, { method: "POST" });
      loadData();
      setCelebrate(true);
      setTimeout(() => setCelebrate(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Das hat nicht geklappt, versuch's nochmal.");
    } finally {
      setRedeemingId(null);
    }
  }

  return (
    <>
      <h1>Belohnungen</h1>
      <p className="subtitle">Sammle Punkte und hol dir eine Belohnung.</p>

      {celebrate && <div className="celebrate-banner">🎉 Belohnung geholt!</div>}

      {/* Balance card */}
      <div className="card">
        <div className="points-badge">
          <span className="value">{balance}</span>
          <span className="label">Punkte</span>
        </div>
        <div className="card-body">
          <div className="card-title">Meine Punkte</div>
          <div className="card-meta">Bonusaufgaben bringen Punkte. Basisaufgaben gehören zum Alltag.</div>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {isParent && (
        <div className="form-card">
          <h2 style={{ marginTop: 0, marginBottom: 20, fontSize: "18px" }}>Neue Belohnung</h2>
          <div className="reward-idea-grid" aria-label="Belohnungsideen">
            {REWARD_IDEAS.map((idea) => (
              <button
                key={idea.title}
                type="button"
                className="reward-idea-button secondary"
                onClick={() => {
                  setNewRewardTitle(idea.title);
                  setNewRewardIcon(idea.icon);
                }}
              >
                <span aria-hidden="true">{idea.icon}</span>
                {idea.title}
              </button>
            ))}
          </div>
          <form onSubmit={handleCreateReward}>
            <label htmlFor="title">Titel</label>
            <input
              id="title"
              type="text"
              value={newRewardTitle}
              onChange={(e) => setNewRewardTitle(e.target.value)}
              placeholder="z.B. Eiskrem"
              required
            />
            <label>Icon</label>
            <IconPicker value={newRewardIcon} onChange={setNewRewardIcon} />

            <label htmlFor="cost">Punkte erforderlich</label>
            <input
              id="cost"
              type="number"
              value={newRewardCost}
              onChange={(e) => setNewRewardCost(e.target.value)}
              placeholder="z.B. 10"
              min="1"
              required
            />
            <button type="submit" disabled={creatingReward}>
              {creatingReward ? "Wird erstellt…" : "Erstellen"}
            </button>
          </form>
        </div>
      )}

      {/* Rewards list */}
      {rewards.length === 0 ? (
        <div className="empty-state-playful">
          <span className="emoji" aria-hidden="true">
            🎁
          </span>
          <div className="headline">Noch keine Belohnungen</div>
          <div className="subtext">
            {isParent ? "Leg oben die erste Belohnung an." : "Frag deine Eltern nach der ersten Belohnung!"}
          </div>
        </div>
      ) : (
        rewards.map((reward) => {
          const canAfford = balance >= reward.points_cost;
          const progressPct = Math.min(100, Math.round((balance / reward.points_cost) * 100));
          const missingPoints = Math.max(0, reward.points_cost - balance);
          return (
            <div key={reward.id} className="card reward-goal-card">
              <div className="icon-chip" aria-hidden="true">
                {reward.icon || "🎁"}
              </div>
              <div className="card-body">
                <div className="reward-goal-header">
                  <div className="card-title">{reward.title}</div>
                  <span className="status-pill accent">
                    {balance} / {reward.points_cost}
                  </span>
                </div>
                <div className="card-meta">Du hast {balance} von {reward.points_cost} Punkten.</div>
                <div className="progress-track" aria-hidden="true">
                  <div className="progress-fill" style={{ width: `${progressPct}%` }} />
                </div>
                <div className="card-meta">
                  {canAfford ? "Du kannst sie jetzt einlösen." : `Noch ${missingPoints} Punkte bis ${reward.title}.`}
                </div>
                <button
                  style={{ marginTop: 10, width: "100%" }}
                  disabled={!canAfford || redeemingId === reward.id}
                  onClick={() => handleRedeem(reward.id)}
                >
                  {redeemingId === reward.id ? "Wird eingelöst…" : "Einlösen"}
                </button>
              </div>
            </div>
          );
        })
      )}
    </>
  );
}
