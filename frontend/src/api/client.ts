// Typed client for the back-end API. All calls are same-origin (/api).
import type {
  AskHistoryItem,
  AskResult,
  ChatTurn,
  DashboardPayload,
  DashboardSpec,
  DatasetInfo,
  InferResult,
  JobRecord,
  JobSubmitted,
  ReplayResponse,
  SemanticModel,
} from "./types";

export class ApiError extends Error {
  status: number;
  details?: unknown;
  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { credentials: "same-origin", ...init });
  } catch {
    throw new ApiError("Could not reach the server. Check your connection and try again.", 0);
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let details: unknown;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
      else if (Array.isArray(body?.detail)) message = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
      details = body?.details;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) message = "Your sign-in has expired. Reload the page to sign in again.";
    throw new ApiError(message, res.status, details);
  }
  return (await res.json()) as T;
}

function json(body: unknown, method = "POST"): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  health: () => request<{ status: string; version: string; llm: { configured: boolean; provider: string; model: string } }>("/api/health"),
  me: () => request<{ name: string; email: string; authenticated: boolean }>("/api/me"),
  listSamples: () => request<{ name: string; size: number }[]>("/api/samples"),
  async fetchSample(name: string): Promise<File> {
    const res = await fetch(`/api/samples/${encodeURIComponent(name)}`);
    if (!res.ok) throw new ApiError("Sample not found", res.status);
    return new File([await res.blob()], name);
  },
  uploadDataset(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<DatasetInfo>("/api/datasets", { method: "POST", body: form });
  },
  selectSheet: (id: string, sheet: string) => request<DatasetInfo>(`/api/datasets/${id}/sheet`, json({ sheet })),
  startInfer: (id: string) => request<JobSubmitted>(`/api/datasets/${id}/infer`, { method: "POST" }),
  startRefineModel: (id: string, model: SemanticModel, message: string, history: ChatTurn[]) =>
    request<JobSubmitted>(`/api/datasets/${id}/refine-model`, json({ model, message, history })),
  startBuild: (id: string, model: SemanticModel) => request<JobSubmitted>(`/api/datasets/${id}/build`, json({ model })),
  render: (id: string, spec: DashboardSpec) => request<DashboardPayload>(`/api/datasets/${id}/render`, json({ spec })),
  startRefineDashboard: (id: string, spec: DashboardSpec, message: string, history: ChatTurn[]) =>
    request<JobSubmitted>(`/api/datasets/${id}/refine`, json({ spec, message, history })),
  startNarratives: (id: string, spec: DashboardSpec, widget_ids: string[] | null, force: boolean) =>
    request<JobSubmitted>(`/api/datasets/${id}/narratives`, json({ spec, widget_ids, force })),
  startAsk: (id: string, question: string, model: SemanticModel, history: AskHistoryItem[]) =>
    request<JobSubmitted>(`/api/datasets/${id}/ask`, json({ question, model, history })),
  replay(file: File, spec: unknown) {
    const form = new FormData();
    form.append("file", file);
    form.append("spec", JSON.stringify(spec));
    return request<ReplayResponse>("/api/replay", { method: "POST", body: form });
  },
  getJob: (id: string) => request<JobRecord>(`/api/jobs/${id}`),
};

/** Start a background job and poll it until it finishes. */
export async function runJob<T>(start: () => Promise<JobSubmitted>, onProgress?: (text: string) => void): Promise<T> {
  const { job_id } = await start();
  let delay = 700;
  for (;;) {
    await new Promise((r) => setTimeout(r, delay));
    const job = await api.getJob(job_id);
    if (job.progress && onProgress) onProgress(job.progress);
    if (job.status === "succeeded") return job.result as T;
    if (job.status === "failed") throw new ApiError(job.error || "The job failed.", 500);
    delay = Math.min(delay * 1.25, 2500);
  }
}

export type { AskResult, DashboardPayload, InferResult };
