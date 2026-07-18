import { type ReactNode } from "react";
import { TerminalSquare } from "lucide-react";
import { Button } from "./button";

export function CommandRow({
  title,
  command,
  detail,
  icon,
  onCopy
}: {
  title: string;
  command?: string;
  detail?: ReactNode;
  icon?: ReactNode;
  onCopy?: (value: string, label?: string) => void;
}) {
  return (
    <div className="ui-command-row">
      {icon || <TerminalSquare size={16} aria-hidden="true" />}
      <span>
        <strong>{title}</strong>
        {command ? <code>{command}</code> : detail}
      </span>
      {command && onCopy ? (
        <Button variant="icon" aria-label={`Copy ${title}`} onClick={() => onCopy(command, title)}>
          <TerminalSquare size={15} aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  );
}
