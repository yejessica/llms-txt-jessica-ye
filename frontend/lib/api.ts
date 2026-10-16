import type { GenerationInfo, Job, JobCreated, Website } from "./types";

export class ApiError extends Error {
  constructor(message: string, public status = 0) {
    super(message);
    this.name = "ApiError";
  }
}

export function validateUrl(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "Enter your website URL to get started.";
  if (trimmed.length > 2048) return "Use a URL with 2,048 characters or fewer.";
  try {
    const url = new URL(trimmed);
    if (!["https:", "http:"].includes(url.protocol)) {
      return "Use an https:// or http:// website URL.";
    }
    if (url.username || url.password || url.port) {
      return "Use a public website URL without credentials or a custom port.";
    }
    if (!url.hostname || /\s/.test(trimmed)) throw new Error("Invalid URL");
    return null;
  } catch {
    return "Enter a complete website URL, like https://example.com.";
  }
}

async function request<T>(path: string, options: RequestInit = {}, asText = false): Promise<T> {
  try {
    const response = await fetch(`/api${path}`, {
      ...options,
      cache: "no-store",
      signal: AbortSignal.any([
        AbortSignal.timeout(25_000),
        ...(options.signal ? [options.signal] : []),
      ]),
    });
    if (!response.ok) {
      if (response.status === 429) {
        throw new ApiError("The demo is busy. Wait a moment before generating or regenerating again.", 429);
      }
      if (response.status === 422) {
        throw new ApiError("This URL must resolve to a public HTTP(S) website without credentials or a custom port.", 422);
      }
      if (response.status === 404) {
        throw new ApiError("This job or file is no longer available. Please generate it again.", 404);
      }
      if (response.status === 504 || response.status === 408) {
        throw new ApiError("The request timed out. Please try again. Your job may still be processing.", response.status);
      }
      // Do not display arbitrary server bodies, proxy HTML, or internal errors.
      throw new ApiError("We couldn’t reach the generation service. Please try again shortly.", response.status);
    }
    return (asText ? await response.text() : await response.json()) as T;
  } catch (error) {
    if (options.signal?.aborted) throw error;
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError("The request timed out. Please try again. Your job may still be processing.", 504);
    }
    throw new ApiError("We couldn’t reach the generation service. Check your connection and try again.");
  }
}

const sitePath = (id: string) => `/websites/${encodeURIComponent(id)}`;

export const api = {
  generate: (url: string, signal?: AbortSignal) => request<JobCreated>("/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }), signal,
  }),
  job: (id: string, signal?: AbortSignal) => request<Job>(`/jobs/${encodeURIComponent(id)}`, { signal }),
  website: (id: string, signal?: AbortSignal) => request<Website>(sitePath(id), { signal }),
  markdown: (id: string, signal?: AbortSignal) => request<string>(`${sitePath(id)}/llms.txt`, { signal }, true),
  refresh: (id: string, signal?: AbortSignal) => request<JobCreated>(`${sitePath(id)}/refresh`, { method: "POST", signal }),
  download: (id: string) => request<string>(`${sitePath(id)}/download`, {}, true),
  generations: (id: string, signal?: AbortSignal) => request<GenerationInfo[]>(`${sitePath(id)}/generations`, { signal }),
  version: (id: string, generationId: string, signal?: AbortSignal) =>
    request<string>(`${sitePath(id)}/generations/${encodeURIComponent(generationId)}`, { signal }, true),
};

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}
