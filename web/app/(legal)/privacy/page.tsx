// Privacy policy (public): what the product handles, why, how long it is kept and the choices a
// user has.
// Bracketed values are the owner's to fill (legal name, address, contact); counsel reviews the text.
import type { Metadata } from "next";
import { LegalPage } from "../LegalPage";

export const metadata: Metadata = { title: "Privacy policy · Account Intelligence" };

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy policy" updated="[Effective date]">
      <p>
        Account Intelligence is a research product run by [Company legal name] (&ldquo;6th&rdquo;, &ldquo;we&rdquo;),
        [Registered address]. This policy explains what information the product handles, why, and the
        choices you have. Questions: [privacy contact email].
      </p>

      <h2>What the product does</h2>
      <p>
        Account Intelligence researches companies your team sells to and presents a sourced, graded brief,
        a list of senior people and alerts. Your organisation (our customer) decides who uses it. We act on
        your organisation&apos;s instructions for the information it adds to its workspace.
      </p>

      <h2>Information we handle</h2>
      <ul>
        <li><b>Your account:</b> your name, work email, role and sign-in records, to let you sign in and to keep the service secure.</li>
        <li><b>Your workspace:</b> the accounts you add, questions you ask, answers, files you upload and research results. They are kept in your organisation&apos;s own workspace and are never visible to another customer.</li>
        <li><b>Public research:</b> information about companies and their senior people gathered from public sources, each fact linked to where it came from.</li>
        <li><b>Usage and security records:</b> what was done and when (an audit log), used to run, secure and improve the service.</li>
      </ul>

      <h2>AI processing</h2>
      <p>
        Answers and research summaries are written with AI models operated by our service providers.
        [Confirm with counsel: those providers&apos; terms for business use do not allow training on your data.]
        We do not use your workspace to train AI models.
      </p>

      <h2>Service providers</h2>
      <p>
        We use carefully chosen providers to host the service (Google Cloud), to run AI models and to gather
        public company information. They process data only on our instructions. A list is available to
        customers on request.
      </p>

      <h2>How long we keep information</h2>
      <p>
        Workspace content is kept while your organisation&apos;s subscription is active and deleted after it
        ends, following our retention schedule. Audit records are kept longer where security and legal
        obligations require.
      </p>

      <h2>Your choices and rights</h2>
      <p>
        You can ask your organisation&apos;s administrator to remove your access. Depending on where you live you may have rights to access, correct or delete your
        information, or to object to its use. Contact [privacy contact email]; if we process your information
        for your organisation, we will pass your request to it.
      </p>

      <h2>Security</h2>
      <p>
        Each customer&apos;s workspace is isolated at the database level, access is logged, and data is
        encrypted in transit and at rest.
      </p>

      <h2>Changes</h2>
      <p>We will update this page when the way we handle information changes, and change the date above.</p>
    </LegalPage>
  );
}
