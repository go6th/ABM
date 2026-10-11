"use client";

// A tiny spring integrator shared by the motion components. Apple's motion feels "gel-like"
// because it is spring-driven rather than eased, so every pointer-following value here settles
// with stiffness and damping instead of a fixed-duration curve.
//
// Values are written to CSS custom properties through the CSSOM (element.style.setProperty).
// The CSP has no 'unsafe-inline' for styles, and CSSOM writes are allowed where style
// attributes in server-rendered HTML are not.

export interface SpringOptions {
  stiffness?: number;
  damping?: number;
}

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Drives a set of named numbers towards targets with a damped spring and calls `write` on every
 * frame until all of them rest. Returns setters for the targets and a disposer.
 */
export function createSpring<K extends string>(
  initial: Record<K, number>,
  write: (values: Record<K, number>) => void,
  { stiffness = 170, damping = 22 }: SpringOptions = {},
) {
  const value = { ...initial };
  const velocity = Object.fromEntries(Object.keys(initial).map((k) => [k, 0])) as Record<K, number>;
  const target = { ...initial };
  let frame = 0;
  let last = 0;

  const step = (now: number) => {
    const dt = Math.min(0.032, last ? (now - last) / 1000 : 1 / 60);
    last = now;
    let moving = false;
    for (const key of Object.keys(value) as K[]) {
      const force = -stiffness * (value[key] - target[key]) - damping * velocity[key];
      velocity[key] += force * dt;
      value[key] += velocity[key] * dt;
      if (Math.abs(velocity[key]) > 0.001 || Math.abs(value[key] - target[key]) > 0.001) moving = true;
      else value[key] = target[key];
    }
    write(value);
    frame = moving ? requestAnimationFrame(step) : 0;
    if (!moving) last = 0;
  };

  return {
    set(next: Partial<Record<K, number>>) {
      Object.assign(target, next);
      if (!frame) frame = requestAnimationFrame(step);
    },
    dispose() {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
    },
  };
}
