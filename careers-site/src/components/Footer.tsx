import Link from "next/link";
import { ArrowRight } from "lucide-react";

export function Footer() {
    return (
        <footer className="w-full border-t border-white/[0.06] bg-[#050508] overflow-hidden relative">
            <div className="max-w-6xl mx-auto px-6 pt-20 pb-12 relative z-10 w-full">
                <div className="grid grid-cols-1 md:grid-cols-4 gap-12 md:gap-8 mb-16">
                    {/* Brand Column */}
                    <div className="col-span-1 md:col-span-2 flex flex-col items-start gap-6">
                        <Link href="/" className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
                            <div className="w-6 h-6 rounded bg-white" />
                            GRQ Holdings
                        </Link>
                        <p className="text-sm text-zinc-600 max-w-sm leading-relaxed font-light">
                            Engineering the systems and driving the operations that scale elite brands. We build, acquire, and grow assets to their absolute maximum potential.
                        </p>
                    </div>

                    {/* Navigation */}
                    <div className="flex flex-col gap-4">
                        <h4 className="text-sm font-medium text-white mb-2">Company</h4>
                        <Link href="/about" className="text-sm text-zinc-600 hover:text-white transition-colors">About Us</Link>
                        <Link href="/work" className="text-sm text-zinc-600 hover:text-white transition-colors">Our Work</Link>
                        <Link href="/careers" className="text-sm text-zinc-600 hover:text-white transition-colors flex items-center gap-2">
                            Careers <span className="px-2 py-0.5 rounded-full bg-white/5 text-white/50 text-[10px] font-mono tracking-widest uppercase border border-white/10">Hiring</span>
                        </Link>
                        <Link href="/contact" className="text-sm text-zinc-600 hover:text-white transition-colors">Contact</Link>
                    </div>

                    <div className="flex flex-col gap-4">
                        <h4 className="text-sm font-medium text-white mb-2">Legal</h4>
                        <Link href="/privacy" className="text-sm text-zinc-600 hover:text-white transition-colors">Privacy Policy</Link>
                        <Link href="/terms" className="text-sm text-zinc-600 hover:text-white transition-colors">Terms of Service</Link>
                    </div>
                </div>

                {/* CTA Banner */}
                <div className="w-full relative rounded-2xl mb-16 overflow-hidden group border border-white/[0.06] hover:border-white/10 transition-colors duration-500">
                    <div className="absolute inset-0 bg-[#080810]" />
                    <div className="absolute -top-24 -left-24 w-96 h-96 bg-white/5 rounded-full blur-[100px] opacity-0 group-hover:opacity-100 transition-opacity duration-1000" />
                    <div className="absolute -bottom-24 -right-24 w-96 h-96 bg-white/5 rounded-full blur-[100px] opacity-0 group-hover:opacity-100 transition-opacity duration-1000" />

                    <div className="w-full h-full relative z-10 p-8 md:p-12 bg-black/40 backdrop-blur-xl flex flex-col md:flex-row items-center justify-between gap-8 border border-white/[0.04] rounded-2xl">
                        <div className="space-y-3 text-center md:text-left">
                            <h3 className="text-3xl font-semibold tracking-tight text-white">Ready to build the future?</h3>
                            <p className="text-zinc-500 text-sm font-light">Join the top 1% of talent scaling eight-figure brands.</p>
                        </div>
                        <Link href="/careers#open-roles" className="inline-flex h-12 items-center justify-center rounded-full bg-white px-8 text-sm font-semibold text-black transition-all hover:bg-zinc-200 active:scale-95 gap-2 group/btn">
                            View Open Roles
                            <ArrowRight className="w-4 h-4 group-hover/btn:translate-x-1 transition-transform" />
                        </Link>
                    </div>
                </div>

                {/* Bottom Bar */}
                <div className="flex flex-col md:flex-row items-center justify-between pt-8 border-t border-white/[0.04] gap-4">
                    <p className="text-xs text-zinc-700 font-mono">
                        &copy; {new Date().getFullYear()} GRQ Holdings. All rights reserved.
                    </p>
                    <div className="flex gap-6">
                        <Link href="#" className="text-zinc-700 hover:text-white transition-colors">
                            <span className="sr-only">LinkedIn</span>
                            <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                <path fillRule="evenodd" d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z" clipRule="evenodd" />
                            </svg>
                        </Link>
                        <Link href="#" className="text-zinc-700 hover:text-white transition-colors">
                            <span className="sr-only">Twitter</span>
                            <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                <path d="M8.29 20.251c7.547 0 11.675-6.253 11.675-11.675 0-.178 0-.355-.012-.53A8.348 8.348 0 0022 5.92a8.19 8.19 0 01-2.357.646 4.118 4.118 0 001.804-2.27 8.224 8.224 0 01-2.605.996 4.107 4.107 0 00-6.993 3.743 11.65 11.65 0 01-8.457-4.287 4.106 4.106 0 001.27 5.477A4.072 4.072 0 012.8 9.713v.052a4.105 4.105 0 003.292 4.022 4.095 4.095 0 01-1.853.07 4.108 4.108 0 003.834 2.85A8.233 8.233 0 012 18.407a11.616 11.616 0 006.29 1.84" />
                            </svg>
                        </Link>
                    </div>
                </div>
            </div>
        </footer>
    );
}
