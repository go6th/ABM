import { Building2, CircleAlert } from "lucide-react";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  GradeBadge,
  Input,
  Notice,
  Progress,
  Skeleton,
  StatusChip,
} from "@/design/components";

export default function DesignCatalogue() {
  return (
    <main className="design-catalogue">
      <header>
        <p className="eyebrow">FOUNDATION</p>
        <h1>Account intelligence design system</h1>
        <p>Reusable, accessible components for evidence-led research workflows.</p>
      </header>

      <section className="design-catalogue-grid" aria-label="Component examples">
        <Card title="Actions and fields" description="Consistent interactions and form feedback.">
          <div className="stack">
            <div className="design-swatch-row">
              <Button variant="primary" disabled><Building2 aria-hidden="true" size={16} /> Primary preview</Button>
              <Button variant="default" disabled>Secondary preview</Button>
              <Button variant="ghost" disabled>Quiet preview</Button>
            </div>
            <Input label="Company domain" placeholder="example.com" required hint="Use the company's canonical website domain." />
            <Input label="Owner email" defaultValue="analyst@example.com" error="Enter a valid work email address." />
          </div>
        </Card>

        <Card title="Evidence and status" description="State always includes readable text, not colour alone.">
          <div className="stack">
            <div className="design-swatch-row">
              <GradeBadge grade="confirmed" />
              <GradeBadge grade="probable" />
              <GradeBadge grade="hypothesis" />
              <GradeBadge grade="lead" />
            </div>
            <div className="design-swatch-row">
              <StatusChip status="done" />
              <StatusChip status="partial" />
              <StatusChip status="blocked" />
              <StatusChip status="none" />
            </div>
            <Progress value={3} max={5} label="Research stages completed" />
            <Notice tone="butter" title="Coverage needs review">
              Some research areas remain unresolved; a finished run does not mean complete coverage.
            </Notice>
          </div>
        </Card>

        <Card title="Loading and empty states">
          <div className="stack">
            <Skeleton variant="title" label="Loading account summary" />
            <Skeleton variant="line" label="Loading research finding" />
            <EmptyState
              title="Empty state preview"
              description="Production screens use an explicit, helpful next step when data is genuinely absent."
              icon={<CircleAlert />}
            />
          </div>
        </Card>
      </section>

      <Card title="Research table" description="Empty-state preview; no example account data is represented as a real finding.">
        <DataTable
          caption="Research table component preview"
          columns={[
            { key: "area", header: "Research area", render: () => null },
            { key: "status", header: "Coverage", render: () => null },
            { key: "grade", header: "Evidence", render: () => null },
            { key: "source", header: "Supporting records", render: () => null },
          ]}
          rows={[]}
          rowKey={() => ""}
          emptyMessage="No account research is loaded in this component catalogue."
        />
      </Card>
    </main>
  );
}
