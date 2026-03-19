"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";

/**
 * Floating Particle Field — a modern, subtle hero background.
 * Renders ~60 small dots that drift slowly. Pure 2D canvas, capped at 30fps on mobile.
 * Much lighter and more premium than a wireframe sphere.
 */
export function FloatingParticles() {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        let width: number, height: number;
        let animationFrameId: number;
        const isMobile = window.innerWidth < 768;
        const particleCount = isMobile ? 30 : 60;
        const frameInterval = isMobile ? 33 : 16; // 30fps mobile, 60fps desktop
        let lastFrame = 0;

        interface Particle {
            x: number;
            y: number;
            vx: number;
            vy: number;
            size: number;
            opacity: number;
        }

        let particles: Particle[] = [];

        const handleResize = () => {
            width = canvas.clientWidth;
            height = canvas.clientHeight;
            const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
            canvas.width = width * dpr;
            canvas.height = height * dpr;
            ctx.scale(dpr, dpr);
        };

        const initParticles = () => {
            particles = [];
            for (let i = 0; i < particleCount; i++) {
                particles.push({
                    x: Math.random() * width,
                    y: Math.random() * height,
                    vx: (Math.random() - 0.5) * 0.3,
                    vy: (Math.random() - 0.5) * 0.2,
                    size: Math.random() * 2 + 0.5,
                    opacity: Math.random() * 0.3 + 0.05,
                });
            }
        };

        window.addEventListener("resize", () => { handleResize(); initParticles(); });
        handleResize();
        initParticles();

        const draw = (time: number) => {
            animationFrameId = requestAnimationFrame(draw);

            // Frame throttle for mobile
            if (time - lastFrame < frameInterval) return;
            lastFrame = time;

            ctx.clearRect(0, 0, width, height);

            for (const p of particles) {
                p.x += p.vx;
                p.y += p.vy;

                // Wrap around edges
                if (p.x < 0) p.x = width;
                if (p.x > width) p.x = 0;
                if (p.y < 0) p.y = height;
                if (p.y > height) p.y = 0;

                ctx.beginPath();
                ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(255, 255, 255, ${p.opacity})`;
                ctx.fill();
            }

            // Draw very subtle connections between nearby particles (desktop only)
            if (!isMobile) {
                const maxDist = 120;
                for (let i = 0; i < particles.length; i++) {
                    for (let j = i + 1; j < particles.length; j++) {
                        const dx = particles[i].x - particles[j].x;
                        const dy = particles[i].y - particles[j].y;
                        const dist = Math.sqrt(dx * dx + dy * dy);
                        if (dist < maxDist) {
                            ctx.beginPath();
                            ctx.moveTo(particles[i].x, particles[i].y);
                            ctx.lineTo(particles[j].x, particles[j].y);
                            ctx.strokeStyle = `rgba(255, 255, 255, ${0.03 * (1 - dist / maxDist)})`;
                            ctx.lineWidth = 0.5;
                            ctx.stroke();
                        }
                    }
                }
            }
        };

        animationFrameId = requestAnimationFrame(draw);

        return () => {
            window.removeEventListener("resize", handleResize);
            cancelAnimationFrame(animationFrameId);
        };
    }, []);

    return (
        <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 2, delay: 0.5 }}
            className="absolute inset-0 z-0 pointer-events-none"
        >
            <canvas
                ref={canvasRef}
                className="block w-full h-full"
            />
        </motion.div>
    );
}
