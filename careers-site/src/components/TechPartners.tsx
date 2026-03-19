import { ShoppingBag, Box, Activity, FileText, Infinity, Triangle } from "lucide-react";

export function TechPartners() {
    // Using stylized text and subtle icons to create a premium "Wordmark" lockup
    const partners = [
        { name: "Shopify", icon: ShoppingBag, style: "font-sans font-bold tracking-tight" },
        { name: "Meta", icon: Infinity, style: "font-sans font-semibold tracking-normal" },
        { name: "Stripe", icon: Activity, style: "font-sans font-black tracking-tighter" },
        { name: "Notion", icon: FileText, style: "font-serif font-medium tracking-tight" },
        { name: "Vercel", icon: Triangle, style: "font-sans font-bold tracking-widest uppercase text-lg leading-none" },
        { name: "Linear", icon: Box, style: "font-sans font-semibold tracking-tight" },
    ];

    // Duplicated twice for seamless pure CSS infinite scroll
    const scrollItems = [...partners, ...partners, ...partners, ...partners];

    return (
        <section className="w-full border-y border-white/5 bg-white/[0.02] overflow-hidden py-10 relative mt-12 mb-8 group">
            {/* Fade Out Edges for seamless entrance/exit using pure CSS masks so it blends perfectly */}
            <div className="absolute inset-y-0 left-0 w-32 bg-gradient-to-r from-black to-transparent z-10 pointer-events-none" />
            <div className="absolute inset-y-0 right-0 w-32 bg-gradient-to-l from-black to-transparent z-10 pointer-events-none" />

            <div className="flex flex-col items-center gap-8">
                <p className="text-xs uppercase tracking-[0.3em] font-mono text-zinc-600 transition-colors group-hover:text-zinc-500">
                    Ecosystem & Infrastructure
                </p>
                <div className="w-full inline-flex flex-nowrap overflow-hidden">
                    <div className="flex flex-row items-center gap-20 justify-start animate-[infinite-scroll_40s_linear_infinite]">
                        {scrollItems.map((partner, index) => {
                            const Icon = partner.icon;
                            return (
                                <div
                                    key={index}
                                    className="flex items-center gap-3 text-zinc-600 hover:text-white transition-colors duration-500 shrink-0"
                                >
                                    <Icon className="w-6 h-6" />
                                    <span className={`text-2xl md:text-3xl ${partner.style}`}>
                                        {partner.name}
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </section>
    );
}
