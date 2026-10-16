import { Generator } from "@/components/generator";

export default async function Home({ searchParams }: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  // Duplicate parameters are invalid, rather than silently selecting a target.
  const url = Array.isArray(params.url) ? "" : params.url;
  const jobId = Array.isArray(params.job) ? "" : params.job;
  return <Generator key={`${url ?? ""}:${jobId ?? ""}`} initialUrl={url} initialJobId={jobId} />;
}
