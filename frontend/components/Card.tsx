import { ReactNode } from "react";

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel p-4 md:p-5 ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between mb-3">
          {title && <h2 className="font-semibold text-base">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
