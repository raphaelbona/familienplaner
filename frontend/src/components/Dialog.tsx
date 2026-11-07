import { ReactNode, useEffect, useRef } from "react";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled])";

type DialogProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
};

/** Modal dialog: focus moves in on open, Esc/backdrop/close button close it, Tab stays inside, focus is restored. */
export default function Dialog({ title, onClose, children }: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    panel?.querySelector<HTMLElement>(FOCUSABLE)?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      // Focus can be lost while the dialog is open (e.g. the focused button got disabled): pull it back in.
      if (!panel.contains(document.activeElement)) {
        const candidates = panel.querySelectorAll<HTMLElement>(FOCUSABLE);
        const target = event.shiftKey ? candidates[candidates.length - 1] : candidates[0];
        if (target) {
          event.preventDefault();
          target.focus();
        }
        return;
      }
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus?.();
    };
  }, []);

  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panelRef} className="dialog-panel" role="dialog" aria-modal="true" aria-label={title}>
        <div className="dialog-header">
          <h2>{title}</h2>
          <button type="button" className="secondary dialog-close" onClick={onClose} aria-label="Schliessen">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
