import { Sparkles } from "lucide-react";

export function PremiumBadge() {
    return (
        <div className="relative inline-flex group animate-in fade-in slide-in-from-bottom-4 duration-1000 fill-mode-both">
            {/* Animated glowing border behind */}
            <div className="absolute transition-all duration-1000 opacity-40 inset-0 bg-gradient-to-r from-zinc-500 via-white to-zinc-500 rounded-full blur-sm group-hover:opacity-100 group-hover:-inset-0.5 group-hover:duration-200" />

            {/* Actual Badge */}
            <div className="relative inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-black border border-white/10 text-xs font-medium text-white shadow-2xl overflow-hidden">
                {/* Internal Shimmer Sweep */}
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-full animate-[shimmer_2s_infinite]" />

                <Sparkles className="w-3.5 h-3.5 text-zinc-400" />
                <span className="bg-gradient-to-r from-zinc-100 to-zinc-400 bg-clip-text text-transparent">
                    Now scaling our ecosystem
                </span>
            </div>
        </div>
    );
}
