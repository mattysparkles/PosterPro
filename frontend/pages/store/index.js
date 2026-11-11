export async function getServerSideProps({ req }) {
  try {
    const base = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8030';
    const response = await fetch(`${base}/storefront/settings`, { headers: { cookie: req.headers.cookie || '' } });
    if (!response.ok) return { redirect: { destination: '/settings/store', permanent: false } };
    const payload = await response.json();
    const slug = payload?.profile?.slug;
    return { redirect: { destination: payload?.profile?.enabled && slug ? `/store/${slug}` : '/settings/store', permanent: false } };
  } catch {
    return { redirect: { destination: '/settings/store', permanent: false } };
  }
}

export default function StoreShortcut() { return null; }
