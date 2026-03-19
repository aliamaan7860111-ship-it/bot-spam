"use client";

import { BarChart3, Globe2, Cpu, ArrowRight } from "lucide-react";
import Link from "next/link";
import { SpotlightCard } from "@/components/SpotlightCard";
import { StaggeredGrid } from "@/components/StaggeredGrid";
import { PremiumBadge } from "@/components/PremiumBadge";
import { AnimatedGrid } from "@/components/AnimatedGrid";
import { MeteorButton } from "@/components/MeteorButton";
import { TechPartners } from "@/components/TechPartners";
import { TechGrid } from "@/components/TechGrid";
import { useEffect, useState } from "react";
import { HeroGlobe } from "@/components/HeroGlobe";
import { ClientOnly } from "@/components/ClientOnly";

// Updated Data reflecting actual hiring needs
const OPEN_ROLES = [
    {
        id: "ai-automation-engineer",
        title: "AI Automation Engineer",
        location: "Remote / Global",
        type: "Full-Time",
        hook: "Architect intelligent workflows and API bridges to power high-volume operations.",
    },
    {
        id: "growth-marketer",
        title: "Growth Marketer",
        location: "Remote / Global",
        type: "Full-Time",
        hook: "Deploy massive budgets across Meta and TikTok to acquire customers at scale.",
    },
    {
        id: "sales-executive",
        title: "Senior Sales Agent",
        location: "Remote / Global",
        type: "Full-Time",
        hook: "Close high-ticket inbound leads with precision and an elite understanding of human psychology.",
    },
    {
        id: "virtual-assistant",
        title: "Executive Virtual Assistant",
        location: "Remote / Global",
        type: "Full-Time",
        hook: "Operate as the operational backbone managing critical logistics for executives.",
    },
];

export default function CareersPage() {
    const [isMounted, setIsMounted] = useState(false);

    useEffect(() => {
        setIsMounted(true);
    }, []);

    return (
        <ClientOnly>
            <div suppressHydrationWarning className="max-w-6xl mx-auto px-6 pt-32 pb-24">
                <div suppressHydrationWarning className="flex flex-col gap-16 md:gap-24 pb-20">
                    {/* Premium Hero Section */}
                    <section className="space-y-6 md:space-y-8 max-w-4xl pt-10 md:pt-16 flex flex-col items-start animate-in fade-in slide-in-from-bottom-8 duration-1000 relative">
                        {isMounted && (
                            <>
                                <HeroGlobe />
                            </>
                        )}
                        <AnimatedGrid />

                        <div className="relative z-10 space-y-6 md:space-y-8">
                            <PremiumBadge />

                            <div className="relative">
                                {/* Subtle Text Glow Behind */}
                                <div className="absolute -inset-1 z-0 bg-white/5 blur-3xl rounded-full" />

                                <h1 className="relative z-10 text-5xl md:text-7xl font-bold tracking-tight pb-2 leading-[1.05] text-shimmer">
                                    Architect the Future of <br className="hidden md:block" />
                                    Scalable Business.
                                </h1>
                            </div>

                            <p className="text-lg md:text-xl text-zinc-400 max-w-2xl leading-relaxed mt-4">
                                We build, acquire, and scale our own portfolio of global brands.
                                Join an elite in-house team engineering the systems that drive eight-figure revenue entirely from within.
                            </p>

                            <div className="pt-4">
                                <Link href="#open-roles">
                                    <MeteorButton>
                                        <span className="flex items-center gap-2">
                                            View Roles <ArrowRight className="w-4 h-4" />
                                        </span>
                                    </MeteorButton>
                                </Link>
                            </div>
                        </div>
                    </section>

                    {/* Seamless Infinite Trust Banner */}
                    <TechPartners />

                    {/* Bento Box Stats Section (Premium Culture Sub-Hero) */}
                    <section className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-12 duration-1000 delay-150 fill-mode-both">
                        <div className="p-8 rounded-2xl border border-white/5 bg-white/5 flex flex-col gap-4">
                            <div className="w-10 h-10 rounded-full bg-blue-500/10 flex items-center justify-center text-blue-400">
                                <BarChart3 className="w-5 h-5" />
                            </div>
                            <div>
                                <h4 className="text-lg font-medium text-white">8-Figure Scale</h4>
                                <p className="text-sm text-zinc-500 mt-1">We don't manage clients. We scale our own assets to the absolute maximum potential.</p>
                            </div>
                        </div>

                        <div className="p-8 rounded-2xl border border-white/5 bg-white/5 flex flex-col gap-4">
                            <div className="w-10 h-10 rounded-full bg-emerald-500/10 flex items-center justify-center text-emerald-400">
                                <Globe2 className="w-5 h-5" />
                            </div>
                            <div>
                                <h4 className="text-lg font-medium text-white">100% Remote Base</h4>
                                <p className="text-sm text-zinc-500 mt-1">Work from anywhere. We care about extreme output and brutal efficiency, not geography.</p>
                            </div>
                        </div>

                        <div className="p-8 rounded-2xl border border-white/5 bg-white/5 flex flex-col gap-4">
                            <div className="w-10 h-10 rounded-full bg-purple-500/10 flex items-center justify-center text-purple-400">
                                <Cpu className="w-5 h-5" />
                            </div>
                            <div>
                                <h4 className="text-lg font-medium text-white">In-House Tech Stack</h4>
                                <p className="text-sm text-zinc-500 mt-1">Proprietary AI workflows and engineered infrastructure backing our ecosystem.</p>
                            </div>
                        </div>
                    </section>

                    {/* Roles Grid */}
                    <section id="open-roles" className="space-y-8 pb-10">
                        <div className="flex items-center justify-between border-b border-white/10 pb-6 animate-in fade-in duration-1000 delay-300 fill-mode-both">
                            <h2 className="text-2xl font-light tracking-wide text-zinc-200">Open Positions</h2>
                            <span className="text-sm font-mono text-zinc-500">{OPEN_ROLES.length} Roles</span>
                        </div>

                        <StaggeredGrid>
                            {OPEN_ROLES.map((role) => (
                                <SpotlightCard key={role.id} {...role} />
                            ))}
                        </StaggeredGrid>
                    </section>

                    {/* Top 1% Technical Stack Breakdown Grid */}
                    <TechGrid />

                </div>
            </div>
        </ClientOnly>
    );
}
