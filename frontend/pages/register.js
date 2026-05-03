import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

import AuthPage from '../components/auth/AuthPage';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { useAuth } from '../contexts/AuthContext';

export default function RegisterPage() {
  const router = useRouter();
  const { user, register } = useAuth();
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (user) {
      router.replace('/app');
    }
  }, [router, user]);

  return (
    <AuthPage
      title="Create your PosterPro account"
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
            await register({
              full_name: String(form.get('full_name') || ''),
              email: String(form.get('email') || ''),
              password: String(form.get('password') || ''),
            });
            router.replace('/app');
          } catch (submissionError) {
            setError(submissionError.message);
          } finally {
            setSubmitting(false);
          }
        }}
      >
        <div className="pp-field">
          <label htmlFor="register-name">Name</label>
          <Input id="register-name" name="full_name" placeholder="Matthew Sparkles" />
        </div>

        <div className="pp-field">
          <label htmlFor="register-email">Email</label>
          <Input id="register-email" name="email" type="email" required placeholder="you@example.com" />
        </div>

        <div className="pp-field">
          <label htmlFor="register-password">Password</label>
          <Input id="register-password" name="password" type="password" required minLength={8} placeholder="Create a password" />
        </div>

        {error ? <p className="text-sm font-medium text-rose-600">{error}</p> : null}

        <Button type="submit" className="mt-2 w-full" size="lg" disabled={submitting}>
          {submitting ? 'Creating account...' : 'Create account'}
        </Button>
      </form>

      <div className="pp-auth-links">
        <p>
          <Link href="/login">Sign in</Link>
        </p>
        <p>
          <Link href="/">Back to home</Link>
        </p>
      </div>
    </AuthPage>
  );
}
