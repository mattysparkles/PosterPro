const path = require('path');
const { execFileSync } = require('child_process');

let gitSha = process.env.NEXT_PUBLIC_FRONTEND_GIT_SHA || 'unknown';
let buildTimestamp = process.env.NEXT_PUBLIC_FRONTEND_BUILD_TIMESTAMP || '';
try {
  if (gitSha === 'unknown') gitSha = execFileSync('git', ['-C', path.resolve(__dirname, '..'), 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
} catch {}
if (!buildTimestamp) buildTimestamp = new Date().toISOString();

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Never let an intermediary/browser pin an HTML document that references
  // CSS chunks from a previous deployment.  Chunks remain content-hashed;
  // only the document shell needs revalidation.
  async headers() {
    return [{
      source: '/((?!_next/static|favicon.ico).*)',
      headers: [{ key: 'Cache-Control', value: 'no-store, max-age=0' }],
    }];
  },
  env: {
    NEXT_PUBLIC_FRONTEND_GIT_SHA: gitSha,
    NEXT_PUBLIC_FRONTEND_BUILD_TIMESTAMP: buildTimestamp,
  },
};

module.exports = nextConfig;
