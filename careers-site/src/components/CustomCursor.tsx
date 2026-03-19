"use client";

import { useEffect, useState } from "react";
import { motion, useSpring, useMotionValue } from "framer-motion";

/**
 * Zero-latency custom smooth cursor.
 * Uses exact mouse position for immediate tracking, with spring physics for the outer trail.
 * Includes `mix-blend-mode: difference` so it's always visible over light or dark content.
 */
export function CustomCursor() {
    const [isVisible, setIsVisible] = useState(false);
    const [isHovering, setIsHovering] = useState(false);

    // Exact coordinates for pure speed without lag
    const mouseX = useMotionValue(0);
    const mouseY = useMotionValue(0);

    // Spring physics for smooth trail effect
    const smoothX = useSpring(mouseX, { stiffness: 600, damping: 30, mass: 0.5 });
    const smoothY = useSpring(mouseY, { stiffness: 600, damping: 30, mass: 0.5 });

    useEffect(() => {
        // Only show on devices with a fine pointer (mouse, not touch)
        if (typeof window === "undefined" || !window.matchMedia("(pointer: fine)").matches) {
            return;
        }

        const manageMouseMove = (e: MouseEvent) => {
            mouseX.set(e.clientX);
            mouseY.set(e.clientY);
            if (!isVisible) setIsVisible(true);
        };

        const manageMouseLeave = () => {
            setIsVisible(false);
        };

        // Use event delegation to detect hovering over clickable elements
        const manageMouseOver = (e: MouseEvent) => {
            const target = e.target as HTMLElement;
            if (
                (target.tagName.toLowerCase() === "a" ||
                    target.tagName.toLowerCase() === "button" ||
                    target.closest("a") ||
                    target.closest("button") ||
                    window.getComputedStyle(target).cursor === "pointer")
            ) {
                setIsHovering(true);
            } else {
                setIsHovering(false);
            }
        };

        window.addEventListener("mousemove", manageMouseMove, { passive: true });
        window.addEventListener("mouseleave", manageMouseLeave);
        window.addEventListener("mouseover", manageMouseOver, { passive: true });

        return () => {
            window.removeEventListener("mousemove", manageMouseMove);
            window.removeEventListener("mouseleave", manageMouseLeave);
            window.removeEventListener("mouseover", manageMouseOver);
        };
    }, [isVisible, mouseX, mouseY]);

    if (!isVisible) return null;

    return (
        <>
            {/* Tiny exact leading dot — Instant mapping to cursor for zero perceived lag */}
            <motion.div
                className="fixed top-0 left-0 w-2 h-2 rounded-full bg-white pointer-events-none z-[9999] mix-blend-difference"
                style={{
                    x: mouseX,
                    y: mouseY,
                    translateX: "-50%",
                    translateY: "-50%",
                    opacity: isHovering ? 0 : 1,
                }}
            />

            {/* Smooth trailing glow — expands on hover */}
            <motion.div
                className="fixed top-0 left-0 w-8 h-8 rounded-full border border-white pointer-events-none z-[9999] mix-blend-difference"
                style={{
                    x: smoothX,
                    y: smoothY,
                    translateX: "-50%",
                    translateY: "-50%",
                    scale: isHovering ? 1.5 : 1,
                    backgroundColor: isHovering ? "rgba(255, 255, 255, 0.1)" : "transparent",
                }}
                animate={{
                    scale: isHovering ? 1.8 : 1,
                    opacity: isHovering ? 0.8 : 0.4,
                }}
                transition={{ type: "spring", stiffness: 300, damping: 20 }}
            />
        </>
    );
}
