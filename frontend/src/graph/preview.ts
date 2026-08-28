export function humanizeNode(name: string): string {
  if (!name) return "node";
  return name.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export function previewSummary(preview: unknown): string {
  if (preview === null || preview === undefined) return "";
  if (typeof preview === "string") return preview.slice(0, 120);
  if (Array.isArray(preview)) return `${preview.length} item(s)`;
  if (typeof preview === "object") {
    const keys = Object.keys(preview as Record<string, unknown>);
    return keys.slice(0, 4).join(", ") + (keys.length > 4 ? "…" : "");
  }
  return String(preview);
}

export function hasPreview(preview: unknown): boolean {
  if (preview === null || preview === undefined) return false;
  if (typeof preview === "object") return Object.keys(preview as object).length > 0;
  if (typeof preview === "string") return preview.length > 0;
  return true;
}
