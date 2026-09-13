/**
 * The downtown skyline that closes the hero.
 *
 * Drawn as one inline SVG rather than an image, so it scales to any width,
 * themes itself from the same CSS variables as everything else, and costs no
 * extra request.
 *
 * Everything here is deterministic. The windows look scattered but are
 * generated from a seeded PRNG, because `Math.random()` in a server component
 * produces one layout on the server and a different one in the browser, and
 * React throws a hydration mismatch when they disagree.
 */

/**
 * Small seeded PRNG (mulberry32).
 *
 * Same seed always gives the same sequence, which is the whole point: the
 * server and the browser must draw identical windows.
 */
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

interface Building {
  /** Left edge, in viewBox units. */
  x: number;
  /** Width. */
  w: number;
  /** Height above the baseline. */
  h: number;
  /** Draw an antenna mast with a light on top. */
  spire?: boolean;
  /** Draw a narrower penthouse block on the roof. */
  setback?: boolean;
}

/**
 * The silhouette, hand-placed rather than generated.
 *
 * Heights rise toward the middle and fall away at the edges, which is what
 * makes it read as a downtown core rather than a random bar chart. The two
 * tallest towers sit slightly left and right of centre so the composition is
 * not perfectly symmetrical.
 */
const BUILDINGS: Building[] = [
  { x: 0, w: 58, h: 68 },
  { x: 60, w: 38, h: 112, setback: true },
  { x: 100, w: 64, h: 86 },
  { x: 166, w: 44, h: 142 },
  { x: 212, w: 70, h: 102 },
  { x: 284, w: 40, h: 170, spire: true },
  { x: 326, w: 58, h: 124 },
  { x: 386, w: 48, h: 198, setback: true },
  { x: 436, w: 66, h: 148 },
  { x: 504, w: 42, h: 226, spire: true },
  { x: 548, w: 72, h: 170 },
  { x: 622, w: 50, h: 264, spire: true },
  { x: 674, w: 60, h: 204, setback: true },
  { x: 736, w: 46, h: 246, spire: true },
  { x: 784, w: 76, h: 178 },
  { x: 862, w: 44, h: 212, setback: true },
  { x: 908, w: 64, h: 150 },
  { x: 974, w: 40, h: 186, spire: true },
  { x: 1016, w: 70, h: 130 },
  { x: 1088, w: 48, h: 162 },
  { x: 1138, w: 62, h: 110 },
  { x: 1202, w: 44, h: 140, setback: true },
  { x: 1248, w: 68, h: 94 },
  { x: 1318, w: 40, h: 122 },
  { x: 1360, w: 80, h: 76 },
];

const VIEW_W = 1440;
const VIEW_H = 320;
const BASELINE = VIEW_H;

/** Every light is the same magenta as the grid below, so the city reads as one
    continuous field of lights with the floor rather than as a separate object
    in its own palette. Depth comes from varying opacity, not hue. */
const WINDOW_LIGHT = "var(--color-neon)";

interface Light {
  x: number;
  y: number;
  w: number;
  h: number;
  fill: string;
  opacity: number;
}

/**
 * Lay windows out on a grid inside a building and drop most of them.
 *
 * A fully lit tower looks like graph paper. Keeping roughly a third lit is
 * what makes it read as an occupied building at night.
 */
function windowsFor(building: Building, random: () => number): Light[] {
  const lights: Light[] = [];

  const stepX = 9;
  const stepY = 11;
  const inset = 6;
  const size = 3;

  for (let x = building.x + inset; x < building.x + building.w - inset; x += stepX) {
    for (let y = BASELINE - inset; y > BASELINE - building.h + inset; y -= stepY) {
      if (random() > 0.34) continue;

      lights.push({
        x,
        y: y - size,
        w: size,
        h: size,
        fill: WINDOW_LIGHT,
        // Varying opacity gives depth; without it every window sits on the
        // same plane and the tower looks flat.
        opacity: 0.28 + random() * 0.55,
      });
    }
  }

  return lights;
}

/** A few faint points of light above the rooftops, for texture. */
function stars(random: () => number): Light[] {
  return Array.from({ length: 46 }, () => {
    return {
      x: random() * VIEW_W,
      // Confined to the upper half so they read as sky, not as stray windows.
      y: random() * (VIEW_H * 0.45),
      w: 2,
      h: 2,
      fill: WINDOW_LIGHT,
      opacity: 0.16 + random() * 0.34,
    };
  });
}

export function Skyline() {
  // One generator for the whole drawing, consumed in a fixed order, so the
  // output is stable across renders and between server and browser.
  const random = seededRandom(20260913);

  const skyLights = stars(random);
  const buildings = BUILDINGS.map((building) => ({
    building,
    lights: windowsFor(building, random),
  }));

  return (
    // Centred and narrow rather than full-bleed: the city sits at the
    // vanishing point of the grid, with the floor running out past it on both
    // sides. Vertical placement is the caller's job - see page.tsx, which
    // overlays this onto the grid so the towers stand in the lights.
    <div
      aria-hidden
      className="pointer-events-none mx-auto w-full max-w-[680px] px-6"
    >
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        // `meet`, not `slice`. `slice` fills the box and crops whatever does
        // not fit, which sheared the tops off the tallest towers against the
        // upper edge of the SVG. `meet` fits the whole viewBox and lets the
        // height follow from the aspect ratio.
        preserveAspectRatio="xMidYMax meet"
        className="block h-auto w-full"
        role="presentation"
      >
        <defs>
          {/* Towers fade toward their base so they sit in haze rather than
              being cut off by a hard line. */}
          <linearGradient id="tower" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-panel)" />
            <stop offset="70%" stopColor="var(--color-night)" />
            <stop offset="100%" stopColor="var(--color-void)" />
          </linearGradient>

          {/* A wash of light along the horizon, as if the city is glowing. */}
          <radialGradient id="cityGlow" cx="50%" cy="100%" r="65%">
            <stop offset="0%" stopColor="var(--color-neon)" stopOpacity="0.22" />
            <stop offset="55%" stopColor="var(--color-neon)" stopOpacity="0.06" />
            <stop offset="100%" stopColor="var(--color-neon)" stopOpacity="0" />
          </radialGradient>
        </defs>

        <rect x="0" y={VIEW_H * 0.25} width={VIEW_W} height={VIEW_H * 0.75} fill="url(#cityGlow)" />

        {skyLights.map((light, index) => (
          <rect
            key={`star-${index}`}
            x={light.x}
            y={light.y}
            width={light.w}
            height={light.h}
            fill={light.fill}
            opacity={light.opacity}
          />
        ))}

        {buildings.map(({ building, lights }, index) => (
          <g key={`building-${index}`}>
            <rect
              x={building.x}
              y={BASELINE - building.h}
              width={building.w}
              height={building.h}
              fill="url(#tower)"
            />
            {/* A lit edge down one side, so adjacent towers separate instead of
                merging into one dark mass. */}
            <rect
              x={building.x}
              y={BASELINE - building.h}
              width="1"
              height={building.h}
              fill="var(--color-edge-bright)"
              opacity="0.55"
            />

            {building.setback ? (
              <rect
                x={building.x + building.w * 0.28}
                y={BASELINE - building.h - 16}
                width={building.w * 0.44}
                height="16"
                fill="url(#tower)"
              />
            ) : null}

            {building.spire ? (
              <>
                <rect
                  x={building.x + building.w / 2 - 0.75}
                  y={BASELINE - building.h - 26}
                  width="1.5"
                  height="26"
                  fill="var(--color-edge-bright)"
                  opacity="0.8"
                />
                {/* Aircraft warning light. */}
                <circle
                  cx={building.x + building.w / 2}
                  cy={BASELINE - building.h - 28}
                  r="2.4"
                  fill="var(--color-neon)"
                  opacity="0.9"
                />
              </>
            ) : null}

            {lights.map((light, lightIndex) => (
              <rect
                key={`w-${index}-${lightIndex}`}
                x={light.x}
                y={light.y}
                width={light.w}
                height={light.h}
                fill={light.fill}
                opacity={light.opacity}
              />
            ))}
          </g>
        ))}
      </svg>
    </div>
  );
}
