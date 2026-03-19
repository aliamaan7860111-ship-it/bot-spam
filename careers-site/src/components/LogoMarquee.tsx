"use client";

import { motion } from "framer-motion";

const techStack = [
    { name: "Next.js", icon: "⬡" },
    { name: "React", icon: "⚛" },
    { name: "TypeScript", icon: "TS" },
    { name: "Notion API", icon: "◧" },
    { name: "Vercel", icon: "▲" },
    { name: "Framer Motion", icon: "◉" },
    { name: "Tailwind CSS", icon: "◈" },
    { name: "AI/ML", icon: "◎" },
];

export function LogoMarquee() {
    const doubled = [...techStack, ...techStack];

    return (
        <motion.section
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 1 }}
            className="w-full py-12 border-y border-white/5 overflow-hidden"
        >
            <p className="text-center text-xs font-mono uppercase tracking-widest text-zinc-600 mb-8">
                Built With
            </p>
            <div className="overflow-hidden whitespace-nowrap">
                <motion.div
                    className="inline-flex gap-12 md:gap-20 items-center"
                    animate={{ x: ["0%", "-50%"] }}
                    transition={{
                        x: {
                            repeat: Infinity,
                            repeatType: "loop",
                            duration: 25,
                            ease: "linear",
                        },
                    }}
                >
                    {doubled.map((tech, i) => (
                        <div
                            key={i}
                            className="flex items-center gap-3 text-zinc-600 hover:text-zinc-400 transition-colors select-none shrink-0"
                        >
                            <span className="text-xl">{tech.icon}</span>
                            <span className="text-sm font-mono tracking-wider uppercase">{tech.name}</span>
                        </div>
                    ))}
                </motion.div>
            </div>
        </motion.section>
    );
}
