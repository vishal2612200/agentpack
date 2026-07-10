import { type ReactNode } from "react";
import { Badge } from "../ui/badge";
import { CardHeader } from "../ui/card";

export function InspectorPanel({
  title,
  eyebrow = "Inspector",
  badges,
  children
}: {
  title: string;
  eyebrow?: string;
  badges?: Array<{ label: string; tone?: string }>;
  children: ReactNode;
}) {
  return (
    <aside className="inspector" aria-label="Selection inspector">
      <CardHeader title={title} subtitle={eyebrow} />
      {badges?.length ? (
        <div className="inspector-badges">
          {badges.map((badge) => (
            <Badge key={`${badge.label}:${badge.tone}`} tone={badge.tone || "neutral"}>{badge.label}</Badge>
          ))}
        </div>
      ) : null}
      {children}
    </aside>
  );
}
