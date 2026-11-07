import { KeyboardEvent, useRef } from "react";

type StarRatingProps = {
  value: number;
  onChange: (stars: number) => void;
  large?: boolean;
  label?: string;
};

/** Five star radio buttons; arrow keys move the selection (roving tabindex). */
export default function StarRating({ value, onChange, large = false, label = "Bewertung" }: StarRatingProps) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function select(stars: number) {
    onChange(stars);
    refs.current[stars - 1]?.focus();
  }

  function handleKeyDown(event: KeyboardEvent, stars: number) {
    if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      event.preventDefault();
      select(Math.min(5, stars + 1));
    } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      event.preventDefault();
      select(Math.max(1, stars - 1));
    }
  }

  return (
    <div role="radiogroup" aria-label={label} className={`star-rating${large ? " star-rating--large" : ""}`}>
      {[1, 2, 3, 4, 5].map((stars) => (
        <button
          key={stars}
          ref={(el) => {
            refs.current[stars - 1] = el;
          }}
          type="button"
          role="radio"
          aria-checked={value === stars}
          aria-label={`${stars} von 5 Sternen`}
          tabIndex={value === stars || (value === 0 && stars === 1) ? 0 : -1}
          className={`star-button${stars <= value ? " is-on" : ""}`}
          onClick={() => onChange(stars)}
          onKeyDown={(e) => handleKeyDown(e, stars)}
        >
          <span aria-hidden="true">{stars <= value ? "★" : "☆"}</span>
        </button>
      ))}
    </div>
  );
}
