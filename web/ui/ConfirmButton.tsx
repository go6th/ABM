"use client";
// A button that asks first, in a dialog (never window.confirm). The trigger and the confirming
// action share a tone; "Keep as is" closes without acting.
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { cx } from "./primitives";

export function ConfirmButton({ label, title, body, action, tone = "primary", size, disabled, onConfirm, children }: {
  label: React.ReactNode; title: string; body: string; action: string; tone?: "primary" | "danger" | "plain";
  size?: "sm"; disabled?: boolean; onConfirm: () => void; children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const toneClass = tone === "danger" ? "danger" : tone === "primary" ? "primary" : undefined;
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger className={cx("btn", tone === "plain" ? undefined : toneClass, size)} disabled={disabled}>{label}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description className="desc">{body}</Dialog.Description>
          {children}
          <div className="actions">
            <Dialog.Close className="btn quiet">Keep as is</Dialog.Close>
            <button type="button" className={cx("btn", tone === "danger" ? "danger" : "primary")} onClick={() => { setOpen(false); onConfirm(); }}>{action}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
