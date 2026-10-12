"use client";
// A labelled text input with an optional hint and error, wired with aria-describedby.
import { useId, type InputHTMLAttributes } from "react";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  error?: string;
}

export function Input({ id, label, hint, error, className = "", required, ...props }: InputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [props["aria-describedby"], hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className="field">
      <label htmlFor={inputId}>{label}{required ? <span aria-hidden="true"> *</span> : null}</label>
      <input
        {...props}
        id={inputId}
        className={["input", className].filter(Boolean).join(" ")}
        required={required}
        aria-invalid={error ? true : props["aria-invalid"]}
        aria-describedby={describedBy}
      />
      {hint ? <span id={hintId} className="hint">{hint}</span> : null}
      {error ? <span id={errorId} className="field-error">{error}</span> : null}
    </div>
  );
}
