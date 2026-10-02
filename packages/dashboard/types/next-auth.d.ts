/**
 * Module augmentation so the NextAuth session/user and the JWT carry the CamAI
 * RBAC claims (role + tenant). These flow from `lib/auth.ts` callbacks onto
 * `session.user`, where `RoleProvider`/`TenantControls` can read them.
 */

import type { Role } from "@/lib/roles";
import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      role?: Role;
      tenant?: string;
    } & DefaultSession["user"];
  }

  interface User {
    role?: Role;
    tenant?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    role?: Role;
    tenant?: string;
  }
}
