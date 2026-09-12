/**
 * Types mirroring the API's response models.
 *
 * Hand-written rather than generated. The backend publishes OpenAPI at
 * /openapi.json and a generator is the right move once the API stabilises;
 * while it is still moving, a generated client adds a build step and a
 * regeneration ritual for types that are read far more often than they change.
 *
 * Everything is camelCase, because the API serialises with a camelCase alias
 * generator — see ecom_shared/schemas.py.
 *
 * Money is always `…Cents`: an integer number of minor units, never a float.
 * Use `formatMoney` from lib/format to display it.
 */

export interface ApiErrorBody {
  error: {
    /** Stable machine-readable slug, e.g. "not_found". Switch on this. */
    code: string;
    /** User-safe message. Rendering it directly is intended. */
    message: string;
    /** Field-level detail, e.g. { email: "value is not a valid email" }. */
    details: Record<string, unknown>;
    /** Correlation ID. Show it on error screens so a user can quote it. */
    request_id: string;
  };
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

/* --- Auth ---------------------------------------------------------------- */

export type Role = "customer" | "admin";

export interface User {
  id: string;
  email: string;
  fullName: string | null;
  role: Role;
  isActive: boolean;
  emailVerifiedAt: string | null;
  lastLoginAt: string | null;
  createdAt: string;
}

export interface Session {
  user: User;
  expiresIn: number;
  csrfToken: string;
}

/* --- Catalog ------------------------------------------------------------- */

export interface Variant {
  id: string;
  sku: string;
  name: string;
  priceCents: number;
  compareAtPriceCents: number | null;
  currency: string;
  /** Availability only. The exact stock figure is admin-only, by design. */
  inStock: boolean;
  position: number;
}

export interface AdminVariant extends Variant {
  inventoryQuantity: number;
  trackInventory: boolean;
  isActive: boolean;
  weightGrams: number | null;
}

export interface Category {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  position?: number;
  isActive?: boolean;
  productCount?: number;
}

export interface Product {
  id: string;
  slug: string;
  title: string;
  subtitle: string | null;
  description: string | null;
  status: "draft" | "active" | "archived";
  imageUrl: string | null;
  category: Pick<Category, "id" | "slug" | "name"> | null;
  variants: Variant[];
  createdAt: string;
}

/* --- Cart and orders ----------------------------------------------------- */

export interface CartItem {
  id: string;
  variantId: string;
  sku: string;
  productTitle: string;
  productSlug: string | null;
  variantName: string;
  imageUrl: string | null;
  unitPriceCents: number;
  quantity: number;
  totalCents: number;
  available: boolean;
}

export interface Cart {
  id: string;
  items: CartItem[];
  subtotalCents: number;
  taxCents: number;
  shippingCents: number;
  totalCents: number;
  currency: string;
  itemCount: number;
  hasUnavailableItems: boolean;
}

export interface Address {
  fullName: string;
  line1: string;
  line2?: string | null;
  city: string;
  region?: string | null;
  postalCode: string;
  country: string;
  phone?: string | null;
}

export type OrderStatus =
  | "pending_payment"
  | "paid"
  | "fulfilled"
  | "delivered"
  | "cancelled"
  | "refunded";

export interface OrderItem {
  id: string;
  variantId: string;
  sku: string;
  productTitle: string;
  productSlug: string | null;
  variantName: string;
  imageUrl: string | null;
  unitPriceCents: number;
  quantity: number;
  totalCents: number;
}

export interface OrderEvent {
  status: string;
  note: string | null;
  createdAt: string;
}

export interface Order {
  id: string;
  orderNumber: string;
  status: OrderStatus;
  email: string;
  subtotalCents: number;
  taxCents: number;
  shippingCents: number;
  totalCents: number;
  currency: string;
  shippingAddress: Record<string, string>;
  billingAddress: Record<string, string>;
  items: OrderItem[];
  events: OrderEvent[];
  placedAt: string | null;
  paidAt: string | null;
  createdAt: string;
}

export interface OrderSummary {
  id: string;
  orderNumber: string;
  status: OrderStatus;
  email: string;
  totalCents: number;
  currency: string;
  itemCount: number;
  createdAt: string;
}

export interface CheckoutResult {
  orderId: string;
  orderNumber: string;
  totalCents: number;
  currency: string;
  /** Scoped to this one payment. Safe in the browser; not an API key. */
  clientSecret: string;
  paymentIntentId: string;
}

/* --- Admin --------------------------------------------------------------- */

export interface UserStats {
  total: number;
  active: number;
  admins: number;
  newThisWeek: number;
}

export interface CatalogStats {
  totalProducts: number;
  activeProducts: number;
  draftProducts: number;
  categories: number;
  unitsInStock: number;
  outOfStockVariants: number;
}

export interface OrderStats {
  totalOrders: number;
  paidOrders: number;
  revenueCents: number;
  averageOrderValueCents: number;
  recentOrders: number;
  recentRevenueCents: number;
  windowDays: number;
  byStatus: Record<string, number>;
}

export interface PaymentStats {
  totalPayments: number;
  succeeded: number;
  failed: number;
  capturedCents: number;
  refundedCents: number;
  netCents: number;
}

export interface TrafficSummary {
  windowDays: number;
  pageViews: number;
  uniqueVisitors: number;
  sessions: number;
  todayPageViews: number;
  todayVisitors: number;
}

export interface TrafficPoint {
  day: string;
  pageViews: number;
  visitors: number;
}

export interface TopPage {
  path: string;
  views: number;
  visitors: number;
}

export interface FunnelStep {
  step: string;
  sessions: number;
  rateFromTop: number;
}

export interface BreakdownRow {
  value: string;
  sessions: number;
}

export interface Payment {
  id: string;
  orderId: string;
  orderNumber: string;
  paymentIntentId: string;
  status: string;
  stripeStatus: string | null;
  amountCents: number;
  amountReceivedCents: number;
  currency: string;
  email: string | null;
  failureMessage: string | null;
  succeededAt: string | null;
  createdAt: string;
}
