import type { DemoProcessingStatus, DemoSummary } from "@/types/demo";

export type DemoLibrarySort = "recent" | "name" | "map" | "status";
export type DemoLibraryOrder = "asc" | "desc";
export type DemoLibraryStatusFilter = DemoProcessingStatus | "all";

export interface DemoLibraryFilters {
  search: string;
  status: DemoLibraryStatusFilter;
  map: string;
  sort: DemoLibrarySort;
  order: DemoLibraryOrder;
  includeArchived: boolean;
}

export interface DemoLibraryFilterOptions {
  statuses: DemoProcessingStatus[];
  maps: string[];
}

const STATUS_ORDER: DemoProcessingStatus[] = ["queued", "parsing", "analyzing", "completed", "failed"];
const ACTIVE_DEMO_STATUSES = new Set<DemoProcessingStatus>(["queued", "parsing", "analyzing"]);
const ACTIVE_RENDER_STATUSES = new Set(["queued", "processing", "rendering"]);
const STATUS_LABELS: Record<DemoProcessingStatus, string> = {
  queued: "uploaded",
  parsing: "parsing",
  analyzing: "analyzing",
  completed: "ready",
  failed: "failed"
};

export function filterAndSortDemos(
  demos: DemoSummary[],
  filters: DemoLibraryFilters
): DemoSummary[] {
  const search = filters.search.trim().toLowerCase();
  const searchTokens = search.split(/\s+/).filter(Boolean);
  const filtered = demos.filter((demo) => {
    if (!filters.includeArchived && demo.archived) {
      return false;
    }
    if (filters.status !== "all" && demo.status !== filters.status) {
      return false;
    }
    if (filters.map !== "all" && demo.map_name !== filters.map) {
      return false;
    }
    if (searchTokens.length === 0) {
      return true;
    }
    const searchableText = searchableDemoFields(demo).join(" ").toLowerCase();
    return searchTokens.every((token) => searchableText.includes(token));
  });

  return filtered.sort((left, right) => compareDemos(left, right, filters.sort, filters.order));
}

export function demoLibraryFilterOptions(demos: DemoSummary[]): DemoLibraryFilterOptions {
  const maps = new Set<string>();
  for (const demo of demos) {
    if (demo.map_name && demo.map_name !== "unknown") {
      maps.add(demo.map_name);
    }
  }

  return {
    statuses: [...STATUS_ORDER],
    maps: [...maps].sort((left, right) => left.localeCompare(right))
  };
}

export function demoStatusLabel(status: DemoProcessingStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function isRenderActiveStatus(status: string | null | undefined): boolean {
  return typeof status === "string" && ACTIVE_RENDER_STATUSES.has(status);
}

export function isDemoLibraryActive(demo: DemoSummary): boolean {
  return (
    ACTIVE_DEMO_STATUSES.has(demo.status) ||
    isRenderActiveStatus(demo.latest_render_status) ||
    isRenderActiveStatus(demo.video_status)
  );
}

export function countActiveLibraryDemos(demos: DemoSummary[]): number {
  return demos.filter(isDemoLibraryActive).length;
}

export function renderStatusLabel(demo: DemoSummary): string {
  if (demo.latest_render_status) {
    return `render ${demo.latest_render_status}`;
  }
  if (demo.video_source === "manual_upload" && demo.video_status) {
    return `manual ${demo.video_status}`;
  }
  if (demo.video_source === "rendered" && demo.video_status) {
    return `render ${demo.video_status}`;
  }
  if (demo.video_status && demo.video_status !== "pending") {
    return `${demo.video_source ?? "video"} ${demo.video_status}`;
  }
  return "not requested";
}

function searchableDemoFields(demo: DemoSummary): string[] {
  return [
    demo.id,
    demo.name,
    demo.original_filename,
    demo.map_name,
    demo.status,
    demoStatusLabel(demo.status),
    renderStatusLabel(demo),
    demo.created_at,
    demo.updated_at,
    `${demo.round_count} rounds`,
    `${demo.coaching_event_count} coaching`
  ];
}

function compareDemos(
  left: DemoSummary,
  right: DemoSummary,
  sort: DemoLibrarySort,
  order: DemoLibraryOrder
): number {
  let result = 0;

  if (sort === "recent") {
    result = compareTimestamps(left.created_at, right.created_at, order);
  } else if (sort === "name") {
    result = compareText(left.name, right.name, order);
  } else if (sort === "map") {
    result = compareText(left.map_name, right.map_name, order);
  } else {
    result = compareNumbers(statusRank(left.status), statusRank(right.status), order);
  }

  return result || compareDemoTiebreakers(left, right);
}

function compareDemoTiebreakers(left: DemoSummary, right: DemoSummary): number {
  return (
    compareText(left.name, right.name, "asc") ||
    compareText(left.original_filename, right.original_filename, "asc") ||
    compareText(left.map_name, right.map_name, "asc") ||
    compareNumbers(statusRank(left.status), statusRank(right.status), "asc") ||
    compareTimestamps(left.updated_at, right.updated_at, "desc") ||
    compareTimestamps(left.created_at, right.created_at, "desc") ||
    compareText(left.id, right.id, "asc")
  );
}

function compareText(left: string | null | undefined, right: string | null | undefined, order: DemoLibraryOrder): number {
  const direction = order === "asc" ? 1 : -1;
  return String(left ?? "").localeCompare(String(right ?? ""), undefined, { sensitivity: "base" }) * direction;
}

function compareNumbers(left: number, right: number, order: DemoLibraryOrder): number {
  const direction = order === "asc" ? 1 : -1;
  return (left - right) * direction;
}

function compareTimestamps(left: string | null | undefined, right: string | null | undefined, order: DemoLibraryOrder): number {
  const leftTimestamp = safeTimestamp(left);
  const rightTimestamp = safeTimestamp(right);

  if (leftTimestamp === null && rightTimestamp === null) {
    return 0;
  }
  if (leftTimestamp === null) {
    return 1;
  }
  if (rightTimestamp === null) {
    return -1;
  }
  return compareNumbers(leftTimestamp, rightTimestamp, order);
}

function safeTimestamp(value: string | null | undefined): number | null {
  const timestamp = Date.parse(value ?? "");
  return Number.isFinite(timestamp) ? timestamp : null;
}

function statusRank(status: DemoProcessingStatus): number {
  const index = STATUS_ORDER.indexOf(status);
  return index === -1 ? STATUS_ORDER.length : index;
}
