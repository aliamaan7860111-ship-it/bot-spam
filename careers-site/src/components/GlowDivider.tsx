"use client";

import { motion } from "framer-motion";

export function GlowDivider({ color = "white" }: { color?: string }) {
    const colorMap: Record<string, string> = {
        white: "rgba(255,255,255,0.15)",
        blue: "rgba(59,130,246,0.2)",
        violet: "rgba(139,92,246,0.2)",
        amber: "rgba(245,158,11,0.15)",
    };
    const glow = colorMap[color] || colorMap.white;

    return (
        <motion.div
            initial={{ opacity: 0, scaleX: 0 }}
            whileInView={{ opacity: 1, scaleX: 1 }}
            viewport={{ once: true, margin: "-50px" }}
            transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
            className="relative w-full h-px my-0"
        >
            <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent" />
            <div
                className="absolute inset-0 blur-md"
                style={{ background: `linear-gradient(90deg, transparent, ${glow}, transparent)` }}
            />
        </motion.div>
    );
}
