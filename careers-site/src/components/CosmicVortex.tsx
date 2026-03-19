"use client";

import { useEffect, useRef } from "react";

/**
 * Cosmic vortex background — obsidian + white/silver theme.
 * Draws a static galactic spiral to canvas once, CSS handles rotation.
 * GPU-composited: zero JS per-frame cost.
 */
export function CosmicVortex() {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        const size = 1024;
        canvas.width = size;
        canvas.height = size;

        const cx = size / 2;
        const cy = size / 2;

        const drawVortex = () => {
            ctx.clearRect(0, 0, size, size);

            // Deep radial glow
            const bgGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, size * 0.5);
            bgGrad.addColorStop(0, "rgba(0, 0, 0, 1)");
            bgGrad.addColorStop(0.25, "rgba(10, 10, 20, 0.9)");
            bgGrad.addColorStop(0.5, "rgba(20, 20, 40, 0.4)");
            bgGrad.addColorStop(0.75, "rgba(255, 255, 255, 0.03)");
            bgGrad.addColorStop(1, "transparent");
            ctx.fillStyle = bgGrad;
            ctx.fillRect(0, 0, size, size);

            // Spiral arms — white/silver tones
            for (let arm = 0; arm < 4; arm++) {
                const armAngle = (arm / 4) * Math.PI * 2;
                ctx.beginPath();

                for (let i = 0; i < 200; i++) {
                    const t = i / 200;
                    const angle = armAngle + t * Math.PI * 3;
                    const radius = t * size * 0.45;
                    const x = cx + Math.cos(angle) * radius;
                    const y = cy + Math.sin(angle) * radius;
                    if (i === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                }

                ctx.strokeStyle = `rgba(255, 255, 255, ${0.03 + arm * 0.01})`;
                ctx.lineWidth = 8 + arm * 3;
                ctx.filter = "blur(6px)";
                ctx.stroke();
            }

            // Central bright core — white
            const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, size * 0.08);
            coreGrad.addColorStop(0, "rgba(255, 255, 255, 0.15)");
            coreGrad.addColorStop(0.5, "rgba(200, 200, 220, 0.06)");
            coreGrad.addColorStop(1, "transparent");
            ctx.filter = "blur(10px)";
            ctx.fillStyle = coreGrad;
            ctx.fillRect(0, 0, size, size);

            // Nebula dust
            for (let i = 0; i < 6; i++) {
                const angle = (i / 6) * Math.PI * 2;
                const dist = size * 0.2 + Math.random() * size * 0.15;
                const x = cx + Math.cos(angle) * dist;
                const y = cy + Math.sin(angle) * dist;
                const cloudGrad = ctx.createRadialGradient(x, y, 0, x, y, 60 + Math.random() * 40);
                cloudGrad.addColorStop(0, "rgba(255, 255, 255, 0.04)");
                cloudGrad.addColorStop(1, "transparent");
                ctx.filter = "blur(20px)";
                ctx.fillStyle = cloudGrad;
                ctx.fillRect(x - 100, y - 100, 200, 200);
            }

            ctx.filter = "none";

            // Star field
            for (let i = 0; i < 120; i++) {
                const x = Math.random() * size;
                const y = Math.random() * size;
                const dist = Math.sqrt((x - cx) ** 2 + (y - cy) ** 2);
                if (dist < size * 0.1) continue;
                const brightness = 0.3 + Math.random() * 0.7;
                const starSize = 0.5 + Math.random() * 1.5;
                ctx.fillStyle = `rgba(255, 255, 255, ${brightness * 0.4})`;
                ctx.beginPath();
                ctx.arc(x, y, starSize, 0, Math.PI * 2);
                ctx.fill();
            }
        };

        drawVortex();
    }, []);

    return (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none overflow-hidden">
            <canvas
                ref={canvasRef}
                className="w-[150vw] h-[150vw] md:w-[100vw] md:h-[100vw] max-w-[1200px] max-h-[1200px] opacity-60"
                style={{
                    animation: "cosmicSpin 120s linear infinite",
                    maskImage: "radial-gradient(ellipse at center, black 15%, transparent 65%)",
                    WebkitMaskImage: "radial-gradient(ellipse at center, black 15%, transparent 65%)",
                    willChange: "transform",
                }}
            />
            <style jsx>{`
        @keyframes cosmicSpin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
        </div>
    );
}
