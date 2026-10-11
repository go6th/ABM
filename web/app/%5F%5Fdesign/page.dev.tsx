// Development-only gallery of the workspace design system (/__design). The .dev.tsx extension is
// a page only in development (next.config.ts pageExtensions), so the production build has no
// such route and none of this sample content. tests/unit/ws-build.test.ts checks that.
import "@fontsource-variable/hanken-grotesk/wght.css";
import "@fontsource-variable/source-serif-4/wght.css";
import "@/design/ws/ws.css";
import { Gallery } from "./gallery";

export default function DesignGallery() {
  return <Gallery />;
}
