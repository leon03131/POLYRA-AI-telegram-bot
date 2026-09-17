import type { ReactNode } from "react";

interface ListProps {
  children: ReactNode;
}

export function List({ children }: ListProps) {
  return <div>{children}</div>;
}

interface ListRowProps {
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  chevron?: boolean;
  onClick?: () => void;
}

export function ListRow({ title, subtitle, right, chevron, onClick }: ListRowProps) {
  const className = "list-row" + (onClick ? " clickable" : "");
  const inner = (
    <>
      <div className="list-row-main">
        <div className="list-row-title">{title}</div>
        {subtitle && <div className="list-row-subtitle">{subtitle}</div>}
      </div>
      {right && <div className="list-row-right">{right}</div>}
      {chevron && <div className="chevron">›</div>}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={className} onClick={onClick}>
        {inner}
      </button>
    );
  }
  return <div className={className}>{inner}</div>;
}
