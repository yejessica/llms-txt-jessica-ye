import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  // A trailing slash belongs to the target URL in /example.com/docs/.
  skipTrailingSlashRedirect: true,
};

export default config;
