"use client";

import { useEffect, useState } from "react";
import { api, type Meta } from "@/lib/api";
import { ErrorBanner, Loading } from "@/components/Loading";

/**
 * The metric definitions, served from the API rather than duplicated here.
 *
 * These will be argued about more than the code will, so they live in one place and are rendered
 * verbatim wherever a number appears.
 */
export default function DefinitionsPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorBanner error={error} />;
  if (!meta) return <Loading what="definitions" />;

  return (
    <>
      <h1>Metric definitions</h1>
      <p className="lede">
        What each figure means in plain language, exactly which rows it counts, and what it cannot
        tell you. Served from the metrics module itself, so the wording here is the wording used
        everywhere else.
      </p>

      {meta.metric_order.map((key) => {
        const d = meta.definitions[key];
        if (!d) return null;
        return (
          <div className="card" key={key}>
            <h3 style={{ marginTop: 0 }}>
              {d.label}{" "}
              <span className="muted small">
                · {d.unit} · <code className="inline">{key}</code>
              </span>
            </h3>
            <p style={{ margin: "0 0 0.5rem" }}>{d.plain}</p>
            <p className="small muted" style={{ margin: "0 0 0.35rem" }}>
              <strong>Counts:</strong> {d.counts}
            </p>
            {d.caveat && (
              <p className="small" style={{ margin: 0, color: "var(--warn)" }}>
                <strong>Caveat:</strong> {d.caveat}
              </p>
            )}
          </div>
        );
      })}

      <h2>Exposure depth weights</h2>
      <div className="card">
        <table>
          <tbody>
            {Object.entries(meta.role_weights).map(([role, weight]) => (
              <tr key={role}>
                <td>{role.replace(/_/g, " ")}</td>
                <td className="num">×{weight}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="small muted" style={{ marginBottom: 0, marginTop: "0.6rem" }}>
          The weighted total is never shown without these components. &quot;We scored 34&quot; is
          exactly the kind of number this tool exists to replace.
        </p>
      </div>

      <h2>Escalation severity anchors</h2>
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
      </div>
    </>
  );
}
