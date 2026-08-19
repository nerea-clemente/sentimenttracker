"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api,
  formatDate,
  type CampaignSummary,
  type Escalation,
  type Meta,
} from "@/lib/api";
import { ErrorBanner, Loading } from "@/components/Loading";

/**
 * The escalation log.
 *
 * Escalations are the most predictive part of the model and the only part entered by hand, so the
 * form enforces what makes an entry defensible: a source URL and a severity with a written anchor.
 */
export default function EscalationsPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [campaigns, setCampaigns] = useState<CampaignSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [logNote, setLogNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([api.meta(), api.campaigns()])
      .then(([m, c]) => {
        setMeta(m);
        setCampaigns(c);
        if (c.length) setSelected(c[0].slug);
      })
      .catch((e) => setError(e.message));
  }, []);

  const reload = useCallback(async (slug: string) => {
    if (!slug) return;
    const payload = await api.escalations(slug);
    setEscalations(payload.escalations);
    setLogNote(payload.log_note);
  }, []);

  useEffect(() => {
    if (selected) reload(selected).catch((e) => setError(e.message));
  }, [selected, reload]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setFormError(null);
    const form = new FormData(event.currentTarget);
    try {
      await api.addEscalation(selected, {
        occurred_at: String(form.get("occurred_at")),
        escalation_type: String(form.get("escalation_type")),
        actor_name: String(form.get("actor_name")),
        actor_type: String(form.get("actor_type") || "") || null,
        description: String(form.get("description")),
        source_url: String(form.get("source_url")),
        severity: Number(form.get("severity")),
      });
      event.currentTarget.reset();
      await reload(selected);
    } catch (e: any) {
      setFormError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function verify(id: number) {
    try {
      await api.verifyEscalation(id);
      await reload(selected);
    } catch (e: any) {
      setError(e.message);
    }
  }

  if (error) return <ErrorBanner error={error} />;
  if (!meta) return <Loading what="escalation log" />;

  return (
    <>
      <h1>Escalation log</h1>
      <p className="lede">
        Downstream consequences: legal petitions, regulatory action, customs measures, buyer and
        retailer statements, parliamentary questions, certifier responses. This is the most
        predictive part of the model and the only part entered by hand.
      </p>

      <div className="field" style={{ maxWidth: "34rem" }}>
        <label htmlFor="campaign">Campaign</label>
        <select
          id="campaign"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          style={{ width: "100%" }}
        >
          {campaigns.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      <h2>Log an escalation</h2>
      <form className="card" onSubmit={submit}>
        {formError && <div className="banner danger">{formError}</div>}
        <div className="row">
          <div className="field">
            <label htmlFor="occurred_at">Occurred at *</label>
            <input id="occurred_at" name="occurred_at" type="date" required />
          </div>
          <div className="field" style={{ flex: "1 1 14rem" }}>
            <label htmlFor="escalation_type">Type *</label>
            <select id="escalation_type" name="escalation_type" required style={{ width: "100%" }}>
              {meta.escalation_types.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ flex: "1 1 12rem" }}>
            <label htmlFor="actor_name">Actor *</label>
            <input id="actor_name" name="actor_name" required style={{ width: "100%" }} />
          </div>
          <div className="field" style={{ flex: "1 1 10rem" }}>
            <label htmlFor="actor_type">Actor type</label>
            <input id="actor_type" name="actor_type" placeholder="regulator, retailer…" style={{ width: "100%" }} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="severity">Severity *</label>
          <select id="severity" name="severity" required defaultValue="3" style={{ width: "100%", maxWidth: "42rem" }}>
            {Object.entries(meta.severity_anchors).map(([value, anchor]) => (
              <option key={value} value={value}>
                {value} — {anchor}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="source_url">
            Source URL * — an escalation is a claim about the outside world and must cite it
          </label>
          <input
            id="source_url"
            name="source_url"
            type="url"
            required
            placeholder="https://…"
            style={{ width: "100%", maxWidth: "42rem" }}
          />
        </div>
        <div className="field">
          <label htmlFor="description">Description *</label>
          <textarea
            id="description"
            name="description"
            required
            rows={2}
            style={{ width: "100%", maxWidth: "42rem" }}
          />
        </div>
        <button className="primary" type="submit" disabled={saving || !selected}>
          {saving ? "Saving…" : "Log escalation"}
        </button>
      </form>

      <h2>Logged events</h2>
      {escalations.length === 0 ? (
        <div className="empty-state">
          <strong>Nothing logged for this campaign.</strong>
          {logNote}
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Actor</th>
                <th className="num">Severity</th>
                <th>Description</th>
                <th>Source</th>
                <th>Verified</th>
                <th>Logged by</th>
              </tr>
            </thead>
            <tbody>
              {escalations.map((e) => (
                <tr key={e.id}>
                  <td className="nowrap small">{formatDate(e.occurred_at)}</td>
                  <td className="small">{e.escalation_type.replace(/_/g, " ")}</td>
                  <td>
                    {e.actor_name}
                    {e.actor_type && <div className="small muted">{e.actor_type}</div>}
                  </td>
                  <td className="num" title={meta.severity_anchors[String(e.severity)]}>
                    {e.severity}
                  </td>
                  <td className="small" style={{ maxWidth: "22rem" }}>
                    {e.description}
                  </td>
                  <td className="small">
                    <a href={e.source_url} target="_blank" rel="noreferrer">
                      source
                    </a>
                  </td>
                  <td className="small">
                    {e.verified_by ? (
                      <span className="muted">
                        {e.verified_by}
                        <br />
                        {formatDate(e.verified_at)}
                      </span>
                    ) : (
                      <button onClick={() => verify(e.id)}>Verify</button>
                    )}
                  </td>
                  <td className="small muted">{e.created_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Severity scale</h2>
      <div className="card">
        <table>
          <tbody>
            {Object.entries(meta.severity_anchors).map(([value, anchor]) => (
              <tr key={value}>
                <td className="num" style={{ width: "3rem" }}>
                  {value}
                </td>
                <td>{anchor}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="small muted" style={{ marginBottom: 0, marginTop: "0.6rem" }}>
          A severity-weighted total is only defensible if the scale is written down. These anchors
          are stored with the schema and printed in every briefing export.
        </p>
      </div>
    </>
  );
}
