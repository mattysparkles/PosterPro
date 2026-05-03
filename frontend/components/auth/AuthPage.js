import Link from 'next/link';
import { Layers3, Package, Send } from 'lucide-react';

export default function AuthPage({ title, subtitle, children }) {
  return (
    <div className="pp-auth-page">
      <Link href="/" className="pp-auth-wordmark">
        PosterPro
      </Link>

      <div className="pp-auth-layout">
        <section className="pp-auth-card">
          <h1>{title}</h1>
          <p>{subtitle}</p>
          {children}
        </section>

        <aside className="pp-auth-preview" aria-hidden="true">
          <div className="pp-auth-preview-window">
            <div className="pp-auth-preview-bar">
              <span />
              <span />
              <span />
            </div>
            <div className="pp-auth-preview-body">
              <div className="pp-auth-preview-row">
                <div className="pp-auth-preview-icon">
                  <Layers3 size={16} />
                </div>
                <div>
                  <strong>Draft queue</strong>
                  <small>42 listings in progress</small>
                </div>
              </div>
              <div className="pp-auth-preview-row">
                <div className="pp-auth-preview-icon">
                  <Package size={16} />
                </div>
                <div>
                  <strong>Inventory</strong>
                  <small>148 items in intake</small>
                </div>
              </div>
              <div className="pp-auth-preview-row">
                <div className="pp-auth-preview-icon">
                  <Send size={16} />
                </div>
                <div>
                  <strong>Ready</strong>
                  <small>19 listings ready to publish</small>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
