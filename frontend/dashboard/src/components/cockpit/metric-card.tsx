import { Card } from "../ui/card";
import { cn } from "../../lib/cn";

export function MetricCard({
  label,
  value,
  tone = "neutral",
  asButton = false,
  onClick
}: {
  label: string;
  value: string | number;
  tone?: string;
  asButton?: boolean;
  onClick?: () => void;
}) {
  const content = (
    <Card className={cn("cockpit-metric", `cockpit-metric-${tone}`)}>
      <span>{label}</span>
      <strong>{value}</strong>
    </Card>
  );

  if (!asButton) return content;
  return (
    <button type="button" className="metric-button" onClick={onClick}>
      {content}
    </button>
  );
}
