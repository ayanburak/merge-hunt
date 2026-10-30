/**
 * file: src/api/client.test.ts
 * description: Checks how answers and failures of the backend reach the pages.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createProfile, deleteProfile, getScan, giveFeedback, listScans, repoIssues, shouldRetry } from "./client";
import type { ProfileSettings } from "./client";

// A fresh Response for each call, the body of one can only be read once.
const answer = (status: number, body?: unknown) =>
  vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status }));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("shouldRetry", () => {
  it("tries once more after a lost connection or a server error", () => {
    expect(shouldRetry(0, new ApiError(0, "gone"))).toBe(true);
    expect(shouldRetry(0, new ApiError(500, "broken"))).toBe(true);
  });

  it("stops after one more try", () => {
    expect(shouldRetry(1, new ApiError(500, "broken"))).toBe(false);
  });

  it("takes the answer of the backend as final when it refuses", () => {
    expect(shouldRetry(0, new ApiError(404, "missing"))).toBe(false);
    expect(shouldRetry(0, new ApiError(422, "rejected"))).toBe(false);
  });
});

describe("request", () => {
  it("gives back the parsed answer", async () => {
    vi.stubGlobal("fetch", answer(200, [{ id: 3 }]));

    await expect(listScans()).resolves.toEqual([{ id: 3 }]);
  });

  it("adds the limit only when asked", async () => {
    const fetch = answer(200, []);
    vi.stubGlobal("fetch", fetch);

    await listScans();
    await listScans(5);

    const urls = fetch.mock.calls.map((call) => String((call as unknown[])[0]));
    expect(urls[0]).toMatch(/\/scans$/);
    expect(urls[1]).toMatch(/\/scans\?limit=5$/);
  });

  it("sends a body as JSON", async () => {
    const fetch = answer(201, { id: 1 });
    vi.stubGlobal("fetch", fetch);

    await createProfile({ name: "Strict", settings: {} as ProfileSettings });

    const sent = (fetch.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(sent.method).toBe("POST");
    expect(sent.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(String(sent.body)).name).toBe("Strict");
  });

  it("reads nothing from an empty answer", async () => {
    vi.stubGlobal("fetch", answer(204));

    await expect(deleteProfile(1)).resolves.toBeUndefined();
  });
});

describe("failures", () => {
  it("keeps the sentence of an error we raised", async () => {
    vi.stubGlobal("fetch", answer(404, { detail: "Scan 9 does not exist" }));

    const error = await getScan(9).catch((failure) => failure);

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.message).toBe("Scan 9 does not exist");
  });

  it("joins a rejected body into one line without pydantic's prefix", async () => {
    vi.stubGlobal("fetch", answer(422, {
      detail: [{ msg: "Value error, min_stars is larger than max_stars" }, { msg: "Field required" }],
    }));

    await expect(getScan(1)).rejects.toThrow("min_stars is larger than max_stars, Field required");
  });

  it("survives an answer that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Bad gateway", { status: 502 })));

    await expect(getScan(1)).rejects.toThrow("The backend answered with status 502");
  });

  it("names a backend that gave no answer with status zero", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await getScan(1).catch((failure) => failure);

    expect(error.status).toBe(0);
    expect(error.message).toBe("The backend is not reachable");
  });
});

describe("repo issues", () => {
  const FILTER = { hide_assigned: true, hide_linked: false, max_age_days: null, show_skipped: false, show_hidden: false };

  it("leaves an empty age limit out of the address", async () => {
    const fetch = answer(200, { issues: [] });
    vi.stubGlobal("fetch", fetch);

    await repoIssues(5, FILTER);

    expect(fetch.mock.calls[0]).toEqual(expect.arrayContaining([expect.stringContaining("/repos/5/issues?hide_assigned=true&hide_linked=false&show_skipped=false")]));
    expect(String((fetch.mock.calls[0] as unknown[])[0])).not.toContain("max_age_days");
  });

  it("sends the age limit when there is one", async () => {
    const fetch = answer(200, { issues: [] });
    vi.stubGlobal("fetch", fetch);

    await repoIssues(5, { ...FILTER, max_age_days: 365 });

    expect(String((fetch.mock.calls[0] as unknown[])[0])).toContain("max_age_days=365");
  });

  it("sends feedback as a PUT", async () => {
    const fetch = answer(204);
    vi.stubGlobal("fetch", fetch);

    await expect(giveFeedback(5, 12, "SKIPPED")).resolves.toBeUndefined();

    const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/repos/5/issues/12/feedback");
    expect(options.method).toBe("PUT");
    expect(options.body).toBe(JSON.stringify({ value: "SKIPPED" }));
  });
});
