import { defineConfig } from "astro/config";

// SITE_URL is set in Vercel (Project → Settings → Environment Variables) once the domain is known.
export default defineConfig({
  site: process.env.SITE_URL ?? "https://usa-immigration-ledger.vercel.app",
  trailingSlash: "always",
});
