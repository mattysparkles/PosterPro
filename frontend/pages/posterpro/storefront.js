import Head from 'next/head';
import Link from 'next/link';

export default function RetiredStorefrontLink() {
  return <>
    <Head><title>Store link updated | PosterPro</title><meta name="robots" content="noindex" /></Head>
    <main className="grid min-h-screen place-items-center bg-slate-50 px-5 text-slate-900">
      <section className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-2xl font-bold">This store link has changed</h1>
        <p className="mt-3 leading-7 text-slate-600">Ask the seller for their current store link. Each PosterPro store has its own address.</p>
        <Link href="/" className="mt-6 inline-flex h-11 items-center rounded-xl bg-slate-900 px-5 font-semibold text-white">Go to PosterPro</Link>
      </section>
    </main>
  </>;
}
