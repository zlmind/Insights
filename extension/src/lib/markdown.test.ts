import { describe, expect, it } from "vitest";
import { buildDigest, markdownToHtml } from "./markdown";

describe("markdownToHtml", () => {
  it("renders headings, paragraphs and quotes", () => {
    const html = markdownToHtml("# 标题\n\n正文段落\n> 引用");
    expect(html).toContain("<h1>标题</h1>");
    expect(html).toContain("<p>正文段落</p>");
    expect(html).toContain("<blockquote>");
  });
});

describe("buildDigest", () => {
  it("strips markdown and truncates to 120 chars", () => {
    const md = "# " + "标".repeat(200);
    expect(buildDigest(md)).toHaveLength(120);
    expect(buildDigest(" short ")).toBe("short");
  });
});
