const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

export async function fetchListings() {
  const response = await fetch(`${API_BASE}/listings`);
  return response.json();
}

export async function fetchClusters() {
  const response = await fetch(`${API_BASE}/clusters`);
  return response.json();
}

export async function updateListing(id, body) {
  const response = await fetch(`${API_BASE}/listings/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return response.json();
}

export async function generateListing(id) {
  const response = await fetch(`${API_BASE}/listings/${id}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return response.json();
}

export async function publishListing(id) {
  const response = await fetch(`${API_BASE}/listings/${id}/publish/ebay`, { method: 'POST' });
  return response.json();
}
