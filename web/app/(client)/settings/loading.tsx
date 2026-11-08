// A Settings tab the instant it is clicked: the tabs stay (they belong to the layout) and the
// tab's body shows placeholder blocks until its data arrives.
export default function SettingsTabLoading() {
  return (
    <div className="stack-14" aria-busy="true" aria-label="Loading">
      <div className="skel line w40 h28" />
      <div className="skel line w60" />
      <div className="skel block" />
    </div>
  );
}
