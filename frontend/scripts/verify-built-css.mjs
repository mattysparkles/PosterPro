import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const cssRoot = path.resolve('.next/static/css');
const requiredUtilities = ['.gap-2', '.px-4', '.h-10', '.rounded-xl', '.w-full'];
const requiredControlRules = ['.pp-tab-item[aria-selected=true]', '-webkit-text-fill-color:#fff!important'];

async function cssFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(entries.map((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? cssFiles(target) : entry.name.endsWith('.css') ? [target] : [];
  }));
  return files.flat();
}

const files = await cssFiles(cssRoot);
const css = (await Promise.all(files.map((file) => readFile(file, 'utf8')))).join('\n');
const missing = requiredUtilities.filter((selector) => !css.includes(selector));
const missingRules = requiredControlRules.filter((rule) => !css.includes(rule));
if (missing.length || missingRules.length) {
  if (missing.length) console.error(`Built CSS is missing required utility selectors: ${missing.join(', ')}`);
  if (missingRules.length) console.error(`Built CSS is missing active-control contrast rules: ${missingRules.join(', ')}`);
  process.exit(1);
}
console.log(`Built CSS contains required control utilities: ${requiredUtilities.join(', ')}`);
console.log(`Built CSS contains selected-tab contrast rules: ${requiredControlRules.join(', ')}`);
