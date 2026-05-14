import Link from 'next/link';
import { useRouter } from 'next/router';
import { useMemo, useState } from 'react';

import AuthPage from '../components/auth/AuthPage';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { useAuth } from '../contexts/AuthContext';

export default function ResetPasswordPage() {
  const router = useRouter();
  const { resetPassword } = useAuth();
  const tokenFromQuery = useMemo(
    () => (typeof router.query.token === 'string' ? router.query.token : ''),
    [router.query.token],
  );
  const [token, setToken] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  return (
    <AuthPage
      title="Choose a new password"
      subtitle="Complete the password reset flow with your recovery token."
    >
      <form
        className="pp-auth-form"
        onSubmit={async (event) => {
          event.preventDefault();
          setSubmitting(true);
          setError('');
          const form = new FormData(event.currentTarget);
          const nextPassword = String(form.get('password') || '');
          if (nextPassword !== confirmPassword) {
            setError('Passwords do not match.');
            setSubmitting(false);
            return;
          }
          try {
            await resetPassword({
              token: token || tokenFromQuery,
              new_password: nextPassword,
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
          <label htmlFor="reset-token">Reset token</label>
          <Input
            id="reset-token"
            name="token"
            required
            value={token || tokenFromQuery}
            onChange={(event) => setToken(event.target.value)}
            placeholder="Paste the reset token"
          />
        </div>

        <div className="pp-field">
          <label htmlFor="reset-password">New password</label>
          <Input id="reset-password" name="password" type="password" required minLength={8} placeholder="Create a new password" />
        </div>

        <div className="pp-field">
          <label htmlFor="reset-password-confirm">Confirm new password</label>
          <Input
            id="reset-password-confirm"
            type="password"
            required
            minLength={8}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            placeholder="Repeat your new password"
          />
        </div>

        {error ? <p className="text-sm font-medium text-rose-600">{error}</p> : null}

        <Button type="submit" className="mt-2 w-full" size="lg" disabled={submitting}>
          {submitting ? 'Resetting password...' : 'Reset password'}
        </Button>
      </form>

      <div className="pp-auth-links">
        <p>
          <Link href="/login">Back to sign in</Link>
        </p>
        <p>
          <Link href="/">Back to home</Link>
        </p>
      </div>
    </AuthPage>
  );
}
