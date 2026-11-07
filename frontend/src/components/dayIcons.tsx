import type { ReactNode } from "react";

type IconProps = { size?: number };

function Icon({ name, size = 14, children }: IconProps & { name: string; children: ReactNode }) {
  return (
    <svg
      data-icon={name}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className="day-icon"
    >
      {children}
    </svg>
  );
}

/** Graduation cap. */
export function SchoolIcon(props: IconProps) {
  return (
    <Icon name="school" {...props}>
      <path d="M2 9l10-5 10 5-10 5z" />
      <path d="M6 11.5V16c0 1.4 2.7 3 6 3s6-1.6 6-3v-4.5" />
      <path d="M22 9v6" />
    </Icon>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon name="check" {...props}>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </Icon>
  );
}

export function ClockIcon(props: IconProps) {
  return (
    <Icon name="clock" {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </Icon>
  );
}

export function SunIcon(props: IconProps) {
  return (
    <Icon name="sun" {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </Icon>
  );
}

export function CalendarIcon(props: IconProps) {
  return (
    <Icon name="calendar" {...props}>
      <rect x="3.5" y="5" width="17" height="15" rx="3" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </Icon>
  );
}

export function CalendarOffIcon(props: IconProps) {
  return (
    <Icon name="calendar-off" {...props}>
      <rect x="3.5" y="5" width="17" height="15" rx="3" />
      <path d="M3.5 10h17M8 3v4M16 3v4M4 21L20 4" />
    </Icon>
  );
}

export function SmileIcon(props: IconProps) {
  return (
    <Icon name="smile" {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 14c1 1.5 2.4 2.2 4 2.2s3-.7 4-2.2" />
      <path d="M9 9.5h.01M15 9.5h.01" />
    </Icon>
  );
}

export function UsersIcon(props: IconProps) {
  return (
    <Icon name="users" {...props}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c0-3.4 2.9-6 6.5-6s6.5 2.6 6.5 6" />
      <path d="M16 4.7a3.5 3.5 0 010 6.6M18 14.4c2.2.7 3.5 2.7 3.5 5.6" />
    </Icon>
  );
}

/** Beach umbrella: holidays ("Ferien"). */
export function BeachIcon(props: IconProps) {
  return (
    <Icon name="beach" {...props}>
      <path d="M3 12a9 9 0 0118 0z" />
      <path d="M12 12v8.5M8.5 21h7" />
      <path d="M7.5 12c.3-2.6 1.9-5.6 4.5-8.5M16.5 12c-.3-2.6-1.9-5.6-4.5-8.5" />
    </Icon>
  );
}

/** Graduation cap crossed out: a free school day ("Schulfrei"). */
export function SchoolOffIcon(props: IconProps) {
  return (
    <Icon name="school-off" {...props}>
      <path d="M2 9l10-5 10 5-10 5z" />
      <path d="M6 11.5V16c0 1.4 2.7 3 6 3s6-1.6 6-3v-4.5" />
      <path d="M22 9v6" />
      <path d="M3 21L21 3" />
    </Icon>
  );
}
