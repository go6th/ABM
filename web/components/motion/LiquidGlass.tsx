"use client";

// Animation 3 of 3: Liquid Glass. Two parts.
//
// 1. Light. One pointer listener writes the pointer position to --px/--py on the root element.
//    The glass surfaces (sidebar, top bar) paint a specular rim from those values in CSS, so the
//    light they "bend" follows the person across the whole window.
// 2. Materialize. On every route change the content layer comes in by sharpening out of a blur
//    with a small spring in scale, the way Liquid Glass objects appear by modulating lensing
//    rather than fading. It uses the Web Animations API, so nothing remounts and page state is
//    kept.

import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";
import { prefersReducedMotion } from "./useSpring";

export function GlassLight() {
  useEffect(() => {
    if (prefersReducedMotion()) return;
    const root = document.documentElement;
    let frame = 0;
    let x = 0;
    let y = 0;
    const move = (event: PointerEvent) => {
      x = event.clientX;
      y = event.clientY;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        root.style.setProperty("--px", `${x}px`);
        root.style.setProperty("--py", `${y}px`);
      });
    };
    window.addEventListener("pointermove", move, { passive: true });
    return () => {
      window.removeEventListener("pointermove", move);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
  return null;
}

export function Materialize({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const ref = useRef<HTMLDivElement>(null);
  const first = useRef(true);

  useEffect(() => {
    const node = ref.current;
    if (!node || prefersReducedMotion() || typeof node.animate !== "function") return;
    // The first paint is handled by the hero's own entrance; animate navigations only.
    if (first.current) {
      first.current = false;
      return;
    }
    const animation = node.animate(
      [
        { filter: "blur(10px) saturate(140%)", transform: "scale(0.985) translateY(6px)", opacity: 0.35 },
        { filter: "blur(0px) saturate(100%)", transform: "scale(1.002) translateY(0)", opacity: 1, offset: 0.7 },
        { filter: "blur(0px) saturate(100%)", transform: "scale(1) translateY(0)", opacity: 1 },
      ],
      { duration: 520, easing: "cubic-bezier(0.2, 0.9, 0.25, 1)" },
    );
    return () => animation.cancel();
  }, [pathname]);

  return <div ref={ref} className="materialize">{children}</div>;
}
