"use strict";
(() => {
  const nativeFetch = window.fetch.bind(window),
    script = document.currentScript,
    root = new URL(".", script.src),
    decoder = new TextDecoder();
  let catalogPromise;
  const packs = new Map();
  function catalog() {
    return (catalogPromise ??= nativeFetch(
      new URL("bundle-manifest.json", root),
    ).then((r) => {
      if (!r.ok) throw Error("Bundle manifest unavailable");
      return r.json();
    }));
  }
  function keyFor(value) {
    const url = new URL(
      value instanceof Request ? value.url : value,
      document.baseURI,
    );
    if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname))
      return null;
    return decodeURIComponent(url.pathname.slice(root.pathname.length));
  }
  async function pack(name) {
    if (!packs.has(name))
      packs.set(
        name,
        nativeFetch(new URL(name, root)).then(async (r) => {
          if (!r.ok) throw Error("Evidence pack unavailable: " + name);
          const stream = r.body.pipeThrough(new DecompressionStream("gzip"));
          return new Uint8Array(await new Response(stream).arrayBuffer());
        }),
      );
    return packs.get(name);
  }
  async function bundled(key) {
    const manifest = await catalog(),
      entry = manifest.files[key];
    if (!entry) return null;
    const bytes = (await pack(entry.pack)).slice(
      entry.offset,
      entry.offset + entry.size,
    );
    return new Response(bytes, {
      status: 200,
      headers: {
        "Content-Type": entry.mime,
        "Content-Length": String(entry.size),
        "X-Content-SHA256": entry.sha256,
      },
    });
  }
  window.fetch = async (input, init) => {
    const key = keyFor(input),
      response = key && (await bundled(key));
    return response ?? nativeFetch(input, init);
  };
  document.addEventListener("click", async (event) => {
    const anchor = event.target.closest("a[href]");
    if (!anchor || anchor.hasAttribute("download")) return;
    const key = keyFor(anchor.href);
    if (!key) return;
    const manifest = await catalog(),
      entry = manifest.files[key];
    if (!entry) return;
    event.preventDefault();
    const response = await bundled(key),
      blob = await response.blob(),
      url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  });
})();
