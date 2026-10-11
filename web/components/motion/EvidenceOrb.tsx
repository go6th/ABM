"use client";

// Animation 1 of 3: the 3D hero on the workspace overview. A refractive glass core (the
// workspace) with one orbiting node per account the API returned, coloured by that account's
// latest run status. The whole system leans towards the pointer and floats with a slow spring.
//
// Rendering rules that keep it polite:
// - lighting comes from local Lightformers, never a remote HDR (connect-src is 'self');
// - the loop stops when the hero scrolls out of view or the tab is hidden;
// - reduced motion renders one still frame.

import { Canvas, useFrame } from "@react-three/fiber";
import { Environment, Float, Lightformer, MeshTransmissionMaterial } from "@react-three/drei";
import { useMemo, useRef } from "react";
import { Color, type Group, type Mesh } from "three";

export type OrbTone = "mint" | "sky" | "butter" | "rose" | "slate";

const TONE_COLOR: Record<OrbTone, string> = {
  mint: "#3fae84",
  sky: "#5b7fd6",
  butter: "#d9a93a",
  rose: "#d0607e",
  slate: "#8a96a8",
};

interface OrbNode {
  tone: OrbTone;
  radius: number;
  speed: number;
  phase: number;
  tilt: number;
  size: number;
}

function layout(tones: OrbTone[]): OrbNode[] {
  // Deterministic placement (golden-angle phases) so the scene is stable between renders.
  return tones.map((tone, index) => ({
    tone,
    radius: 1.75 + (index % 3) * 0.32,
    speed: 0.16 + (index % 5) * 0.025,
    phase: index * 2.39996,
    tilt: ((index % 7) - 3) * 0.17,
    size: tone === "slate" ? 0.055 : 0.075,
  }));
}

function Orbiters({ nodes, still }: { nodes: OrbNode[]; still: boolean }) {
  const refs = useRef<(Mesh | null)[]>([]);
  useFrame(({ clock }) => {
    const t = still ? 0 : clock.getElapsedTime();
    nodes.forEach((node, index) => {
      const mesh = refs.current[index];
      if (!mesh) return;
      const angle = node.phase + t * node.speed;
      const x = Math.cos(angle) * node.radius;
      const z = Math.sin(angle) * node.radius;
      mesh.position.set(x, Math.sin(angle) * node.radius * node.tilt, z);
    });
  });
  return (
    <>
      {nodes.map((node, index) => (
        <mesh key={index} ref={(mesh) => { refs.current[index] = mesh; }}>
          <sphereGeometry args={[node.size, 24, 24]} />
          <meshStandardMaterial
            color={TONE_COLOR[node.tone]}
            emissive={TONE_COLOR[node.tone]}
            emissiveIntensity={0.55}
            roughness={0.25}
            metalness={0.1}
          />
        </mesh>
      ))}
    </>
  );
}

function Rig({ children, still }: { children: React.ReactNode; still: boolean }) {
  const group = useRef<Group>(null);
  useFrame(({ pointer }, delta) => {
    const node = group.current;
    if (!node || still) return;
    // Critically damped follow, framerate independent.
    const k = 1 - Math.exp(-delta * 3.2);
    node.rotation.y += (pointer.x * 0.45 - node.rotation.y) * k;
    node.rotation.x += (-pointer.y * 0.3 - node.rotation.x) * k;
  });
  return <group ref={group}>{children}</group>;
}

// The transmission pass samples what is behind the glass. The canvas is transparent, so give it
// the hero's own tint to refract instead of an empty (black) buffer.
const GLASS_BACKDROP = new Color("#e9eff9");

function Core() {
  return (
    <mesh>
      <icosahedronGeometry args={[1.12, 16]} />
      <MeshTransmissionMaterial
        samples={6}
        resolution={512}
        thickness={1.1}
        roughness={0.04}
        transmission={1}
        ior={1.32}
        chromaticAberration={0.06}
        anisotropy={0.25}
        distortion={0.35}
        distortionScale={0.4}
        temporalDistortion={0.08}
        backside
        backsideThickness={0.6}
        color="#eef4ff"
        background={GLASS_BACKDROP}
      />
    </mesh>
  );
}

export default function EvidenceOrb({
  tones,
  running,
  still,
}: {
  tones: OrbTone[];
  running: boolean;
  still: boolean;
}) {
  const nodes = useMemo(() => layout(tones), [tones]);
  return (
    <Canvas
      className="orb-canvas"
      dpr={[1, 2]}
      camera={{ position: [0, 0.3, 6.4], fov: 34 }}
      gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      frameloop={still ? "demand" : running ? "always" : "never"}
      aria-hidden="true"
    >
      <ambientLight intensity={0.6} />
      <directionalLight position={[3, 4, 5]} intensity={1.4} />
      <Rig still={still}>
        <Float speed={still ? 0 : 1.2} rotationIntensity={0.35} floatIntensity={0.6}>
          <Core />
        </Float>
        <Orbiters nodes={nodes} still={still} />
      </Rig>
      <Environment resolution={256}>
        <Lightformer form="rect" intensity={3} position={[0, 4, -6]} scale={[10, 2, 1]} />
        <Lightformer form="rect" intensity={2} position={[-6, 1, 2]} scale={[2, 6, 1]} color="#cfe0ff" />
        <Lightformer form="ring" intensity={2.5} position={[5, -1, 3]} scale={3} color="#ffe9cf" />
      </Environment>
    </Canvas>
  );
}
