import { type HTMLAttributes, type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";
import { cn } from "../../lib/cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={cn("ui-card", className)} {...props} />;
}

export function CardHeader({
  title,
  subtitle,
  icon: Icon,
  action,
  className
}: {
  title: string;
  subtitle?: string;
  icon?: LucideIcon;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("ui-card-header", className)}>
      <div className="ui-card-title-row">
        {Icon ? <Icon size={17} aria-hidden="true" /> : null}
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </div>
      {action ? <div className="ui-card-action">{action}</div> : null}
    </div>
  );
}
