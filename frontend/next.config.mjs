/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ['*'],
  async rewrites() {
    return [
      { source: '/chat', destination: 'http://localhost:8001/chat' },
      { source: '/records', destination: 'http://localhost:8001/records' },
      { source: '/records/:path*', destination: 'http://localhost:8001/records/:path*' },
      { source: '/sessions', destination: 'http://localhost:8001/sessions' },
      { source: '/sessions/:path*', destination: 'http://localhost:8001/sessions/:path*' },
      { source: '/models', destination: 'http://localhost:8001/models' },
      { source: '/health', destination: 'http://localhost:8001/health' },
      { source: '/upload', destination: 'http://localhost:8001/upload' },
      { source: '/uploads/:path*', destination: 'http://localhost:8001/uploads/:path*' },
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
