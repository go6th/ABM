"use client";

// Wrapper for the 3D hero. three.js is loaded only here, on demand and client side, so it stays
// out of every other route's first-load JavaScript. Without WebGL the hero shows a CSS glass
// orb instead, so the page never depends on the 3D layer.

import dynamic from "next/dynamic";
import { Component, useEffect, useRef, useState, type ReactNode } from "react";
import type { OrbTone } from "./EvidenceOrb";
import { prefersReducedMotion } from "./useSpring";

const EvidenceOrb = dynamic(() => import("./EvidenceOrb"), {
  ssr: false,
  loading: () => <span className="orb-fallback" aria-hidden="true" />,
});

class OrbBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override render() {
    return this.state.failed ? <span className="orb-fallback" aria-hidden="true" /> : this.props.children;
  }
}

function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

export function EvidenceHero({ tones, children }: { tones: OrbTone[]; children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const [ready, setReady] = useState<{ webgl: boolean; still: boolean } | null>(null);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const frame = requestAnimationFrame(() => setReady({ webgl: webglAvailable(), still: prefersReducedMotion() }));
    const node = ref.current;
    if (!node) return () => cancelAnimationFrame(frame);
    const observer = new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? true), { threshold: 0.05 });
    observer.observe(node);
    const onVisibility = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <section ref={ref} className="evidence-hero" aria-labelledby="workspace-title">
      <div className="evidence-hero-copy">{children}</div>
      <div className="evidence-hero-stage">
        {ready?.webgl ? (
          <OrbBoundary>
            <EvidenceOrb tones={tones} running={visible} still={ready.still} />
          </OrbBoundary>
        ) : (
          <span className="orb-fallback" aria-hidden="true" />
        )}
      </div>
    </section>
  );
}
