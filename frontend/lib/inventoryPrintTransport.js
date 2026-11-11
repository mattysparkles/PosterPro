/**
 * Structured PosterPro label transport boundary. Printer protocols and drivers
 * remain local to the Windows bridge and are intentionally absent here.
 */
export function dispatchBrowserLabel(imageDataUrl, { onReturn, scalePercent = 100 } = {}) {
  const frame = document.createElement('iframe');
  frame.style.position = 'fixed'; frame.style.width = '1px'; frame.style.height = '1px'; frame.style.opacity = '0';
  document.body.appendChild(frame);
  const doc = frame.contentWindow.document;
  doc.open();
  const scale = Math.max(70, Math.min(130, Number(scalePercent) || 100)) / 100;
  doc.write(`<!doctype html><html><head><title>PosterPro Inventory Label</title><style>@page{size:58mm 40mm;margin:0}html,body{margin:0;width:58mm;height:40mm}img{width:58mm;height:40mm;display:block;transform:scale(${scale});transform-origin:top left}</style></head><body><img src="${imageDataUrl}" onload="setTimeout(()=>{window.focus();window.print()},100)"></body></html>`);
  doc.close();
  frame.contentWindow.addEventListener('afterprint', () => {
    setTimeout(() => frame.remove(), 1200);
    window.focus();
    onReturn?.();
  }, { once: true });
  return { status: 'dispatched', physicallyVerified: false };
}

/**
 * Future local bridge adapter contract. The bridge accepts only structured
 * label payloads, never raw printer-language commands. CORS, token provisioning
 * and actual spooler result fields must be agreed with the local bridge report.
 */
export async function dispatchLocalPrintBridge({ bridgeUrl, printerName, allowedPrinterName, authorization, jobId, label, fetchImpl = fetch }) {
  const url = new URL(bridgeUrl);
  if (!['localhost', '127.0.0.1', '::1'].includes(url.hostname)) throw new Error('Print bridge must bind to localhost.');
  if (!printerName || printerName !== allowedPrinterName) throw new Error('Printer is not in the configured local allowlist.');
  if (!authorization) throw new Error('Local bridge authorization is required.');
  if (!jobId || !label?.imageDataUrl || label.widthMm !== 58 || label.heightMm !== 40) throw new Error('Invalid structured label job.');
  const response = await fetchImpl(url.toString(), {
    method: 'POST', mode: 'cors', credentials: 'omit',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authorization}` },
    body: JSON.stringify({ jobId, printerName, label: { imageDataUrl: label.imageDataUrl, widthMm: 58, heightMm: 40 } }),
  });
  if (!response.ok) throw new Error(`Local print bridge rejected job (${response.status}).`);
  const result = await response.json();
  const allowed = new Set(['dispatched', 'spooler_accepted', 'completed', 'failed', 'unknown']);
  if (!allowed.has(result.status)) throw new Error('Local bridge returned an unsupported print status.');
  return { status: result.status, physicallyVerified: result.status === 'completed' && result.physicalVerification === true };
}
