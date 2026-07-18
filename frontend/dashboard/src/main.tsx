import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { DashboardStateProvider } from "./state/dashboard-state";
import "@fontsource-variable/manrope";
import "@xyflow/react/dist/style.css";
import "./styles/tokens.css";
import "./styles/app.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <DashboardStateProvider><App /></DashboardStateProvider>
  </React.StrictMode>
);
