/** Start the unchanged Firecrawl server and attest the bytes/configuration it uses. */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { lstat, readdir, readFile, readlink } from 'node:fs/promises';
import path from 'node:path';

async function treeHash(root) {
  const hash = createHash('sha256');
  async function visit(relative) {
    const target = path.join(root, relative);
    const info = await lstat(target);
    hash.update(relative + '\0');
    if (info.isSymbolicLink()) {
      hash.update('link\0' + await readlink(target) + '\0');
    } else if (info.isDirectory()) {
      hash.update('dir\0');
      for (const name of (await readdir(target)).sort()) await visit(path.join(relative, name));
    } else if (info.isFile()) {
      hash.update('file\0');
      hash.update(await readFile(target));
    }
  }
  await visit('');
  return hash.digest('hex');
}

const controls = [
  'MODEL_NAME', 'OPENAI_BASE_URL', 'OLLAMA_BASE_URL', 'MODEL_EMBEDDING_NAME',
  'NUM_WORKERS_PER_QUEUE', 'NUQ_WORKER_COUNT', 'MAX_CONCURRENT_JOBS', 'CRAWL_CONCURRENT_REQUESTS', 'BROWSER_POOL_SIZE',
];
// The native child inherits this exact environment; none of its credentials are exposed.
const childEnv = { ...process.env };
const identity = {
  native_bundle_sha256: await treeHash(path.resolve('dist')),
  native_dependencies_sha256: await treeHash(path.resolve('node_modules')),
  node_sha256: createHash('sha256').update(await readFile(process.execPath)).digest('hex'),
  wrapper_sha256: createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex'),
  controls: Object.fromEntries(controls.map(key => [key, childEnv[key] ?? ''])),
};
const child = spawn(process.execPath, ['dist/src/harness.js', '--start-docker'], {
  env: childEnv, stdio: 'inherit',
});
const server = createServer((request, response) => {
  if (request.method !== 'GET' || request.url !== '/identity') {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(identity));
});
server.listen(Number(process.env.FIRECRAWL_IDENTITY_PORT ?? 3020), '0.0.0.0', () => {
  console.log(JSON.stringify({ identity_port: server.address().port }));
});
for (const name of ['SIGINT', 'SIGTERM']) process.on(name, () => child.kill(name));
child.on('error', error => { console.error(error.message); server.close(); process.exitCode = 1; });
child.on('exit', code => { server.close(); process.exitCode = code ?? 1; });
