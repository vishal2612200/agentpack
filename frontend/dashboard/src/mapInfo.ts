import type { MapBuilding, MapRoad } from "./data/schema";

export interface MapHoverInfo {
  kind: "building" | "road";
  title: string;
  subtitle: string;
  tone: string;
  position: [number, number, number];
  rows: Array<{ label: string; value: string }>;
}

export interface RouteVisual {
  label: string;
  tone: string;
  width: number;
  height: number;
  y: number;
  opacity: number;
  color: string;
}

export function buildingHoverInfo(building: MapBuilding): MapHoverInfo {
  const tier = buildingTier(building);
  const source = building.confidence_source || "fallback";
  return {
    kind: "building",
    title: building.path,
    subtitle: `${labelize(tier.label)} ${building.building_type || "file"} in ${building.district_id}`,
    tone: building.risk || tier.tone,
    position: [building.x, 24 + building.confidence * 14, building.z],
    rows: [
      { label: "Confidence", value: `${Math.round(building.confidence * 100)}% (${labelize(tier.label)})` },
      { label: "Source", value: source.replace(/_/g, " ") },
      { label: "Score", value: String(Math.round(building.score || 0)) },
      { label: "Risk", value: building.risk || "unknown" },
      { label: "Context", value: building.selected ? "selected" : building.include_mode || "available" },
      { label: "Memory", value: building.memory_linked ? "linked" : "none" },
      { label: "Tests", value: building.tests?.length ? building.tests.slice(0, 2).join(", ") : "none" },
      { label: "Reason", value: building.reasons?.[0] || "No selection reason reported." }
    ]
  };
}

export function roadHoverInfo(road: MapRoad, position: [number, number, number]): MapHoverInfo {
  const visual = routeVisual(road);
  return {
    kind: "road",
    title: labelize(visual.label),
    subtitle: `${road.type.replace(/_/g, " ")} route`,
    tone: visual.tone,
    position,
    rows: [
      { label: "Strength", value: `${Math.round(routeConfidence(road) * 100)}%` },
      { label: "Class", value: labelize(visual.label) },
      { label: "Source", value: (road.relationship_source || "fallback").replace(/_/g, " ") },
      { label: "From", value: road.source.replace(/^file:/, "") },
      { label: "To", value: road.target.replace(/^file:/, "") },
      { label: "Reason", value: road.reason || "No route reason reported." }
    ]
  };
}

export function buildingTier(building: MapBuilding) {
  const backendTier = building.building_tier;
  if (backendTier) {
    return {
      label: backendTier,
      tone: backendTier === "tower" ? "good" : backendTier === "block" ? "memory" : backendTier === "service" ? "warn" : "neutral"
    };
  }
  if (building.confidence >= 0.8) return { label: "tower", tone: "good" };
  if (building.confidence >= 0.55) return { label: "block", tone: "memory" };
  if (building.confidence >= 0.3) return { label: "service", tone: "warn" };
  return { label: "pavilion", tone: "neutral" };
}

export function routeVisual(road: MapRoad): RouteVisual {
  const routeClass = road.route_class || fallbackRouteClass(road);
  const memory = road.type === "memory_influenced";
  if (routeClass === "expressway") {
    return {
      label: "expressway",
      tone: "good",
      width: 1.42,
      height: 0.12,
      y: 0.15,
      opacity: 0.56,
      color: memory ? "#246a70" : "#3f5f88"
    };
  }
  if (routeClass === "highway") {
    return {
      label: "highway",
      tone: "memory",
      width: 0.82,
      height: 0.08,
      y: 0.13,
      opacity: 0.42,
      color: memory ? "#2d777a" : "#5f7da6"
    };
  }
  if (routeClass === "county") {
    return {
      label: "county road",
      tone: "neutral",
      width: 0.44,
      height: 0.06,
      y: 0.11,
      opacity: 0.3,
      color: memory ? "#2f7d81" : "#52657f"
    };
  }
  return {
    label: "local street",
    tone: "neutral",
    width: 0.22,
    height: 0.045,
    y: 0.1,
    opacity: 0.2,
    color: memory ? "#2f7075" : "#3d4c62"
  };
}

export function routeConfidence(road: MapRoad) {
  if (typeof road.relationship_strength === "number" && road.relationship_strength > 0) {
    return Math.min(1, Math.max(0.05, road.relationship_strength));
  }
  if (typeof road.confidence === "number" && road.confidence > 0) return Math.min(1, Math.max(0.05, road.confidence));
  if (road.type === "selected_because") return 0.86;
  if (road.type === "tested_by") return 0.64;
  if (road.type === "memory_influenced") return 0.58;
  return 0.34;
}

export function labelize(value: string) {
  return value.replace(/[_-]/g, " ");
}

function fallbackRouteClass(road: MapRoad) {
  const confidence = routeConfidence(road);
  if (confidence >= 0.78) return "expressway";
  if (confidence >= 0.5) return "highway";
  if (confidence >= 0.26) return "county";
  return "local";
}
