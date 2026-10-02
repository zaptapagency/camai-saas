/**
 * NextAuth (v4) configuration — real SSO scaffolding that replaces the stub
 * role picker while keeping the app fully usable signed out.
 *
 * - "Demo SSO" Credentials provider: three seeded users (password "demo") whose
 *   role + tenant are returned on the user object and flow onto the session.
 * - OAuth is env-gated: if GOOGLE_CLIENT_ID is set we also register a Google
 *   provider, so production is config-only — no code change, no hard env
 *   dependency when it's absent.
 * - JWT sessions; the jwt/session callbacks carry role + tenant end to end.
 */

import type { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";

import type { Role } from "@/lib/roles";

interface DemoUser {
  id: string;
  email: string;
  name: string;
  role: Role;
  tenant: string;
}

/** Seeded demo accounts. Password for all of them is "demo". */
const DEMO_USERS: DemoUser[] = [
  {
    id: "demo-admin",
    email: "admin@camai.dev",
    name: "Demo Admin",
    role: "admin",
    tenant: "demo-tenant",
  },
  {
    id: "acme-manager",
    email: "manager@acme.dev",
    name: "Acme Manager",
    role: "manager",
    tenant: "acme-foods",
  },
  {
    id: "demo-viewer",
    email: "viewer@camai.dev",
    name: "Demo Viewer",
    role: "viewer",
    tenant: "demo-tenant",
  },
];

const DEMO_PASSWORD = "demo";

// Start with the always-available demo credentials provider; OAuth is appended
// below only when configured, so the module never requires those envs to exist.
const providers: NextAuthOptions["providers"] = [
  CredentialsProvider({
    id: "credentials",
    name: "Demo SSO",
    credentials: {
      email: { label: "Email", type: "email" },
      password: { label: "Password", type: "password" },
    },
    async authorize(credentials) {
      if (!credentials?.email || !credentials?.password) return null;
      const email = credentials.email.trim().toLowerCase();
      const match = DEMO_USERS.find((u) => u.email === email);
      if (!match || credentials.password !== DEMO_PASSWORD) return null;
      return {
        id: match.id,
        email: match.email,
        name: match.name,
        role: match.role,
        tenant: match.tenant,
      };
    },
  }),
];

// Env-gated OAuth: only register Google when a client id is present. Imported
// lazily so a missing dependency/env never breaks the demo credentials flow.
if (process.env.GOOGLE_CLIENT_ID) {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const GoogleProvider = require("next-auth/providers/google").default;
  providers.push(
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET ?? "",
    }),
  );
}

export const authOptions: NextAuthOptions = {
  providers,
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  callbacks: {
    async jwt({ token, user }) {
      // On sign-in `user` is present — copy the RBAC claims onto the token.
      if (user) {
        token.role = (user as { role?: Role }).role;
        token.tenant = (user as { tenant?: string }).tenant;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.role = token.role;
        session.user.tenant = token.tenant;
      }
      return session;
    },
  },
};
