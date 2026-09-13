/**
 * TEMPORARY comparison page — not linked from anywhere, deleted before merge.
 */

import { Skyline } from "@/components/skyline";
import { SkylineV2 } from "@/components/skyline-v2";

function Frame({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-edge">
      <p className="inscription px-6 pt-6 text-[0.62rem] text-cyan/80">{label}</p>
      <div className="relative">
        <div className="px-6 pt-12 pb-10 text-center">
          <h1 className="inscription chrome-text text-4xl drop-shadow-[0_2px_18px_rgba(10,7,24,0.9)] sm:text-5xl">
            Excellent choice
          </h1>
        </div>
        <div className="relative">
          <div aria-hidden className="horizon-grid" />
          <div className="absolute inset-x-0 bottom-[26px]">{children}</div>
        </div>
      </div>
    </section>
  );
}

/** Centre of the skyline blown up, so the silhouettes can actually be judged. */
function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-edge">
      <p className="inscription px-6 pt-6 text-[0.62rem] text-cyan/80">{label}</p>
      <div className="relative h-[280px] overflow-hidden">
        {/* Bottom-aligned: the two SVGs have different viewBox heights, so
            top-aligning them would clip the mockup's bases and make its towers
            look shorter than they are. */}
        <div className="absolute bottom-0 w-[300%] -translate-x-[33%]">{children}</div>
      </div>
    </section>
  );
}

export default function SkylinePreviewPage() {
  return (
    <main>
      <Frame label="Current — shipping today">
        <Skyline />
      </Frame>
      <Frame label="Mockup — detailed silhouettes">
        <SkylineV2 />
      </Frame>
      <Detail label="Current — centre, 3x">
        <Skyline />
      </Detail>
      <Detail label="Mockup — centre, 3x">
        <SkylineV2 />
      </Detail>
    </main>
  );
}
