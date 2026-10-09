import { createHmac, timingSafeEqual } from "node:crypto";

export interface StaffSessionData {
  staff_id: string;
  email: string;
  role: "operator" | "support";
  mfa_fresh: boolean;
  exp: number;
  created_at: number;
}

const DEFAULT_SECRET = "dev-operator-insecure-secret-key-32-chars-minimum-safe";
const SECRET = process.env.OPERATOR_SESSION_SECRET || DEFAULT_SECRET;

export function signStaffSession(data: Omit<StaffSessionData, "created_at">): string {
  const full: StaffSessionData = {
    ...data,
    created_at: Date.now(),
  };
  const json = Buffer.from(JSON.stringify(full)).toString("base64url");
  const signature = createHmac("sha256", SECRET).update(json).digest("base64url");
  return `staff.${json}.${signature}`;
}

export function verifyStaffSession(token: string | undefined | null): StaffSessionData | null {
  if (!token || typeof token !== "string" || !token.startsWith("staff.")) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const json = parts[1];
  const signature = parts[2];
  if (!json || !signature) return null;

  try {
    const expectedSig = createHmac("sha256", SECRET).update(json).digest("base64url");
    const a = Buffer.from(signature);
    const b = Buffer.from(expectedSig);
    if (a.length !== b.length || !timingSafeEqual(a, b)) return null;

    const payload = JSON.parse(Buffer.from(json, "base64url").toString("utf8")) as StaffSessionData;
    if (payload.exp && Date.now() / 1000 > payload.exp) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

export function sanitizeStaffId(email: string): string {
  const parts = email.trim().toLowerCase().split("@");
  const prefix = parts[0] || "staff_user";
  return prefix.replace(/[^a-z0-9_-]/g, "_") || "staff_user";
}
