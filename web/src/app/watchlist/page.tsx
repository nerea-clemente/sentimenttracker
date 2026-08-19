"use client";

import { useCallback, useEffect, useState } from "react";
import { api, formatDate } from "@/lib/api";
import { ErrorBanner, Loading } from "@/components/Loading";

/**
 * Watchlist and triggers.
 *
 * The one thing this page must never do is look healthy while nothing is being watched, so the
 * poller's last run and the count of enabled rules are the first things on it.
 */
export default function WatchlistPage() {
  const [rules, setRules] = useState<any[] | null>(null);
  const [hits, setHits] = useState<any[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [r, h, s] = await Promise.all([
      api.watchRules(),
      api.watchHits(50),
      api.watchStatus(),
    ]);
    setRules(r);
    setHits(h);
    setStatus(s);
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  async function toggle(id: number, enabled: boolean) {
    try {
      await api.setWatchRuleEnabled(id, enabled);
      await load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  if (error) return <ErrorBanner error={error} />;
  if (!rules || !status) return <Loading what="watch rules" />;

  const nothingRunning = !status.last_run;
  const nothingEnabled = status.rules_enabled === 0 && status.rules_total > 0;

  return (
    <>
      <h1>Watchlist &amp; triggers</h1>
      <p className="lede">
        For a campaign that has not published, the tool&apos;s job is to catch day zero. A rule
        marked <em>promotes to live</em> sets its campaign&apos;s publication date on a hit and
        starts daily snapshotting.
      </p>

      {nothingRunning && (
        <div className="banner danger">
          <strong>Nothing is being watched</strong>
          No poll run has ever been recorded. Schedule{" "}
          <code className="inline">cib watch poll</code> from cron, or run{" "}
          <code className="inline">cib watch run</code> to poll and snapshot on a schedule.
        </div>
      )}
      {!nothingRunning && nothingEnabled && (
        <div className="banner danger">
          <strong>Every rule is disabled</strong>
          The poller is running but has nothing to poll. Day zero will not be caught.
        </div>
      )}
      {status.rules_with_errors?.length > 0 && (
        <div className="banner warn">
          <strong>Rules failing to poll</strong>
          {status.rules_with_errors.join(", ")} — see the last error column below.
        </div>
      )}
      {!nothingRunning && (
        <div className="banner info">
          <strong>Poller status</strong>
          Last run {formatDate(status.last_run.started_at)} ({status.last_run.started_at}):{" "}
          {status.last_run.rules_polled} rule(s) polled, {status.last_run.hits_new} new hit(s),{" "}
          {status.last_run.errors} error(s). {status.rules_enabled} of {status.rules_total} rules
          enabled.
        </div>
      )}

      <h2>Rules</h2>
      {rules.length === 0 ? (
        <div className="empty-state">
          <strong>No watch rules.</strong>
          Add one with{" "}
          <code className="inline">
            cib watch add --name … --type rss --pattern https://… --campaign …
          </code>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rule</th>
                <th>Campaign</th>
                <th>Type</th>
                <th>Pattern</th>
                <th className="num">Hits</th>
                <th>Last polled</th>
                <th>Last error</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id}>
                  <td>
                    {r.name}
                    {r.promotes_to_live ? (
                      <div>
                        <span className="pill live">promotes to live</span>
                      </div>
                    ) : null}
                  </td>
                  <td className="small mono">{r.campaign_slug ?? <span className="muted">—</span>}</td>
                  <td className="small">{r.rule_type}</td>
                  <td
                    className="small mono"
                    style={{ maxWidth: "18rem", wordBreak: "break-all" }}
                  >
                    {r.pattern}
                  </td>
                  <td className="num">{r.hits}</td>
                  <td className="small muted nowrap">
                    {r.last_polled_at ? formatDate(r.last_polled_at) : "never"}
                  </td>
                  <td className="small" style={{ color: "var(--danger)", maxWidth: "14rem" }}>
                    {r.last_error ?? ""}
                  </td>
                  <td>
                    <button onClick={() => toggle(r.id, !r.enabled)}>
                      {r.enabled ? "Disable" : "Enable"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Recent hits</h2>
      {hits.length === 0 ? (
        <div className="empty-state">
          <strong>No hits recorded.</strong>
          With no poll run yet, this is not evidence that nothing has published.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Detected</th>
                <th>Rule</th>
                <th>Title</th>
                <th>URL</th>
                <th>Notified</th>
                <th>Promoted</th>
              </tr>
            </thead>
            <tbody>
              {hits.map((h) => (
                <tr key={h.id}>
                  <td className="small nowrap">{h.detected_at}</td>
                  <td className="small">{h.rule_name}</td>
                  <td>{h.matched_title ?? <span className="muted">—</span>}</td>
                  <td className="small" style={{ maxWidth: "18rem", wordBreak: "break-all" }}>
                    <a href={h.matched_url} target="_blank" rel="noreferrer">
                      {h.matched_url}
                    </a>
                  </td>
                  <td className="small muted">{h.notified_at ? "yes" : "no"}</td>
                  <td className="small">
                    {h.promoted ? (
                      <span className="pill live">set day zero</span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
