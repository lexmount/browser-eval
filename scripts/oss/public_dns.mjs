import { isIP } from "node:net";

export const DNS_ENDPOINT = "https://cloudflare-dns.com/dns-query";
export function validateDnsMode(mode) {
  if (mode !== "system" && mode !== "public_doh") throw new Error("dns_mode must be system or public_doh");
  return mode;
}

// This replaces only DNS resolution. The product validates and pins every
// answer through its unchanged native public-network guard and HTTP client.
export function createPublicDnsLookup(signal, traces, fetcher = fetch) {
  return async (hostname) => {
    signal.throwIfAborted();
    const literal = isIP(hostname);
    if (literal) return [{ address: hostname, family: literal }];
    const responses = await Promise.all([1, 28].map(async (rrtype) => {
      const url = new URL(DNS_ENDPOINT);
      url.searchParams.set("name", hostname);
      url.searchParams.set("type", String(rrtype));
      const response = await fetcher(url, { headers: { accept: "application/dns-json" }, signal, redirect: "error" });
      if (!response.ok) throw new Error(`DNS HTTP ${response.status}`);
      const reader = response.body.getReader();
      const chunks = [];
      let size = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.length;
          if (size > 65536) throw new Error("DNS response exceeds 64 KiB");
          chunks.push(value);
        }
      } finally {
        await reader.cancel();
      }
      const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (payload.Status !== 0 || payload.TC) throw new Error("DNS returned an unsuccessful or truncated answer");
      return (payload.Answer ?? []).filter((answer) => answer.type === rrtype).map((answer) => {
        const family = isIP(answer.data);
        if (family !== (rrtype === 1 ? 4 : 6)) throw new Error("DNS record family mismatch");
        return { address: answer.data, family };
      });
    }));
    const addresses = responses.flat().filter((value, i, all) => all.findIndex(v => v.address === value.address && v.family === value.family) === i);
    if (!addresses.length) throw new Error("DNS returned no addresses");
    traces.push({ hostname, addresses });
    return addresses;
  };
}
