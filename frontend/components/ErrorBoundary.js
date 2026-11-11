import React from 'react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // Keep the browser console useful during live debugging without breaking
    // the user into the generic Next.js client error screen.
    // eslint-disable-next-line no-console
    console.error('PosterPro client error boundary caught:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      const message = this.state.error?.message || 'A client-side error occurred.';
      return (
        <div className="flex min-h-screen items-center justify-center bg-[var(--pp-bg)] px-6 py-10 text-[var(--pp-text)]">
          <div className="w-full max-w-2xl rounded-[24px] border border-[var(--pp-border)] bg-white p-6 shadow-[0_18px_50px_rgba(16,24,40,0.12)]">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--pp-muted)]">PosterPro client error</p>
            <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em]">The page crashed in the browser.</h1>
            <p className="mt-3 text-sm leading-6 text-[var(--pp-muted)]">
              {message}
            </p>
            {this.state.error?.stack ? (
              <pre className="mt-4 max-h-[320px] overflow-auto rounded-[18px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 text-xs leading-6 text-[var(--pp-muted)]">
                {this.state.error.stack}
              </pre>
            ) : null}
            <div className="mt-5 flex flex-wrap gap-2">
              <button
                type="button"
                className="inline-flex h-10 items-center justify-center rounded-2xl border border-[var(--pp-border)] bg-[var(--pp-primary)] px-4 text-sm font-semibold text-white"
                onClick={() => window.location.reload()}
              >
                Reload page
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
