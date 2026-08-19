export function Loading({ what }: { what: string }) {
  return <p className="muted">Loading {what}…</p>;
}

export function ErrorBanner({ error }: { error: string }) {
  return (
    <div className="banner danger">
      <strong>Could not load</strong>
      {error}
    </div>
  );
}
