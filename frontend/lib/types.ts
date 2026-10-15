// Mirrors backend/app/api/schemas.py. Counts are final, not live crawl progress.
export type JobStatus = "pending" | "running" | "completed" | "failed";

export interface JobCreated {
  job_id: string;
  website_id: string;
  status: JobStatus;
}

export interface Job extends JobCreated {
  job_type: "initial" | "scheduled";
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  pages_crawled: number;
  pages_selected: number;
  error: string | null;
  warnings: string[];
  output_changed: boolean | null;
}

export interface GenerationInfo {
  generation_id: string;
  content_hash: string;
  generated_at: string;
  is_latest: boolean;
}

export interface Website {
  website_id: string;
  original_url: string;
  normalized_url: string;
  title: string | null;
  created_at: string;
  last_crawl_at: string | null;
  last_successful_generation_at: string | null;
  next_check_at: string | null;
  status: JobStatus;
  latest_generation: GenerationInfo | null;
}

export interface GenerationResult {
  job: Job;
  website: Website;
  markdown: string;
}
