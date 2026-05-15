"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

type ToastKind = "info" | "success" | "error";
type Toast = { id: number; kind: ToastKind; message: string };

const ToastCtx = createContext<{
  push: (msg: string, kind?: ToastKind) => void;
}>({ push: () => {} });

let _counter = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, kind: ToastKind = "info") => {
    const id = ++_counter;
    setToasts((t) => [...t, { id, kind, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto panel px-3 py-2 text-sm shadow-lg max-w-md
              ${t.kind === "error" ? "border-red-500/50" : t.kind === "success" ? "border-emerald-500/50" : ""}`}
          >
            <span className={`mr-2 ${t.kind === "error" ? "text-red-400" : t.kind === "success" ? "text-emerald-400" : "text-muted"}`}>
              {t.kind === "error" ? "✕" : t.kind === "success" ? "✓" : "→"}
            </span>
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}
