"use client";

import { motion } from "framer-motion";

export function MobileGlobePulse() {
    return (
        <div className="absolute top-1/2 right-[-20%] -translate-y-1/2 w-[400px] h-[400px] pointer-events-none -z-10">
            <div className="relative w-full h-full" style={{ maskImage: 'radial-gradient(circle, black, transparent 70%)', WebkitMaskImage: 'radial-gradient(circle, black, transparent 70%)' }}>
                {/* Core Glow */}
                <div className="absolute inset-0 bg-blue-500/20 rounded-full blur-[60px]" />

                {/* Pulsing Concentric Circles */}
                <motion.div
                    animate={{ scale: [1, 1.5, 1], opacity: [0.1, 0.2, 0.1] }}
                    transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
                    className="absolute inset-4 border border-blue-500/20 rounded-full"
                />
                <motion.div
                    animate={{ scale: [1.2, 0.8, 1.2], opacity: [0.05, 0.15, 0.05] }}
                    transition={{ duration: 6, repeat: Infinity, ease: "easeInOut", delay: 1 }}
                    className="absolute inset-12 border border-blue-500/10 rounded-full"
                />

                {/* Random "Nodes" appearing/disappearing */}
                <motion.div
                    animate={{ opacity: [0, 1, 0] }}
                    transition={{ duration: 3, repeat: Infinity, delay: 0.5 }}
                    className="absolute top-1/4 left-1/2 w-1 h-1 bg-cyan-400 rounded-full shadow-[0_0_8px_cyan]"
                />
                <motion.div
                    animate={{ opacity: [0, 1, 0] }}
                    transition={{ duration: 4, repeat: Infinity, delay: 2 }}
                    className="absolute bottom-1/3 right-1/4 w-1 h-1 bg-cyan-400 rounded-full shadow-[0_0_8px_cyan]"
                />
            </div>
        </div>
    );
}
