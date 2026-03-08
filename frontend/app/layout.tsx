import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import './globals.css';
import { SidebarProvider } from './components/SidebarContext';

const inter = Inter({ subsets: ['latin'], variable: '--font-sans' });
const jetbrainsMono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-mono' });

export const metadata: Metadata = {
  title: 'AIND Metadata Capture',
  description:
    'Agentic metadata capture system for Allen Institute for Neural Dynamics',
};

// Critical for mobile: without this, mobile browsers render at a ~980px
// "virtual" desktop width and then shrink-to-fit, making touch targets tiny.
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // Don't disable user zoom — accessibility requirement.
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${jetbrainsMono.variable} font-sans`}>
        <SidebarProvider>{children}</SidebarProvider>
      </body>
    </html>
  );
}
