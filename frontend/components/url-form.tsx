"use client";

import { useState } from "react";
import { ArrowRight, Globe2, LoaderCircle, ShieldCheck } from "lucide-react";
import { validateUrl } from "@/lib/api";

interface UrlFormProps {
  initialUrl?: string;
  busy: boolean;
  hasResult: boolean;
  onSubmit: (url: string) => Promise<void>;
}

export function UrlForm({ initialUrl = "", busy, hasResult, onSubmit }: UrlFormProps) {
  const [url, setUrl] = useState(initialUrl);
  const [error, setError] = useState<string | null>(null);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const message = validateUrl(url);
    setError(message);
    if (!message) void onSubmit(url.trim());
  }

  return (
    <form onSubmit={submit} noValidate className="url-form" aria-label="Generate llms.txt">
      <label htmlFor="website-url" className="form-label">{hasResult ? "Generate for another website" : "Your website URL"}</label>
      <div className="form-row">
        <div className={`input-wrap ${error ? "input-invalid" : ""}`}>
          <Globe2 size={19} aria-hidden="true" />
          <input id="website-url" name="url" type="url" inputMode="url" autoComplete="url"
            placeholder="https://example.com" value={url} maxLength={2048}
            onChange={event => { setUrl(event.target.value); if (error) setError(null); }}
            aria-invalid={!!error} aria-describedby={error ? "url-error" : "url-hint"} disabled={busy} required />
        </div>
        <button className="button button-primary" type="submit" disabled={busy}>
          {busy ? <><LoaderCircle className="spin" size={17} aria-hidden="true" /> Generating…</>
            : <>Generate llms.txt <ArrowRight size={17} aria-hidden="true" /></>}
        </button>
      </div>
      {error && <p id="url-error" className="field-error" role="alert">{error}</p>}
      <p id="url-hint" className="form-hint"><ShieldCheck size={14} aria-hidden="true" /> Public websites only.</p>
    </form>
  );
}
