'use client';

import { useState } from 'react';
import Cookies from 'js-cookie';
import { api } from '@/lib/api';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await api.login(email, password);
      if (data.access_token) {
        Cookies.set('token', data.access_token, { expires: 1 });
        window.location.href = '/dashboard';
      } else {
        setError('Credenziali non valide');
      }
    } catch {
      setError('Accesso non riuscito. Riprova.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-bg">
      <div className="bg-white rounded-card shadow-card p-10 w-full max-w-sm">
        <h1 className="text-2xl font-semibold text-brand-text mb-1">PEPODDS21</h1>
        <p className="text-brand-muted text-sm mb-8">Accedi al tuo account</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-brand-text mb-1">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              className="w-full border border-brand-border rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue"
              placeholder="giuseppe@pepodds21.com"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-brand-text mb-1">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full border border-brand-border rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-blue"
            />
          </div>

          {error && <p className="text-brand-red text-sm">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-brand-blue text-white rounded-xl py-3 text-sm font-medium hover:opacity-90 disabled:opacity-50 transition"
          >
            {loading ? 'Accesso in corso…' : 'Accedi'}
          </button>
        </form>
      </div>
    </div>
  );
}
