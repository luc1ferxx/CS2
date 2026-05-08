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

export function filterAndSortDemos(
  demos: DemoSummary[],
  filters: DemoLibraryFilters
): DemoSummary[] {
  const search = filters.search.trim().toLowerCase();
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
    if (!search) {
      return true;
    }
    return [demo.name, demo.original_filename, demo.map_name]
      .some((value) => value.toLowerCase().includes(search));
  });

  return filtered.sort((left, right) => compareDemos(left, right, filters.sort, filters.order));
}

export function demoLibraryFilterOptions(demos: DemoSummary[]): DemoLibraryFilterOptions {
  const statuses = new Set<DemoProcessingStatus>();
  const maps = new Set<string>();
  for (const demo of demos) {
    statuses.add(demo.status);
    if (demo.map_name && demo.map_name !== "unknown") {
      maps.add(demo.map_name);
    }
  }

  return {
    statuses: STATUS_ORDER.filter((status) => statuses.has(status)),
    maps: [...maps].sort((left, right) => left.localeCompare(right))
  };
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

function compareDemos(
  left: DemoSummary,
  right: DemoSummary,
  sort: DemoLibrarySort,
  order: DemoLibraryOrder
): number {
  const direction = order === "asc" ? 1 : -1;
  let result = 0;

  if (sort === "recent") {
    result = Date.parse(left.created_at) - Date.parse(right.created_at);
  } else if (sort === "name") {
    result = left.name.localeCompare(right.name);
  } else if (sort === "map") {
    result = left.map_name.localeCompare(right.map_name);
  } else {
    result = statusRank(left.status) - statusRank(right.status);
  }

  if (result === 0) {
    result = left.name.localeCompare(right.name);
  }
  return result * direction;
}

function statusRank(status: DemoProcessingStatus): number {
  const index = STATUS_ORDER.indexOf(status);
  return index === -1 ? STATUS_ORDER.length : index;
}
