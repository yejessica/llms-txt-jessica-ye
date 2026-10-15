import { Check, Circle, FileText, LoaderCircle } from "lucide-react";
import type { Job } from "@/lib/types";

interface StatusProps {
  submitting: boolean;
  job: Job | null;
  url?: string;
  reconnecting: boolean;
  refreshing: boolean;
}

export function GenerationStatus({ submitting, job, url, reconnecting, refreshing }: StatusProps) {
  const running = job?.status === "running";
  const completed = job?.status === "completed";
  const steps = [
    { label: "Website submitted", done: !submitting, active: submitting },
    { label: "Discover and analyze pages", done: completed, active: !submitting && running },
    { label: "Generate your llms.txt", done: false, active: completed },
  ];

  return (
    <section className="status-card" aria-label="Generation progress" aria-busy="true">
      <div className="status-heading"><span className="status-icon"><FileText size={21} aria-hidden="true" /></span>
        <div><h2>{refreshing ? "Refreshing your llms.txt…" : "Generating your llms.txt…"}</h2>
          <p className="truncate-url">{url || "Connecting to the generation service"}</p></div>
      </div>
      <div role="status" aria-live="polite" className="status-message">
        {reconnecting ? "Connection interrupted. Reconnecting automatically…"
          : submitting ? "Submitting your website…"
            : completed ? "Crawl complete. Loading your generated file…"
              : running ? "Your website is being crawled and analyzed."
                : "Website queued. Waiting for an available worker."}
      </div>
      <ol className="progress-steps">
        {steps.map(step => <li key={step.label} className={step.done ? "step-done" : step.active ? "step-active" : "step-waiting"}>
          {step.done ? <Check size={17} aria-hidden="true" /> : step.active ? <LoaderCircle className="spin" size={17} aria-hidden="true" /> : <Circle size={15} aria-hidden="true" />}
          {step.label}{step.done && <span className="sr-only">: complete</span>}{step.active && <span className="sr-only">: in progress</span>}
        </li>)}
      </ol>
      <p className="status-note">Larger websites can take a few minutes. Page counts appear when the crawl finishes.</p>
      {!submitting && !running && !completed && <p className="status-note">Running locally? Keep the backend worker running to process queued jobs.</p>}
    </section>
  );
}
