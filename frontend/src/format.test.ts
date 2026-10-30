/**
 * file: src/format.test.ts
 * description: Checks how numbers, dates and durations are written.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ago, clock, day, duration, number } from "./format";

afterEach(() => {
  vi.useRealTimers();
});

describe("number", () => {
  it("groups thousands with commas", () => {
    expect(number(0)).toBe("0");
    expect(number(5000)).toBe("5,000");
    expect(number(1000000)).toBe("1,000,000");
  });
});

// Built from local time so the test does not depend on the machine's time zone.
describe("day and clock", () => {
  const moment = new Date(2026, 9, 30, 9, 5).toISOString();

  it("writes the date with its year", () => {
    expect(day(moment)).toBe("Oct 30, 2026");
  });

  it("writes a twenty four hour clock", () => {
    expect(clock(moment)).toBe("09:05");
  });
});

describe("duration", () => {
  it("writes minutes and seconds", () => {
    expect(duration(867)).toBe("14m 27s");
    expect(duration(5)).toBe("0m 05s");
  });

  it("rounds to whole seconds before splitting", () => {
    expect(duration(59.6)).toBe("1m 00s");
  });

  it("drops seconds once a scan takes hours", () => {
    expect(duration(3900)).toBe("1h 05m");
  });
});

describe("ago", () => {
  it("counts days and then months", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-30T12:00:00Z"));

    expect(ago("2026-10-30T11:00:00Z")).toBe("today");
    expect(ago("2026-10-27T12:00:00Z")).toBe("3d ago");
    expect(ago("2026-09-01T12:00:00Z")).toBe("59d ago");
    expect(ago("2026-04-30T12:00:00Z")).toBe("6mo ago");
    expect(ago("2022-10-30T12:00:00Z")).toBe("4y ago");
  });

  it("treats a date ahead of the clock as today", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-30T12:00:00Z"));

    expect(ago("2026-10-30T13:00:00Z")).toBe("today");
  });
});
