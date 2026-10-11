"use client";

// Animation 2 of 3: a 3D parallax tilt, after the Apple TV focus effect. The card leans towards
// the pointer, a specular sheen follows it across the surface, and children marked with
// data-depth float at different heights so the card reads as layered glass, not a flat panel.
// Keyboard focus gives a gentle lift so the effect is not pointer-only. Touch and reduced motion
// get a still card.

import { useEffect, useRef, type HTMLAttributes, type ReactNode } from "react";
import { createSpring, prefersReducedMotion } from "./useSpring";

interface TiltCardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  /** Maximum lean in degrees. */
  max?: number;
}

export function TiltCard({ children, max = 9, className, ...rest }: TiltCardProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node || prefersReducedMotion()) return;

    const spring = createSpring(
      { rx: 0, ry: 0, mx: 50, my: 50, lift: 0, shine: 0 },
      (v) => {
        node.style.setProperty("--rx", `${v.rx.toFixed(3)}deg`);
        node.style.setProperty("--ry", `${v.ry.toFixed(3)}deg`);
        node.style.setProperty("--mx", `${v.mx.toFixed(2)}%`);
        node.style.setProperty("--my", `${v.my.toFixed(2)}%`);
        node.style.setProperty("--lift", v.lift.toFixed(3));
        node.style.setProperty("--shine", v.shine.toFixed(3));
      },
      { stiffness: 210, damping: 20 },
    );

    const move = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      const box = node.getBoundingClientRect();
      const x = (event.clientX - box.left) / box.width;
      const y = (event.clientY - box.top) / box.height;
      spring.set({
        ry: (x - 0.5) * 2 * max,
        rx: -(y - 0.5) * 2 * max,
        mx: x * 100,
        my: y * 100,
        lift: 1,
        shine: 1,
      });
    };
    const leave = () => spring.set({ rx: 0, ry: 0, mx: 50, my: 50, lift: 0, shine: 0 });
    const focusIn = () => spring.set({ rx: 2, ry: -3, lift: 1, shine: 0.6 });

    node.addEventListener("pointermove", move);
    node.addEventListener("pointerleave", leave);
    node.addEventListener("focusin", focusIn);
    node.addEventListener("focusout", leave);
    return () => {
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerleave", leave);
      node.removeEventListener("focusin", focusIn);
      node.removeEventListener("focusout", leave);
      spring.dispose();
    };
  }, [max]);

  return (
    <div ref={ref} className={`tilt ${className ?? ""}`} {...rest}>
      <div className="tilt-body">{children}</div>
    </div>
  );
}
