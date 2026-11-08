// An account tab the instant it is clicked: the header and tabs stay (they belong to the layout)
// and the tab's body shows placeholder rows until its data arrives.
export default function AccountTabLoading() {
  return (
    <div className="pg" aria-busy="true" aria-label="Loading">
      <div className="card pad stack-14">
        <div className="skel line w40" />
        <div className="skel row" />
        <div className="skel row" />
        <div className="skel row" />
        <div className="skel line w60" />
      </div>
    </div>
  );
}
