import { ThemeToggle } from "@/ui/ThemeToggle";
import { currentTheme } from "@/ui/theme-server";

export default async function OperatorAuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <div className="auth-top"><ThemeToggle initial={await currentTheme()} /></div>
      {children}
    </>
  );
}
