'use client';

import { useEffect } from 'react';

interface Props {
  message: string;
  type?: 'success' | 'error' | 'info';
  onDismiss: () => void;
}

const styles = {
  success: 'bg-brand-green',
  error: 'bg-brand-red',
  info: 'bg-brand-blue',
};

const icons = {
  success: '✓',
  error: '✕',
  info: 'ℹ',
};

export default function Toast({ message, type = 'success', onDismiss }: Props) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 3500);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-5 py-3.5 rounded-2xl text-white text-sm font-medium shadow-xl ${styles[type]}`}>
      <span className="text-base">{icons[type]}</span>
      {message}
    </div>
  );
}
