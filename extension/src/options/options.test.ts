import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
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
    expect(statusText("unauthorized")).toBe("密钥被拒绝（401）——回发布队列页重新配对");
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

describe("options.html page contract", () => {
  const html = readFileSync(
    new URL("./options.html", import.meta.url), "utf-8");

  it("actually loads the options script — a page without it renders dead buttons", () => {
    expect(html).toMatch(/<script\s+src="options\.js"><\/script>/);
  });
  it("wires every button the page renders", () => {
    for (const id of ["pair"]) {
      expect(html).toContain(`id="${id}"`);
    }
  });
  it("the manual token fallback stays gone — pairing is the only path", () => {
    expect(html).not.toContain('id="save"');
    expect(html).not.toContain("agent-token");
  });
});

describe("toolbar popup contract", () => {
  const root = new URL("../", import.meta.url);
  const manifest = JSON.parse(
    readFileSync(new URL("manifest.json", root), "utf-8"));

  it("opens the pairing view straight from the toolbar icon", () => {
    expect(manifest.action?.default_popup).toBe("popup/popup.html");
  });
  it("popup.html loads its script and carries the pairing controls", () => {
    const html = readFileSync(new URL("popup/popup.html", root), "utf-8");
    expect(html).toMatch(/<script\s+src="popup\.js"><\/script>/);
    for (const id of ["server-url", "pair-code", "pair", "status"]) {
      expect(html).toContain(`id="${id}"`);
    }
  });
});
