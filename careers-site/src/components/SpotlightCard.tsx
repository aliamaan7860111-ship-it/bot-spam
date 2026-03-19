"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useRef, useState } from "react";
import { motion, useMotionTemplate, useMotionValue } from "framer-motion";

interface RoleCardProps {
    id: string;
    title: string;
    location: string;
    type: string;
    hook: string;
}

export function SpotlightCard({ id, title, location, type, hook }: RoleCardProps) {
    const mouseX = useMotionValue(0);
    const mouseY = useMotionValue(0);
    const [isHovering, setIsHovering] = useState(false);

    function handleMouseMove({ currentTarget, clientX, clientY }: React.MouseEvent) {
        const { left, top } = currentTarget.getBoundingClientRect();
        mouseX.set(clientX - left);
        mouseY.set(clientY - top);
    }

    // Create a motion-enabled Link component
    const MotionLink = motion.create(Link);

    return (
        <MotionLink
            href={`/apply?role=${id}`}
            whileHover={{ y: -6, scale: 1.02 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
            className="group relative flex flex-col h-full overflow-hidden rounded-2xl border border-white/5 bg-black/40 backdrop-blur-sm p-8 transition-colors hover:border-white/10"
            onMouseMove={handleMouseMove}
            onMouseEnter={() => setIsHovering(true)}
            onMouseLeave={() => setIsHovering(false)}
        >
            {/* Magnetic Glow Effect */}
            <motion.div
                className="pointer-events-none absolute -inset-px rounded-2xl opacity-0 transition duration-300 group-hover:opacity-100"
                style={{
                    background: useMotionTemplate`
            radial-gradient(
              650px circle at ${mouseX}px ${mouseY}px,
              rgba(255,255,255,0.06),
              transparent 80%
            )
          `,
                }}
            />



            {/* Subtle Inner Border Glow on Hover */}
            <div
                className="pointer-events-none absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-500 group-hover:opacity-100 ring-1 ring-inset ring-white/10"
            />

            <div className="relative z-10 flex flex-col h-full justify-between gap-10">
                <div className="space-y-4">
                    <div className="flex items-center gap-3 text-xs font-mono text-zinc-500 uppercase tracking-wider">
                        <span>{location}</span>
                        <span className="w-1 h-1 rounded-full bg-zinc-700" />
                        <span>{type}</span>
                    </div>
                    <h3 className="text-2xl font-medium text-white tracking-tight">
                        {title}
                    </h3>
                    <p className="text-sm text-zinc-400 leading-relaxed font-light">
                        {hook}
                    </p>
                </div>

                <div className="flex items-center gap-2 text-sm font-medium text-zinc-300 group-hover:text-white transition-colors">
                    Apply Now
                    <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                </div>
            </div>
        </MotionLink>
    );
}
