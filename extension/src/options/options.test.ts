import { describe, expect, it } from "vitest";
import { normalizePairCode, originPattern, statusText } from "./options";

describe("originPattern", () => {
  it("derives the match pattern from the server URL", () => {
    expect(originPattern("https://insights.example/")).toBe("https://insights.example/*");
    expect(originPattern("http://localhost:8000")).toBe("http://localhost:8000/*");
  });
  it("returns null for a non-http URL", () => {
    expect(originPattern("ftp://x")).toBeNull();
  });
});

describe("statusText", () => {
  it("renders saved, unauthorized and offline states", () => {
    expect(statusText("ok")).toBe("已连接");
    expect(statusText("unauthorized")).toBe("密钥被拒绝（401），请核对 AGENT_TOKEN");
    expect(statusText("unreachable")).toBe("服务器不可达，请检查地址");
  });
});

describe("normalizePairCode", () => {
  it("trims, strips separators and uppercases what a human types", () => {
    expect(normalizePairCode(" ab23-cd45 ")).toBe("AB23CD45");
    expect(normalizePairCode("AB23CD45")).toBe("AB23CD45");
  });
  it("flags anything that is not eight alphanumeric characters", () => {
    expect(normalizePairCode("AB23")).toBeNull();
    expect(normalizePairCode("AB23CD4!")).toBeNull();
  });
});
