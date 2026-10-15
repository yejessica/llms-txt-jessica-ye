"use client";

import { useEffect, useRef, useState } from "react";
import { Check, CheckCircle2, Clipboard, Download, FileCode2, LoaderCircle, RefreshCw } from "lucide-react";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { GenerationResult } from "@/lib/types";

export function LlmsPreview({ result, busy, onRegenerate }: {
  result: GenerationResult;
  busy: boolean;
  onRegenerate: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const code = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current); }, []);
  useEffect(() => { heading.current?.focus(); }, []);

  async function copy() {
    setError(null);
    try {
      await navigator.clipboard.writeText(result.markdown);
      setCopied(true);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 2_000);
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      if (code.current && selection) {
        range.selectNodeContents(code.current);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      setError("Clipboard access is unavailable. The text is selected; use your device’s copy command.");
    }
  }

  async function download() {
    if (downloading) return;
    setDownloading(true);
    setError(null);
    try {
      const content = await api.download(result.website.website_id);
      if (!content.trim()) throw new ApiError("The downloaded file is empty. Please try again.");
      const href = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = "llms.txt";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(href), 1_000);
    } catch (err) { setError(errorMessage(err)); }
    finally { setDownloading(false); }
  }

  const generatedAt = result.website.latest_generation?.generated_at;
  return (
    <section className="result-section" aria-label="Generated llms.txt">
      <div className="result-heading">
        <span className="success-icon"><CheckCircle2 size={23} aria-hidden="true" /></span>
        <div><h2 ref={heading} tabIndex={-1}>llms.txt generated successfully!</h2><p className="result-url">{result.website.normalized_url}</p></div>
        <span className="ready-badge">{busy ? "Previous version" : "Ready to use"}</span>
      </div>
      <div className="result-stats">
        <div><span>Pages analyzed</span><strong>{result.job.pages_crawled}</strong></div>
        <div><span>Pages selected</span><strong>{result.job.pages_selected}</strong></div>
        {generatedAt && <div><span>Generated</span><time dateTime={generatedAt}>{new Date(generatedAt).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time></div>}
      </div>
      {result.job.output_changed === false && <p className="result-note">Website checked. Your file is already up to date; no new version was needed.</p>}
      {result.job.warnings.length > 0 && <details className="warnings"><summary>Completed with {result.job.warnings.length} crawl {result.job.warnings.length === 1 ? "warning" : "warnings"}</summary>
        <p>Some pages could not be analyzed. Review the output before using it.</p>
        <ul>{result.job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
      </details>}
      <div className="preview-card">
        <div className="preview-toolbar">
          <div className="file-label"><FileCode2 size={17} aria-hidden="true" /><span>llms.txt</span><span className="file-type">Markdown</span></div>
          <div className="preview-actions">
            <button type="button" className="button button-small" onClick={copy}>{copied ? <Check size={15} aria-hidden="true" /> : <Clipboard size={15} aria-hidden="true" />}{copied ? "Copied!" : "Copy"}</button>
            <button type="button" className="button button-small" disabled={downloading} onClick={download}>{downloading ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}Download</button>
          </div>
        </div>
        <pre className="markdown-preview" tabIndex={0} aria-label="llms.txt Markdown preview"><code ref={code}>{result.markdown}</code></pre>
        <div className="preview-footer"><span>Plain text. Open format. Yours to keep.</span><span>{new TextEncoder().encode(result.markdown).length.toLocaleString()} bytes</span></div>
      </div>
      <p className="sr-only" role="status">{copied ? "llms.txt copied to clipboard." : ""}</p>
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="result-bottom"><p>Place this file at <code>your-domain.com/llms.txt</code></p>
        <button type="button" className="button button-secondary" disabled={busy} onClick={onRegenerate}><RefreshCw size={15} aria-hidden="true" />Regenerate</button>
      </div>
    </section>
  );
}
