import { useEffect, useState } from "react";

/** Reference categorical palette (fixed order) with light/dark steps. */
const LIGHT = {
  series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
  ink: "#0b0b0b",
  ink2: "#52514e",
  grid: "#e4e3df",
  surface: "#fcfcfb",
  band: "#dcdad4",
  // Sequential blue ramp, low -> high (low recedes toward the surface).
  ramp: ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
};
const DARK = {
  series: ["#3987e5", "#d95926", "#199e70", "#c98500"],
  ink: "#ffffff",
  ink2: "#c3c2b7",
  grid: "#383835",
  surface: "#1a1a19",
  band: "#44433f",
  ramp: ["#184f95", "#256abf", "#2a78d6", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
};
export type Palette = typeof LIGHT;

export function usePalette(): Palette {
  const query = typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)");
  const [dark, setDark] = useState<boolean>(query ? query.matches : false);
  useEffect(() => {
    if (!query) return;
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [query]);
  return dark ? DARK : LIGHT;
}

/** Colour for t in [0, 1] on the palette's sequential ramp (linear RGB interpolation). */
export function rampColor(pal: Palette, t: number): string {
  const r = pal.ramp;
  const x = Math.min(Math.max(t, 0), 1) * (r.length - 1);
  const i = Math.min(Math.floor(x), r.length - 2);
  const f = x - i;
  const a = [1, 3, 5].map((j) => parseInt(r[i].slice(j, j + 2), 16));
  const b = [1, 3, 5].map((j) => parseInt(r[i + 1].slice(j, j + 2), 16));
  return `rgb(${a.map((v, j) => Math.round(v + (b[j] - v) * f)).join(",")})`;
}
