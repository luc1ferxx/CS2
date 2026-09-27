// Build-time settings for the public privacy page (NEXT_PUBLIC_*, baked into the bundle).

export const DATA_REGION_FALLBACK = "海外 VPS，具体地区由站长部署时选定";

export type PrivacyContact =
  | { kind: "link"; href: string; label: string; external: boolean }
  | { kind: "text"; label: string };

const EMAIL = /^[^\s@/:]+@[^\s@/]+\.[^\s@/]+$/;
const WEB_URL = /^https?:\/\/\S+$/i;

// An email becomes a mailto link, an http(s) URL a link, anything else plain text.
export function privacyContact(value: string | null | undefined): PrivacyContact | null {
  const trimmed = value?.trim() ?? "";
  if (!trimmed) return null;
  if (/^mailto:/i.test(trimmed)) {
    const address = trimmed.slice("mailto:".length).split("?")[0];
    return EMAIL.test(address)
      ? { kind: "link", href: trimmed, label: address, external: false }
      : { kind: "text", label: trimmed };
  }
  if (EMAIL.test(trimmed)) {
    return { kind: "link", href: `mailto:${trimmed}`, label: trimmed, external: false };
  }
  if (WEB_URL.test(trimmed)) {
    return { kind: "link", href: trimmed, label: trimmed, external: true };
  }
  return { kind: "text", label: trimmed };
}

export function dataRegion(value: string | null | undefined): string {
  return value?.trim() || DATA_REGION_FALLBACK;
}
