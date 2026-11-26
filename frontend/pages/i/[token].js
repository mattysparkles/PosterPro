/**
 * Short permanent inventory lookup URL used by the QR symbol on 58x40 labels.
 * The token is an opaque, database-unique lookup key; the census page performs
 * the authenticated, account-scoped resolution.
 */
export async function getServerSideProps({ params }) {
  const token = String(params?.token || '').toUpperCase();
  if (!/^[A-Z2-7]{13}$/.test(token)) {
    return { redirect: { destination: '/inventory/census', permanent: false } };
  }
  return { redirect: { destination: `/inventory/census?item=${encodeURIComponent(token)}`, permanent: false } };
}

export default function InventoryItemShortLink() {
  return null;
}
