"use client";

/**
 * Customer accounts: browse, search and change status or role.
 *
 * Every view of a customer record here is written to the audit log by the auth
 * service. Watching the watchers is standard practice for any system holding
 * personal data, and it is what makes "who looked at this record" answerable.
 */

import { useState } from "react";

import { TableWrap, Td, Th } from "@/components/admin-ui";
import { errorMessage, useSession } from "@/components/session-provider";
import { Button, EmptyState, ErrorNote, Pill, Skeleton } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { useAsyncData } from "@/lib/use-async";
import { formatDate, formatRelative } from "@/lib/format";
import type { Page, User } from "@/lib/types";

const PAGE_SIZE = 25;

export default function AdminCustomersPage() {
  const { user: currentAdmin } = useSession();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [page, setPage] = useState(1);

  const { data, error: loadError, loading, reload } = useAsyncData<Page<User>>(() => {
    const query = new URLSearchParams({
      page: String(page),
      pageSize: String(PAGE_SIZE),
    });
    if (search.trim()) query.set("search", search.trim());
    if (role) query.set("role", role);
    return apiFetch<Page<User>>(`/auth/admin/users?${query}`);
  }, [page, search, role]);

  const error = actionError ?? loadError;

  async function setActive(userId: string, isActive: boolean) {
    setBusyId(userId);
    setActionError(null);
    try {
      await apiFetch<User>(`/auth/admin/users/${userId}`, {
        method: "PATCH",
        json: { isActive },
      });
      // Refetch rather than patching the row in place. Disabling an account
      // also revokes its sessions server-side, so re-reading is the only way
      // to be sure the table reflects what actually happened.
      reload();
    } catch (caught) {
      setActionError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1">
          <label htmlFor="customer-search" className="inscription mb-2 block text-[0.58rem] text-cyan/70">
            Search
          </label>
          <input
            id="customer-search"
            type="search"
            value={search}
            placeholder="Email or name"
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
            className="w-full rounded-md border border-edge bg-night px-4 py-2 text-sm text-ink placeholder:text-faint focus:border-cyan focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="customer-role" className="inscription mb-2 block text-[0.58rem] text-cyan/70">
            Role
          </label>
          <select
            id="customer-role"
            value={role}
            onChange={(event) => {
              setRole(event.target.value);
              setPage(1);
            }}
            className="rounded-md border border-edge bg-night px-4 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
          >
            <option value="">All roles</option>
            <option value="customer">Customers</option>
            <option value="admin">Administrators</option>
          </select>
        </div>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : data && data.items.length > 0 ? (
        <>
          <TableWrap>
            <thead>
              <tr>
                <Th>Email</Th>
                <Th>Name</Th>
                <Th>Role</Th>
                <Th>Joined</Th>
                <Th>Last seen</Th>
                <Th>Status</Th>
                <Th className="text-right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((account) => {
                const isSelf = account.id === currentAdmin?.id;
                return (
                  <tr key={account.id} className="transition-colors hover:bg-raised/50">
                    <Td className="text-xs">{account.email}</Td>
                    <Td className="text-xs text-muted">{account.fullName ?? "—"}</Td>
                    <Td>
                      <Pill
                        className={
                          account.role === "admin"
                            ? "border-gold/40 text-gold"
                            : "border-edge text-muted"
                        }
                      >
                        {account.role}
                      </Pill>
                    </Td>
                    <Td className="whitespace-nowrap text-xs text-muted">
                      {formatDate(account.createdAt)}
                    </Td>
                    <Td className="whitespace-nowrap text-xs text-muted">
                      {formatRelative(account.lastLoginAt)}
                    </Td>
                    <Td>
                      <Pill
                        className={
                          account.isActive
                            ? "border-ok/40 text-ok"
                            : "border-danger/40 text-danger"
                        }
                      >
                        {account.isActive ? "Active" : "Disabled"}
                      </Pill>
                    </Td>
                    <Td className="text-right">
                      {isSelf ? (
                        // The API rejects self-modification outright; saying so
                        // here is clearer than a button that always 403s.
                        <span className="text-xs text-faint">That&rsquo;s you</span>
                      ) : (
                        <Button
                          tone={account.isActive ? "danger" : "ghost"}
                          disabled={busyId === account.id}
                          onClick={() => void setActive(account.id, !account.isActive)}
                        >
                          {account.isActive ? "Disable" : "Enable"}
                        </Button>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </TableWrap>

          <div className="flex items-center justify-between text-xs text-muted">
            <span>
              {data.total} account{data.total === 1 ? "" : "s"}
            </span>
            <div className="flex items-center gap-3">
              <Button tone="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span>
                {page} / {totalPages}
              </span>
              <Button
                tone="ghost"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>

          <p className="text-xs text-faint">
            Disabling an account blocks sign-in immediately and revokes every active
            session, while preserving order history. Promotion to administrator
            additionally requires the address to be listed in{" "}
            <code className="rounded bg-void px-1.5 py-0.5 font-mono text-cyan">
              ADMIN_EMAILS
            </code>
            .
          </p>
        </>
      ) : (
        <EmptyState title="No accounts">
          {search || role ? "Nothing matches those filters." : "No one has signed up yet."}
        </EmptyState>
      )}
    </div>
  );
}
