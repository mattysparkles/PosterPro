import fs from 'fs';
import path from 'path';
import { execFileSync } from 'child_process';

export async function getServerSideProps() {
  let gitSha = 'unknown';
  try { gitSha = execFileSync('git', ['-C', path.resolve(process.cwd(), '..'), 'rev-parse', 'HEAD'], { encoding: 'utf8', timeout: 1500 }).trim(); } catch {}
  let buildId = 'unknown';
  try { buildId = fs.readFileSync(path.join(process.cwd(), '.next', 'BUILD_ID'), 'utf8').trim(); } catch {}
  return { props: { gitSha, buildId, buildTimestamp: new Date().toISOString(), sourcePath: process.cwd() } };
}

export default function FrontendDeployment({ gitSha, buildId, buildTimestamp, sourcePath }) {
  return <main data-frontend-git-sha={gitSha} data-frontend-build-id={buildId} data-frontend-source={sourcePath} style={{ fontFamily: 'system-ui', padding: 24 }}><h1>PosterPro frontend deployment</h1><dl><dt>Git SHA</dt><dd>{gitSha}</dd><dt>Next build ID</dt><dd>{buildId}</dd><dt>Build observed</dt><dd>{buildTimestamp}</dd><dt>Source path</dt><dd>{sourcePath}</dd></dl></main>;
}
