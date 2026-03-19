import { motion } from "framer-motion";
import { Database, Workflow, ShoppingCart, Megaphone } from "lucide-react";

export function TechGrid() {
    return (
        <section className="animate-in fade-in slide-in-from-bottom-12 duration-1000 delay-500 fill-mode-both w-full">
            <div className="flex items-center justify-between border-b border-white/10 pb-6 mb-8">
                <h2 className="text-2xl font-light tracking-wide text-zinc-200">Our Stack</h2>
                <span className="text-sm font-mono text-zinc-500">In-House Infrastructure</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 auto-rows-[160px]">

                {/* Large Central Tile: Notion API */}
                <motion.div
                    whileHover={{ scale: 0.98 }}
                    className="md:col-span-2 md:row-span-2 rounded-2xl border border-white/10 glass-panel p-8 flex flex-col justify-between relative overflow-hidden group"
                >
                    <div className="absolute inset-0 bg-blue-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                    {/* Decorative Background Icon */}
                    <Database className="absolute -bottom-10 -right-6 w-48 h-48 text-blue-500/5 rotate-12 transition-transform duration-700 group-hover:rotate-0 group-hover:scale-110" />

                    <div className="relative z-10">
                        <div className="flex items-center gap-3 mb-3">
                            <Database className="w-8 h-8 text-blue-400 flex-shrink-0" />
                            <h3 className="text-2xl font-bold text-white tracking-tight">Centralized Data <span className="text-blue-400 font-normal text-xl">(Notion)</span></h3>
                        </div>
                        <p className="text-sm text-zinc-400 max-w-[80%] relative z-10">A programmatic, single source of truth routing global operations, financial modeling, and asset management in real-time.</p>
                    </div>
                </motion.div>

                {/* Automation Tile: n8n / Antigravity */}
                <motion.div
                    whileHover={{ scale: 0.98 }}
                    className="md:col-span-2 rounded-2xl border border-white/10 glass-panel p-6 flex flex-col justify-between relative overflow-hidden group"
                >
                    <div className="absolute inset-0 bg-emerald-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                    {/* Decorative Background Icon */}
                    <Workflow className="absolute -bottom-8 -right-4 w-40 h-40 text-emerald-500/5 -rotate-12 transition-transform duration-700 group-hover:-rotate-3 group-hover:scale-110" />

                    <div className="relative z-10">
                        <div className="flex items-center gap-3 mb-2">
                            <Workflow className="w-6 h-6 text-emerald-400 flex-shrink-0" />
                            <h4 className="text-base font-medium text-white tracking-tight leading-tight">n8n / Agentic Workflows</h4>
                        </div>
                        <p className="text-xs text-zinc-400 mt-1 max-w-[85%] relative z-10">Custom event-driven architectures and AI agents running 24/7. Eradicating manual data entry across the entire portfolio.</p>
                    </div>
                </motion.div>

                {/* Small Tile 1: Shopify */}
                <motion.div
                    whileHover={{ scale: 0.98 }}
                    className="rounded-2xl border border-white/10 glass-panel p-6 flex flex-col justify-between relative overflow-hidden group"
                >
                    <div className="absolute inset-0 bg-[#95BF47]/10 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                    {/* Decorative Background Icon */}
                    <ShoppingCart className="absolute -bottom-6 -right-6 w-32 h-32 text-[#95BF47]/5 rotate-12 transition-transform duration-700 group-hover:rotate-0 group-hover:scale-110 pointer-events-none" />

                    <div className="relative z-10">
                        <div className="flex items-center gap-3 mb-2">
                            <ShoppingCart className="w-6 h-6 text-[#95BF47] flex-shrink-0" />
                            <h4 className="text-base font-medium text-white leading-tight">Headless Commerce</h4>
                        </div>
                        <p className="text-xs text-zinc-500 mt-1 relative z-10">Multi-storefront architecture built for high-velocity environments and massive traffic spikes.</p>
                    </div>
                </motion.div>

                {/* Small Tile 2: Meta Ads */}
                <motion.div
                    whileHover={{ scale: 0.98 }}
                    className="rounded-2xl border border-white/10 glass-panel p-6 flex flex-col justify-between relative overflow-hidden group"
                >
                    <div className="absolute inset-0 bg-[#0668E1]/10 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                    {/* Decorative Background Icon */}
                    <Megaphone className="absolute -bottom-6 -right-2 w-32 h-32 text-[#0668E1]/5 -rotate-12 transition-transform duration-700 group-hover:-rotate-3 group-hover:scale-110 pointer-events-none" />

                    <div className="relative z-10">
                        <div className="flex items-center gap-3 mb-2">
                            <Megaphone className="w-6 h-6 text-[#0668E1] flex-shrink-0" />
                            <h4 className="text-base font-medium text-white leading-tight">Aggressive Acquisition</h4>
                        </div>
                        <p className="text-xs text-zinc-500 mt-1 relative z-10">High-frequency creative testing and ML-optimized tracking to dominate globally.</p>
                    </div>
                </motion.div>

            </div>
        </section>
    );
}
