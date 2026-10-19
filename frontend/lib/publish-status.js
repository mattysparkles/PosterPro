export function isEbayReconnectRequiredError(error) {
  const message = String(error || '').toLowerCase();
  return message.includes('invalid access token') || message.includes('oauth') || message.includes('(401)');
}

export function formatPublishFailureMessage(error, marketplace = 'ebay') {
  if (!error) return 'Publish failed';
  if (marketplace === 'ebay' && isEbayReconnectRequiredError(error)) {
    return 'eBay token invalid. Reconnect eBay in Settings, then retry publish.';
  }
  return String(error);
}
