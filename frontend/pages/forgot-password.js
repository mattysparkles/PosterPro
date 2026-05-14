import Link from 'next/link';
import { useState } from 'react';

import AuthPage from '../components/auth/AuthPage';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { useAuth } from '../contexts/AuthContext';

export default function ForgotPasswordPage() {
  const { forgotPassword } = useAuth();
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [resetPreview, setResetPreview] = useState('');
  const [submitting, setSubmitting] = useState(false);

  return (
    <AuthPage
      title="Reset your password"
      subtitle="Request a password reset token for your PosterPro account."
    >
      <form
        className="pp-auth-form"
        onSubmit={async (event) => {
          event.preventDefault();
          setSubmitting(true);
          setError('');
          setMessage('');
          setResetPreview('');
          const form = new FormData(event.currentTarget);
          try {
            const response = await forgotPassword({
              email: String(form.get('email') || ''),
            });
            setMessage(response.message || 'If that account exists, the reset request is ready.');
            if (response.reset_token_preview) {
              setResetPreview(response.reset_token_preview);
            }
          } catch (submissionError) {
            setError(submissionError.message);
          } finally {
            setSubmitting(false);
          }
        }}
      >
        <div className="pp-field">
          <label htmlFor="forgot-email">Email</label>
          <Input id="forgot-email" name="email" type="email" required placeholder="you@example.com" />
        </div>

        {error ? <p className="text-sm font-medium text-rose-600">{error}</p> : null}
        {message ? <p className="text-sm text-[#475467]">{message}</p> : null}
        {resetPreview ? (
          <div className="rounded-[14px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <p className="font-semibold">Local reset token preview</p>
            <p className="mt-1 break-all">{resetPreview}</p>
            <p className="mt-2">
              Open <Link href={`/reset-password?token=${encodeURIComponent(resetPreview)}`}>the reset form</Link> to complete the flow.
            </p>
          </div>
        ) : null}

        <Button type="submit" className="mt-2 w-full" size="lg" disabled={submitting}>
          {submitting ? 'Requesting reset...' : 'Request reset'}
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
