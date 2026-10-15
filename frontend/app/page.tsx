"use client";

import { AlertCircle, ArrowUpRight, FileCode2, FileText, Globe2, ScanLine, Sparkles } from "lucide-react";
import Link from "next/link";
import { GenerationStatus } from "@/components/generation-status";
import { LlmsPreview } from "@/components/llms-preview";
import { UrlForm } from "@/components/url-form";
import { useGeneration } from "@/lib/use-generation";

const steps = [
  { icon: Globe2, title: "Start with your URL", description: "Enter a public website. We’ll discover its pages and follow the links that matter." },
  { icon: ScanLine, title: "Find the useful pages", description: "Your pages are analyzed and organized into a focused, structured overview." },
  { icon: FileText, title: "Take your file with you", description: "Review, copy, or download your llms.txt. Add it to your site when you’re ready." },
];

export default function Home() {
  const generation = useGeneration();
  const initial = !generation.result && !generation.busy && !generation.error;
  return (
    <div className="site-shell">
      <a href="#main" className="skip-link">Skip to content</a>
      <header className="site-header">
        <Link href="/" className="brand" aria-label="llms.txt Generator home"><span className="brand-mark"><FileCode2 size={21} strokeWidth={1.7} aria-hidden="true" /></span><span>llms.txt <span className="brand-light">Generator</span></span></Link>
        <a href="https://llmstxt.org/" target="_blank" rel="noopener noreferrer" className="header-link">About llms.txt <ArrowUpRight size={14} aria-hidden="true" /></a>
      </header>

      <main id="main" className="main-content">
        <div className={`hero ${initial ? "" : "hero-compact"}`}>
          <span className="eyebrow"><Sparkles size={14} aria-hidden="true" /> A LITTLE STRUCTURE GOES A LONG WAY</span>
          <h1>Make your website easier<br className="desktop-break" /> <span>for AI to understand.</span></h1>
          <p className="hero-description">Automatically discover your website’s most important pages<br className="desktop-break" /> and generate a structured <code>llms.txt</code> file.</p>
        </div>

        <div className="workspace">
          <UrlForm busy={generation.busy} hasResult={!!generation.result} onSubmit={generation.start} />
          {generation.error && <div className="error-banner" role="alert"><AlertCircle size={19} aria-hidden="true" /><div><p>{generation.error}</p>
            {generation.result && <p className="error-context">Your last successful file is still available below.</p>}
            {generation.phase === "paused" && <button className="text-button" type="button" onClick={generation.resume}>Check job again <ArrowUpRight size={14} aria-hidden="true" /></button>}
          </div></div>}
          {generation.busy && <GenerationStatus submitting={generation.phase === "submitting"} job={generation.job} url={generation.active?.url} reconnecting={generation.reconnecting} refreshing={!!generation.result} />}
          {generation.result && <LlmsPreview key={generation.result.job.job_id} result={generation.result} busy={generation.busy} onRegenerate={generation.regenerate} />}
        </div>

        {initial && <section className="how-section" aria-labelledby="how-title" id="how-it-works">
          <div className="section-label"><span /><h2 id="how-title">FROM URL TO USEFUL, IN THREE STEPS</h2><span /></div>
          <div className="how-grid">{steps.map((step, index) => <article key={step.title} className="how-step">
            <div className="how-icon"><step.icon size={21} strokeWidth={1.6} aria-hidden="true" /><span>0{index + 1}</span></div>
            <h3>{step.title}</h3><p>{step.description}</p>
          </article>)}</div>
          <p className="format-note"><FileCode2 size={16} aria-hidden="true" /><span><code>llms.txt</code> is a Markdown overview of your website, designed to help AI tools find useful content.</span></p>
        </section>}
      </main>

      <footer className="site-footer"><span>A clearer website. A more readable web.</span><span className="footer-detail"><span className="small-dot" />No API key required</span></footer>
    </div>
  );
}
