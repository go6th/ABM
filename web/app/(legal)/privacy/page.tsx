// Privacy policy (public). Required by Google and Microsoft before an OAuth app that reads Gmail,
// Drive, Outlook or OneDrive is verified; its "Mail and drive" section states exactly what is read,
// kept and deleted, and the Google API Services User Data Policy (Limited Use) commitment.
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

      <h2>Mail and drive (Gmail, Google Drive, Outlook, OneDrive, SharePoint)</h2>
      <p>
        If you choose to connect your own mailbox or drive, you sign in with Google or Microsoft and grant
        <b> read-only</b> access. You can disconnect at any time from the Mail and drive page.
      </p>
      <ul>
        <li><b>What we read:</b> for email, the sender, recipients, subject, date and the short preview the provider supplies; for files, the file name, owner, and modified date. We do not read email bodies or attachments, and we never send, change or delete anything in your mailbox or drive.</li>
        <li><b>What we keep:</b> only items that match one of the accounts you research (by a company&apos;s email domain or its name). Everything else is discarded straight away.</li>
        <li><b>Who sees it:</b> only you. Matched items are never shown to colleagues, never included in files your team shares or downloads, and never shown to 6th staff.</li>
        <li><b>How it is used:</b> to show you, inside the product, that you have been in touch with an account and to let the answers you ask for cite it. It is not used for advertising, not sold, and not used to train AI models.</li>
        <li><b>Your sign-in tokens</b> are stored encrypted in a secrets store, are never shown in the browser and are never given to an AI model.</li>
        <li><b>Disconnecting</b> revokes our access with the provider where it allows it, destroys the stored token and deletes every item we kept. If your organisation turns the feature off, or your access ends, the same happens automatically.</li>
      </ul>
      <p>
        <b>Google user data.</b> Account Intelligence&apos;s use and transfer to any other app of information
        received from Google APIs will adhere to the{" "}
        <a href="https://developers.google.com/terms/api-services-user-data-policy" rel="noopener noreferrer" target="_blank">Google API Services User Data Policy</a>,
        including the Limited Use requirements.
      </p>
      <p>
        <b>Microsoft user data.</b> Information received through Microsoft Graph is used only to provide the
        Mail and drive feature described above and is handled under the same rules.
      </p>

      <h2>AI processing</h2>
      <p>
        Answers and research summaries are written with AI models operated by our service providers.
        [Confirm with counsel: those providers&apos; terms for business use do not allow training on your data.]
        We do not use your workspace or your mail and drive items to train AI models, and your sign-in
        tokens are never sent to any model.
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
        ends, following our retention schedule. Mail and drive items are deleted when you disconnect. Audit
        records are kept longer where security and legal obligations require.
      </p>

      <h2>Your choices and rights</h2>
      <p>
        You can disconnect mail and drive at any time, and ask your organisation&apos;s administrator to remove
        your access. Depending on where you live you may have rights to access, correct or delete your
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
