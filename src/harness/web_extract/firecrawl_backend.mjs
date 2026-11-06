/** Start the unchanged Firecrawl server and attest the bytes/configuration it uses. */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import {
  lstatSync as lstat, readdirSync as readdir, readFileSync as readFile,
  readlinkSync as readlink, realpathSync as realpath,
} from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const fileHashes = new Map();
function fileHash(target, info) {
  info ??= lstat(target, { bigint: true });
  const signature = [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs].join(':');
  const prior = fileHashes.get(target);
  if (prior?.signature === signature) return prior.hash;
  const hash = createHash('sha256').update(readFile(target)).digest('hex');
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
  'NUM_WORKERS_PER_QUEUE', 'NUQ_WORKER_COUNT', 'MAX_CONCURRENT_JOBS', 'CRAWL_CONCURRENT_REQUESTS', 'BROWSER_POOL_SIZE',
];
// The native child inherits this exact environment; none of its credentials are exposed.
const childEnv = { ...process.env };
for (const key of ['NODE_OPTIONS', 'NODE_PATH']) {
  if (childEnv[key]?.trim()) throw new Error('Node code-loading overrides are unsupported');
}
for (const key of ['OPENAI_BASE_URL', 'OLLAMA_BASE_URL']) {
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
async function currentIdentity() {
  return {
    native_bundle_sha256: await treeHash(path.resolve('dist')),
    native_dependencies_sha256: await treeHash(path.resolve('node_modules')),
    node_sha256: await fileHash(process.execPath),
    wrapper_sha256: await fileHash(fileURLToPath(import.meta.url)),
    controls: Object.fromEntries(controls.map(key => [key, childEnv[key] ?? ''])),
  };
}
await currentIdentity();
let checking;
const server = createServer(async (request, response) => {
  if (request.method !== 'GET' || request.url !== '/identity') {
    response.writeHead(404).end();
    return;
  }
  try {
    checking ??= currentIdentity().finally(() => { checking = undefined; });
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
const child = spawn(process.execPath, ['dist/src/harness.js', '--start-docker'], {
  env: childEnv, stdio: 'inherit',
});
server.on('error', error => { console.error(error.message); child.kill('SIGTERM'); server.close(); process.exitCode = 1; });
for (const name of ['SIGINT', 'SIGTERM']) process.on(name, () => child.kill(name));
child.on('error', error => { console.error(error.message); server.close(); process.exitCode = 1; });
child.on('exit', code => { server.close(); process.exitCode = code ?? 1; });
