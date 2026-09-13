"use client";

/**
 * The product create/edit form.
 *
 * One component serves both, because the two forms are otherwise identical and
 * keeping them separate guarantees they drift. `productId` being null means
 * "create".
 *
 * Images are only manageable once the product exists, since a gallery entry
 * needs a product to attach to. On the create form that section explains
 * itself rather than appearing broken.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessage } from "@/components/session-provider";
import { Button, ErrorNote, Meander, Panel, Skeleton } from "@/components/ui";
import { ApiError, apiBaseUrl, apiFetch, mediaUrl } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { Category, Product, ProductImage } from "@/lib/types";

/** A variant row being edited. Prices are strings so the input can be empty. */
interface VariantDraft {
  sku: string;
  name: string;
  price: string;
  compareAt: string;
  stock: string;
  trackInventory: boolean;
  isActive: boolean;
}

const EMPTY_VARIANT: VariantDraft = {
  sku: "",
  name: "",
  price: "",
  compareAt: "",
  stock: "0",
  trackInventory: true,
  isActive: true,
};

/**
 * Turn a title into a URL slug.
 *
 * Matches the pattern the API and the database CHECK constraint enforce:
 * lowercase, alphanumeric, single hyphens, no leading or trailing hyphen.
 */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFD")
    // Strip accents, so "Café" becomes "cafe" rather than "caf".
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 160);
}

/** Parse a money input into integer minor units. */
function toCents(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const amount = Number.parseFloat(trimmed);
  if (Number.isNaN(amount) || amount < 0) return null;
  // Rounded rather than truncated: 19.99 in float is 19.989999…, and
  // truncating would charge a cent less than the price on the label.
  return Math.round(amount * 100);
}

export function ProductEditor({ productId }: { productId: string | null }) {
  const router = useRouter();
  const isNew = productId === null;

  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  const [categories, setCategories] = useState<Category[]>([]);

  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [subtitle, setSubtitle] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState("draft");
  const [categoryId, setCategoryId] = useState("");
  const [variants, setVariants] = useState<VariantDraft[]>([{ ...EMPTY_VARIANT }]);
  const [images, setImages] = useState<ProductImage[]>([]);

  // Load categories always; load the product only when editing.
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const cats = await apiFetch<Category[]>("/catalog/admin/categories");
        if (!cancelled) setCategories(cats);
      } catch {
        // A missing category list is not fatal; the select just stays empty.
      }

      if (isNew) return;

      try {
        const product = await apiFetch<Product>(`/catalog/admin/products/${productId}`);
        if (cancelled) return;

        setTitle(product.title);
        setSlug(product.slug);
        setSlugTouched(true);
        setSubtitle(product.subtitle ?? "");
        setDescription(product.description ?? "");
        setStatus(product.status);
        setCategoryId(product.category?.id ?? "");
        setImages(product.images ?? []);
        setVariants(
          product.variants.length > 0
            ? product.variants.map((variant) => ({
                sku: variant.sku,
                name: variant.name,
                price: (variant.priceCents / 100).toFixed(2),
                compareAt:
                  variant.compareAtPriceCents != null
                    ? (variant.compareAtPriceCents / 100).toFixed(2)
                    : "",
                stock: String(variant.inventoryQuantity ?? 0),
                trackInventory: variant.trackInventory ?? true,
                isActive: variant.isActive ?? true,
              }))
            : [{ ...EMPTY_VARIANT }],
        );
      } catch (caught) {
        if (!cancelled) setError(errorMessage(caught));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isNew, productId]);

  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 2500);
    return () => clearTimeout(timer);
  }, [saved]);

  /**
   * Keep the slug in step with the title until the slug is edited by hand.
   *
   * Done here rather than in an effect watching `title`: an effect that calls
   * setState triggers a second render pass for something that is really just
   * part of handling the keystroke.
   *
   * Editing a saved product never auto-rewrites the slug, because the slug is
   * the public URL and silently changing it breaks every existing link.
   */
  function handleTitleChange(value: string) {
    setTitle(value);
    if (!slugTouched) setSlug(slugify(value));
  }

  function updateVariant(index: number, patch: Partial<VariantDraft>) {
    setVariants((current) =>
      current.map((variant, i) => (i === index ? { ...variant, ...patch } : variant)),
    );
  }

  async function save() {
    setSaving(true);
    setError(null);
    setFieldErrors({});

    const payloadVariants = variants
      .filter((variant) => variant.sku.trim() && variant.name.trim())
      .map((variant, index) => ({
        sku: variant.sku.trim(),
        name: variant.name.trim(),
        priceCents: toCents(variant.price) ?? 0,
        compareAtPriceCents: toCents(variant.compareAt),
        currency: "usd",
        inventoryQuantity: Math.max(0, Number.parseInt(variant.stock, 10) || 0),
        trackInventory: variant.trackInventory,
        position: index,
        isActive: variant.isActive,
      }));

    if (payloadVariants.length === 0) {
      setError("Add at least one variant with a SKU and a name.");
      setSaving(false);
      return;
    }

    const core = {
      slug: slug.trim(),
      title: title.trim(),
      subtitle: subtitle.trim() || null,
      description: description.trim() || null,
      status,
      categoryId: categoryId || null,
      position: 0,
    };

    try {
      if (isNew) {
        const created = await apiFetch<Product>("/catalog/admin/products", {
          method: "POST",
          json: { ...core, variants: payloadVariants },
        });
        // Straight to the edit page, which is where images can be added.
        router.push(`/admin/products/${created.id}`);
        router.refresh();
        return;
      }

      // Two calls: the product record, then the variant set. The API keeps
      // them separate because replacing variants matches on SKU to preserve
      // stock and IDs, which a single PATCH could not express.
      await apiFetch<Product>(`/catalog/admin/products/${productId}`, {
        method: "PATCH",
        json: core,
      });
      await apiFetch<Product>(`/catalog/admin/products/${productId}/variants`, {
        method: "PUT",
        json: payloadVariants,
      });
      setSaved(true);
      router.refresh();
    } catch (caught) {
      if (caught instanceof ApiError && Object.keys(caught.fieldErrors).length > 0) {
        setFieldErrors(caught.fieldErrors);
      }
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  async function archive() {
    if (!window.confirm("Archive this product? It will be hidden from the shop.")) return;
    try {
      await apiFetch<unknown>(`/catalog/admin/products/${productId}`, { method: "DELETE" });
      router.push("/admin/products");
      router.refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-72" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error ? <ErrorNote>{error}</ErrorNote> : null}

      <Panel className="p-6">
        <h2 className="inscription text-[0.68rem] text-cyan/80">Details</h2>
        <Meander className="my-4 max-w-[6rem]" />

        <div className="space-y-5">
          <Field
            label="Title"
            value={title}
            onChange={handleTitleChange}
            required
            error={fieldErrors.title}
          />
          <Field
            label="URL slug"
            value={slug}
            onChange={(value) => {
              setSlugTouched(true);
              setSlug(value);
            }}
            hint={`Appears in the address: /shop/${slug || "your-product"}`}
            error={fieldErrors.slug}
          />
          <Field
            label="Subtitle"
            value={subtitle}
            onChange={setSubtitle}
            hint="One line shown under the title."
          />

          <div>
            <label
              htmlFor="description"
              className="inscription mb-2 block text-[0.62rem] text-cyan/70"
            >
              Description
            </label>
            <textarea
              id="description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={6}
              className="w-full rounded-md border border-edge bg-night px-4 py-2.5 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
            />
          </div>

          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <label
                htmlFor="status"
                className="inscription mb-2 block text-[0.62rem] text-cyan/70"
              >
                Status
              </label>
              <select
                id="status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                className="w-full rounded-md border border-edge bg-night px-4 py-2.5 text-sm text-ink focus:border-cyan focus:outline-none"
              >
                <option value="draft">Draft — hidden from the shop</option>
                <option value="active">Active — on sale</option>
                <option value="archived">Archived — withdrawn</option>
              </select>
            </div>

            <div>
              <label
                htmlFor="category"
                className="inscription mb-2 block text-[0.62rem] text-cyan/70"
              >
                Category
              </label>
              <select
                id="category"
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
                className="w-full rounded-md border border-edge bg-night px-4 py-2.5 text-sm text-ink focus:border-cyan focus:outline-none"
              >
                <option value="">No category</option>
                {categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </Panel>

      <Panel className="p-6">
        <div className="flex items-center justify-between">
          <h2 className="inscription text-[0.68rem] text-cyan/80">Variants</h2>
          <Button
            tone="ghost"
            type="button"
            onClick={() => setVariants((current) => [...current, { ...EMPTY_VARIANT }])}
          >
            Add variant
          </Button>
        </div>
        <Meander className="my-4 max-w-[6rem]" />

        <p className="mb-5 text-xs text-faint">
          A variant is what a customer actually buys, and what holds the price and
          the stock. A product with one option still needs one variant.
        </p>

        <div className="space-y-4">
          {variants.map((variant, index) => (
            <div key={index} className="rounded-md border border-edge bg-night/50 p-4">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Field
                  label="SKU"
                  value={variant.sku}
                  onChange={(value) => updateVariant(index, { sku: value })}
                  hint="Unique across the catalogue."
                />
                <Field
                  label="Option name"
                  value={variant.name}
                  onChange={(value) => updateVariant(index, { name: value })}
                  hint='e.g. "Large" or "Standard".'
                />
                <Field
                  label="Price"
                  value={variant.price}
                  onChange={(value) => updateVariant(index, { price: value })}
                  inputMode="decimal"
                  hint={
                    toCents(variant.price) !== null
                      ? formatMoney(toCents(variant.price) ?? 0)
                      : "In dollars, e.g. 24.00"
                  }
                />
                <Field
                  label="Compare at"
                  value={variant.compareAt}
                  onChange={(value) => updateVariant(index, { compareAt: value })}
                  inputMode="decimal"
                  hint="Optional. Shows a sale badge."
                />
              </div>

              <div className="mt-4 flex flex-wrap items-end gap-5">
                <Field
                  label="Stock"
                  value={variant.stock}
                  onChange={(value) => updateVariant(index, { stock: value })}
                  inputMode="numeric"
                  className="w-28"
                />
                <Toggle
                  label="Track stock"
                  checked={variant.trackInventory}
                  onChange={(checked) => updateVariant(index, { trackInventory: checked })}
                  hint="Off for digital or made-to-order items."
                />
                <Toggle
                  label="Available"
                  checked={variant.isActive}
                  onChange={(checked) => updateVariant(index, { isActive: checked })}
                />
                {variants.length > 1 ? (
                  <button
                    type="button"
                    onClick={() =>
                      setVariants((current) => current.filter((_, i) => i !== index))
                    }
                    className="ml-auto text-xs text-faint underline-offset-4 transition-colors hover:text-danger hover:underline"
                  >
                    Remove
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      {isNew ? (
        <Panel className="p-6">
          <h2 className="inscription text-[0.68rem] text-cyan/80">Images</h2>
          <Meander className="my-4 max-w-[6rem]" />
          <p className="text-sm text-muted">
            Save the product first, then add images here.
          </p>
        </Panel>
      ) : (
        <GalleryEditor
          productId={productId}
          images={images}
          onChange={setImages}
          onError={setError}
        />
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={save} disabled={saving}>
          {saving ? "Saving…" : saved ? "Saved ✓" : isNew ? "Create product" : "Save changes"}
        </Button>
        <Button tone="ghost" type="button" onClick={() => router.push("/admin/products")}>
          Back to catalogue
        </Button>
        {!isNew ? (
          <Button tone="danger" type="button" onClick={archive} className="ml-auto">
            Archive
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Upload, order and annotate a product's images.
 */
function GalleryEditor({
  productId,
  images,
  onChange,
  onError,
}: {
  productId: string;
  images: ProductImage[];
  onChange: (images: ProductImage[]) => void;
  onError: (message: string | null) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const upload = useCallback(
    async (files: FileList) => {
      setUploading(true);
      onError(null);
      try {
        // Sequential rather than parallel. Each upload decodes and re-encodes
        // an image server-side, and firing ten at once just makes them all
        // slow while risking the gateway's rate limit.
        const added: ProductImage[] = [];
        for (const file of Array.from(files)) {
          const form = new FormData();
          form.append("file", file);

          // FormData must not be JSON-encoded, so this bypasses apiFetch's
          // json helper and sets no Content-Type - the browser has to add the
          // multipart boundary itself.
          const csrf = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)?.[1];
          const response = await fetch(
            `${apiBaseUrl()}/api/catalog/admin/media`,
            {
              method: "POST",
              body: form,
              credentials: "include",
              headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : undefined,
            },
          );
          if (!response.ok) {
            const body = await response.json().catch(() => null);
            throw new Error(body?.error?.message ?? "That image could not be uploaded.");
          }
          const media = (await response.json()) as { id: string };

          const attached = await apiFetch<ProductImage>(
            `/catalog/admin/products/${productId}/images`,
            { method: "POST", json: { mediaId: media.id, alt: null } },
          );
          added.push(attached);
        }
        onChange([...images, ...added]);
      } catch (caught) {
        onError(errorMessage(caught));
      } finally {
        setUploading(false);
        if (fileInput.current) fileInput.current.value = "";
      }
    },
    [productId, images, onChange, onError],
  );

  async function remove(imageId: string) {
    onError(null);
    try {
      await apiFetch<unknown>(`/catalog/admin/products/${productId}/images/${imageId}`, {
        method: "DELETE",
      });
      onChange(images.filter((image) => image.id !== imageId));
    } catch (caught) {
      onError(errorMessage(caught));
    }
  }

  async function move(index: number, direction: -1 | 1) {
    const next = [...images];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    const [moved] = next.splice(index, 1);
    if (!moved) return;
    next.splice(target, 0, moved);

    // Shown immediately, then confirmed. Waiting for the round trip makes
    // reordering feel broken; if it fails the error explains why.
    onChange(next);
    try {
      const confirmed = await apiFetch<ProductImage[]>(
        `/catalog/admin/products/${productId}/images/order`,
        { method: "PUT", json: { imageIds: next.map((image) => image.id) } },
      );
      onChange(confirmed);
    } catch (caught) {
      onError(errorMessage(caught));
    }
  }

  async function setAlt(imageId: string, alt: string) {
    try {
      await apiFetch<ProductImage>(
        `/catalog/admin/products/${productId}/images/${imageId}`,
        { method: "PATCH", json: { alt: alt || null } },
      );
    } catch (caught) {
      onError(errorMessage(caught));
    }
  }

  return (
    <Panel className="p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="inscription text-[0.68rem] text-cyan/80">Images</h2>
        <div>
          <input
            ref={fileInput}
            id="image-upload"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            multiple
            className="sr-only"
            onChange={(event) => {
              if (event.target.files?.length) void upload(event.target.files);
            }}
          />
          <label
            htmlFor="image-upload"
            className="inscription inline-flex cursor-pointer items-center rounded-md border border-edge-bright px-5 py-2.5 text-[0.7rem] text-ink transition-colors hover:border-cyan hover:text-cyan"
          >
            {uploading ? "Uploading…" : "Upload images"}
          </label>
        </div>
      </div>
      <Meander className="my-4 max-w-[6rem]" />

      <p className="mb-5 text-xs text-faint">
        JPEG, PNG or WebP, up to 8 MB each. Images are resized and stripped of
        metadata on upload. The first image is the one shown in listings.
      </p>

      {images.length === 0 ? (
        <p className="py-8 text-center text-sm text-faint">No images yet.</p>
      ) : (
        <ul className="space-y-3">
          {images.map((image, index) => (
            <li
              key={image.id}
              className="flex flex-wrap items-center gap-4 rounded-md border border-edge bg-night/50 p-3"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={mediaUrl(image.thumbUrl) ?? ""}
                alt=""
                className="h-16 w-16 rounded object-cover"
                width={64}
                height={64}
              />

              <div className="min-w-48 flex-1">
                <label
                  htmlFor={`alt-${image.id}`}
                  className="mb-1 block text-[0.62rem] text-faint"
                >
                  Alt text {index === 0 ? "(shown in listings)" : ""}
                </label>
                <input
                  id={`alt-${image.id}`}
                  defaultValue={image.alt ?? ""}
                  placeholder="Describe the image for screen readers"
                  onBlur={(event) => void setAlt(image.id, event.target.value)}
                  className="w-full rounded-md border border-edge bg-night px-3 py-1.5 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
                />
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void move(index, -1)}
                  disabled={index === 0}
                  aria-label="Move earlier"
                  className="rounded border border-edge-bright px-2 py-1 text-xs text-muted transition-colors hover:border-cyan hover:text-cyan disabled:opacity-30"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => void move(index, 1)}
                  disabled={index === images.length - 1}
                  aria-label="Move later"
                  className="rounded border border-edge-bright px-2 py-1 text-xs text-muted transition-colors hover:border-cyan hover:text-cyan disabled:opacity-30"
                >
                  ↓
                </button>
                <button
                  type="button"
                  onClick={() => void remove(image.id)}
                  className="text-xs text-faint underline-offset-4 transition-colors hover:text-danger hover:underline"
                >
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** A labelled text input. */
function Field({
  label,
  value,
  onChange,
  hint,
  error,
  className = "",
  ...props
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  error?: string;
  className?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const id = `field-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div className={className}>
      <label htmlFor={id} className="inscription mb-2 block text-[0.62rem] text-cyan/70">
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        className="w-full rounded-md border border-edge bg-night px-4 py-2.5 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
        {...props}
      />
      {error ? (
        <p className="mt-1.5 text-xs text-danger">{error}</p>
      ) : hint ? (
        <p className="mt-1.5 text-xs text-faint">{hint}</p>
      ) : null}
    </div>
  );
}

/** A labelled checkbox. */
function Toggle({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: string;
}) {
  const id = `toggle-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <div>
      <label htmlFor={id} className="flex cursor-pointer items-center gap-2 text-sm text-ink">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          className="h-4 w-4 accent-[var(--color-neon)]"
        />
        {label}
      </label>
      {hint ? <p className="mt-1 text-xs text-faint">{hint}</p> : null}
    </div>
  );
}
