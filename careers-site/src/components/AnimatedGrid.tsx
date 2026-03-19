"use client";

import { memo } from "react";

// Memoized to prevent any unnecessary re-renders during scroll events
export const AnimatedGrid = memo(function AnimatedGrid() {
    return (
        <div className="absolute inset-0 -z-10 overflow-hidden pointer-events-none [mask-image:radial-gradient(ellipse_at_center,black,transparent_80%)]">
            {/* The Static Grid Layer */}
            <div
                className="absolute inset-0 opacity-20"
                style={{
                    backgroundImage: `
                        linear-gradient(to right, rgba(255, 255, 255, 0.1) 1px, transparent 1px),
                        linear-gradient(to bottom, rgba(255, 255, 255, 0.1) 1px, transparent 1px)
                    `,
                    backgroundSize: "40px 40px"
                }}
            />

            {/* The Hardware-Accelerated Light Wash */}
            <div
                className="absolute inset-0 w-[200%] h-full opacity-30"
                style={{
                    background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.05) 50%, transparent 100%)",
                    animation: "slide 8s linear infinite",
                    // Force GPU rendering layer
                    transform: "translateZ(0)",
                    willChange: "transform"
                }}
            />

            <style jsx>{`
                @keyframes slide {
                    0% { transform: translateX(-50%) translateZ(0); }
                    100% { transform: translateX(0%) translateZ(0); }
                }
            `}</style>
        </div>
    );
});
