"use client";

import createGlobe from "cobe";
import { useEffect, useRef, useState } from "react";
import { useScroll, useTransform, motion } from "framer-motion";

/**
 * Ultra-premium, massive, half-visible spinning globe.
 * Parallax-linked to scroll depth for buttery smooth transitions.
 */
export function HeroGlobe() {
    if (typeof window === "undefined") return null;
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);

    // Track scroll specifically within the hero area (first 100vh)
    const { scrollY } = useScroll();

    // Parallax effects
    const yTransform = useTransform(scrollY, [0, 800], [0, 150]);
    const scaleTransform = useTransform(scrollY, [0, 800], [1, 0.85]);
    const opacityTransform = useTransform(scrollY, [0, 800], [1, 0.1]);

    useEffect(() => {
        let phi = 0;
        let width = 0;

        const onResize = () => {
            if (canvasRef.current && canvasRef.current.parentElement) {
                // Force a massive size relative to the viewport for the "half visible" look
                width = window.innerWidth;
                if (width < 768) {
                    width = width * 1.5; // Even bigger proportion on mobile
                }
            } else {
                width = 1200; // Safe fallback
            }
        };

        window.addEventListener("resize", onResize);
        onResize();

        if (!canvasRef.current) return;

        let globe: any = null;

        const initTimeout = setTimeout(() => {
            if (!canvasRef.current) return;
            globe = createGlobe(canvasRef.current, {
                devicePixelRatio: Math.min(window.devicePixelRatio, 2), // Cap at 2x for performance on massive globes
                width: width * 2,
                height: width * 2,
                phi: 0,
                theta: -0.1, // Tilt slightly up to show the top hemisphere mostly
                dark: 1, // Full dark mode
                diffuse: 1.8, // High diffuse for dramatic lighting
                mapSamples: 24000, // Very high resolution map
                mapBrightness: 6, // Bright landmasses
                baseColor: [0.03, 0.03, 0.05], // Obsidian deep core
                markerColor: [1, 1, 1], // Pure white markers
                glowColor: [1.1, 1.15, 1.25], // Intense silver/white atmospheric halo
                markers: [
                    { location: [40.7128, -74.0060], size: 0.1 },
                    { location: [51.5074, -0.1278], size: 0.08 },
                    { location: [25.2048, 55.2708], size: 0.1 },
                    { location: [1.3521, 103.8198], size: 0.08 },
                    { location: [35.6762, 139.6503], size: 0.08 }
                ],
                onRender: (state) => {
                    state.phi = phi;
                    phi += 0.0015; // Slow, majestic rotation
                    state.width = width * 2;
                    state.height = width * 2;
                }
            });
        }, 100);

        return () => {
            if (globe) globe.destroy();
            clearTimeout(initTimeout);
            window.removeEventListener("resize", onResize);
        };
    }, []);

    return (
        <motion.div
            ref={containerRef}
            className="absolute -top-[10vh] left-1/2 -translate-x-1/2 w-[150vw] h-[150vw] md:w-[100vw] md:h-[100vw] lg:w-[1200px] lg:h-[1200px] pointer-events-none z-0"
            style={{
                y: yTransform,
                scale: scaleTransform,
                opacity: opacityTransform,
                willChange: "transform, opacity"
            }}
        >
            <div className="w-full h-full relative" style={{ transform: "translateZ(0)" }}>
                {/* 
                  Mask image removes the bottom half of the globe completely, 
                  blending it flawlessly into the background 
                */}
                <canvas
                    ref={canvasRef}
                    style={{
                        width: "100%",
                        height: "100%",
                        contain: "strict",
                        transform: "translateZ(0)",
                        willChange: "transform",
                        maskImage: "radial-gradient(ellipse at 50% 30%, black 20%, transparent 60%)",
                        WebkitMaskImage: "radial-gradient(ellipse at 50% 30%, black 20%, transparent 60%)"
                    }}
                    className="opacity-90"
                />
            </div>
        </motion.div>
    );
}
