"use client";

import { ReactNode } from "react";
import { Copy } from "lucide-react";

export function MeteorButton({ children, onClick }: { children: ReactNode, onClick?: () => void }) {
    return (
        <button
            onClick={onClick}
            className="relative inline-flex h-12 overflow-hidden rounded-full p-[1px] focus:outline-none focus:ring-2 focus:ring-zinc-400 focus:ring-offset-2 focus:ring-offset-zinc-50 group transition-all hover:scale-105 active:scale-95"
        >
            <span className="absolute inset-[-1000%] animate-[spin_3s_linear_infinite] bg-[conic-gradient(from_90deg_at_50%_50%,#E2CBFF_0%,#393BB2_50%,#E2CBFF_100%)] opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
            <span className="absolute inset-[-1000%] animate-[spin_3s_linear_infinite] bg-[conic-gradient(from_90deg_at_50%_50%,#717171_0%,#1a1a1a_50%,#717171_100%)] group-hover:opacity-0 transition-opacity duration-500" />
            <span className="inline-flex h-full w-full cursor-pointer items-center justify-center rounded-full bg-zinc-950 px-8 py-1 text-sm font-medium text-white backdrop-blur-3xl transition-colors group-hover:bg-zinc-900 border border-white/5">
                {children}
            </span>
        </button>
    );
}
