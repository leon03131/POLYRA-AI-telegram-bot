import type { ReactNode } from "react";

interface EmptyStateProps {
  icon: string;
  text: string;
  children?: ReactNode;
}

export function EmptyState({ icon, text, children }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">{icon}</div>
      <div>{text}</div>
      {children}
    </div>
  );
}
