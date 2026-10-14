// What the operator proxy lets through besides JSON: the one operation whose body is a file (a 6th
// Library package), up to the API's own 100 MB limit.
const BINARY_UPLOADS = new Set(["POST /operator/v1/library/uploads"]);
export const BINARY_LIMIT = 100 * 1024 * 1024;

export function isBinaryUpload(method: string, apiPath: string): boolean {
  return BINARY_UPLOADS.has(`${method} ${apiPath}`);
}
