import { clsx } from 'clsx';

interface Props {
  label: string;
  value: string | number;
  sub?: string;
  color?: 'green' | 'red' | 'blue' | 'default';
}

const colorMap = {
  green: 'text-brand-green',
  red: 'text-brand-red',
  blue: 'text-brand-blue',
  default: 'text-brand-text',
};

export default function StatCard({ label, value, sub, color = 'default' }: Props) {
  return (
    <div className="bg-white rounded-card shadow-card p-5">
      <div className="text-xs text-brand-muted font-medium uppercase tracking-wide mb-2">{label}</div>
      <div className={clsx('text-2xl font-semibold tabular-nums', colorMap[color])}>{value}</div>
      {sub && <div className="text-xs text-brand-muted mt-1">{sub}</div>}
    </div>
  );
}
