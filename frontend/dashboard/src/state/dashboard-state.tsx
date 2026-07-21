import { createContext, useContext, useMemo, useReducer, type Dispatch, type ReactNode } from "react";
import type { PresentationMode } from "../data/schema";

export type DashboardView = "home" | "roadmap" | "health" | "activity" | "analytics" | "cockpit" | "tasks" | "threads" | "context" | "graph" | "files" | "settings" | "integrations" | "workflow" | "learning" | "raw";
export type MapMode = "city" | "network" | "semantic" | "table";
export type ResourceStatus = "idle" | "loading" | "ready" | "empty" | "stale" | "unavailable" | "forbidden" | "conflict" | "error";
export type OperationStatus = "idle" | "pending" | "success" | "conflict" | "stale" | "repository_mismatch" | "permission_denied" | "error";

export interface DashboardResourceState {
  status: ResourceStatus;
  message?: string;
  retryable?: boolean;
}

export interface DashboardState {
  view: DashboardView;
  presentationMode: PresentationMode;
  selectedEntityId: string;
  mapMode: MapMode;
  cameraRequest: number;
  resources: Record<string, DashboardResourceState>;
  handoffOperations: Record<string, OperationStatus>;
  refreshGeneration: number;
}

type DashboardAction =
  | { type: "view"; value: DashboardView }
  | { type: "presentation"; value: PresentationMode }
  | { type: "select"; value: string }
  | { type: "map_mode"; value: MapMode }
  | { type: "focus"; entityId?: string }
  | { type: "resource"; key: string; value: DashboardResourceState }
  | { type: "handoff"; name: string; value: OperationStatus }
  | { type: "refresh_generation"; value: number };

const initialState: DashboardState = {
  view: "home",
  presentationMode: typeof window !== "undefined" && window.localStorage.getItem("agentpack.dashboard.presentation_mode") === "build" ? "build" : "explain",
  selectedEntityId: "",
  mapMode: "city",
  cameraRequest: 0,
  resources: {},
  handoffOperations: {},
  refreshGeneration: 0
};

function reducer(state: DashboardState, action: DashboardAction): DashboardState {
  switch (action.type) {
    case "view": return { ...state, view: action.value };
    case "presentation":
      window.localStorage.setItem("agentpack.dashboard.presentation_mode", action.value);
      return { ...state, presentationMode: action.value };
    case "select": return { ...state, selectedEntityId: action.value };
    case "map_mode": return { ...state, mapMode: action.value };
    case "focus": return { ...state, selectedEntityId: action.entityId || state.selectedEntityId, cameraRequest: state.cameraRequest + 1 };
    case "resource": return { ...state, resources: { ...state.resources, [action.key]: action.value } };
    case "handoff": return { ...state, handoffOperations: { ...state.handoffOperations, [action.name]: action.value } };
    case "refresh_generation": return { ...state, refreshGeneration: action.value };
  }
}

const DashboardStateContext = createContext<{ state: DashboardState; dispatch: Dispatch<DashboardAction> } | null>(null);

export function DashboardStateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const value = useMemo(() => ({ state, dispatch }), [state]);
  return <DashboardStateContext.Provider value={value}>{children}</DashboardStateContext.Provider>;
}

export function useDashboardState() {
  const value = useContext(DashboardStateContext);
  if (!value) throw new Error("useDashboardState must be used inside DashboardStateProvider");
  return value;
}
