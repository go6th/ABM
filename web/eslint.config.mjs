// ESLint for the client app: Next.js core web vitals and TypeScript flat configs, plus a ban on
// raw HTML injection (VW4: untrusted text is only ever rendered as React text nodes).
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  { ignores: [".next/**", ".next-parity/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts", "components/ProviderKey.tsx", "components/ws/AccountAsk.tsx", "components/ws/AccountList.tsx", "components/ws/CitationChip.tsx", "components/ws/CitedText.tsx", "components/ws/Conversations.tsx", "components/ws/DeepDiveLinks.tsx", "components/ws/Downloads.tsx", "components/ws/EvidenceDrawer.tsx", "components/ws/evidence.ts", "components/ws/FileUploader.tsx", "components/ws/HomeAsk.tsx", "components/ws/InternalView.tsx", "components/ws/RefreshDialog.tsx", "components/ws/RunStatus.tsx", "components/ws/RunStopReport.tsx", "components/ws/SettingsFrame.tsx", "components/ws/SettingsNav.tsx", "components/ws/UsedPanel.tsx", "components/ws/WebOffer.tsx", "ui/ThemeToggle.tsx", "ui/theme.ts", "ui/theme-server.ts", "ui/Scene.tsx", "ui/Feedback.tsx", "ui/ConfirmButton.tsx", "ui/contrast.ts", "ui/Input.tsx", "ui/RunProgress.tsx", "ui/DataTable.tsx", "lib/research-files.ts", "lib/file-generator.ts", "lib/run-stop.ts", "lib/report.ts", "lib/provider-labels.ts", "lib/operator-render.ts", "lib/client.ts", "app/operator/(console)/SupportGrantLookup.tsx", "app/operator/(console)/OperatorStates.tsx", "app/operator/(console)/OperatorCrumb.tsx", "tests/unit/components.test.tsx", "tests/unit/research-files.test.tsx", "tests/unit/run-stop.test.tsx", "tests/unit/ws-components.test.tsx", "tests/unit/ui-tokens.test.ts", "tests/unit/ws-copy.test.ts", "tests/unit/documents.test.tsx", "e2e/parity/**", "e2e/review/**", "playwright.parity.config.ts", "playwright.review.config.ts", "screenshot_test.spec.ts"] },
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "react/no-danger": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
    },
  },
];

export default config;
