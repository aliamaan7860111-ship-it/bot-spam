"use client";

import { useEffect, useRef, useState } from "react";
import { motion, useInView } from "framer-motion";

function CountUp({ target, suffix = "", duration = 2000 }: { target: number; suffix?: string; duration?: number }) {
    const [count, setCount] = useState(0);
    const ref = useRef<HTMLSpanElement>(null);
    const isInView = useInView(ref, { once: true, margin: "-50px" });

    useEffect(() => {
        if (!isInView) return;
        const start = Date.now();
        const step = () => {
            const elapsed = Date.now() - start;
            const progress = Math.min(elapsed / duration, 1);
            // Ease-out cubic for smooth deceleration
            const eased = 1 - Math.pow(1 - progress, 3);
            setCount(Math.floor(eased * target));
            if (progress < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    }, [isInView, target, duration]);

    return <span ref={ref}>{count}{suffix}</span>;
}

const stats = [
    { label: "Verticals", value: 5, suffix: "+" },
    { label: "Sub-Brands", value: 12, suffix: "" },
    { label: "Markets", value: 3, suffix: "+" },
    { label: "System Uptime", value: 99, suffix: ".9%" },
];

export function AnimatedStatsBar() {
    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
            className="w-full grid grid-cols-2 md:grid-cols-4 gap-6 md:gap-0 py-12 md:py-16 border-y border-white/10"
        >
            {stats.map((stat, i) => (
                <div
                    key={i}
                    className={`flex flex-col items-center gap-2 ${i < stats.length - 1 ? "md:border-r md:border-white/10" : ""
                        }`}
                >
                    <span className="text-4xl md:text-5xl font-bold tracking-tighter text-white">
                        <CountUp target={stat.value} suffix={stat.suffix} />
                    </span>
                    <span className="text-xs font-mono uppercase tracking-widest text-zinc-500">
                        {stat.label}
                    </span>
                </div>
            ))}
        </motion.div>
    );
}
