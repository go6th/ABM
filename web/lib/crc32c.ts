// CRC32C (Castagnoli, reflected polynomial 0x82F63B78): the checksum Cloud Storage reports for an
// object as ``x-goog-hash: crc32c=<base64 of the 4 big-endian bytes>``. The browser computes it
// over the file before sending, and the API's complete call compares it with the stored object.
// Pure TypeScript, table driven, no dependency.

const TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? (c >>> 1) ^ 0x82f63b78 : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

/** Continue a CRC32C over more bytes (start with 0); returns the unsigned 32-bit checksum. */
export function crc32cUpdate(crc: number, data: Uint8Array): number {
  let c = ~crc >>> 0;
  for (let i = 0; i < data.length; i += 1) c = (TABLE[(c ^ (data[i] ?? 0)) & 0xff] ?? 0) ^ (c >>> 8);
  return ~c >>> 0;
}

/** The 4 big-endian bytes of a checksum as base64 (the GCS ``x-goog-hash`` form). */
export function crc32cBase64(crc: number): string {
  const bytes = [(crc >>> 24) & 0xff, (crc >>> 16) & 0xff, (crc >>> 8) & 0xff, crc & 0xff];
  return btoa(String.fromCharCode(...bytes));
}

/** CRC32C of a whole file or blob, read in pieces so a large file never sits in memory at once. */
export async function crc32cOfBlob(blob: Blob, piece: number = 8 * 1024 * 1024): Promise<string> {
  let crc = 0;
  // LOOP(crc32c pieces): cap=size / piece pieces; exit=end of the blob
  for (let offset = 0; offset < blob.size; offset += piece) {
    crc = crc32cUpdate(crc, new Uint8Array(await blob.slice(offset, offset + piece).arrayBuffer()));
  }
  return crc32cBase64(crc);
}
