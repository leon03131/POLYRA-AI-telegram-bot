import type { ButtonHTMLAttributes, ReactNode } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger";
  size?: "normal" | "small";
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "normal",
  loading = false,
  disabled,
  children,
  className,
  type,
  ...rest
}: ButtonProps) {
  const cls = ["btn", `btn-${variant}`, size === "small" ? "btn-small" : "", className ?? ""]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type ?? "button"} className={cls} disabled={disabled || loading} {...rest}>
      {loading ? "…" : children}
    </button>
  );
}
