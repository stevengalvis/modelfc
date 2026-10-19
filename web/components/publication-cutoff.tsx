"use client";
import { useState } from "react";

export function PublicationCutoff({ onApply }: { onApply: (value: string) => void }) {
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState(false);
  return <div><form onSubmit={event => {
    event.preventDefault();
    const value = draft.trim();
    if (value && (!/(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value)) || Date.parse(value) > Date.now())) {
      setInvalid(true); return;
    }
    setInvalid(false); onApply(value ? new Date(value).toISOString() : "");
  }}>
    <label>Publication cutoff (ISO timestamp with timezone; blank for current) <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="2026-10-19T12:00:00Z" /></label>
    <button className="secondary-button" type="submit">Apply publication cutoff</button>
    {invalid ? <p role="alert">Enter a timestamp with timezone no later than now.</p> : null}
  </form><p>History uses recorded publication and result times in the saved evidence available now. It does not prove which records were stored or visible on the site then. Missing source provenance is not reconstructed.</p></div>;
}
