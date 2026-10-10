import { marked } from "marked";

export function markdownToHtml(md: string): string {
  return marked.parse(md, { async: false });
}

// The digest box on the mp draft takes at most 120 chars; markdown markers
// would read as noise there, so strip the common ones before truncating.
export function buildDigest(md: string, max = 120): string {
  const text = md
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^>\s?/gm, "")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text.slice(0, max);
}
