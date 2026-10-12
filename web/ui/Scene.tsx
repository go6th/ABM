"use client";
// The sign-in stage: decorative CSS 3D (ui/auth.css). The pointer tilts the rig by at most six
// degrees through two CSS variables set with CSSOM (allowed by the CSP, unlike inline style
// attributes). Nothing moves under reduced motion, and the whole scene is hidden from assistive
// technology; the copy beside it carries the meaning.
import { useEffect, useRef, type ReactNode } from "react";

export function Stage({ plane, children }: { plane: "client" | "operator"; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const stage = ref.current;
    if (!stage || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    const move = (event: PointerEvent) => {
      const box = stage.getBoundingClientRect();
      const x = (event.clientX - box.left) / box.width - 0.5;
      const y = (event.clientY - box.top) / box.height - 0.5;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        stage.style.setProperty("--ry", `${(x * 12).toFixed(2)}deg`);
        stage.style.setProperty("--rx", `${(-y * 12).toFixed(2)}deg`);
      });
    };
    const leave = () => {
      stage.style.setProperty("--ry", "0deg");
      stage.style.setProperty("--rx", "0deg");
    };
    stage.addEventListener("pointermove", move);
    stage.addEventListener("pointerleave", leave);
    return () => {
      cancelAnimationFrame(frame);
      stage.removeEventListener("pointermove", move);
      stage.removeEventListener("pointerleave", leave);
    };
  }, []);
  return (
    <div className="stage" data-plane={plane} ref={ref}>
      <div className="scene" aria-hidden="true">{plane === "client" ? <ClientRig /> : <OperatorRig />}</div>
      {children}
    </div>
  );
}

function ClientRig() {
  return (
    <div className="rig">
      <span className="orb orb-1" />
      <div className="slab slab-a">
        <span className="pill fill-peri" />
        <span className="bar w80" />
        <span className="bar w60" />
        <div className="dotrow"><i className="fill-mint" /><i className="fill-lilac" /><i className="fill-peach" /></div>
      </div>
      <div className="slab slab-b">
        <span className="pill fill-mint" />
        <span className="bar w60" />
        <span className="bar w40" />
      </div>
      <div className="slab slab-c">
        <span className="pill fill-butter" />
        <span className="bar w80" />
        <span className="bar w60" />
      </div>
      <div className="slab slab-d">
        <svg className="ring" viewBox="0 0 80 80">
          <circle className="track" cx="40" cy="40" r="32" />
          <circle className="arc-1" cx="40" cy="40" r="32" strokeDasharray="120 201" strokeLinecap="round" transform="rotate(-90 40 40)" />
          <circle className="arc-2" cx="40" cy="40" r="32" strokeDasharray="50 201" strokeDashoffset="-128" strokeLinecap="round" transform="rotate(-90 40 40)" />
        </svg>
      </div>
      <span className="orb orb-2" />
    </div>
  );
}

function OperatorRig() {
  return (
    <div className="rig console">
      {Array.from({ length: 9 }, (_, i) => <span key={i} className="cell" />)}
    </div>
  );
}
