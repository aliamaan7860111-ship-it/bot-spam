"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";

export function GeometricMeshHero() {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext("2d", { alpha: false }); // Disable alpha for better performance
        if (!ctx) return;

        let width: number, height: number;
        let animationFrameId: number;

        const points: { x: number, y: number, z: number, bx: number, by: number, bz: number }[] = [];
        const numPoints = 80;
        const connectionDistance = 150;

        // Rotation angles
        let angleX = 0;
        let angleY = 0;

        const handleResize = () => {
            width = canvas.parentElement?.offsetWidth || window.innerWidth / 2;
            height = canvas.parentElement?.offsetHeight || window.innerHeight / 2;

            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = width * dpr;
            canvas.height = height * dpr;
            ctx.scale(dpr, dpr);

            // Re-initialize points in a sphere shape
            points.length = 0;
            const radius = Math.min(width, height) * 0.4;

            for (let i = 0; i < numPoints; i++) {
                // Fibonacci sphere distribution for even spread
                const phi = Math.acos(-1 + (2 * i) / numPoints);
                const theta = Math.sqrt(numPoints * Math.PI) * phi;

                points.push({
                    x: radius * Math.cos(theta) * Math.sin(phi),
                    y: radius * Math.sin(theta) * Math.sin(phi),
                    z: radius * Math.cos(phi),
                    bx: radius * Math.cos(theta) * Math.sin(phi),
                    by: radius * Math.sin(theta) * Math.sin(phi),
                    bz: radius * Math.cos(phi),
                });
            }
        };

        window.addEventListener("resize", handleResize);
        handleResize();

        const draw = () => {
            // Very dark zinc background
            ctx.fillStyle = "#09090b"; // bg-zinc-950
            ctx.fillRect(0, 0, width, height);

            // Slow elegant rotation
            angleX += 0.002;
            angleY += 0.003;

            const cosX = Math.cos(angleX);
            const sinX = Math.sin(angleX);
            const cosY = Math.cos(angleY);
            const sinY = Math.sin(angleY);

            const projectedPoints = points.map(p => {
                // Rotate around Y
                let x1 = p.x * cosY - p.z * sinY;
                let z1 = p.z * cosY + p.x * sinY;

                // Rotate around X
                let y2 = p.y * cosX - z1 * sinX;
                let z2 = z1 * cosX + p.y * sinX;

                // Simple perspective projection
                const distance = 800;
                const scale = distance / (distance + z2);

                return {
                    x: (x1 * scale) + (width / 2),
                    y: (y2 * scale) + (height / 2),
                    z: z2,
                    scale
                };
            });

            // Draw connections
            ctx.lineWidth = 1;

            for (let i = 0; i < projectedPoints.length; i++) {
                for (let j = i + 1; j < projectedPoints.length; j++) {
                    const p1 = projectedPoints[i];
                    const p2 = projectedPoints[j];

                    const dx = p1.x - p2.x;
                    const dy = p1.y - p2.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);

                    if (dist < connectionDistance) {
                        // Fade lines based on depth (Z) and screen distance
                        const alpha = (1 - dist / connectionDistance) * 0.4;
                        // Darker lines if pushed to the back
                        const depthFade = Math.max(0.1, (p1.scale + p2.scale) / 2);

                        ctx.beginPath();
                        ctx.moveTo(p1.x, p1.y);
                        ctx.lineTo(p2.x, p2.y);
                        ctx.strokeStyle = `rgba(255, 255, 255, ${alpha * depthFade})`;
                        ctx.stroke();
                    }
                }
            }

            // Draw nodes
            projectedPoints.forEach(p => {
                const radius = Math.max(0.5, p.scale * 2);
                const alpha = Math.max(0.2, p.scale);

                ctx.beginPath();
                ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(255, 255, 255, ${alpha})`;
                ctx.fill();

                // Add subtle blue glow to forward-facing nodes
                if (p.scale > 1.1) {
                    ctx.shadowBlur = 10;
                    ctx.shadowColor = "rgba(59, 130, 246, 0.5)"; // Blue 500
                    ctx.fill();
                    ctx.shadowBlur = 0; // Reset
                }
            });

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
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 1.5, ease: "easeOut" }}
            className="w-full h-full relative"
        >
            {/* Hardware accelerated mask to fade edges perfectly */}
            <canvas
                ref={canvasRef}
                className="w-full h-full"
                style={{
                    maskImage: "radial-gradient(circle at center, black 40%, transparent 80%)",
                    WebkitMaskImage: "radial-gradient(circle at center, black 40%, transparent 80%)"
                }}
            />
        </motion.div>
    );
}
