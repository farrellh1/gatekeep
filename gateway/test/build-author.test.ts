import { describe, it, expect } from "vitest";
import { buildAuthor } from "../src/normalize.js";

const now = new Date("2021-01-01T00:00:00Z");

describe("buildAuthor", () => {
  it("computes account age in whole days from the injected clock", () => {
    const author = buildAuthor(
      { login: "octocat", author_association: "MEMBER", created_at: "2020-01-01T00:00:00Z" },
      now,
    );
    expect(author.login).toBe("octocat");
    expect(author.account_age_days).toBe(366);
  });

  it.each([
    ["FIRST_TIME_CONTRIBUTOR", true],
    ["NONE", true],
    ["MEMBER", false],
    ["CONTRIBUTOR", false],
    ["OWNER", false],
  ])("treats %s as first-time=%s", (association, expected) => {
    const author = buildAuthor(
      { login: "u", author_association: association, created_at: "2020-01-01T00:00:00Z" },
      now,
    );
    expect(author.is_first_time_contributor).toBe(expected);
  });
});
