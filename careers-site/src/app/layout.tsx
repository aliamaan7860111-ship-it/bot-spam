import type { Metadata } from "next";
import Link from "next/link";
import { ScrollProgress } from "@/components/ScrollProgress";
import { SmoothScroll } from "@/components/SmoothScroll";
import { MobileNav } from "@/components/MobileNav";
import "./globals.css";

import { Footer } from "@/components/Footer";

export const metadata: Metadata = {
  title: "GRQ Holdings",
  description: "Join GRQ Holdings.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark scroll-smooth" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet" />
      </head>
      <body suppressHydrationWarning className="font-sans min-h-screen bg-[#050508] text-white flex flex-col" style={{ fontFamily: "'Inter', sans-serif" }}>
        <SmoothScroll>
          {/* ──── EOS-style Minimal Nav ──── */}
          <header suppressHydrationWarning className="fixed top-0 left-0 right-0 z-50">
            <ScrollProgress />
            <div className="max-w-7xl mx-auto px-5 md:px-10 h-16 md:h-[72px] flex items-center justify-between">

              {/* Logo */}
              <Link href="/" className="text-lg font-bold tracking-tight text-white flex items-center gap-2.5 relative z-10">
                <div className="w-5 h-5 rounded bg-white" />
                GRQ
              </Link>

              {/* Desktop nav links — centered */}
              <nav suppressHydrationWarning className="hidden md:flex items-center gap-8 absolute left-1/2 -translate-x-1/2">
                <Link href="/" className="text-[13px] font-medium text-zinc-400 hover:text-white transition-colors">
                  Home
                </Link>
                <Link href="/about" className="text-[13px] font-medium text-zinc-400 hover:text-white transition-colors">
                  About
                </Link>
                <Link href="/work" className="text-[13px] font-medium text-zinc-400 hover:text-white transition-colors">
                  Our Work
                </Link>
                <Link href="/careers" className="text-[13px] font-medium text-zinc-400 hover:text-white transition-colors">
                  Careers
                </Link>
              </nav>

              {/* Desktop CTA pill */}
              <Link
                href="/careers"
                className="hidden md:flex items-center justify-center px-5 py-2 text-[13px] font-semibold rounded-full border border-white/20 text-white hover:bg-white hover:text-black transition-all duration-300 relative z-10"
              >
                Get Started
              </Link>

              {/* Mobile CTA + Menu */}
              <MobileNav />
            </div>
          </header>

          {/* Main Content */}
          <main suppressHydrationWarning className="flex-1 w-full overflow-x-hidden">
            {children}
          </main>

          <Footer />
        </SmoothScroll>
      </body>
    </html>
  );
}
