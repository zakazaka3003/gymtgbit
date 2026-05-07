import { motion } from "framer-motion";

type Option<T extends string> = { value: T; label: string };

type Props<T extends string> = {
  options: Option<T>[];
  value: T;
  onChange: (v: T) => void;
  size?: "sm" | "md";
};

export function SegmentedControl<T extends string>({ options, value, onChange, size = "md" }: Props<T>) {
  const px = size === "sm" ? "px-3" : "px-4";
  const py = size === "sm" ? "py-1.5" : "py-2";
  const text = size === "sm" ? "text-xs" : "text-sm";
  return (
    <div className={`surface-2 rounded-full p-1 inline-flex ${text}`}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={`relative ${px} ${py} font-medium rounded-full transition-colors ${
              active ? "text-fg" : "text-muted"
            }`}
          >
            {active ? (
              <motion.span
                layoutId="seg-bg"
                transition={{ type: "spring", stiffness: 500, damping: 36 }}
                className="absolute inset-0 rounded-full bg-surface border border-default shadow-sm"
              />
            ) : null}
            <span className="relative z-10 whitespace-nowrap">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}
