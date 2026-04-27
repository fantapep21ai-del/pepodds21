import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'PEPODDS21',
  description: 'AI-powered sports betting system',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
