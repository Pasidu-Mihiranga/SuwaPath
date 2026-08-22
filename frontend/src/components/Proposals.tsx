/**
 * Suggestions the system prepared on its own.
 *
 * These arrive without the patient having asked for anything — a detector
 * noticed that care they were advised to arrange never happened, worked out
 * who could provide it, and got as far as a specific bookable slot.
 *
 * Two things the design has to get right:
 *
 * **It must be obvious this was not a human.** The card says so, and it shows
 * the reason the system had. Something that acts on your behalf without
 * explaining itself is unsettling in a way that costs more trust than the
 * convenience gains.
 *
 * **Declining must be as easy as accepting.** "Not now" sits beside the
 * primary action, not behind a menu. A suggestion you cannot refuse cheaply is
 * a demand.
 */

import { useCallback, useEffect, useState } from "react";
import { Card, Icon, Notice } from "./ui";
import { api, errorMessage } from "../lib/api";

interface Proposal {
  id: string;
  action: string;
  risk_tier: string;
  title: string;
  preview: string;
  status: string;
  evidence?: Record<string, any>;
}

export function useProposals() {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/actions");
      setProposals(data.proposals ?? []);
    } catch {
      /* suggestions are an extra; failing to load one must not break a page */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { proposals, loading, reload: load };
}

export function ProposalCard({
  proposal,
  onDone,
}: {
  proposal: Proposal;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/actions/${proposal.id}/${decision}`);
      onDone();
    } catch (err) {
      // The common failure is a slot taken since the suggestion was made. The
      // server explains that and says to search again, so show it rather than
      // a generic message.
      setError(errorMessage(err, "That could not be completed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/40 p-4">
      <div className="flex items-start gap-3">
        <span className="sp-icon-tile bg-surface text-brand-700 shrink-0">
          <Icon name="ai" size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-brand-700">
            Suggested for you
          </p>
          <p className="mt-0.5 font-semibold text-ink-900">{proposal.title}</p>
          {/* Preserves the line breaks the server composed: doctor, then time
              and fee, then the reason. */}
          <p className="mt-1.5 whitespace-pre-line text-sm text-ink-600">
            {proposal.preview}
          </p>
        </div>
      </div>

      {error && (
        <div className="mt-3">
          <Notice tone="warn">{error}</Notice>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          className="sp-btn sp-btn-primary sp-btn-sm"
          disabled={busy}
          onClick={() => void decide("approve")}
        >
          {busy ? "Working…" : "Book this"}
        </button>
        <button
          className="sp-btn sp-btn-ghost sp-btn-sm"
          disabled={busy}
          onClick={() => void decide("reject")}
        >
          Not now
        </button>
        <p className="text-xs text-ink-500 sm:ml-auto w-full sm:w-auto">
          Nothing happens until you choose.
        </p>
      </div>
    </div>
  );
}

/** The whole list, for the notifications page. */
export function ProposalInbox() {
  const { proposals, loading, reload } = useProposals();
  if (loading || proposals.length === 0) return null;

  return (
    <Card
      title="Suggestions"
      subtitle="Prepared automatically from care you were advised to arrange."
    >
      <div className="space-y-3">
        {proposals.map((proposal) => (
          <ProposalCard key={proposal.id} proposal={proposal} onDone={reload} />
        ))}
      </div>
    </Card>
  );
}
