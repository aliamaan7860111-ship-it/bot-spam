"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

/**
 * Sticky floating CTA — bottom-center Jesko-style pill.
 * White theme, appears after scrolling past hero.
 */
export function StickyFloatingCTA() {
    const [visible, setVisible] = useState(false);

    useEffect(() => {
        const handleScroll = () => {
            setVisible(window.scrollY > window.innerHeight);
        };
        window.addEventListener("scroll", handleScroll, { passive: true });
        return () => window.removeEventListener("scroll", handleScroll);
    }, []);

    return (
        <AnimatePresence>
            {visible && (
                <motion.div
                    initial={{ opacity: 0, y: 20, scale: 0.9 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 20, scale: 0.9 }}
                    transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                    className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50"
                >
                    <Link
                        href="/corporate-inquiry"
                        className="group flex items-center gap-2 px-6 py-3 bg-white text-black text-[13px] font-semibold rounded-full shadow-[0_4px_20px_rgba(0,0,0,0.3)] hover:bg-zinc-100 transition-all"
                    >
                        Get in Touch
                        <ArrowUpRight className="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                    </Link>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
