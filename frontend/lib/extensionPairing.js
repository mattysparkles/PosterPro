/** Create a one-time account pairing code and deliver it to the installed
 * extension after a user has explicitly selected Pair this browser. */
export async function requestCurrentBrowserPairing(createPairingCode, pageWindow = window) {
  if (typeof createPairingCode !== 'function') throw new Error('Browser pairing is unavailable. Refresh the page and try again.');
  const result = await createPairingCode('PosterPro browser');
  if (!result?.pairing_code) throw new Error('PosterPro did not return a pairing code. Refresh the page and try again.');
  pageWindow.postMessage({
    source: 'posterpro-settings',
    type: 'PAIR_EXTENSION',
    pairing_code: result.pairing_code,
  }, pageWindow.location.origin);
  return result;
}
