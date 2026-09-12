"use client";

/**
 * Session and cart state, shared across the app.
 *
 * One provider rather than two, because they are coupled: signing in claims
 * the guest cart, and signing out must clear it. Splitting them would mean
 * every sign-in and sign-out had to remember to poke the other context.
 *
 * The session is *derived*, never authoritative. The real credential is an
 * httpOnly cookie the browser holds and JavaScript cannot read; this is a copy
 * of who the API says you are, kept so the UI can render without asking again.
 * Every protected action is still authorised server-side — hiding the admin
 * link is a courtesy, not a control.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { track } from "@/lib/analytics";
import type { Cart, Session, User } from "@/lib/types";

interface SessionContextValue {
  user: User | null;
  cart: Cart | null;
  /** True until the first session check resolves. */
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, fullName?: string) => Promise<void>;
  signOut: () => Promise<void>;
  refreshCart: () => Promise<void>;
  addToCart: (variantId: string, quantity?: number) => Promise<void>;
  setCartQuantity: (itemId: string, quantity: number) => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/**
 * Provides session and cart state to the tree.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [cart, setCart] = useState<Cart | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshCart = useCallback(async () => {
    try {
      setCart(await apiFetch<Cart>("/orders/cart"));
    } catch {
      // A cart that fails to load must not blank the page. The badge simply
      // shows nothing until the next successful fetch.
    }
  }, []);

  // Establish who we are on first mount. A 401 here is the normal state for a
  // visitor who is not signed in, so it is not an error.
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const me = await apiFetch<User>("/auth/me", { retryOnUnauthorized: true });
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
      if (!cancelled) await refreshCart();
    })();

    return () => {
      cancelled = true;
    };
  }, [refreshCart]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const session = await apiFetch<Session>("/auth/login", {
        method: "POST",
        json: { email, password },
      });
      setUser(session.user);
      track("login");
      // The guest cart is claimed by the API on sign-in, so it is re-fetched
      // rather than cleared — a shopper who signs in at checkout keeps what
      // they were buying.
      await refreshCart();
    },
    [refreshCart],
  );

  const signUp = useCallback(
    async (email: string, password: string, fullName?: string) => {
      const session = await apiFetch<Session>("/auth/register", {
        method: "POST",
        json: { email, password, fullName: fullName || null },
      });
      setUser(session.user);
      track("signup");
      await refreshCart();
    },
    [refreshCart],
  );

  const signOut = useCallback(async () => {
    try {
      await apiFetch<unknown>("/auth/logout", { method: "POST" });
    } finally {
      // Cleared even if the request failed. A sign-out that appears not to
      // work is alarming, and the cookies are gone either way.
      setUser(null);
      await refreshCart();
    }
  }, [refreshCart]);

  const addToCart = useCallback(
    async (variantId: string, quantity = 1) => {
      const updated = await apiFetch<Cart>("/orders/cart/items", {
        method: "POST",
        json: { variantId, quantity },
      });
      setCart(updated);
      track("add_to_cart", { variantId, quantity });
    },
    [],
  );

  const setCartQuantity = useCallback(async (itemId: string, quantity: number) => {
    const updated = await apiFetch<Cart>(`/orders/cart/items/${itemId}`, {
      method: "PATCH",
      json: { quantity },
    });
    setCart(updated);
    if (quantity === 0) track("remove_from_cart", { itemId });
  }, []);

  const value = useMemo(
    () => ({
      user,
      cart,
      loading,
      signIn,
      signUp,
      signOut,
      refreshCart,
      addToCart,
      setCartQuantity,
    }),
    [user, cart, loading, signIn, signUp, signOut, refreshCart, addToCart, setCartQuantity],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/**
 * Access session and cart state.
 *
 * @throws If called outside `SessionProvider`. Failing loudly here beats
 *   returning undefined and crashing somewhere less obvious.
 */
export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used inside <SessionProvider>.");
  }
  return context;
}

/** Narrow an unknown error to a user-safe message. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong. Please try again.";
}
