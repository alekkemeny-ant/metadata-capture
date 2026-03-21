/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ['*.replit.dev', '*.repl.co', '*.replit.app', '*.kirk.replit.dev'],
  eslint: {
    ignoreDuringBuilds: true,
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8001',
  },
  async rewrites() {
    return [
      { source: '/records', destination: 'http://localhost:8001/records' },
      { source: '/records/:path*', destination: 'http://localhost:8001/records/:path*' },
      { source: '/sessions', destination: 'http://localhost:8001/sessions' },
      { source: '/sessions/:path*', destination: 'http://localhost:8001/sessions/:path*' },
      { source: '/models', destination: 'http://localhost:8001/models' },
      { source: '/health', destination: 'http://localhost:8001/health' },
      { source: '/upload', destination: 'http://localhost:8001/upload' },
      { source: '/upload/init', destination: 'http://localhost:8001/upload/init' },
      { source: '/upload/chunk', destination: 'http://localhost:8001/upload/chunk' },
      { source: '/upload/finalize', destination: 'http://localhost:8001/upload/finalize' },
      { source: '/uploads/:path*', destination: 'http://localhost:8001/uploads/:path*' },
      { source: '/artifacts/:path*', destination: 'http://localhost:8001/artifacts/:path*' },
      { source: '/schema/:path*', destination: 'http://localhost:8001/schema/:path*' },
      { source: '/chat', destination: 'http://localhost:8001/chat' },
    ];
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' },
        ],
      },
    ];
  },
};

export default nextConfig;
