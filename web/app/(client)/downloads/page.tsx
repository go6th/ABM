"use client";
// Files: the Word, Excel and PDF packages built for an account (GET /v1/exports?domain=),
// newest first, with each delivered file. Names and sizes only; never cell content.
import Link from "next/link";
import { useState } from "react";
import { Download, FileText } from "lucide-react";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { day, humanize } from "@/lib/ws-format";
import { Badge, EmptyState, LoadError, PageHead, PageSkeleton } from "@/ui/primitives";
import type { AccountPage } from "@/lib/gen/api.gen";

interface File { index: number; kind: string; name: string; size: number }
interface Pkg { package_id: string; run_id: string; kind: string; status: string; reason: string | null; created_at: string | null; files: File[] }

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Packages({ domain }: { domain: string }) {
  const list = useApi<{ packages: Pkg[] }>(`/v1/exports?domain=${encodeURIComponent(domain)}`);
  if (list.error) return <LoadError reference={null} retryHref="/downloads" />;
  if (!list.data) return <PageSkeleton />;
  if (list.data.packages.length === 0) {
    return <EmptyState title="No files for this account yet" body="Build Word and Excel files or a summary PDF from the account page." icon={<FileText size={20} strokeWidth={1.8} />} action={{ href: `/accounts/${encodeURIComponent(domain)}`, label: "Open the account" }} />;
  }
  return (
    <ul className="stack" aria-label={`Files for ${domain}`}>
      {list.data.packages.map((p) => (
        <li key={p.package_id} className="card stack">
          <div className="row between">
            <div>
              <p className="card-title">{p.kind === "pdf" ? "Account summary PDF" : "Word and Excel files"}</p>
              <p className="card-sub">{p.created_at ? `Built ${day(p.created_at)}` : "Build time unknown"}</p>
            </div>
            <Badge tone={p.status === "delivered" ? "mint" : p.status === "failed" || p.status === "withheld" ? "rose" : "butter"} dot>{humanize(p.status)}</Badge>
          </div>
          {p.status === "delivered" && p.files.length ? (
            <ul className="group">
              {p.files.map((f) => (
                <li key={f.index}>
                  <a className="group-item" href={`/v1/exports/${encodeURIComponent(p.package_id)}/files/${f.index}`}>
                    <FileText size={18} strokeWidth={1.7} aria-hidden="true" />
                    <span className="grow"><span className="title">{cellText(f.name)}</span><br /><span className="sub">{size(f.size)}</span></span>
                    <Download size={16} strokeWidth={1.7} aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          ) : p.reason ? <p className="t-footnote ink-2">{humanize(p.reason)}</p> : null}
        </li>
      ))}
    </ul>
  );
}

function Downloads() {
  const accounts = useApi<AccountPage>("/v1/accounts?limit=200");
  const domains = (accounts.data?.accounts ?? []).map((a) => a.domain).sort();
  const [picked, setPicked] = useState<string | null>(null);
  const domain = picked ?? domains[0] ?? null;
  return (
    <div className="content">
      <PageHead title="Files" sub="Word, Excel and PDF files built for your accounts. Build new ones from an account page." />
      {accounts.error ? <LoadError reference={null} retryHref="/downloads" /> : !accounts.data ? <PageSkeleton /> : domains.length === 0 ? (
        <EmptyState title="No accounts yet" body="Add an account and research it to build files." action={{ href: "/accounts#add", label: "Add account" }} />
      ) : (
        <div className="stack">
          <div className="field">
            <label htmlFor="dl-account">Account</label>
            <select id="dl-account" className="select" value={domain ?? ""} onChange={(e) => setPicked(e.target.value)}>
              {domains.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          {domain ? <Packages key={domain} domain={domain} /> : null}
          <p className="t-footnote ink-2">Files are built from the latest research. <Link className="link" href={domain ? `/accounts/${encodeURIComponent(domain)}` : "/accounts"}>Open the account</Link> to build new ones.</p>
        </div>
      )}
    </div>
  );
}

export default function DownloadsPage() { return <SignedIn>{() => <Downloads />}</SignedIn>; }
