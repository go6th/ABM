// Shown the instant a page is asked for, before its data arrives: the page's frame (title and
// cards) in placeholder bars, so a click always answers at once.
export default function ClientLoading() {
  return (
    <div className="pg" aria-busy="true" aria-label="Loading">
      <div className="head"><div className="ttl stack-8"><div className="skel line w40 h28" /><div className="skel line w60" /></div><div className="skel btn-w" /></div>
      <div className="kpi-tiles"><div className="skel tile" /><div className="skel tile" /><div className="skel tile" /><div className="skel tile" /></div>
      <div className="card pad stack-14"><div className="skel line w40" /><div className="skel line" /><div className="skel line w80" /><div className="skel line w60" /></div>
    </div>
  );
}
