/**
 * The downtown skyline that closes the hero.
 *
 * Drawn as one inline SVG rather than an image, so it scales to any width,
 * themes itself from the same CSS variables as everything else, and costs no
 * extra request. Nothing here is interactive, so it stays a server component
 * and ships no JavaScript at all.
 *
 * Everything is deterministic. The silhouette and the windows look scattered
 * but come from a seeded PRNG, because `Math.random()` in a server component
 * produces one layout on the server and a different one in the browser, and
 * React throws a hydration mismatch when they disagree.
 *
 * Three things carry the look:
 *
 * 1. **Width is a property of the building type, not one random band.** An
 *    earlier version drew every tower from the same 24-76 unit range, so height
 *    was the only thing that varied and the result read as a bar chart. A spire
 *    is 16-32 units here and a deco block is 72-112.
 *
 * 2. **Thirteen silhouettes instead of one rectangle.** Setbacks, ziggurats,
 *    tapers, twin peaks, domes, notched corners, rooftop water towers, sheared
 *    crowns.
 *
 * 3. **Construction, drawn in outline.** Two of the types are unfinished
 *    buildings: an open steel frame above the finished floors, and a tower
 *    crane with a jib and a counterweight. The scaffolding forms part of the
 *    silhouette rather than being a texture on a facade - at hero size a facade
 *    pattern just disappears into the window grid.
 */

/** Small seeded PRNG (mulberry32), so server and browser draw the same city. */
function seededRandom(seed: number): () => number {
  let state = seed;
  return () => {
    state |= 0;
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Round to two decimals, so server and browser serialise identical strings. */
function q(n: number): number {
  return Math.round(n * 100) / 100;
}

const VIEW_W = 2400;
// Identical to the viewBox the previous skyline used, and it has to stay that
// way. The glow wash and the star field are both sized as fractions of VIEW_H,
// so a taller box silently grows the pink haze and spreads it further up over
// the dot grid underneath. The crane masts get their headroom from a lower
// height clamp instead.
const VIEW_H = 255;
const BASELINE = VIEW_H;

const LIGHT = "var(--color-neon)";
const MASS = "url(#tower)";
const STEEL = "var(--color-edge-bright)";

/** A filled mass. Windows are generated inside these. */
interface Solid {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A thin structural element: lattice, mast, crane arm. Never gets windows. */
interface Detail {
  x: number;
  y: number;
  w: number;
  h: number;
  opacity?: number;
}

interface Shape {
  solids: Solid[];
  details: Detail[];
  /** Polygon points, for tapered and angled crowns. */
  polys?: string[];
  /** Circles, for domes and crane counterweights. */
  circles?: { cx: number; cy: number; r: number; fill?: string }[];
}

/**
 * Building archetypes.
 *
 * `weight` controls how often each appears; `minW`/`maxW` are what actually
 * breaks the sameness, since a spire and a deco block are different *widths*,
 * not just different heights.
 */
const TYPES = [
  { name: "flat", weight: 16, minW: 26, maxW: 66 },
  { name: "setback", weight: 13, minW: 36, maxW: 76 },
  { name: "ziggurat", weight: 8, minW: 50, maxW: 92 },
  { name: "tapered", weight: 8, minW: 26, maxW: 52 },
  { name: "spire", weight: 8, minW: 16, maxW: 32 },
  { name: "twin", weight: 6, minW: 46, maxW: 84 },
  { name: "domed", weight: 5, minW: 30, maxW: 56 },
  { name: "notched", weight: 7, minW: 36, maxW: 70 },
  { name: "angled", weight: 6, minW: 30, maxW: 60 },
  { name: "watertower", weight: 8, minW: 48, maxW: 96 },
  { name: "lattice", weight: 4, minW: 30, maxW: 56 }, // under construction
  { name: "crane", weight: 3, minW: 34, maxW: 62 }, // under construction
  { name: "deco", weight: 4, minW: 72, maxW: 112 },
] as const;

type TypeName = (typeof TYPES)[number]["name"];

const TOTAL_WEIGHT = TYPES.reduce((sum, t) => sum + t.weight, 0);

function pickType(random: () => number): (typeof TYPES)[number] {
  let roll = random() * TOTAL_WEIGHT;
  for (const type of TYPES) {
    roll -= type.weight;
    if (roll <= 0) return type;
  }
  return TYPES[0]!;
}

/**
 * An open steel frame: uprights and floor rails.
 *
 * Kept fine and faint on purpose. A coarse, bright grid stops reading as
 * scaffolding and starts reading as a wireframe box sitting on the skyline,
 * which is louder than anything else in the hero. The bay spacing is tied to
 * the window pitch below it, so the frame looks like the same building
 * continued rather than a different object.
 */
function lattice(x: number, y: number, w: number, h: number): Detail[] {
  const out: Detail[] = [];
  const bays = Math.max(2, Math.round(w / 11));
  const bayW = w / bays;

  for (let i = 0; i <= bays; i += 1) {
    out.push({ x: x + i * bayW - 0.6, y, w: 1.2, h, opacity: 0.4 });
  }
  const rails = Math.max(2, Math.round(h / 9));
  for (let i = 0; i <= rails; i += 1) {
    out.push({ x, y: y + (i * h) / rails, w, h: 1, opacity: 0.3 });
  }
  return out;
}

/**
 * Build one building's geometry.
 *
 * Everything is expressed as rectangles, polygons and circles so the whole
 * skyline stays a single inline SVG with no library and no images.
 */
function shapeFor(
  type: TypeName,
  x: number,
  w: number,
  h: number,
  random: () => number,
): Shape {
  const top = BASELINE - h;
  const solids: Solid[] = [];
  const details: Detail[] = [];
  const polys: string[] = [];
  const circles: Shape["circles"] = [];

  switch (type) {
    case "setback": {
      const lower = h * 0.66;
      solids.push({ x, y: BASELINE - lower, w, h: lower });
      const upperW = w * (0.5 + random() * 0.18);
      const offset = (w - upperW) * (random() > 0.5 ? 0.15 : 0.85);
      solids.push({ x: x + offset, y: top, w: upperW, h: h - lower });
      break;
    }

    case "ziggurat": {
      // Three stacked stages, each narrower — the classic Art Deco profile.
      let currentW = w;
      let currentX = x;
      let y = BASELINE;
      const stages = 3;
      for (let i = 0; i < stages; i += 1) {
        const stageH = (h / stages) * (i === 0 ? 1.25 : 0.9);
        solids.push({ x: currentX, y: y - stageH, w: currentW, h: stageH });
        y -= stageH;
        const shrink = currentW * 0.22;
        currentW -= shrink;
        currentX += shrink / 2;
      }
      break;
    }

    case "tapered": {
      const shoulder = BASELINE - h * 0.62;
      const inset = w * 0.22;
      polys.push(
        `${x},${BASELINE} ${x},${shoulder} ${x + inset},${top} ` +
          `${x + w - inset},${top} ${x + w},${shoulder} ${x + w},${BASELINE}`,
      );
      // A shorter solid behind the taper, so windows have somewhere to sit.
      solids.push({ x: x + inset, y: shoulder, w: w - inset * 2, h: h * 0.62 });
      break;
    }

    case "spire": {
      solids.push({ x, y: top, w, h });
      const mastH = 26 + random() * 20;
      polys.push(
        `${x + w * 0.5},${top - mastH} ${x + w * 0.82},${top} ${x + w * 0.18},${top}`,
      );
      circles.push({ cx: x + w * 0.5, cy: top - mastH - 3, r: 2, fill: LIGHT });
      break;
    }

    case "twin": {
      const baseH = h * 0.45;
      solids.push({ x, y: BASELINE - baseH, w, h: baseH });
      const prongW = w * 0.4;
      const leftH = h;
      const rightH = h * (0.78 + random() * 0.18);
      solids.push({ x, y: BASELINE - leftH, w: prongW, h: leftH - baseH + 1 });
      solids.push({
        x: x + w - prongW,
        y: BASELINE - rightH,
        w: prongW,
        h: rightH - baseH + 1,
      });
      break;
    }

    case "domed": {
      solids.push({ x, y: top, w, h });
      circles.push({ cx: x + w / 2, cy: top, r: w / 2, fill: MASS });
      break;
    }

    case "notched": {
      // A chunk removed from one upper corner.
      const notchW = w * 0.34;
      const notchH = h * 0.3;
      const left = random() > 0.5;
      solids.push({ x, y: top + notchH, w, h: h - notchH });
      solids.push({
        x: left ? x + notchW : x,
        y: top,
        w: w - notchW,
        h: notchH,
      });
      break;
    }

    case "angled": {
      const lowSide = top + h * 0.24;
      const lean = random() > 0.5;
      polys.push(
        lean
          ? `${x},${BASELINE} ${x},${lowSide} ${x + w},${top} ${x + w},${BASELINE}`
          : `${x},${BASELINE} ${x},${top} ${x + w},${lowSide} ${x + w},${BASELINE}`,
      );
      solids.push({ x, y: lowSide, w, h: BASELINE - lowSide });
      break;
    }

    case "watertower": {
      solids.push({ x, y: top, w, h });
      // The New York rooftop tank: a drum on four legs.
      const tankW = 16;
      const tankH = 14;
      const legH = 7;
      const tx = x + w * (0.2 + random() * 0.55);
      details.push({ x: tx, y: top - legH - tankH, w: tankW, h: tankH, opacity: 0.85 });
      polys.push(
        `${tx},${top - legH - tankH} ${tx + tankW / 2},${top - legH - tankH - 7} ` +
          `${tx + tankW},${top - legH - tankH}`,
      );
      for (const legX of [tx + 1.5, tx + tankW - 3]) {
        details.push({ x: legX, y: top - legH, w: 1.6, h: legH, opacity: 0.6 });
      }
      break;
    }

    case "lattice": {
      // Finished floors below, exposed steel frame above.
      const builtH = h * (0.72 + random() * 0.12);
      solids.push({ x, y: BASELINE - builtH, w, h: builtH });
      details.push(...lattice(x, BASELINE - h, w, h - builtH));
      break;
    }

    case "crane": {
      const builtH = h * (0.74 + random() * 0.12);
      solids.push({ x, y: BASELINE - builtH, w, h: builtH });
      details.push(...lattice(x, BASELINE - h, w, h - builtH));

      // Tower crane: mast rising past the frame, jib one way, counter-jib the
      // other with a counterweight block. Read entirely in silhouette.
      const mastX = x + w * (0.3 + random() * 0.4);
      // Clamped clear of the top edge: the tallest tower plus the longest mast
      // would otherwise put the jib outside the viewBox and lose its top.
      const mastTop = Math.max(5, BASELINE - h - (26 + random() * 24));
      details.push({
        x: mastX - 1,
        y: mastTop,
        w: 2,
        h: BASELINE - builtH - mastTop,
        opacity: 0.55,
      });

      const jib = 40 + random() * 34;
      const counter = jib * 0.42;
      const armY = mastTop + 5;
      const side = random() > 0.5 ? 1 : -1;
      details.push({
        x: side > 0 ? mastX : mastX - jib,
        y: armY,
        w: jib,
        h: 1.8,
        opacity: 0.55,
      });
      details.push({
        x: side > 0 ? mastX - counter : mastX,
        y: armY,
        w: counter,
        h: 1.8,
        opacity: 0.55,
      });
      // Counterweight.
      details.push({
        x: side > 0 ? mastX - counter : mastX + counter - 9,
        y: armY - 4,
        w: 8,
        h: 6,
        opacity: 0.6,
      });
      // Hoist cable and hook.
      const hookX = side > 0 ? mastX + jib * 0.72 : mastX - jib * 0.72;
      details.push({ x: hookX, y: armY, w: 0.8, h: 16 + random() * 14, opacity: 0.35 });
      circles.push({ cx: mastX + 0.5, cy: mastTop - 2, r: 2, fill: LIGHT });
      break;
    }

    case "deco": {
      // Wide base with a central tower — the pre-war office block.
      const baseH = h * 0.5;
      solids.push({ x, y: BASELINE - baseH, w, h: baseH });
      const towerW = w * 0.36;
      solids.push({
        x: x + (w - towerW) / 2,
        y: top,
        w: towerW,
        h: h - baseH + 1,
      });
      // Shoulder blocks either side of the tower.
      const shoulderW = w * 0.16;
      const shoulderH = (h - baseH) * 0.45;
      solids.push({
        x: x + w * 0.06,
        y: BASELINE - baseH - shoulderH,
        w: shoulderW,
        h: shoulderH,
      });
      solids.push({
        x: x + w - w * 0.06 - shoulderW,
        y: BASELINE - baseH - shoulderH,
        w: shoulderW,
        h: shoulderH,
      });
      break;
    }

    default: {
      solids.push({ x, y: top, w, h });
      // Some plain towers still get a mast, for rhythm.
      if (h > 110 && random() > 0.6) {
        details.push({ x: x + w / 2 - 0.75, y: top - 18, w: 1.5, h: 18, opacity: 0.8 });
        circles.push({ cx: x + w / 2, cy: top - 20, r: 2, fill: LIGHT });
      }
    }
  }

  return { solids, details, polys, circles };
}

interface Light {
  x: number;
  y: number;
  size: number;
  opacity: number;
}

/** Windows inside a solid mass. Skipped on anything too small to hold a grid. */
function windowsIn(solid: Solid, random: () => number): Light[] {
  const lights: Light[] = [];
  const stepX = 8;
  const stepY = 9;
  const inset = 5;
  const size = 2.6;

  if (solid.w < 2 * inset + size || solid.h < 2 * inset + size) return lights;

  for (let x = solid.x + inset; x < solid.x + solid.w - inset; x += stepX) {
    for (let y = solid.y + solid.h - inset; y > solid.y + inset; y -= stepY) {
      if (random() > 0.34) continue;
      lights.push({ x, y: y - size, size, opacity: 0.28 + random() * 0.55 });
    }
  }
  return lights;
}

function stars(random: () => number): Light[] {
  return Array.from({ length: 70 }, () => ({
    x: random() * VIEW_W,
    y: random() * (VIEW_H * 0.34),
    size: 2,
    opacity: 0.14 + random() * 0.3,
  }));
}

export function Skyline() {
  // One generator for the whole drawing, consumed in a fixed order, so the
  // output is stable across renders and between server and browser.
  const random = seededRandom(20260913);
  const skyLights = stars(random);

  const buildings: { shape: Shape; lights: Light[] }[] = [];
  const centre = VIEW_W / 2;
  let x = -30;

  let lastWasSite = false;

  while (x < VIEW_W + 30) {
    let type = pickType(random);
    // Two building sites side by side reads as a demolition zone rather than a
    // city with some work going on in it, so a second one in a row is re-rolled
    // once.
    const isSite = type.name === "lattice" || type.name === "crane";
    if (isSite && lastWasSite) type = pickType(random);
    lastWasSite = type.name === "lattice" || type.name === "crane";

    const w = type.minW + random() * (type.maxW - type.minW);

    const closeness = Math.max(0, 1 - Math.abs(x + w / 2 - centre) / centre);
    // Squared, written as a multiplication rather than `Math.pow(c, 2.1)`.
    //
    // `Math.pow` with a fractional exponent is not required to be correctly
    // rounded, and Node and the browser can land one unit in the last place
    // apart. That is enough to make the server emit height="73.97513972203586"
    // while the browser computes ...85, which React reports as a hydration
    // mismatch. Multiplication is correctly rounded by IEEE 754, so this is
    // identical everywhere - and the slightly gentler exponent widens the tall
    // core, which the extra silhouette variety needs room to show in.
    const falloff = closeness * closeness;
    const base = 17 + falloff * 172;
    const jitter = (random() - 0.5) * base * 0.62;
    // Only the widest blocks are held back. A 110-unit slab at full height is a
    // wall; everything narrower is free to reach the same height the current
    // skyline does, because losing that tall central core is what made an
    // earlier pass of this look squat.
    const damping = w > 78 ? 1 - Math.min(0.2, (w - 78) / 200) : 1;
    const h = Math.min(196, Math.max(13, (base + jitter) * damping));

    // Quantised before anything is derived from them, as a second guard on the
    // same problem: coordinates that agree to two decimal places serialise to
    // the same string on both sides, and nobody can see a hundredth of a
    // viewBox unit anyway.
    const shape = shapeFor(type.name, q(x), q(w), q(h), random);
    const lights = shape.solids.flatMap((solid) => windowsIn(solid, random));
    buildings.push({ shape, lights });

    x += w + (random() > 0.8 ? 5 + random() * 16 : 1.5);
  }

  return (
    <div
      aria-hidden
      className="pointer-events-none w-full [mask-image:linear-gradient(to_right,transparent_0%,black_9%,black_91%,transparent_100%)]"
    >
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMax meet"
        className="block h-auto w-full"
        role="presentation"
      >
        <defs>
          <linearGradient id="tower" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-panel)" />
            <stop offset="70%" stopColor="var(--color-night)" />
            <stop offset="100%" stopColor="var(--color-void)" />
          </linearGradient>
          <radialGradient id="cityGlow" cx="50%" cy="100%" r="52%">
            <stop offset="0%" stopColor="var(--color-neon)" stopOpacity="0.2" />
            <stop offset="55%" stopColor="var(--color-neon)" stopOpacity="0.05" />
            <stop offset="100%" stopColor="var(--color-neon)" stopOpacity="0" />
          </radialGradient>
        </defs>

        <rect x="0" y={VIEW_H * 0.2} width={VIEW_W} height={VIEW_H * 0.8} fill="url(#cityGlow)" />

        {skyLights.map((light, i) => (
          <rect
            key={`s${i}`}
            x={light.x}
            y={light.y}
            width={light.size}
            height={light.size}
            fill={LIGHT}
            opacity={light.opacity}
          />
        ))}

        {buildings.map(({ shape, lights }, i) => (
          <g key={`b${i}`}>
            {shape.circles?.map((circle, j) => (
              <circle
                key={`c${i}-${j}`}
                cx={circle.cx}
                cy={circle.cy}
                r={circle.r}
                fill={circle.fill ?? MASS}
                opacity={circle.fill === LIGHT ? 0.9 : 1}
              />
            ))}

            {shape.polys?.map((points, j) => (
              <polygon key={`p${i}-${j}`} points={points} fill={MASS} />
            ))}

            {shape.solids.map((solid, j) => (
              <g key={`m${i}-${j}`}>
                <rect x={solid.x} y={solid.y} width={solid.w} height={solid.h} fill={MASS} />
                {/* A lit edge, so adjacent masses separate instead of merging
                    into one dark shape. */}
                <rect
                  x={solid.x}
                  y={solid.y}
                  width="1"
                  height={solid.h}
                  fill={STEEL}
                  opacity="0.45"
                />
              </g>
            ))}

            {shape.details.map((detail, j) => (
              <rect
                key={`d${i}-${j}`}
                x={detail.x}
                y={detail.y}
                width={detail.w}
                height={detail.h}
                fill={STEEL}
                opacity={detail.opacity ?? 0.7}
              />
            ))}

            {lights.map((light, j) => (
              <rect
                key={`w${i}-${j}`}
                x={light.x}
                y={light.y}
                width={light.size}
                height={light.size}
                fill={LIGHT}
                opacity={light.opacity}
              />
            ))}
          </g>
        ))}
      </svg>
    </div>
  );
}
