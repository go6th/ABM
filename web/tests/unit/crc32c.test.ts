import { describe, expect, it } from "vitest";
import { crc32cBase64, crc32cOfBlob, crc32cUpdate } from "@/lib/crc32c";

const bytes = (text: string) => new TextEncoder().encode(text);

describe("crc32c", () => {
  it("matches the standard check value", () => {
    expect(crc32cUpdate(0, bytes("123456789"))).toBe(0xe3069283);
  });
  it("is zero for no bytes", () => {
    expect(crc32cUpdate(0, new Uint8Array())).toBe(0);
  });
  it("encodes the 4 big-endian bytes as base64, the x-goog-hash form", () => {
    expect(crc32cBase64(0xe3069283)).toBe("4waSgw==");
    expect(crc32cBase64(0)).toBe("AAAAAA==");
  });
  it("can be fed in pieces", () => {
    const first = crc32cUpdate(0, bytes("12345"));
    expect(crc32cUpdate(first, bytes("6789"))).toBe(0xe3069283);
  });
  it("reads a file in pieces and gives the same answer", async () => {
    const data = new Uint8Array(100_000).map((_, i) => (i * 31) % 251);
    const whole = crc32cUpdate(0, data);
    expect(await crc32cOfBlob(new Blob([data]), 8192)).toBe(crc32cBase64(whole));
    expect(await crc32cOfBlob(new Blob([bytes("123456789")]))).toBe("4waSgw==");
    expect(await crc32cOfBlob(new Blob([]))).toBe("AAAAAA==");
  });
});
