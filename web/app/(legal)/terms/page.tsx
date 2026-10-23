// Terms of service (public), named on the OAuth consent screens. Bracketed values are the owner's
// to fill; counsel reviews the text before it is relied on.
import type { Metadata } from "next";
import { LegalPage } from "../LegalPage";

export const metadata: Metadata = { title: "Terms of service · Account Intelligence" };

export default function TermsPage() {
  return (
    <LegalPage title="Terms of service" updated="[Effective date]">
      <p>
        These terms apply to your use of Account Intelligence, provided by [Company legal name]
        (&ldquo;6th&rdquo;). Your organisation&apos;s agreement with 6th governs its subscription; where the two
        differ, that agreement wins.
      </p>
      <h2>Using the service</h2>
      <ul>
        <li>Use it only for your organisation&apos;s business research, and keep your sign-in to yourself.</li>
        <li>Do not use it to send unsolicited messages, to harass anyone, or to break any law, including data protection law.</li>
        <li>Research is graded (Confirmed, Probable, Hypothesis, Lead) and sourced: check what matters before you rely on it.</li>
      </ul>
      <h2>Your content</h2>
      <p>
        Content you and your organisation add stays yours. You give 6th the rights needed to run the service
        for you. Connected mail and drive items are handled as the{" "}
        <a href="/privacy">privacy policy</a> describes.
      </p>
      <h2>Availability and changes</h2>
      <p>We work to keep the service available and secure, and may change features over time. We will tell your organisation about significant changes.</p>
      <h2>Ending use</h2>
      <p>Your access ends when your organisation removes it or its subscription ends. Your data is then handled as the privacy policy describes.</p>
      <h2>Liability</h2>
      <p>[To be completed by counsel: warranties, limitation of liability and governing law ([Jurisdiction]).]</p>
      <h2>Contact</h2>
      <p>[Support contact email]</p>
    </LegalPage>
  );
}
