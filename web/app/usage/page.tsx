"use client";
// Plan usage in units, never cost or credits (MT1, D-63).
import { useEffect, useState } from "react";
import { api, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";

interface Item { dimension: string; used: number; limit: number | null; unit: string; percent: number | null }

function Usage({ s }: { s: Session }) {
  const [items, setItems] = useState<Item[]>([]);
  const [period, setPeriod] = useState("");
  useEffect(() => { if (s.role === "admin") void api<{ period: string; items: Item[] }>("GET", "/v1/usage").then((b) => { setItems(b.items); setPeriod(b.period); }); }, [s.role]);
  if (s.role !== "admin") return <p>Only admins see plan usage.</p>;
  return (
    <section>
      <h1>Usage {period}</h1>
      <table className="data"><thead><tr><th scope="col">Allowance</th><th scope="col">Used</th><th scope="col">Plan</th><th scope="col">Share used</th></tr></thead>
        <tbody>{items.map((i) => (
          <tr key={i.dimension}><td>{i.dimension.replaceAll("_", " ")}</td><td>{i.used} {i.unit}</td><td>{i.limit ?? "not set"}</td><td>{i.percent === null ? "" : `${i.percent}%`}</td></tr>
        ))}</tbody></table>
    </section>
  );
}

export default function UsagePage() { return <SignedIn>{(s) => <Usage s={s} />}</SignedIn>; }
