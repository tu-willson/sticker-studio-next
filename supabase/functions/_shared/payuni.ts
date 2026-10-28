export type PayuniResult = Record<string, string>;

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function base64Encode(bytes: Uint8Array): string {
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary);
}

function base64Decode(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function hexEncode(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function hexDecode(value: string): Uint8Array {
  if (!/^[0-9a-f]+$/i.test(value) || value.length % 2 !== 0) {
    throw new Error("payuni_invalid_hex");
  }
  const bytes = new Uint8Array(value.length / 2);
  for (let index = 0; index < value.length; index += 2) {
    bytes[index / 2] = Number.parseInt(value.slice(index, index + 2), 16);
  }
  return bytes;
}

function concatenate(first: Uint8Array, second: Uint8Array): Uint8Array {
  const output = new Uint8Array(first.length + second.length);
  output.set(first, 0);
  output.set(second, first.length);
  return output;
}

async function aesKey(hashKey: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw", encoder.encode(hashKey), { name: "AES-GCM" }, false, ["encrypt", "decrypt"],
  );
}

export async function encryptPayuniInfo(
  values: Record<string, string | number>, hashKey: string, hashIv: string,
): Promise<string> {
  const plain = new URLSearchParams(
    Object.entries(values).map(([key, value]) => [key, String(value)]),
  ).toString();
  const encrypted = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: encoder.encode(hashIv) }, await aesKey(hashKey), encoder.encode(plain),
  ));
  const tag = encrypted.slice(-16);
  const ciphertext = encrypted.slice(0, -16);
  return hexEncode(encoder.encode(`${base64Encode(ciphertext)}:::${base64Encode(tag)}`));
}

export async function decryptPayuniInfo(
  encryptedInfo: string, hashKey: string, hashIv: string,
): Promise<PayuniResult> {
  const [ciphertext, tag, ...unexpected] = decoder.decode(hexDecode(encryptedInfo)).split(":::");
  if (!ciphertext || !tag || unexpected.length) throw new Error("payuni_invalid_encrypt_info");
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: encoder.encode(hashIv) },
    await aesKey(hashKey),
    concatenate(base64Decode(ciphertext), base64Decode(tag)),
  );
  return Object.fromEntries(new URLSearchParams(decoder.decode(plain)).entries());
}

export async function payuniHashInfo(
  encryptedInfo: string, hashKey: string, hashIv: string,
): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(`${hashKey}${encryptedInfo}${hashIv}`));
  return hexEncode(new Uint8Array(digest)).toUpperCase();
}

export function constantTimeEqual(first: string, second: string): boolean {
  if (first.length !== second.length) return false;
  let difference = 0;
  for (let index = 0; index < first.length; index += 1) {
    difference |= first.charCodeAt(index) ^ second.charCodeAt(index);
  }
  return difference === 0;
}

export async function requestFields(request: Request): Promise<Record<string, string>> {
  const contentType = request.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const json = await request.json();
    return Object.fromEntries(Object.entries(json || {}).map(([key, value]) => [key, String(value ?? "")]));
  }
  const form = await request.formData();
  return Object.fromEntries(Array.from(form.entries()).map(([key, value]) => [key, String(value)]));
}

export function payuniResultValues(decoded: PayuniResult): PayuniResult {
  const rawResult = decoded.Result || "";
  if (!rawResult) return decoded;
  try {
    const nested = JSON.parse(rawResult);
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      return { ...decoded, ...Object.fromEntries(Object.entries(nested).map(([key, value]) => [key, String(value ?? "")])) };
    }
  } catch {
    // PAYUNi may return a URL-encoded Result on older UPP versions.
    const params = new URLSearchParams(rawResult);
    if (Array.from(params.keys()).length) return { ...decoded, ...Object.fromEntries(params.entries()) };
  }
  return decoded;
}

export function htmlEscape(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character] || character));
}
