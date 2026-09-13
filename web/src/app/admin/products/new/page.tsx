"use client";

/** Create a product. */

import Link from "next/link";

import { ProductEditor } from "@/components/product-editor";

export default function NewProductPage() {
  return (
    <div className="space-y-6">
      <Link
        href="/admin/products"
        className="text-xs text-faint underline-offset-4 transition-colors hover:text-cyan hover:underline"
      >
        ← Catalogue
      </Link>
      <h2 className="inscription text-lg text-marble">New product</h2>
      <ProductEditor productId={null} />
    </div>
  );
}
