/**
 * file: src/api/client.ts
 * importable: ApiError, scanDefaults, startScan, listScans, getScan, scanResults, defaultProfile,
 *             listProfiles, createProfile, updateProfile, deleteProfile, and the types they use
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
