function headerEntries(extra?: HeadersInit): [string, string][] {
  if (!extra) return [];
  if (extra instanceof Headers) return [...extra.entries()];
  if (Array.isArray(extra)) return extra.map(([key, value]) => [String(key), String(value)]);
  return Object.entries(extra).map(([key, value]) => [key, String(value)]);
}

function setHeader(headers: Record<string, string>, key: string, value: string): void {
  const lower = key.toLowerCase();
  for (const existing of Object.keys(headers)) {
    if (existing.toLowerCase() === lower) delete headers[existing];
  }
  headers[key] = value;
}

/** Merge caller headers with exactly one Authorization value.

A caller-supplied Authorization is kept only when `token` is null, so the
request never sends two Authorization headers and never drops the session
token when other headers are passed.
*/
export function buildHeaders(
  extra: HeadersInit | undefined,
  token: string | null,
): Record<string, string> {
  const headers: Record<string, string> = {};
  setHeader(headers, "content-type", "application/json");
  let callerAuthorization: string | undefined;
  for (const [key, value] of headerEntries(extra)) {
    if (key.toLowerCase() === "authorization") {
      callerAuthorization = value;
      continue;
    }
    setHeader(headers, key, value);
  }
  if (token) setHeader(headers, "Authorization", `Bearer ${token}`);
  else if (callerAuthorization) setHeader(headers, "Authorization", callerAuthorization);
  return headers;
}

/** Spread `init` after `base`, then force the merged headers to win. */
export function mergeRequestInit(
  base: RequestInit,
  init: RequestInit | undefined,
  token: string | null,
): RequestInit {
  return {
    ...base,
    ...init,
    headers: buildHeaders(init?.headers, token),
  };
}
