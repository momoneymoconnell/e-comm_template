"use client";

/** Edit a product, its variants and its images. */

import Link from "next/link";
import { use } from "react";

import { ProductEditor } from "@/components/product-editor";

export default function EditProductPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  return (
    <div className="space-y-6">
      <Link
        href="/admin/products"
        className="text-xs text-faint underline-offset-4 transition-colors hover:text-cyan hover:underline"
      >
        ← Catalogue
      </Link>
      <h2 className="inscription text-lg text-marble">Edit product</h2>
      <ProductEditor productId={id} />
    </div>
  );
}
