import Link from "next/link";
import { StateCard } from "@/ui/design";

export default function NotFound() {
  return (
    <div className="pg">
      <StateCard title="Page not found" body="This page does not exist, or it is not in your workspace." action={<Link className="btn" href="/">Back to Ask</Link>} />
    </div>
  );
}
