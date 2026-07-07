import { type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";
import { Button } from "../ui/button";

export interface CockpitNavItem<T extends string> {
  id: T;
  label: string;
  icon: LucideIcon;
}

export function AppShell<T extends string>({
  navItems,
  activeView,
  onViewChange,
  topbar,
  inspector,
  children
}: {
  navItems: Array<CockpitNavItem<T>>;
  activeView: T;
  onViewChange: (view: T) => void;
  topbar: ReactNode;
  inspector: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Dashboard navigation">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">AP</div>
          <div>
            <strong>AgentPack</strong>
            <span>Context cockpit</span>
          </div>
        </div>
        <nav className="nav-list">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <Button
                key={item.id}
                variant="ghost"
                className={activeView === item.id ? "nav-item active" : "nav-item"}
                onClick={() => onViewChange(item.id)}
              >
                <Icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
              </Button>
            );
          })}
        </nav>
      </aside>
      <main className="workspace">
        {topbar}
        <section className="main-panel" aria-label={`${activeView} view`}>
          {children}
        </section>
      </main>
      {inspector}
    </div>
  );
}
