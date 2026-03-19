"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";

export function PremiumAuroraBackground() {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        let width: number, height: number;
        let animationFrameId: number;
        let time = 0;

        const handleResize = () => {
            width = window.innerWidth;
            height = window.innerHeight;
            const dpr = Math.min(window.devicePixelRatio || 1, 1.5); // Cap low for mobile perf
            canvas.width = width * dpr;
            canvas.height = height * dpr;
            ctx.scale(dpr, dpr);
        };

        window.addEventListener("resize", handleResize);
        handleResize();

        const points = 10;
        const speed = 0.00015; // Slower for subtle movement

        // Obsidian + cyan — deep volcanic glow
        const color1 = "rgba(8, 40, 65, 0.35)";   // Deep obsidian cyan
        const color2 = "rgba(5, 15, 35, 0.4)";     // Obsidian dark
        const color3 = "rgba(14, 80, 120, 0.15)";  // Subtle cyan refraction

        const draw = () => {
            time += speed;
            ctx.clearRect(0, 0, width, height);

            // Obsidian base
            ctx.fillStyle = "rgba(5, 5, 8, 1)";
            ctx.fillRect(0, 0, width, height);

            ctx.globalCompositeOperation = "lighter";

            const drawWave = (offsetY: number, amplitude: number, offsetTime: number, fill: string) => {
                ctx.beginPath();
                ctx.moveTo(0, height);
                for (let i = 0; i <= points; i++) {
                    const x = (i / points) * width;
                    const y = offsetY +
                        Math.sin((i * 1.5) + time + offsetTime) * amplitude +
                        Math.cos((i * 0.5) + (time * 1.2)) * (amplitude * 0.4);
                    ctx.lineTo(x, y);
                }
                ctx.lineTo(width, height);
                ctx.closePath();
                ctx.fillStyle = fill;
                ctx.fill();
            };

            drawWave(height * 0.6, height * 0.25, 0, color1);
            drawWave(height * 0.55, height * 0.2, 4, color2);
            drawWave(height * 0.7, height * 0.15, 8, color3);

            ctx.globalCompositeOperation = "source-over";
            animationFrameId = requestAnimationFrame(draw);
        };

        draw();

        return () => {
            window.removeEventListener("resize", handleResize);
            cancelAnimationFrame(animationFrameId);
        };
    }, []);

    return (
        <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 2, ease: "easeOut" }}
            className="fixed inset-0 z-0 pointer-events-none"
        >
            <canvas
                ref={canvasRef}
                className="block w-full h-full opacity-50"
                style={{ filter: "blur(80px)" }}
            />
        </motion.div>
    );
}
