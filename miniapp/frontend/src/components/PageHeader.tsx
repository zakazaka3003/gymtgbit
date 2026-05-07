type Props = {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
};

export function PageHeader({ title, subtitle, right }: Props) {
  return (
    <div className="flex items-end justify-between gap-3 px-5 pt-6 pb-4 safe-top">
      <div className="min-w-0">
        <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? (
          <p className="text-muted text-sm mt-1 truncate">{subtitle}</p>
        ) : null}
      </div>
      {right ? <div className="shrink-0">{right}</div> : null}
    </div>
  );
}
