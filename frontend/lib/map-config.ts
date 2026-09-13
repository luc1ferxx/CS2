import type { ReplayMapMetadata } from "@/types/replay";

export type TacticalMapConfidence = "calibrated" | "approximate" | "fallback";
export type TacticalMapLevel = "upper" | "lower";
export type TacticalMapLevelMode = "auto" | TacticalMapLevel;
export type TacticalMapTransform =
  | {
      type: "overview";
      posX: number;
      posY: number;
      scale: number;
      imageSize: number;
    }
  | {
      type: "bounds";
      minX: number;
      maxX: number;
      minY: number;
      maxY: number;
    }
  | {
      type: "dynamicBounds";
    };

export interface TacticalMapConfig {
  mapName: string;
  displayName: string;
  radarImagePath: string;
  secondaryRadarImagePath?: string;
  lowerLevelMaxZ?: number;
  calibrationSource?: string;
  calibrated: boolean;
  confidence: Exclude<TacticalMapConfidence, "fallback">;
  attribution: string;
  source: string;
  transform: TacticalMapTransform;
}

export interface TacticalMapPresentation {
  mapName: string;
  displayName: string;
  radarImagePath: string | null;
  secondaryRadarImagePath?: string | null;
  lowerLevelMaxZ?: number;
  calibrationSource?: string;
  calibrated: boolean;
  confidence: TacticalMapConfidence;
  attribution: string;
  source: string | null;
  transform: TacticalMapTransform;
}

export interface RadarPoint {
  x: number;
  y: number;
  confidence: TacticalMapConfidence;
}

export const SUPPORTED_TACTICAL_MAP_NAMES = [
  "de_dust2",
  "de_mirage",
  "de_inferno",
  "de_ancient",
  "de_nuke",
  "de_anubis"
] as const;

const ASSET_ATTRIBUTION =
  "CS2 radar asset from rabume/cs2-dma-radar; source README attributes map assets to CS2 React HUD by Lexogrine and Boltobserv by boltgolt.";

const TACTICAL_MAP_CONFIGS: Record<(typeof SUPPORTED_TACTICAL_MAP_NAMES)[number], TacticalMapConfig> = {
  de_dust2: {
    mapName: "de_dust2",
    displayName: "Dust II",
    radarImagePath: "/maps/de_dust2_radar.png",
    calibrated: true,
    confidence: "calibrated",
    attribution: ASSET_ATTRIBUTION,
    source: "https://github.com/rabume/cs2-dma-radar",
    transform: {
      type: "overview",
      posX: -2476,
      posY: 3239,
      scale: 4.4,
      imageSize: 1024
    }
  },
  de_mirage: {
    mapName: "de_mirage",
    displayName: "Mirage",
    radarImagePath: "/maps/de_mirage_radar.png",
    calibrated: false,
    confidence: "approximate",
    attribution: ASSET_ATTRIBUTION,
    source: "https://github.com/rabume/cs2-dma-radar",
    transform: {
      type: "bounds",
      minX: -3400,
      maxX: 1720,
      minY: -3220,
      maxY: 1880
    }
  },
  de_inferno: {
    mapName: "de_inferno",
    displayName: "Inferno",
    radarImagePath: "/maps/de_inferno_radar.png",
    calibrated: false,
    confidence: "approximate",
    attribution: ASSET_ATTRIBUTION,
    source: "https://github.com/rabume/cs2-dma-radar",
    transform: {
      type: "bounds",
      minX: -1120,
      maxX: 3800,
      minY: -2060,
      maxY: 2920
    }
  },
  de_ancient: {
    mapName: "de_ancient",
    displayName: "Ancient",
    radarImagePath: "/maps/de_ancient_radar.png",
    calibrated: false,
    confidence: "approximate",
    attribution: ASSET_ATTRIBUTION,
    source: "https://github.com/rabume/cs2-dma-radar",
    transform: {
      type: "bounds",
      minX: -2940,
      maxX: 2170,
      minY: -2890,
      maxY: 2130
    }
  },
  de_nuke: {
    mapName: "de_nuke",
    displayName: "Nuke",
    radarImagePath: "/maps/de_nuke_radar.png",
    secondaryRadarImagePath: "/maps/de_nuke_lower_radar.png",
    lowerLevelMaxZ: -495,
    calibrationSource: "https://github.com/akiver/cs-demo-manager/blob/main/src/node/database/maps/default-maps.ts",
    calibrated: true,
    confidence: "calibrated",
    attribution: ASSET_ATTRIBUTION,
    source: "https://github.com/rabume/cs2-dma-radar",
    transform: {
      type: "overview",
      posX: -3453,
      posY: 2887,
      scale: 7,
      imageSize: 1024
    }
  },
  de_anubis: {
    mapName: "de_anubis",
    displayName: "Anubis",
    radarImagePath: "/maps/de_anubis_radar.png",
    calibrated: false,
    confidence: "approximate",
    attribution: ASSET_ATTRIBUTION,
    source: "https://github.com/rabume/cs2-dma-radar",
    transform: {
      type: "bounds",
      minX: -3300,
      maxX: 1560,
      minY: -3150,
      maxY: 1850
    }
  }
};

export function getTacticalMapConfig(mapName: string | null | undefined): TacticalMapConfig | null {
  if (!mapName) {
    return null;
  }
  return TACTICAL_MAP_CONFIGS[mapName as keyof typeof TACTICAL_MAP_CONFIGS] ?? null;
}

export function getTacticalMapPresentation(replay: {
  mapName?: string | null;
  mapMetadata?: ReplayMapMetadata;
}): TacticalMapPresentation {
  const config = getTacticalMapConfig(replay.mapName);
  if (config) {
    return config;
  }

  const metadata = replay.mapMetadata;
  if (metadata?.radarImagePath) {
    return {
      mapName: metadata.mapName,
      displayName: metadata.displayName,
      radarImagePath: metadata.radarImagePath,
      secondaryRadarImagePath: metadata.secondaryRadarImagePath ?? null,
      lowerLevelMaxZ: metadata.lowerLevelMaxZ,
      calibrationSource: metadata.calibrationSource,
      calibrated: metadata.calibrated,
      confidence: metadata.confidence,
      attribution: metadata.attribution,
      source: metadata.source,
      transform: metadata.transform
    };
  }

  const mapName = replay.mapName || metadata?.mapName || "unknown";
  return {
    mapName,
    displayName: metadata?.displayName || mapName,
    radarImagePath: null,
    secondaryRadarImagePath: null,
    calibrated: false,
    confidence: "fallback",
    attribution: metadata?.attribution || "Fallback grid generated by the local replay viewer.",
    source: metadata?.source || null,
    transform: { type: "dynamicBounds" }
  };
}

export function worldToRadarPercent(
  mapName: string | null | undefined,
  x: number,
  y: number
): RadarPoint | null {
  const config = getTacticalMapConfig(mapName);
  if (!config) {
    return null;
  }
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }

  const transform = config.transform;
  let radarX: number;
  let radarY: number;

  if (transform.type === "overview") {
    const radarSize = transform.scale * transform.imageSize;
    radarX = ((x - transform.posX) / radarSize) * 100;
    radarY = ((transform.posY - y) / radarSize) * 100;
  } else if (transform.type === "bounds") {
    radarX = scaleToPercent(x, transform.minX, transform.maxX);
    radarY = 100 - scaleToPercent(y, transform.minY, transform.maxY);
  } else {
    return null;
  }

  return {
    x: roundPercent(clamp(radarX)),
    y: roundPercent(clamp(radarY)),
    confidence: config.confidence
  };
}

export function sanitizeRadarPercent(value: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return roundPercent(clamp(value));
}

export function getTacticalMapLevel(
  config: Pick<TacticalMapPresentation, "secondaryRadarImagePath" | "lowerLevelMaxZ">,
  z: number | null | undefined
): TacticalMapLevel | null {
  if (!config.secondaryRadarImagePath || !Number.isFinite(config.lowerLevelMaxZ) ||
      typeof z !== "number" || !Number.isFinite(z)) {
    return null;
  }
  return z <= config.lowerLevelMaxZ! ? "lower" : "upper";
}

export function resolveTacticalMapLevel(
  config: TacticalMapPresentation,
  mode: TacticalMapLevelMode,
  selectedZ: number | null | undefined
): { level: TacticalMapLevel; radarImagePath: string | null; followingPlayer: boolean } {
  const playerLevel = getTacticalMapLevel(config, selectedZ);
  const level = mode === "auto" ? playerLevel ?? "upper" : mode;
  return {
    level,
    radarImagePath: level === "lower" && config.secondaryRadarImagePath
      ? config.secondaryRadarImagePath : config.radarImagePath,
    followingPlayer: mode === "auto" && playerLevel !== null
  };
}

export function sanitizeRadarPoint<T extends { x?: number | null; y?: number | null }>(
  point: T
): (T & { x: number; y: number }) | null {
  const x = sanitizeRadarPercent(point.x);
  const y = sanitizeRadarPercent(point.y);
  if (x === null || y === null) {
    return null;
  }
  return { ...point, x, y };
}

function scaleToPercent(value: number, low: number, high: number) {
  if (high === low) {
    return 50;
  }
  return ((value - low) / (high - low)) * 100;
}

function clamp(value: number) {
  return Math.max(0, Math.min(100, value));
}

function roundPercent(value: number) {
  return Math.round(value * 100) / 100;
}
