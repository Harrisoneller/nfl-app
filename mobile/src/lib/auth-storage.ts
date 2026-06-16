// Token storage for React Native.
//
// The web app read the JWT synchronously from localStorage inside every request
// (buildHeaders). SecureStore is async, so we keep a synchronous in-memory
// mirror of the token and hydrate it once at app boot. This lets the ported
// api.ts keep calling getStoredToken() synchronously, unchanged.

import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "statletics_auth_token";

let cachedToken: string | null = null;

/** Synchronous read used by the API client on every request. */
export function getStoredToken(): string | null {
  return cachedToken;
}

/** Persist the token (memory + secure storage). Fire-and-forget on the disk write. */
export function setStoredToken(token: string): void {
  cachedToken = token;
  // SecureStore keys must be alphanumeric/._-; our key qualifies.
  void SecureStore.setItemAsync(TOKEN_KEY, token);
}

export function clearStoredToken(): void {
  cachedToken = null;
  void SecureStore.deleteItemAsync(TOKEN_KEY);
}

/** Load the persisted token into the in-memory cache. Call once at app start. */
export async function hydrateToken(): Promise<string | null> {
  try {
    cachedToken = await SecureStore.getItemAsync(TOKEN_KEY);
  } catch {
    cachedToken = null;
  }
  return cachedToken;
}
