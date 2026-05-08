import { motion } from "framer-motion";
import type { ReactNode } from "react";

type TileProps = {
  label: string;
  value: string;
  delta?: string | null;
  deltaTone?: "good" | "bad" | "neutral";
  hint?: string;
  icon?: ReactNode;
  className?: string;
};

const toneToClass = {
  good: "text-accent",
  bad: "text-danger",
  neutral: "text-muted",
};

export function Tile({ label, value, delta, deltaTone = "neutral", hint, icon, className }: TileProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 240, damping: 24 }}
      className={`surface rounded-3xl p-4 flex flex-col gap-2 ${className ?? ""}`}
    >
      <div className="flex items-center justify-between">
        <span className="text-muted text-xs uppercase tracking-wider">{label}</span>
        {icon ? <span className="text-muted">{icon}</span> : null}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold tracking-tight font-mono">{value}</span>
        {delta ? (
          <span className={`text-xs font-medium ${toneToClass[deltaTone]}`}>{delta}</span>
        ) : null}
      </div>
      {hint ? <span className="text-muted text-xs">{hint}</span> : null}
    </motion.div>
  );
}
