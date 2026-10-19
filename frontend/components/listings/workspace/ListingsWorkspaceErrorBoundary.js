import React from 'react';

export default class ListingsWorkspaceErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    // Keep the failure visible in the browser console while preventing a full route crash.
    // This is a stability guard for the listings workspace while the page is being refactored.
    console.error('Listings workspace render failed', error, info);
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false });
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <section className="rounded-[24px] border border-[#f0c9c3] bg-[#fff7f5] p-5 shadow-[0_12px_30px_rgba(16,24,40,0.05)]">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#b42318]">Listings workspace error</p>
          <h2 className="mt-3 text-xl font-semibold tracking-[-0.03em] text-[#7a271a]">A listings module failed to render.</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[#7a271a]">
            The route stayed alive instead of crashing the whole app. Change the queue or refresh the page to retry while the underlying module is being hardened.
          </p>
        </section>
      );
    }

    return this.props.children;
  }
}
