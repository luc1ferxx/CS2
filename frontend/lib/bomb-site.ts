/** Numeric CS2 entity indices do not identify A/B across maps or demos. */
export function normalizeBombSite(value: unknown): "A" | "B" | undefined {
  if (typeof value !== "string") return undefined;
  const site = value.trim().toUpperCase();
  return site === "A" || site === "B" ? site : undefined;
}

export function bombPlantEvidenceLabel(label: unknown, site: unknown): string {
  // Older coaching rows may only retain the parser's label.
  const explicitLabelSite = typeof label === "string" ? /^Bomb planted\s+([AB])$/i.exec(label.trim())?.[1] : undefined;
  const knownSite = normalizeBombSite(site) ?? (site == null ? normalizeBombSite(explicitLabelSite) : undefined);
  return knownSite ? `炸弹已安放（${knownSite} 点）` : "炸弹已安放（包点未知）";
}
