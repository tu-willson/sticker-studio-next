export type NewebPayValues = Record<string, string>;

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function hexEncode(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function hexDecode(value: string): Uint8Array {
  if (!/^[0-9a-f]+$/i.test(value) || value.length % 2 !== 0) {
    throw new Error("newebpay_invalid_hex");
  }
  const bytes = new Uint8Array(value.length / 2);
  for (let index = 0; index < value.length; index += 2) {
    bytes[index / 2] = Number.parseInt(value.slice(index, index + 2), 16);
  }
  return bytes;
}

async function cbcKey(hashKey: string, usage: KeyUsage[]): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", encoder.encode(hashKey), { name: "AES-CBC" }, false, usage);
}

export async function encryptNewebpayCbc(
  values: Record<string, string | number>, hashKey: string, hashIv: string,
): Promise<string> {
  const plain = new URLSearchParams(
    Object.entries(values).map(([key, value]) => [key, String(value)]),
  ).toString();
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-CBC", iv: encoder.encode(hashIv) },
    await cbcKey(hashKey, ["encrypt"]), encoder.encode(plain),
  );
  return hexEncode(new Uint8Array(ciphertext));
}

export async function decryptNewebpayCbc(
  encrypted: string, hashKey: string, hashIv: string,
): Promise<string> {
  const plain = await crypto.subtle.decrypt(
    { name: "AES-CBC", iv: encoder.encode(hashIv) },
    await cbcKey(hashKey, ["decrypt"]), hexDecode(encrypted),
  );
  return decoder.decode(plain);
}

export async function newebpayTradeSha(tradeInfo: string, hashKey: string, hashIv: string): Promise<string> {
  const source = `HashKey=${hashKey}&${tradeInfo}&HashIV=${hashIv}`;
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(source));
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

export function parseNewebpayValues(plain: string): NewebPayValues {
  const trimmed = plain.trim();
  if (!trimmed) throw new Error("newebpay_empty_result");
  const parsed = JSON.parse(trimmed) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("newebpay_invalid_result");
  }
  const output: NewebPayValues = {};
  for (const [key, value] of Object.entries(parsed)) {
    if (key === "Result" && value && typeof value === "object" && !Array.isArray(value)) {
      output.Result = JSON.stringify(value);
      for (const [nestedKey, nestedValue] of Object.entries(value as Record<string, unknown>)) {
        output[nestedKey] = String(nestedValue ?? "");
      }
    } else {
      output[key] = String(value ?? "");
    }
  }
  return output;
}

export function parseNewebpayMpgValues(plain: string): NewebPayValues {
  if (plain.trim().startsWith("{")) return parseNewebpayValues(plain);
  const values = Object.fromEntries(new URLSearchParams(plain).entries());
  if (!Object.keys(values).length) throw new Error("newebpay_empty_result");
  const rawResult = values.Result || "";
  if (!rawResult) return values;
  try {
    const nested = JSON.parse(rawResult) as unknown;
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      return { ...values, ...Object.fromEntries(Object.entries(nested).map(([key, value]) => [key, String(value ?? "")])) };
    }
  } catch {
    const nested = new URLSearchParams(rawResult);
    if (Array.from(nested.keys()).length) return { ...values, ...Object.fromEntries(nested.entries()) };
  }
  return values;
}

export function nestedResult(values: NewebPayValues): NewebPayValues {
  const raw = values.Result || "";
  if (!raw) return values;
  try {
    const nested = JSON.parse(raw) as unknown;
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      return { ...values, ...Object.fromEntries(Object.entries(nested).map(([key, value]) => [key, String(value ?? "")])) };
    }
  } catch {
    // The periodic API documents JSON; do not treat arbitrary text as success.
  }
  return values;
}

export function safeProviderText(value: unknown, maxLength = 180): string {
  return String(value ?? "").replace(/[\u0000-\u001F\u007F]/g, " ").trim().slice(0, maxLength);
}
