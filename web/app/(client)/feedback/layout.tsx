import { SettingsFrame } from "@/components/ws/SettingsFrame";

export default function Layout({ children }: { children: React.ReactNode }) {
  return <SettingsFrame>{children}</SettingsFrame>;
}
