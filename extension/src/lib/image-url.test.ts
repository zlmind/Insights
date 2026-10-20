import { describe, expect, it } from "vitest";
import { sameServerImageUrl } from "./image-url";

describe("sameServerImageUrl", () => {
  it("rewrites a loopback image host to the paired server host", () => {
    expect(sameServerImageUrl(
      "http://127.0.0.1:5001/static/uploads/a.png",
      "http://localhost:5001"))
      .toBe("http://localhost:5001/static/uploads/a.png");
  });
  it("rewrites the other loopback direction too", () => {
    expect(sameServerImageUrl(
      "http://localhost:5001/static/uploads/a.png",
      "http://127.0.0.1:5001"))
      .toBe("http://127.0.0.1:5001/static/uploads/a.png");
  });
  it("leaves a non-loopback image host untouched", () => {
    expect(sameServerImageUrl(
      "https://img.example.com/a.png",
      "http://localhost:5001"))
      .toBe("https://img.example.com/a.png");
  });
  it("leaves a loopback image with an empty server URL untouched", () => {
    expect(sameServerImageUrl("http://127.0.0.1:5001/a.png", ""))
      .toBe("http://127.0.0.1:5001/a.png");
  });
});
