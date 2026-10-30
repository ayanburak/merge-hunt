/**
 * file: src/api/client.ts
 * importable: ApiError, shouldRetry, scanDefaults, startScan, listScans, getScan, scanResults, defaultProfile,
 *             listProfiles, createProfile, updateProfile, deleteProfile, getRepo, repoIssues,
 *             refreshIssues, giveFeedback, takeBackFeedback, and the types they use
 *
 * Every call to the backend goes through here, pages and hooks never call fetch themselves.
 * A call that fails throws ApiError, its message is ready to show on the screen.
 *
 * description: Typed functions for each backend endpoint.
 */

// The types come from schema.d.ts, which npm run api:types writes from the backend's own
// OpenAPI schema. That file is never edited by hand, after a backend change it is written again
// and any field that moved shows up as a type error here or in the pages.

import type { components } from "./schema";

// --- Types ---

type Schemas = components["schemas"];

export type Scan = Schemas["Scan"];
export type ScanDetail = Schemas["ScanDetail"];
export type ScanCreated = Schemas["ScanCreated"];
export type ScanRequest = Schemas["ScanRequest"];
export type ScoredRepo = Schemas["ScoredRepo"];
export type ProfileSettings = Schemas["ProfileSettings"];
export type ProfileWrite = Schemas["ProfileWrite"];
export type ProfileAnswer = Schemas["ProfileAnswer"];
export type Repo = Schemas["Repo"];
export type RepoIssues = Schemas["RepoIssues"];
export type AnalyzedIssue = Schemas["AnalyzedIssue"];
export type FeedbackValue = Schemas["FeedbackValue"];
export type HideReason = Schemas["HideReason"];

// What the issue list can hide, the backend does the hiding.
export type IssueFilter = {
  hide_assigned: boolean;
  hide_linked: boolean;
  max_age_days: number | null;
  show_skipped: boolean;
  show_hidden: boolean;
};

// --- Requests ---

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// status is zero when the backend gave no answer at all.
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// A 4xx answer is the backend's decision and it would say the same again, only a lost connection
// or a server error is worth one more try. The wait before that try is what kept a page blank for
// a second when a repo did not exist.
export function shouldRetry(count: number, error: Error): boolean {
  const refused = error instanceof ApiError && error.status >= 400 && error.status < 500;
  return count < 1 && !refused;
}

// FastAPI sends a plain sentence for the errors we raise and a list for a body it rejected.
// The list goes to the screen as one line, only its messages mean anything to a person, and
// pydantic starts the ones from our own validators with a prefix that means nothing to them.
function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg.replace("Value error, ", "")).join(", ");
  return `The backend answered with status ${status}`;
}

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(BASE_URL + path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "The backend is not reachable");
  }

  if (!response.ok) {
    const answer = await response.json().catch(() => null);
    throw new ApiError(response.status, errorMessage(answer?.detail, response.status));
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

// --- Scans ---

export function scanDefaults(): Promise<ScanRequest> {
  return request("/scans/defaults");
}

export function startScan(options: ScanRequest): Promise<ScanCreated> {
  return request("/scans", "POST", options);
}

export function listScans(limit?: number): Promise<Scan[]> {
  return request(limit === undefined ? "/scans" : `/scans?limit=${limit}`);
}

export function getScan(id: number): Promise<ScanDetail> {
  return request(`/scans/${id}`);
}

// A POST only because the settings do not fit in a URL, nothing is saved.
export function scanResults(id: number, settings: ProfileSettings): Promise<ScoredRepo[]> {
  return request(`/scans/${id}/results`, "POST", settings);
}

// --- Profiles ---

export function defaultProfile(): Promise<ProfileSettings> {
  return request("/profiles/default");
}

export function listProfiles(): Promise<ProfileAnswer[]> {
  return request("/profiles");
}

export function createProfile(profile: ProfileWrite): Promise<ProfileAnswer> {
  return request("/profiles", "POST", profile);
}

export function updateProfile(id: number, profile: ProfileWrite): Promise<ProfileAnswer> {
  return request(`/profiles/${id}`, "PUT", profile);
}

export function deleteProfile(id: number): Promise<void> {
  return request(`/profiles/${id}`, "DELETE");
}

// --- Repos ---

export function getRepo(id: number): Promise<Repo> {
  return request(`/repos/${id}`);
}

// An empty age limit is left out of the address, the backend reads that as no limit.
export function repoIssues(id: number, filter: IssueFilter): Promise<RepoIssues> {
  const query = new URLSearchParams({
    hide_assigned: String(filter.hide_assigned),
    hide_linked: String(filter.hide_linked),
    show_skipped: String(filter.show_skipped),
    show_hidden: String(filter.show_hidden),
  });
  if (filter.max_age_days !== null) query.set("max_age_days", String(filter.max_age_days));
  return request(`/repos/${id}/issues?${query}`);
}

export function refreshIssues(id: number): Promise<void> {
  return request(`/repos/${id}/issues/refresh`, "POST");
}

export function giveFeedback(id: number, number: number, value: FeedbackValue): Promise<void> {
  return request(`/repos/${id}/issues/${number}/feedback`, "PUT", { value });
}

export function takeBackFeedback(id: number, number: number): Promise<void> {
  return request(`/repos/${id}/issues/${number}/feedback`, "DELETE");
}
