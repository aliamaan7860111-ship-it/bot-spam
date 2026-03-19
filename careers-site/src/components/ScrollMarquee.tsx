"use client";

import { motion } from "framer-motion";

interface MarqueeProps {
    items: string[];
    speed?: number;
    className?: string;
}

export function ScrollMarquee({ items, speed = 30, className = "" }: MarqueeProps) {
    // Double the items for seamless infinite loop
    const doubled = [...items, ...items];

    return (
        <div className={`overflow-hidden whitespace-nowrap ${className}`}>
            <motion.div
                className="inline-flex gap-8 md:gap-16"
                animate={{ x: ["0%", "-50%"] }}
                transition={{
                    x: {
                        repeat: Infinity,
                        repeatType: "loop",
                        duration: speed,
                        ease: "linear",
                    },
                }}
            >
                {doubled.map((item, i) => (
                    <span
                        key={i}
                        className="inline-flex items-center gap-4 text-3xl md:text-5xl lg:text-7xl font-bold tracking-tighter text-zinc-800 select-none"
                    >
                        {item}
                        <span className="text-zinc-700 text-lg">◆</span>
                    </span>
                ))}
            </motion.div>
        </div>
    );
}
