import type { ReactNode } from "react";

interface SectionProps {
  title?: string;
  footer?: string;
  children: ReactNode;
}

/** Секция в стиле Telegram Settings: заголовок сверху, карточка, подпись снизу. */
export function Section({ title, footer, children }: SectionProps) {
  return (
    <div className="section-block">
      {title && <div className="section-title">{title}</div>}
      <div className="section">{children}</div>
      {footer && <div className="section-footer">{footer}</div>}
    </div>
  );
}
