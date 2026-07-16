const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface PrivateMediaSource {
  src: string;
  crossOrigin: "use-credentials";
}

export function resolveMediaUrl(url: string | null): string | null {
  if (!url) {
    return null;
  }
  if (url.startsWith("/media/videos/") || url.startsWith("media/videos/")) {
    return null;
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith("//")) {
    return null;
  }
  const path = url.startsWith("/") ? url : `/${url}`;
  if (!/^\/demos\/[A-Za-z0-9_-]+\/media\/video$/.test(path)) {
    return null;
  }
  return `${API_BASE_URL}${path}`;
}

export function resolvePrivateMediaSource(
  url: string | null
): PrivateMediaSource | null {
  const src = resolveMediaUrl(url);
  return src ? { src, crossOrigin: "use-credentials" } : null;
}
