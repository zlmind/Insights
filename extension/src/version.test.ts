import { describe, expect, it } from "vitest";
import { VERSION } from "./version";

describe("version", () => {
  it("matches the manifest version", () => {
    expect(VERSION).toBe("0.1.0");
  });
});
