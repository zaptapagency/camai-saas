"use client";

/**
 * Client-side provider stack for the whole app: React Query (data/polling),
 * tenant selection, and the stub RBAC role context. Kept in one place so the
 * root layout stays a server component.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { useState, type ReactNode } from "react";

import { RoleProvider } from "@/lib/roles";
import { TenantProvider } from "@/lib/tenant";

export function Providers({ children }: { children: ReactNode }) {
  // One client per browser session. `staleTime` short because the data is live;
  // component-level `refetchInterval` (from useTenant) drives polling.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 2_000,
            refetchOnWindowFocus: true,
          },
        },
      }),
  );

  return (
    <SessionProvider>
      <QueryClientProvider client={client}>
        <TenantProvider>
          <RoleProvider>{children}</RoleProvider>
        </TenantProvider>
      </QueryClientProvider>
    </SessionProvider>
  );
}
