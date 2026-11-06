/** Start the unchanged Firecrawl server and attest the bytes/configuration it uses. */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import {
  lstatSync as lstat, readdirSync as readdir,
  readlinkSync as readlink, realpathSync as realpath,
  openSync as open, closeSync as close, readSync as read,
} from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const fileHashes = new Map();
function fileHash(target, info) {
  info ??= lstat(target, { bigint: true });
  const signature = [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs].join(':');
  const prior = fileHashes.get(target);
  if (prior?.signature === signature) return prior.hash;
  const digest = createHash('sha256');
  const descriptor = open(target, 'r');
  try {
    const buffer = Buffer.alloc(1024 * 1024);
    let count;
    while ((count = read(descriptor, buffer, 0, buffer.length, null))) digest.update(buffer.subarray(0, count));
  } finally { close(descriptor); }
  const hash = digest.digest('hex');
  fileHashes.set(target, { signature, hash });
  return hash;
}

function treeHash(root) {
  root = path.join(realpath(path.dirname(root)), path.basename(root));
  const hash = createHash('sha256');
  const seen = new Set();
  function record(value) {
    hash.update(JSON.stringify(value) + '\n');
  }
  function visit(target, relative) {
    const info = lstat(target, { bigint: true });
    if (info.isSymbolicLink()) {
      record(['link', relative, readlink(target)]);
      let resolved;
      try {
        resolved = realpath(target);
      } catch (error) {
        if (!['ENOENT', 'ENOTDIR', 'ELOOP'].includes(error.code)) throw error;
        // Pruned optional packages may leave links that Node cannot load.
        record(['unresolved-link', relative, error.code]);
        return;
      }
      visit(resolved, relative + '/@target');
      return;
    }
    // Descendants start from a canonical directory; link targets resolve above.
    const resolved = path.resolve(target);
    if (seen.has(resolved)) {
      record(['reference', relative, resolved]);
      return;
    }
    seen.add(resolved);
    if (info.isDirectory()) {
      record(['dir', relative]);
      for (const name of readdir(target).sort()) {
        visit(path.join(target, name), path.join(relative, name));
      }
    } else if (info.isFile()) {
      record(['file', relative, fileHash(target, info)]);
    } else {
      throw new Error('Unsupported file type in backend attestation');
    }
  }
  visit(root, '');
  return hash.digest('hex');
}

const controls = [
  'MODEL_NAME', 'OPENAI_BASE_URL', 'OLLAMA_BASE_URL', 'MODEL_EMBEDDING_NAME',
  'PLAYWRIGHT_MICROSERVICE_URL', 'FIRECRAWL_BROWSER_IDENTITY_URL',
  'FIRECRAWL_SERVICE_KIND', 'FIRECRAWL_UPSTREAM_IMAGE', 'PLAYWRIGHT_BROWSERS_PATH',
  'PORT', 'MAX_CONCURRENT_PAGES', 'BLOCK_MEDIA',
  'NUM_WORKERS_PER_QUEUE', 'NUQ_WORKER_COUNT', 'MAX_CONCURRENT_JOBS', 'CRAWL_CONCURRENT_REQUESTS', 'BROWSER_POOL_SIZE',
];
// The native child inherits this exact environment; none of its credentials are exposed.
const childEnv = { ...process.env };
for (const key of ['NODE_OPTIONS', 'NODE_PATH']) {
  if (childEnv[key]?.trim()) throw new Error('Node code-loading overrides are unsupported');
}
for (const key of ['OPENAI_BASE_URL', 'OLLAMA_BASE_URL', 'PLAYWRIGHT_MICROSERVICE_URL', 'FIRECRAWL_BROWSER_IDENTITY_URL']) {
  if (!childEnv[key]) continue;
  let url;
  try {
    url = new URL(childEnv[key]);
  } catch {
    throw new Error('Model base URL must be a credential-free HTTP(S) URL');
  }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('Model base URL must not contain credentials, query or fragment');
  }
}
const kind = childEnv.FIRECRAWL_SERVICE_KIND || 'extractor';
if (!['extractor', 'browser'].includes(kind)) throw new Error('Unsupported Firecrawl service kind');
async function currentIdentity() {
  const identity = {
    schema: "firecrawl-runtime/2",
    kind,
    native_bundle_sha256: treeHash(path.resolve('dist')),
    native_dependencies_sha256: treeHash(path.resolve('node_modules')),
    node_sha256: fileHash(process.execPath),
    wrapper_sha256: fileHash(fileURLToPath(import.meta.url)),
    controls: Object.fromEntries(controls.map(key => [key, childEnv[key] ?? ''])),
  };
  if (kind === 'browser') {
    if (!childEnv.PLAYWRIGHT_BROWSERS_PATH) throw new Error('Browser installation path is required');
    identity.browser_installation_sha256 = treeHash(childEnv.PLAYWRIGHT_BROWSERS_PATH);
  } else if (childEnv.PLAYWRIGHT_MICROSERVICE_URL) {
    if (!childEnv.FIRECRAWL_BROWSER_IDENTITY_URL) throw new Error('Browser identity endpoint is required');
    const render = new URL(childEnv.PLAYWRIGHT_MICROSERVICE_URL);
    const attestation = new URL(childEnv.FIRECRAWL_BROWSER_IDENTITY_URL);
    if (render.hostname !== attestation.hostname || render.protocol !== attestation.protocol ||
        attestation.pathname !== '/identity') throw new Error('Browser identity must use the rendering host');
    const response = await fetch(attestation, { signal: AbortSignal.timeout(5000), redirect: 'error' });
    if (!response.ok) throw new Error('Browser identity is unavailable');
    const browser = await response.json();
    if (browser.schema !== 'firecrawl-runtime/2' || browser.kind !== 'browser' ||
        !['native_bundle_sha256', 'native_dependencies_sha256', 'node_sha256', 'wrapper_sha256',
          'browser_installation_sha256'].every(key => /^[a-f0-9]{64}$/.test(browser[key] ?? '')) ||
        String(browser.controls?.PORT || '3000') !== (render.port || (render.protocol === 'https:' ? '443' : '80'))) {
      throw new Error('Browser identity or rendering port differs');
    }
    identity.browser = browser;
  }
  return identity;
}
const startupIdentity = JSON.stringify(await currentIdentity());
let invalidated = false;
let checking;
const server = createServer(async (request, response) => {
  if (request.method !== 'GET' || request.url !== '/identity') {
    response.writeHead(404).end();
    return;
  }
  try {
    if (invalidated) throw new Error('Executable files changed; restart required');
    checking ??= currentIdentity().then(identity => {
      if (JSON.stringify(identity) !== startupIdentity) {
        invalidated = true;
        throw new Error('Executable files changed; restart required');
      }
      return identity;
    }).finally(() => { checking = undefined; });
    const identity = await checking;
    response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify(identity));
  } catch {
    response.writeHead(503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify({ error: 'Backend identity could not be verified' }));
  }
});
await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(Number(process.env.FIRECRAWL_IDENTITY_PORT ?? 3020), '0.0.0.0', () => {
    server.off('error', reject);
    resolve();
  });
});
console.log(JSON.stringify({ identity_port: server.address().port }));
const nativeArguments = kind === 'browser' ? ['dist/api.js'] : ['dist/src/harness.js', '--start-docker'];
const child = spawn(process.execPath, nativeArguments, {
  env: childEnv, stdio: 'inherit',
});
server.on('error', error => { console.error(error.message); child.kill('SIGTERM'); server.close(); process.exitCode = 1; });
for (const name of ['SIGINT', 'SIGTERM']) process.on(name, () => child.kill(name));
child.on('error', error => { console.error(error.message); server.close(); process.exitCode = 1; });
child.on('exit', code => { server.close(); process.exitCode = code ?? 1; });
