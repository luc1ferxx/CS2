const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function resolveMediaUrl(url: string | null): string | null {
  if (!url) {
    return null;
  }
  if (/^(https?:|blob:|data:)/.test(url)) {
    return url;
  }
  const path = url.startsWith("/") ? url : `/${url}`;
  return `${API_BASE_URL}${path}`;
}
