"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, validateUrl } from "./api";
import type { GenerationResult, Job } from "./types";

interface ActiveJob { job_id: string; url: string }
const SESSION_KEY = "llms-active-job";

function linkToJob(url: string, jobId?: string) {
  const location = new URL("/", window.location.href);
  location.searchParams.set("url", url);
  if (jobId) location.searchParams.set("job", jobId);
  window.history.replaceState(window.history.state, "", location);
}

function remember(job: ActiveJob | null) {
  try {
    if (job) sessionStorage.setItem(SESSION_KEY, JSON.stringify(job));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch { /* Storage is optional, including in private browsing. */ }
}

function crawlError(job: Job): string {
  const detail = job.error?.toLowerCase() ?? "";
  if (detail.includes("robots")) return "This website blocks crawling or its robots.txt could not be checked. Please try another website.";
  if (detail.includes("time limit")) return "This website took too long to crawl. Please try again or use a smaller section of the site.";
  return "We couldn’t crawl this website. Please check that the URL is public and accessible, then try again.";
}

export function useGeneration(initialUrl?: string, initialJobId?: string) {
  const [phase, setPhase] = useState<"idle" | "submitting" | "polling" | "paused">("idle");
  const [active, setActive] = useState<ActiveJob | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const locked = useRef(false);
  const submission = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!active || phase !== "polling") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;

    async function poll() {
      if (!active) return;
      try {
        const current = await api.job(active.job_id, controller.signal);
        if (controller.signal.aborted) return;
        setJob(current);
        setReconnecting(false);
        if (current.status === "failed") {
          setError(crawlError(current));
          setPhase("idle");
          locked.current = false;
          remember(null);
          return;
        }
        if (current.status === "completed") {
          const [website, markdown] = await Promise.all([
            api.website(current.website_id, controller.signal),
            api.markdown(current.website_id, controller.signal),
          ]);
          if (controller.signal.aborted) return;
          if (!markdown.trim()) throw new ApiError("The generated file is empty. Please generate it again.", 422);
          setResult({ job: current, website, markdown });
          setPhase("idle");
          setError(null);
          locked.current = false;
          remember(null);
          return;
        }
        failures = 0;
        timer = setTimeout(poll, 2_000);
      } catch (err) {
        if (controller.signal.aborted) return;
        failures += 1;
        if (failures < 3 && (!(err instanceof ApiError) || err.status === 0 || err.status >= 500)) {
          setReconnecting(true);
          timer = setTimeout(poll, 2_000 * failures);
        } else {
          setError(errorMessage(err));
          setReconnecting(false);
          setPhase("paused");
          locked.current = false;
        }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active, phase]);

  const start = useCallback(async (url: string, websiteId?: string) => {
    if (locked.current) return;
    locked.current = true;
    submission.current?.abort();
    const controller = new AbortController();
    submission.current = controller;
    setError(null);
    setActive(null);
    remember(null);
    setJob(null);
    setReconnecting(false);
    setPhase("submitting");
    linkToJob(url);
    try {
      const created = websiteId
        ? await api.refresh(websiteId, controller.signal)
        : await api.generate(url, controller.signal);
      if (controller.signal.aborted) return;
      const next = { ...created, url };
      linkToJob(url, created.job_id);
      // A new website should never be paired with the previous site's preview.
      setResult(previous => previous?.website.website_id === created.website_id ? previous : null);
      setActive(next);
      remember(next);
      setPhase("polling");
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(errorMessage(err));
      setPhase("idle");
      locked.current = false;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Defer bootstrapping so React Strict Mode's effect cleanup cannot abort
    // the one automatic submission and leave the duplicate-submission lock set.
    queueMicrotask(() => {
      if (cancelled) return;
      if (initialUrl !== undefined || initialJobId !== undefined) {
        const message = validateUrl(initialUrl ?? "");
        if (message) { setError(message); return; }
        if (initialJobId !== undefined) {
          if (!/^[\w-]+$/.test(initialJobId)) {
            setError("This job link is invalid. Generate the file again.");
            return;
          }
          locked.current = true;
          setActive({ job_id: initialJobId, url: initialUrl! });
          setPhase("polling");
        } else {
          // An explicit new target takes precedence over another saved job.
          void start(initialUrl!.trim());
        }
        return;
      }
      try {
        const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "null");
        if (saved && typeof saved.job_id === "string" && /^[\w-]+$/.test(saved.job_id)
          && typeof saved.url === "string" && !validateUrl(saved.url)) {
          locked.current = true;
          linkToJob(saved.url, saved.job_id);
          setActive(saved);
          setPhase("polling");
        }
      } catch { remember(null); }
    });
    return () => { cancelled = true; submission.current?.abort(); };
  }, [initialUrl, initialJobId, start]);

  function resume() {
    if (locked.current || !active) return;
    locked.current = true;
    setError(null);
    setPhase("polling");
  }

  return {
    phase, active, job, result, error, reconnecting, start, resume,
    busy: phase === "submitting" || phase === "polling",
    regenerate: () => result && start(result.website.normalized_url, result.website.website_id),
  };
}
