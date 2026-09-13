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
 * Four things carry the look:
 *
 * 1. **The city is small against the floor.** It is drawn in its own unit
 *    space and then scaled down into the viewBox, so the towers read as a
 *    distant skyline the perspective grid recedes toward rather than as
 *    buildings standing at the front of the picture. See `SCALE`.
 *
 * 2. **Width is a property of the building type, not one random band.** An
 *    earlier version drew every tower from the same narrow range, so height
 *    was the only thing that varied and the result read as a bar chart.
 *
 * 3. **Thirteen silhouettes instead of one rectangle.** Setbacks, ziggurats,
 *    tapers, twin peaks, domes, notched corners, rooftop water towers, sheared
 *    crowns, and two unfinished buildings whose scaffolding forms part of the
 *    outline - an open steel frame, and a tower crane with a jib and a
 *    counterweight. The scaffolding is in the silhouette rather than painted on
 *    a facade, because at this size a facade pattern just disappears into the
 *    window grid.
 *
 * 4. **The gaps between blocks line up with the grid's lines.** See the
 *    `Streets` section below - this is the part that makes the floor read as
 *    lit streets running into the city instead of a rug it happens to sit on.
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

/** Round to two decimals, so server and browser serialise identical strings. */
function q(n: number): number {
  return Math.round(n * 100) / 100;
}

// -----------------------------------------------------------------------------
// Canvas
// -----------------------------------------------------------------------------

const VIEW_W = 2400;

/**
 * How much of the viewBox the city occupies.
 *
 * Buildings are generated at their natural size in a wider "unit space" and the
 * whole city is then scaled into the box by this factor. One number controls
 * how big the skyline reads against the perspective floor, instead of the
 * fifty-odd literals in the shape functions.
 *
 * At 1 the tallest towers stood about 50px clear of the grid's horizon line,
 * which is what made them look like they were in the foreground. At 0.66 they
 * crest it by about 10px, which is what a city seen from a distance does.
 */
const SCALE = 0.66;

/** Baseline in unit space. Building heights are still quoted against this. */
const BASELINE = 255;

/** Generation width in unit space - wider than the box, since it gets shrunk. */
const UNIT_W = VIEW_W / SCALE;

/** Box height. Derived, so the baseline lands exactly on the bottom edge. */
const VIEW_H = BASELINE * SCALE;

const LIGHT = "var(--color-neon)";
const MASS = "url(#tower)";
const STEEL = "var(--color-edge-bright)";

// -----------------------------------------------------------------------------
// Streets
// -----------------------------------------------------------------------------

/**
 * Where the floor's lines arrive at the foot of the city.
 *
 * `.horizon-grid` draws its streets as a repeating gradient on a pseudo-element
 * that is then run through `perspective(340px) rotateX(72deg)`. The city is a
 * flat SVG laid over the top, so for the gaps between blocks to sit on those
 * lines - rather than near them - the projection has to be solved rather than
 * eyeballed.
 *
 * Take a line at depth `d` above the transform origin. The rotation sends it to
 * `z = -d sin(tilt)`, and the perspective divide then scales everything at that
 * depth by `P / (P + d sin(tilt))` while placing it `d cos(tilt)` times that
 * same factor above the origin. Setting that height equal to where the city's
 * baseline sits gives the depth the city stands at, and the scale factor there
 * is how much the street spacing has been squeezed by the time it reaches the
 * buildings.
 *
 * Every constant below is read off `.horizon-grid` in `globals.css`. Changing
 * either file without the other pulls the streets out of alignment, so they are
 * named rather than folded into a single magic number.
 */
const GRID_HEIGHT_PX = 190; // .horizon-grid height
const GRID_UNDERHANG = 0.1; // ::before inset-bottom, as a fraction of that
const GRID_SPREAD = 2.2; // ::before width, as a multiple of the container's
const GRID_PITCH_FRACTION = 0.5 / 26; // street spacing, as a fraction of ::before
const SKYLINE_LIFT_PX = 26; // the hero's bottom-[26px] on the skyline
const PERSPECTIVE_PX = 340;
const TILT = (72 * Math.PI) / 180;

/** Height of the city's baseline above the grid's transform origin. */
const BASELINE_LIFT_PX = SKYLINE_LIFT_PX + GRID_HEIGHT_PX * GRID_UNDERHANG;

/** Depth of the plane the city stands on, in the floor's own flat coordinates. */
const CITY_DEPTH_PX =
  (BASELINE_LIFT_PX * PERSPECTIVE_PX) /
  (PERSPECTIVE_PX * Math.cos(TILT) - BASELINE_LIFT_PX * Math.sin(TILT));

/** How much the perspective divide squeezes that plane. */
const FORESHORTEN = PERSPECTIVE_PX / (PERSPECTIVE_PX + CITY_DEPTH_PX * Math.sin(TILT));

/**
 * Street spacing at the foot of the city, in unit space.
 *
 * The page-pixel width cancels out: the grid's spacing is a fraction of the
 * viewport and so is the SVG, which is exactly why `globals.css` has to express
 * that spacing as a percentage rather than the fixed 62px it used to use. With
 * a fixed pitch the two drift apart as the window is resized and only ever meet
 * at one width.
 */
const STREET_PITCH =
  (GRID_PITCH_FRACTION * GRID_SPREAD * FORESHORTEN * VIEW_W) / SCALE;

interface Span {
  start: number;
  end: number;
}

/** The gaps to leave in the city, one per line on the floor. */
function streetSpans(random: () => number): Span[] {
  const spans: Span[] = [];
  const centre = UNIT_W / 2;
  const reach = Math.ceil((centre + 80) / STREET_PITCH);

  for (let k = -reach; k <= reach; k += 1) {
    // Most are side streets; roughly one in eight is an avenue, which stops the
    // blocks from marching across at one unvarying rhythm.
    const width = random() > 0.87 ? 9 + random() * 6 : 4 + random() * 3;
    const at = centre + k * STREET_PITCH;
    spans.push({ start: at - width / 2, end: at + width / 2 });
  }
  return spans;
}

// -----------------------------------------------------------------------------
// Buildings
// -----------------------------------------------------------------------------

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
  /** Circles, for domes and mast lights. */
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

const FLAT = TYPES[0];
const TOTAL_WEIGHT = TYPES.reduce((sum, t) => sum + t.weight, 0);

/** Narrowest thing worth building. Anything less and the block is skipped. */
const MIN_PLOT = 13;

function pickType(random: () => number): (typeof TYPES)[number] {
  let roll = random() * TOTAL_WEIGHT;
  for (const type of TYPES) {
    roll -= type.weight;
    if (roll <= 0) return type;
  }
  return FLAT;
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
    out.push({ x: x + i * bayW - 1.1, y, w: 2.2, h, opacity: 0.4 });
  }
  const rails = Math.max(2, Math.round(h / 9));
  for (let i = 0; i <= rails; i += 1) {
    out.push({ x, y: y + (i * h) / rails, w, h: 1.8, opacity: 0.3 });
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
      // Three stacked stages, each narrower - the classic Art Deco profile.
      let currentW = w;
      let currentX = x;
      let y = BASELINE;
      for (let i = 0; i < 3; i += 1) {
        const stageH = (h / 3) * (i === 0 ? 1.25 : 0.9);
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
      circles.push({ cx: x + w * 0.5, cy: top - mastH - 3, r: 2.6, fill: LIGHT });
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
      solids.push({ x: left ? x + notchW : x, y: top, w: w - notchW, h: notchH });
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
      // The New York rooftop tank: a drum on legs, under a shallow cap.
      const tankW = 16;
      const tankH = 14;
      const legH = 7;
      const tx = x + w * (0.2 + random() * 0.55);
      details.push({ x: tx, y: top - legH - tankH, w: tankW, h: tankH, opacity: 0.85 });
      polys.push(
        `${tx},${top - legH - tankH} ${tx + tankW / 2},${top - legH - tankH - 7} ` +
          `${tx + tankW},${top - legH - tankH}`,
      );
      for (const legX of [tx + 1.5, tx + tankW - 3.7]) {
        details.push({ x: legX, y: top - legH, w: 2.2, h: legH, opacity: 0.6 });
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
      //
      // Clamped clear of the top edge: the tallest tower plus the longest mast
      // would otherwise put the jib outside the viewBox and lose its top.
      const mastX = x + w * (0.3 + random() * 0.4);
      const mastTop = Math.max(6, BASELINE - h - (26 + random() * 24));
      details.push({
        x: mastX - 1.4,
        y: mastTop,
        w: 2.8,
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
        h: 2.6,
        opacity: 0.55,
      });
      details.push({
        x: side > 0 ? mastX - counter : mastX,
        y: armY,
        w: counter,
        h: 2.6,
        opacity: 0.55,
      });
      details.push({
        x: side > 0 ? mastX - counter : mastX + counter - 10,
        y: armY - 5,
        w: 10,
        h: 8,
        opacity: 0.6,
      });
      // Hoist cable and hook.
      const hookX = side > 0 ? mastX + jib * 0.72 : mastX - jib * 0.72;
      details.push({ x: hookX, y: armY, w: 1.4, h: 16 + random() * 14, opacity: 0.35 });
      circles.push({ cx: mastX, cy: mastTop - 2, r: 2.6, fill: LIGHT });
      break;
    }

    case "deco": {
      // Wide base with a central tower - the pre-war office block.
      const baseH = h * 0.5;
      solids.push({ x, y: BASELINE - baseH, w, h: baseH });
      const towerW = w * 0.36;
      solids.push({ x: x + (w - towerW) / 2, y: top, w: towerW, h: h - baseH + 1 });
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
        details.push({ x: x + w / 2 - 1.1, y: top - 18, w: 2.2, h: 18, opacity: 0.8 });
        circles.push({ cx: x + w / 2, cy: top - 20, r: 2.6, fill: LIGHT });
      }
    }
  }

  return { solids, details, polys, circles };
}

// -----------------------------------------------------------------------------
// Lights
// -----------------------------------------------------------------------------

interface Light {
  x: number;
  y: number;
  size: number;
  opacity: number;
}

/**
 * Windows inside a solid mass.
 *
 * Sized so that once the city is scaled down, a lit window lands at roughly the
 * same pixel size as a dot on the floor below. That is what makes the city and
 * the grid read as one field of light rather than two drawings at different
 * resolutions. Skipped on anything too small to hold a grid.
 */
function windowsIn(solid: Solid, random: () => number): Light[] {
  const lights: Light[] = [];
  const stepX = 9;
  const stepY = 10;
  const inset = 6;
  const size = 3.2;

  if (solid.w < 2 * inset + size || solid.h < 2 * inset + size) return lights;

  for (let x = solid.x + inset; x < solid.x + solid.w - inset; x += stepX) {
    for (let y = solid.y + solid.h - inset; y > solid.y + inset; y -= stepY) {
      if (random() > 0.34) continue;
      lights.push({ x, y: y - size, size, opacity: 0.28 + random() * 0.55 });
    }
  }
  return lights;
}

/** A few faint points of light above the rooftops, for texture. Box space. */
function stars(random: () => number): Light[] {
  return Array.from({ length: 70 }, () => ({
    x: random() * VIEW_W,
    // Confined to the upper part so they read as sky, not as stray windows.
    y: random() * (VIEW_H * 0.34),
    size: 2,
    opacity: 0.14 + random() * 0.3,
  }));
}

// -----------------------------------------------------------------------------

export function Skyline() {
  // One generator for the whole drawing, consumed in a fixed order, so the
  // output is stable across renders and between server and browser.
  const random = seededRandom(20260913);

  const skyLights = stars(random);
  const spans = streetSpans(random);

  const buildings: { shape: Shape; lights: Light[] }[] = [];
  const centre = UNIT_W / 2;
  let x = -40;
  let span = 0;
  let lastWasSite = false;

  while (x < UNIT_W + 40) {
    // Advance past any street already behind us, then see how much room is left
    // before the next one. Blocks are filled street to street rather than by
    // walking the whole width and hoping gaps land in the right places.
    while (span < spans.length && spans[span]!.end <= x) span += 1;
    const next = spans[span];
    const room = next ? next.start - x : UNIT_W + 40 - x;

    if (next && room < MIN_PLOT) {
      x = next.end;
      span += 1;
      continue;
    }

    let type = pickType(random);
    // Two building sites side by side reads as a demolition zone rather than a
    // city with some work going on in it, so a second one in a row is re-rolled
    // once.
    if ((type.name === "lattice" || type.name === "crane") && lastWasSite) {
      type = pickType(random);
    }
    // A plot too narrow for the archetype gets a plain tower instead of a
    // squashed one - a deco block crushed to a third of its width stops looking
    // like a deco block.
    if (room < type.minW) type = FLAT;
    lastWasSite = type.name === "lattice" || type.name === "crane";

    const w = Math.min(room, type.minW + random() * (type.maxW - type.minW));

    const closeness = Math.max(0, 1 - Math.abs(x + w / 2 - centre) / centre);
    // Squared, written as a multiplication rather than `Math.pow(c, 2.1)`.
    //
    // `Math.pow` with a fractional exponent is not required to be correctly
    // rounded, and Node and the browser can land one unit in the last place
    // apart. That is enough to make the server emit height="73.97513972203586"
    // while the browser computes ...85, which React reports as a hydration
    // mismatch. Multiplication is correctly rounded by IEEE 754, so this is
    // identical everywhere.
    const falloff = closeness * closeness;
    const base = 17 + falloff * 172;
    const jitter = (random() - 0.5) * base * 0.62;
    // Only the widest blocks are held back. A 110-unit slab at full height is a
    // wall; everything narrower is free to reach the clamp.
    const damping = w > 78 ? 1 - Math.min(0.2, (w - 78) / 200) : 1;
    const h = Math.min(196, Math.max(13, (base + jitter) * damping));

    // Quantised before anything is derived from them, as a second guard on the
    // same problem: coordinates that agree to two decimal places serialise to
    // the same string on both sides, and nobody can see a hundredth of a
    // viewBox unit anyway.
    const shape = shapeFor(type.name, q(x), q(w), q(h), random);
    buildings.push({
      shape,
      lights: shape.solids.flatMap((solid) => windowsIn(solid, random)),
    });

    // Usually butt the next building up against this one. The streets are
    // already carved out above, so this gap is only the seam between two
    // buildings sharing a block.
    x += w + (random() > 0.82 ? 3 + random() * 7 : 1.5);
  }

  return (
    // Full width, with the tall core central and the outskirts running to both
    // edges. The horizontal mask fades the tails out instead of letting the
    // city stop at a hard vertical line.
    <div
      aria-hidden
      className="pointer-events-none w-full [mask-image:linear-gradient(to_right,transparent_0%,black_9%,black_91%,transparent_100%)]"
    >
      <svg
        viewBox={`0 0 ${VIEW_W} ${q(VIEW_H)}`}
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
            <stop offset="0%" stopColor={LIGHT} stopOpacity="0.2" />
            <stop offset="55%" stopColor={LIGHT} stopOpacity="0.05" />
            <stop offset="100%" stopColor={LIGHT} stopOpacity="0" />
          </radialGradient>
        </defs>

        <rect x="0" y={q(VIEW_H * 0.2)} width={VIEW_W} height={q(VIEW_H * 0.8)} fill="url(#cityGlow)" />

        {skyLights.map((light, index) => (
          <rect
            key={`star-${index}`}
            x={q(light.x)}
            y={q(light.y)}
            width={light.size}
            height={light.size}
            fill={LIGHT}
            opacity={q(light.opacity)}
          />
        ))}

        {/* The city, drawn at full size and shrunk into the box. */}
        <g transform={`scale(${SCALE})`}>
          {buildings.map(({ shape, lights }, index) => (
            <g key={`building-${index}`}>
              {shape.circles?.map((circle, i) => (
                <circle
                  key={`c-${index}-${i}`}
                  cx={q(circle.cx)}
                  cy={q(circle.cy)}
                  r={circle.r}
                  fill={circle.fill ?? MASS}
                  opacity={circle.fill === LIGHT ? 0.9 : 1}
                />
              ))}

              {shape.polys?.map((points, i) => (
                <polygon key={`p-${index}-${i}`} points={points} fill={MASS} />
              ))}

              {shape.solids.map((solid, i) => (
                <g key={`m-${index}-${i}`}>
                  <rect
                    x={q(solid.x)}
                    y={q(solid.y)}
                    width={q(solid.w)}
                    height={q(solid.h)}
                    fill={MASS}
                  />
                  {/* A lit edge down one side, so adjacent towers separate
                      instead of merging into one dark mass. */}
                  <rect
                    x={q(solid.x)}
                    y={q(solid.y)}
                    width="1.5"
                    height={q(solid.h)}
                    fill={STEEL}
                    opacity="0.45"
                  />
                </g>
              ))}

              {shape.details.map((detail, i) => (
                <rect
                  key={`d-${index}-${i}`}
                  x={q(detail.x)}
                  y={q(detail.y)}
                  width={q(detail.w)}
                  height={q(detail.h)}
                  fill={STEEL}
                  opacity={detail.opacity ?? 0.7}
                />
              ))}

              {lights.map((light, i) => (
                <rect
                  key={`w-${index}-${i}`}
                  x={q(light.x)}
                  y={q(light.y)}
                  width={light.size}
                  height={light.size}
                  fill={LIGHT}
                  opacity={q(light.opacity)}
                />
              ))}
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}
