"use client";

/**
 * Star ratings and the review section on a product page.
 */

import Link from "next/link";
import { useState } from "react";

import { errorMessage, useSession } from "@/components/session-provider";
import { Button, ErrorNote, Meander, Panel, Pill, Skeleton } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import { useAsyncData } from "@/lib/use-async";
import { formatDate } from "@/lib/format";
import type { Page, RatingSummary, Review } from "@/lib/types";

/**
 * One star, filled from 0 to 1.
 *
 * Split out so the rating picker can render a single star per button. Reusing
 * the five-star row there produced twenty-five stars.
 */
function Star({ fill, size }: { fill: number; size: number }) {
  const clipped = Math.max(0, Math.min(1, fill));
  // The clip id must be unique per fill level within a document, or two stars
  // at different fills would share one clip and render identically.
  const id = `star-clip-${size}-${Math.round(clipped * 100)}`;

  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden className="shrink-0">
      <defs>
        <clipPath id={id}>
          <rect x="0" y="0" width={24 * clipped} height="24" />
        </clipPath>
      </defs>
      <path
        d="M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4 6.1 20.5l1.2-6.5L2.5 9.4l6.6-.9z"
        fill="none"
        stroke="var(--color-gold-dim)"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4 6.1 20.5l1.2-6.5L2.5 9.4l6.6-.9z"
        fill="var(--color-gold)"
        clipPath={`url(#${id})`}
      />
    </svg>
  );
}

/**
 * A row of stars.
 *
 * Drawn as one SVG path repeated, rather than a font icon or an image. The
 * partial star is done with a clip rectangle, so 4.3 renders as four and a bit
 * instead of being rounded to something the number does not say.
 */
export function Stars({
  rating,
  size = 16,
  className = "",
}: {
  rating: number;
  size?: number;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(5, rating));

  return (
    <span
      className={`inline-flex items-center gap-0.5 ${className}`}
      // One label for the whole row. Five separate images would make a screen
      // reader announce "star, star, star…" instead of the actual rating.
      role="img"
      aria-label={`${clamped.toFixed(1)} out of 5 stars`}
    >
      {[0, 1, 2, 3, 4].map((index) => (
        <Star key={index} fill={clamped - index} size={size} />
      ))}
    </span>
  );
}

/**
 * Compact rating for product cards and listings.
 *
 * Renders nothing at all when a product has no reviews. An empty star row on a
 * new product reads as "rated zero", which is worse than saying nothing.
 */
export function RatingBadge({
  average,
  count,
  size = 13,
}: {
  average: number | null;
  count: number;
  size?: number;
}) {
  if (average === null || count === 0) return null;

  return (
    <span className="inline-flex items-center gap-1.5">
      <Stars rating={average} size={size} />
      <span className="text-xs text-faint">({count})</span>
    </span>
  );
}

/**
 * The full review section: summary, write form and list.
 */
export function ProductReviews({ slug }: { slug: string }) {
  const { user } = useSession();

  // The summary and the list are fetched together, because they are always
  // rendered together and two sequential round trips would show the histogram
  // before the reviews it describes.
  const {
    data,
    loading,
    error,
    reload,
  } = useAsyncData<{ summary: RatingSummary; reviews: Review[] }>(async () => {
    const [summary, list] = await Promise.all([
      apiFetch<RatingSummary>(`/catalog/products/${slug}/reviews/summary`),
      apiFetch<Page<Review>>(`/catalog/products/${slug}/reviews?pageSize=20`),
    ]);
    return { summary, reviews: list.items };
  }, [slug]);

  const summary = data?.summary ?? null;
  const reviews = data?.reviews ?? [];

  // Whether the caller has already reviewed this, so the form can be replaced
  // with their review instead of offering one that the one-per-person
  // constraint will reject.
  //
  // Uses the shared hook rather than a hand-rolled effect: setting state
  // synchronously inside an effect is what React 19 flags, and the hook only
  // ever sets it from a promise continuation. A 401 here is the normal state
  // for a signed-out visitor, so it resolves to null rather than erroring.
  const { data: mine, reload: reloadMine } = useAsyncData<Review | null>(
    async () =>
      user
        ? await apiFetch<Review | null>(`/catalog/products/${slug}/reviews/mine`).catch(
            () => null,
          )
        : null,
    [user?.id, slug],
  );

  return (
    <section className="mt-20" id="reviews">
      <h2 className="inscription text-lg text-marble">Reviews</h2>
      <Meander className="mt-3 max-w-[8rem]" />

      {error ? (
        <div className="mt-6">
          <ErrorNote>{error}</ErrorNote>
        </div>
      ) : null}

      <div className="mt-8 grid gap-8 lg:grid-cols-[20rem_1fr]">
        <div className="space-y-6">
          <Panel className="p-6">
            {loading ? (
              <Skeleton className="h-28" />
            ) : summary && summary.count > 0 ? (
              <>
                <div className="flex items-baseline gap-3">
                  <span className="inscription text-3xl text-gold">
                    {summary.average?.toFixed(1)}
                  </span>
                  <span className="text-xs text-faint">
                    {summary.count} review{summary.count === 1 ? "" : "s"}
                  </span>
                </div>
                <Stars rating={summary.average ?? 0} size={18} className="mt-2" />

                <dl className="mt-5 space-y-1.5">
                  {[5, 4, 3, 2, 1].map((star) => {
                    const count = summary.breakdown?.[star] ?? 0;
                    const pct = summary.count > 0 ? (count / summary.count) * 100 : 0;
                    return (
                      <div key={star} className="flex items-center gap-2 text-xs">
                        <dt className="w-8 text-faint">{star}★</dt>
                        <dd className="flex-1">
                          <div className="h-1.5 overflow-hidden rounded-full bg-raised">
                            <div
                              className="h-full rounded-full bg-gold/70"
                              style={{ width: `${Math.max(pct === 0 ? 0 : 2, pct)}%` }}
                            />
                          </div>
                        </dd>
                        <span className="w-6 text-right text-faint">{count}</span>
                      </div>
                    );
                  })}
                </dl>
              </>
            ) : (
              <p className="text-sm text-muted">No reviews yet.</p>
            )}
          </Panel>

          {mine ? (
            <Panel className="p-5">
              <p className="inscription text-[0.62rem] text-cyan/80">Your review</p>
              <Stars rating={mine.rating} size={14} className="mt-2" />
              <p className="mt-2 text-sm text-muted">{mine.body}</p>
            </Panel>
          ) : user ? (
            <WriteReview
              slug={slug}
              onWritten={() => {
                reloadMine();
                reload();
              }}
            />
          ) : (
            <Panel className="p-5 text-sm text-muted">
              <Link href="/login" className="text-cyan underline-offset-4 hover:underline">
                Sign in
              </Link>{" "}
              to leave a review.
            </Panel>
          )}
        </div>

        <div>
          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
            </div>
          ) : reviews.length === 0 ? (
            <p className="text-sm text-faint">
              Nothing here yet. Be the first to say something.
            </p>
          ) : (
            <ul className="space-y-4">
              {reviews.map((review) => (
                <li key={review.id} className="surface p-5">
                  <div className="flex flex-wrap items-center gap-3">
                    <Stars rating={review.rating} size={14} />
                    <span className="text-sm text-ink">{review.authorName}</span>
                    {review.isVerifiedPurchase ? (
                      <Pill className="border-ok/40 text-ok">Verified purchase</Pill>
                    ) : null}
                    <span className="ml-auto text-xs text-faint">
                      {formatDate(review.createdAt)}
                    </span>
                  </div>
                  {review.title ? (
                    <p className="mt-3 text-sm font-medium text-marble">{review.title}</p>
                  ) : null}
                  <p className="mt-2 whitespace-pre-line text-sm text-muted">{review.body}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * The write-a-review form.
 */
function WriteReview({
  slug,
  onWritten,
}: {
  slug: string;
  onWritten: (review: Review) => void;
}) {
  const [rating, setRating] = useState(0);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsPurchase, setNeedsPurchase] = useState(false);

  async function submit() {
    if (rating === 0) {
      setError("Choose a star rating.");
      return;
    }
    setPending(true);
    setError(null);

    try {
      const created = await apiFetch<Review>(`/catalog/products/${slug}/reviews`, {
        method: "POST",
        json: { rating, title: title.trim() || null, body: body.trim() },
      });
      onWritten(created);
    } catch (caught) {
      // The API distinguishes "you have not bought this" from other failures,
      // so the UI can explain the rule rather than showing a bare 403.
      if (caught instanceof ApiError && caught.details?.reason === "purchase_required") {
        setNeedsPurchase(true);
      }
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  if (needsPurchase) {
    return (
      <Panel className="p-5">
        <p className="inscription text-[0.62rem] text-cyan/80">Write a review</p>
        <p className="mt-3 text-sm text-muted">
          Reviews are limited to customers who have bought this, which is what keeps
          the ratings on this site honest.
        </p>
      </Panel>
    );
  }

  return (
    <Panel className="p-5">
      <p className="inscription text-[0.62rem] text-cyan/80">Write a review</p>
      <Meander className="my-3 max-w-[5rem]" />

      <fieldset className="mb-4">
        <legend className="mb-2 text-xs text-faint">Your rating</legend>
        <div className="flex gap-1">
          {[1, 2, 3, 4, 5].map((star) => (
            <button
              key={star}
              type="button"
              onClick={() => setRating(star)}
              aria-label={`${star} star${star === 1 ? "" : "s"}`}
              aria-pressed={rating === star}
              className="rounded transition-transform hover:scale-110"
            >
              <Star fill={rating >= star ? 1 : 0} size={24} />
            </button>
          ))}
        </div>
      </fieldset>

      <label htmlFor="review-title" className="mb-1 block text-xs text-faint">
        Headline (optional)
      </label>
      <input
        id="review-title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        maxLength={160}
        className="mb-4 w-full rounded-md border border-edge bg-night px-3 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
      />

      <label htmlFor="review-body" className="mb-1 block text-xs text-faint">
        Your review
      </label>
      <textarea
        id="review-body"
        value={body}
        onChange={(event) => setBody(event.target.value)}
        rows={4}
        minLength={10}
        maxLength={4000}
        className="mb-4 w-full rounded-md border border-edge bg-night px-3 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
      />

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <Button
        type="button"
        onClick={submit}
        disabled={pending || body.trim().length < 10}
        className="mt-3 w-full"
      >
        {pending ? "Posting…" : "Post review"}
      </Button>
    </Panel>
  );
}
