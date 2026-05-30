import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

import AuthPage from '../components/auth/AuthPage';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { useAuth } from '../contexts/AuthContext';

export default function LoginPage() {
  const router = useRouter();
  const { user, login } = useAuth();
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (user) {
      router.replace(typeof router.query.next === 'string' ? router.query.next : '/app');
    }
  }, [router, user]);

  return (
    <AuthPage
      title="Sign in to PosterPro"
      subtitle="Manage listings, inventory, and marketplace work from one workspace."
    >
      <form
        className="pp-auth-form"
        onSubmit={async (event) => {
          event.preventDefault();
          setSubmitting(true);
          setError('');
          const form = new FormData(event.currentTarget);
          try {
            await login({
              email: String(form.get('email') || ''),
              password: String(form.get('password') || ''),
            });
            router.replace(typeof router.query.next === 'string' ? router.query.next : '/app');
          } catch (submissionError) {
            setError(submissionError.message);
          } finally {
            setSubmitting(false);
          }
        }}
      >
        <div className="pp-field">
          <label htmlFor="login-email">Email</label>
          <Input id="login-email" name="email" type="email" required placeholder="you@example.com" />
        </div>

        <div className="pp-field">
          <label htmlFor="login-password">Password</label>
          <Input id="login-password" name="password" type="password" required placeholder="Enter your password" />
        </div>

        {error ? <p className="text-sm font-medium text-rose-600">{error}</p> : null}

        <Button type="submit" className="mt-2 w-full" size="lg" disabled={submitting}>
          {submitting ? 'Signing in...' : 'Sign in'}
        </Button>
      </form>

      <div className="pp-auth-links">
        <p>
          <Link href="/forgot-password">Forgot password</Link>
        </p>
        <p>
          <Link href="/register">Create account</Link>
        </p>
        <p>
          <Link href="/">Back to home</Link>
        </p>
      </div>
    </AuthPage>
  );
}
