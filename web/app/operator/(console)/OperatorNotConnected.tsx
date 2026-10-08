import { notFound } from "next/navigation";

export function OperatorNotConnected({ title, detail }: { title: string; detail: string }) {
  if (process.env.NODE_ENV === "production") notFound();
  return (
    <section>
      <p className="eyebrow">OPERATOR CONSOLE · DEVELOPMENT</p>
      <div className="operator-page-heading"><div><h1>{title}</h1><p>This screen has no connected backend endpoint yet.</p></div></div>
      <div className="operator-unconnected" role="status">
        <span className="operator-unconnected-mark" aria-hidden="true">i</span>
        <div><strong>Not connected</strong><p>{detail}</p></div>
        <span className="badge tone-butter">Development only</span>
      </div>
    </section>
  );
}
