# ADR-0103: Frontend depth and 3D budget (D-93)

Status: proposal

Create depth primarily with layered CSS surfaces and shadows. Use WebGL only in an optional, lazy-loaded sign-in or empty-state hero with a static fallback. Reduced motion must disable all decorative movement and the hero scene.

Current implementation uses CSS depth only. There is no WebGL hero scene or complete reduced-motion visual test yet.
