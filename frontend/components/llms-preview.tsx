"use client";

import { useEffect, useRef, useState } from "react";
import { Check, CheckCircle2, Clipboard, Download, FileCode2, History, LoaderCircle, RefreshCw } from "lucide-react";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { GenerationInfo, GenerationResult } from "@/lib/types";

const formatDate = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function saveFile(content: string) {
  const href = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = "llms.txt";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1_000);
}

// Keep the shareable address in step with the version on screen.
function linkToVersion(generationId: string | null) {
  const location = new URL(window.location.href);
  if (generationId) location.searchParams.set("version", generationId);
  else location.searchParams.delete("version");
  window.history.replaceState(window.history.state, "", location);
}

export function LlmsPreview({ result, busy, onRegenerate, initialVersion }: {
  result: GenerationResult;
  busy: boolean;
  onRegenerate: () => void;
  initialVersion?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<GenerationInfo[] | null>(null);
  const [historyState, setHistoryState] = useState<"idle" | "loading" | "error">("idle");
  const [loadingVersion, setLoadingVersion] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ info: GenerationInfo; markdown: string } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const code = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const requests = useRef(new AbortController());
  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current); }, []);
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    // Created per mount: Strict Mode remounts, and an aborted controller can't be reused.
    const controller = new AbortController();
    requests.current = controller;
    return () => controller.abort();
  }, []);
  const markdown = viewing?.markdown ?? result.markdown;
  const websiteId = result.website.website_id;

  // Each request keeps its own signal: after a Strict Mode remount, requests.current
  // is a fresh controller, and a superseded request must not report its abort.
  async function loadHistory(): Promise<GenerationInfo[] | null> {
    if (history) return history;
    if (historyState === "loading") return null;
    const signal = requests.current.signal;
    setHistoryState("loading");
    try {
      const list = await api.generations(websiteId, signal);
      if (signal.aborted) return null;
      setHistory(list);
      setHistoryState("idle");
      return list;
    } catch {
      if (!signal.aborted) setHistoryState("error");
      return null;
    }
  }

  function showCurrent() {
    setViewing(null);
    linkToVersion(null);
  }

  async function view(info: GenerationInfo) {
    if (info.is_latest) { showCurrent(); return; }
    if (loadingVersion) return;
    const signal = requests.current.signal;
    setLoadingVersion(info.generation_id);
    setError(null);
    try {
      const content = await api.version(websiteId, info.generation_id, signal);
      if (signal.aborted) return;
      if (!content.trim()) throw new ApiError("That version is empty and can’t be shown.");
      setViewing({ info, markdown: content });
      linkToVersion(info.generation_id);
    } catch (err) {
      if (!signal.aborted) setError(errorMessage(err));
    } finally { if (!signal.aborted) setLoadingVersion(null); }
  }

  useEffect(() => {
    if (!initialVersion) return;
    const signal = requests.current.signal;
    void (async () => {
      const list = /^[\w-]+$/.test(initialVersion) ? await loadHistory() : [];
      if (signal.aborted || !list) return;
      const linked = list.find(item => item.generation_id === initialVersion);
      if (!linked) {
        setError("That version link is no longer available. Showing the current file.");
        linkToVersion(null);
        return;
      }
      setHistoryOpen(true);
      if (!linked.is_latest) await view(linked);
    })();
    // Runs once per linked result; later changes come from the user's own choices.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function copy() {
    setError(null);
    try {
      await navigator.clipboard.writeText(markdown);
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
      // A previous version is already loaded in full; only the current file is fetched.
      const content = viewing ? viewing.markdown : await api.download(websiteId);
      if (!content.trim()) throw new ApiError("The downloaded file is empty. Please try again.");
      saveFile(content);
    } catch (err) { setError(errorMessage(err)); }
    finally { setDownloading(false); }
  }

  const generatedAt = result.website.latest_generation?.generated_at;
  const previous = history?.filter(item => !item.is_latest) ?? [];
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
        {generatedAt && <div><span>Generated</span><time dateTime={generatedAt}>{formatDate(generatedAt)}</time></div>}
      </div>
      {result.job.output_changed === false && <p className="result-note">Website checked. Your file is already up to date; no new version was needed.</p>}
      {result.job.warnings.length > 0 && <details className="warnings"><summary>Completed with {result.job.warnings.length} crawl {result.job.warnings.length === 1 ? "warning" : "warnings"}</summary>
        <p>Some pages could not be analyzed. Review the output before using it.</p>
        <ul>{result.job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
      </details>}
      <details className="versions" open={historyOpen} onToggle={event => {
        const open = event.currentTarget.open;
        setHistoryOpen(open);
        if (open) void loadHistory();
      }}>
        <summary><History size={14} aria-hidden="true" />Previous versions</summary>
        {historyState === "loading" && <p className="versions-note"><LoaderCircle className="spin" size={13} aria-hidden="true" />Loading versions…</p>}
        {historyState === "error" && <p className="versions-note">Previous versions couldn’t be loaded. <button type="button" className="link-button" onClick={() => void loadHistory()}>Try again</button></p>}
        {history && previous.length === 0 && <p className="versions-note">No previous versions yet. A new version is saved whenever a regeneration changes the file.</p>}
        {previous.length > 0 && <ul className="versions-list">
          {history?.map(item => {
            const selected = viewing ? viewing.info.generation_id === item.generation_id : item.is_latest;
            return <li key={item.generation_id} className={selected ? "version-selected" : undefined}>
              <time dateTime={item.generated_at}>{formatDate(item.generated_at)}</time>
              {item.is_latest && <span className="version-badge">Current</span>}
              {selected
                ? <span className="version-viewing">Viewing</span>
                : <button type="button" className="link-button" disabled={loadingVersion !== null} onClick={() => void view(item)}
                    aria-label={`View version from ${formatDate(item.generated_at)}`}>
                    {loadingVersion === item.generation_id ? "Loading…" : "View"}
                  </button>}
            </li>;
          })}
        </ul>}
      </details>
      {viewing && <p className="version-banner" role="status">
        Viewing a previous version from <time dateTime={viewing.info.generated_at}>{formatDate(viewing.info.generated_at)}</time>. Copy and download use this version.
        <button type="button" className="link-button" onClick={showCurrent}>Back to current</button>
      </p>}
      <div className="preview-card">
        <div className="preview-toolbar">
          <div className="file-label"><FileCode2 size={17} aria-hidden="true" /><span>llms.txt</span><span className="file-type">Markdown</span></div>
          <div className="preview-actions">
            <button type="button" className="button button-small" onClick={copy}>{copied ? <Check size={15} aria-hidden="true" /> : <Clipboard size={15} aria-hidden="true" />}{copied ? "Copied!" : "Copy"}</button>
            <button type="button" className="button button-small" disabled={downloading} onClick={download}>{downloading ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}Download</button>
          </div>
        </div>
        <pre className="markdown-preview" tabIndex={0} aria-label="llms.txt Markdown preview"><code ref={code}>{markdown}</code></pre>
        <div className="preview-footer"><span>Plain text. Open format. Yours to keep.</span><span>{new TextEncoder().encode(markdown).length.toLocaleString()} bytes</span></div>
      </div>
      <p className="sr-only" role="status">{copied ? "llms.txt copied to clipboard." : ""}</p>
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="result-bottom"><p>Place this file at <code>your-domain.com/llms.txt</code></p>
        <button type="button" className="button button-secondary" disabled={busy} onClick={onRegenerate}><RefreshCw size={15} aria-hidden="true" />Regenerate</button>
      </div>
    </section>
  );
}
