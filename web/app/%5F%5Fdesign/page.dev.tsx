// Development-only gallery of the design system (/__design). The .dev.tsx extension is a page
// only in development (next.config.ts pageExtensions), so the production build has no such route
// and none of this sample content. tests/unit/ws-build.test.ts checks that.
import { currentTheme } from "@/ui/theme-server";
import { Gallery } from "./gallery";

export default async function DesignGallery() {
  return <Gallery theme={await currentTheme()} />;
}
