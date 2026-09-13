/**
 * The downtown skyline that closes the hero.
 *
 * Drawn as one inline SVG rather than an image, so it scales to any width,
 * themes itself from the same CSS variables as everything else, and costs no
 * extra request.
 *
 * Everything here is deterministic. The silhouette and the windows look
 * scattered but come from a seeded PRNG, because `Math.random()` in a server
 * component produces one layout on the server and a different one in the
 * browser, and React throws a hydration mismatch when they disagree.
 */

/**
 * Small seeded PRNG (mulberry32).
 *
 * Same seed always gives the same sequence, which is the whole point: the
 * server and the browser must draw identical buildings.
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

const VIEW_W = 2400;
const VIEW_H = 340;
const BASELINE = VIEW_H;

/** Every light is the same magenta as the grid below, so the city and the floor
    read as one continuous field rather than two objects in different palettes.
    Depth comes from varying opacity, not hue. */
const WINDOW_LIGHT = "var(--color-neon)";

/**
 * Generate the silhouette.
 *
 * Hand-placing the buildings, as an earlier version did, produced a visibly
 * alternating tall/short/tall rhythm - the eye picks that up immediately and it
 * stops looking like a city.
 *
 * Height comes from two parts instead:
 *
 * 1. A falloff curve peaking at the centre, so the tall core is central and the
 *    city thins toward the edges rather than ending abruptly at a hard border.
 * 2. Jitter proportional to that base height, so neighbouring towers differ by
 *    a believable amount at every scale - big towers vary a lot, outskirts vary
 *    a little.
 *
 * Buildings are generated right across the full width, so the outskirts run to
 * both edges of the screen and the horizontal fade in the wrapper takes them
 * out gently.
 */
function generateBuildings(random: () => number): Building[] {
  const buildings: Building[] = [];
  const centre = VIEW_W / 2;

  let x = -20;
  while (x < VIEW_W + 20) {
    const w = 24 + random() * 52;

    // 1 dead centre, 0 at either edge.
    const closeness = 1 - Math.abs(x + w / 2 - centre) / centre;
    // Raising it to a power keeps the tall core compact and lets the tails run
    // out long and low, rather than sloping evenly like a pyramid.
    const falloff = Math.pow(Math.max(0, closeness), 2.1);

    const base = 26 + falloff * 226;
    const jitter = (random() - 0.5) * base * 0.62;
    const h = Math.max(16, base + jitter);

    buildings.push({
      x,
      w,
      h,
      // Only tall towers get masts, and only some of them.
      spire: h > 150 && random() > 0.5,
      setback: h > 90 && random() > 0.68,
    });

    // Usually butt the next building up against this one; occasionally leave a
    // gap, which reads as a cross street.
    x += w + (random() > 0.82 ? 6 + random() * 16 : 1);
  }

  return buildings;
}

interface Light {
  x: number;
  y: number;
  w: number;
  h: number;
  opacity: number;
}

/**
 * Lay windows out on a grid inside a building and drop most of them.
 *
 * A fully lit tower looks like graph paper. Keeping roughly a third lit is what
 * makes it read as an occupied building at night.
 */
function windowsFor(building: Building, random: () => number): Light[] {
  const lights: Light[] = [];

  const stepX = 9;
  const stepY = 11;
  const inset = 6;
  const size = 3;

  // Too narrow or too short to hold a readable window grid.
  if (building.w < 2 * inset + size || building.h < 2 * inset + size) return lights;

  for (let x = building.x + inset; x < building.x + building.w - inset; x += stepX) {
    for (let y = BASELINE - inset; y > BASELINE - building.h + inset; y -= stepY) {
      if (random() > 0.34) continue;

      lights.push({
        x,
        y: y - size,
        w: size,
        h: size,
        // Varying opacity gives depth; without it every window sits on the same
        // plane and the tower looks flat.
        opacity: 0.28 + random() * 0.55,
      });
    }
  }

  return lights;
}

/** A few faint points of light above the rooftops, for texture. */
function stars(random: () => number): Light[] {
  return Array.from({ length: 70 }, () => ({
    x: random() * VIEW_W,
    // Confined to the upper part so they read as sky, not as stray windows.
    y: random() * (VIEW_H * 0.4),
    w: 2,
    h: 2,
    opacity: 0.14 + random() * 0.3,
  }));
}

export function Skyline() {
  // One generator for the whole drawing, consumed in a fixed order, so the
  // output is stable across renders and between server and browser.
  const random = seededRandom(20260913);

  const skyLights = stars(random);
  const buildings = generateBuildings(random).map((building) => ({
    building,
    lights: windowsFor(building, random),
  }));

  return (
    // Full width, with the tall core central and the outskirts running to both
    // edges. The horizontal mask fades the tails out instead of letting the
    // city stop at a hard vertical line.
    <div
      aria-hidden
      className="pointer-events-none w-full [mask-image:linear-gradient(to_right,transparent_0%,black_9%,black_91%,transparent_100%)]"
    >
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        // `meet`, not `slice`. `slice` fills the box and crops whatever does not
        // fit, which sheared the tops off the tallest towers.
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
          <radialGradient id="cityGlow" cx="50%" cy="100%" r="52%">
            <stop offset="0%" stopColor="var(--color-neon)" stopOpacity="0.2" />
            <stop offset="55%" stopColor="var(--color-neon)" stopOpacity="0.05" />
            <stop offset="100%" stopColor="var(--color-neon)" stopOpacity="0" />
          </radialGradient>
        </defs>

        <rect x="0" y={VIEW_H * 0.2} width={VIEW_W} height={VIEW_H * 0.8} fill="url(#cityGlow)" />

        {skyLights.map((light, index) => (
          <rect
            key={`star-${index}`}
            x={light.x}
            y={light.y}
            width={light.w}
            height={light.h}
            fill={WINDOW_LIGHT}
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
              opacity="0.5"
            />

            {building.setback ? (
              <rect
                x={building.x + building.w * 0.28}
                y={BASELINE - building.h - 14}
                width={building.w * 0.44}
                height="14"
                fill="url(#tower)"
              />
            ) : null}

            {building.spire ? (
              <>
                <rect
                  x={building.x + building.w / 2 - 0.75}
                  y={BASELINE - building.h - 24}
                  width="1.5"
                  height="24"
                  fill="var(--color-edge-bright)"
                  opacity="0.8"
                />
                {/* Aircraft warning light. */}
                <circle
                  cx={building.x + building.w / 2}
                  cy={BASELINE - building.h - 26}
                  r="2.2"
                  fill={WINDOW_LIGHT}
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
                fill={WINDOW_LIGHT}
                opacity={light.opacity}
              />
            ))}
          </g>
        ))}
      </svg>
    </div>
  );
}
