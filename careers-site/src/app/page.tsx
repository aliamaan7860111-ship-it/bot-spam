"use client";

import { useRef, useState } from "react";
import { motion, useScroll, useTransform } from "framer-motion";
import Image from "next/image";
import Link from "next/link";
import { Command, Lock, ChevronRight, ArrowUpRight } from "lucide-react";
import { NoiseOverlay } from "@/components/NoiseOverlay";
import { HeroGlobe } from "@/components/HeroGlobe";
import { StickyFloatingCTA } from "@/components/StickyFloatingCTA";

// ────────────────────────────────────────────────────────────
//  ANIMATION PRESETS
// ────────────────────────────────────────────────────────────
const ease = [0.16, 1, 0.3, 1] as [number, number, number, number];

const fadeUp = {
  hidden: { opacity: 0, y: 30 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.8, ease } },
};
const fadeIn = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.6 } },
};
const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.1 } },
};

// ────────────────────────────────────────────────────────────
//  DATA
// ────────────────────────────────────────────────────────────
const divisions = [
  {
    num: "1.0",
    tag: "Commerce",
    title: "Transactional infrastructure built for scale",
    desc: "Direct-to-consumer and B2B commerce architectures engineered for maximum retention and lifetime value. Multi-brand portfolio management with centralized fulfilment, inventory orchestration, and real-time margin tracking.",
    img: "/divisions/commerce.png",
    link: "/work",
    accent: "from-sky-500/20 to-transparent",
    dotColor: "bg-sky-400",
  },
  {
    num: "2.0",
    tag: "Performance",
    title: "Data-driven acquisition at global scale",
    desc: "Precision capital deployment engines optimizing ROAS across every market. Real-time analytics, automated bid management, and creative testing frameworks that turn spend into predictable, compounding revenue.",
    img: "/divisions/performance.png",
    link: "/work",
    accent: "from-blue-500/20 to-transparent",
    dotColor: "bg-blue-400",
  },
  {
    num: "3.0",
    tag: "AI & Automation",
    title: "Intelligent systems replacing manual friction",
    desc: "Proprietary algorithmic workflows that eliminate human bottlenecks. From automated order routing to predictive inventory management — every pipeline is self-healing, self-optimizing, and built to compound.",
    img: "/divisions/ai.png",
    link: "/work",
    accent: "from-emerald-500/20 to-transparent",
    dotColor: "bg-emerald-400",
  },
  {
    num: "4.0",
    tag: "Operations",
    title: "End-to-end control over every process",
    desc: "Centralized fulfillment, logistics, and data warehousing enforcing strict SLA standards. Real-time visibility across every node in the supply chain. Zero black boxes, zero guesswork.",
    img: "/divisions/operations.png",
    link: "/work",
    accent: "from-amber-500/20 to-transparent",
    dotColor: "bg-amber-400",
  },
  {
    num: "5.0",
    tag: "Investments",
    title: "Strategic capital allocation for digital assets",
    desc: "Identifying high-leverage digital assets and infrastructure opportunities. Portfolio diversification across digital verticals with rigorous due diligence and mathematical conviction before every deployment.",
    img: "/divisions/investments.png",
    link: "/work",
    accent: "from-purple-500/20 to-transparent",
    dotColor: "bg-purple-400",
  },
];

const metrics = [
  { label: "System Uptime", value: "99.9%", suffix: "" },
  { label: "Deployments", value: "1.4k", suffix: "+" },
  { label: "Efficiency Gain", value: "314", suffix: "%" },
  { label: "Active Nodes", value: "8.9k", suffix: "" },
];

const steps = [
  { num: "01", title: "Research & Mapping", desc: "Deep market analysis, competitive mapping, and opportunity identification before any capital deployment." },
  { num: "02", title: "Operational Modeling", desc: "Precise KPI frameworks, unit economics, and risk projection across all target verticals." },
  { num: "03", title: "Controlled Launch", desc: "Staged rollouts with real-time analytics and fail-safe mechanisms at every level." },
  { num: "04", title: "Algorithmic Scale", desc: "Performance-driven scaling that pushes automation pipelines to eliminate every bottleneck." },
];

// ────────────────────────────────────────────────────────────
//  ANIMATION & UI COMPONENTS
// ────────────────────────────────────────────────────────────

function GeometricTunnel({ align = "left" }: { align?: "left" | "right" }) {
  // A monolithic, screen-edge wireframe structure.
  // Instead of a deep tunnel, it draws a massive, technical array of lines
  return (
    <div className={`absolute top-0 bottom-0 ${align === "left" ? "left-0 -translate-x-1/3" : "right-0 translate-x-1/3"} w-[600px] z-0 pointer-events-none opacity-20 mix-blend-screen flex items-center`}>
      <motion.svg
        width="600"
        height="1200"
        viewBox="0 0 100 200"
        fill="none"
      >
        {/* Draw a massive converging grid structure on the edge */}
        {[...Array(20)].map((_, i) => {
          const spacing = i * 5;
          return (
            <motion.line
              key={i}
              x1={align === "left" ? 10 : 90}
              y1={100}
              x2={align === "left" ? 100 : 0}
              y2={spacing * 2}
              stroke="rgba(56, 189, 248, 0.4)" // Cyan
              strokeWidth="0.1"
              initial={{ pathLength: 0, opacity: 0 }}
              whileInView={{ pathLength: 1, opacity: 1 }}
              transition={{ duration: 2, delay: i * 0.1, ease: "easeOut" }}
            />
          );
        })}
        {/* Vertical strict lines */}
        {[...Array(5)].map((_, i) => (
          <line
            key={`v-${i}`}
            x1={align === "left" ? 20 + i * 15 : 80 - i * 15}
            y1={0}
            x2={align === "left" ? 20 + i * 15 : 80 - i * 15}
            y2={200}
            stroke="rgba(56, 189, 248, 0.2)"
            strokeWidth="0.1"
          />
        ))}
      </motion.svg>
      {/* Mask the raw edges to fade into the background smoothly */}
      <div className={`absolute inset-0 bg-gradient-to-${align === "left" ? "r" : "l"} from-[#050508] via-transparent to-[#050508]`} />
      <div className="absolute inset-x-0 top-0 h-64 bg-gradient-to-b from-[#050508] to-transparent" />
      <div className="absolute inset-x-0 bottom-0 h-64 bg-gradient-to-t from-[#050508] to-transparent" />
    </div>
  );
}

function SystemDirectiveCard({ index, question, answer }: { index: string, question: string, answer: string }) {
  return (
    <div className="group relative bg-[#0a0a0f]/80 backdrop-blur-md border border-white/[0.06] rounded-3xl p-8 md:p-10 hover:bg-[#0c0c12] hover:border-white/15 transition-all duration-500 overflow-hidden flex flex-col justify-between h-full">
      {/* Hover Glow Effect */}
      <div className={`absolute top-0 right-0 translate-x-1/3 -translate-y-1/3 w-64 h-64 bg-sky-500/5 rounded-full blur-[60px] opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none`} />

      <div className="relative z-10">
        <div className="flex items-center gap-3 mb-6">
          <span className="w-1.5 h-1.5 rounded-full bg-sky-500/50" />
          <span className="text-sky-500/50 font-mono text-[10px] uppercase tracking-[0.2em]">Directive // {index}</span>
        </div>
        <h3 className="text-xl md:text-2xl font-bold tracking-tight text-white mb-4 leading-snug">
          {question}
        </h3>
        <p className="text-sm md:text-base text-zinc-400 leading-relaxed font-light">
          {answer}
        </p>
      </div>

      {/* Accent line at bottom on hover */}
      <div className="absolute bottom-0 left-0 right-0 h-px scale-x-0 group-hover:scale-x-100 bg-gradient-to-r from-transparent via-sky-500/30 to-transparent transition-transform duration-700 ease-out origin-center" />
    </div>
  );
}

// ────────────────────────────────────────────────────────────
//  PAGE
// ────────────────────────────────────────────────────────────
export default function HomePage() {
  const heroRef = useRef<HTMLDivElement>(null);
  const { scrollYProgress: heroScroll } = useScroll({ target: heroRef, offset: ["start start", "end start"] });
  const heroY = useTransform(heroScroll, [0, 1], ["0%", "40%"]);
  const heroOpacity = useTransform(heroScroll, [0, 0.8], [1, 0]);

  const divisionsRef = useRef<HTMLDivElement>(null);
  const { scrollYProgress: divisionsScroll } = useScroll({ target: divisionsRef, offset: ["start start", "end start"] });
  const shapeRotate = useTransform(divisionsScroll, [0, 1], [0, 360]);
  const shapeRadius = useTransform(divisionsScroll, [0, 0.5, 1], ["50%", "30%", "50%"]);
  const innerRotate = useTransform(divisionsScroll, [0, 1], [45, -45]);

  const [hoveredPortal, setHoveredPortal] = useState<"left" | "right" | null>(null);

  return (
    <main className="min-h-screen bg-[#050508] text-white font-sans selection:bg-white/20 selection:text-white relative">
      <NoiseOverlay />
      <StickyFloatingCTA />

      {/* ═══════════════════════════════════════════════
          HERO — Globe + Typography
          ═══════════════════════════════════════════════ */}
      <section ref={heroRef} className="relative h-[120vh] w-full bg-[#050508] z-10 overflow-hidden">
        <HeroGlobe />
        <motion.div
          className="absolute inset-0 flex flex-col items-center justify-center px-5 md:px-12 z-20 pointer-events-none"
          style={{ y: heroY, opacity: heroOpacity }}
        >
          <motion.div initial="hidden" animate="visible" variants={stagger} className="w-full max-w-7xl text-center flex flex-col items-center">
            <motion.div variants={fadeUp} className="mb-8">
              <span className="inline-flex items-center gap-2.5 text-white/60 font-mono tracking-[0.25em] uppercase text-[10px] border border-white/10 px-5 py-2 rounded-full bg-black/40 backdrop-blur-md">
                <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse shadow-[0_0_10px_rgba(255,255,255,0.8)]" />
                GRQ Holdings
              </span>
            </motion.div>
            <motion.h1 variants={fadeUp} className="font-extrabold tracking-tighter leading-[0.85] uppercase pointer-events-auto" style={{ fontSize: "clamp(3rem, 10vw, 9rem)" }}>
              <span className="text-transparent bg-clip-text bg-gradient-to-b from-white via-white to-zinc-500 block drop-shadow-2xl">Structured</span>
              <span className="text-transparent bg-clip-text bg-gradient-to-b from-white/90 via-zinc-400 to-zinc-800 block -mt-2 md:-mt-6">Infrastructure</span>
            </motion.h1>
            <motion.p variants={fadeUp} className="mt-8 text-sm md:text-lg text-zinc-400 max-w-2xl font-light leading-relaxed pointer-events-auto">
              Engineering the systems and driving the operations that scale elite brands. We build, acquire, and grow assets to their absolute maximum potential.
            </motion.p>

            {/* Hero Stats Row */}
            <motion.div variants={fadeUp} className="mt-16 pt-10 border-t border-white/[0.05] w-full max-w-4xl grid grid-cols-2 lg:grid-cols-4 gap-8 md:gap-12 pointer-events-auto">
              <div className="flex flex-col items-center justify-center text-center">
                <span className="text-3xl md:text-4xl font-bold text-white mb-2 tracking-tight">99.9%</span>
                <span className="text-[11px] font-mono text-zinc-500 uppercase tracking-widest">System Uptime</span>
              </div>
              <div className="flex flex-col items-center justify-center text-center">
                <span className="text-3xl md:text-4xl font-bold text-white mb-2 tracking-tight">140+</span>
                <span className="text-[11px] font-mono text-zinc-500 uppercase tracking-widest">Proprietary Nodes</span>
              </div>
              <div className="flex flex-col items-center justify-center text-center">
                <span className="text-3xl md:text-4xl font-bold text-white mb-2 tracking-tight">{"<50ms"}</span>
                <span className="text-[11px] font-mono text-zinc-500 uppercase tracking-widest">Execution Latency</span>
              </div>
              <div className="flex flex-col items-center justify-center text-center">
                <span className="text-3xl md:text-4xl font-bold text-white mb-2 tracking-tight">100%</span>
                <span className="text-[11px] font-mono text-zinc-500 uppercase tracking-widest">Data Sovereignty</span>
              </div>
            </motion.div>
          </motion.div>
        </motion.div>
        <div className="absolute bottom-0 left-0 right-0 h-64 bg-gradient-to-t from-[#050508] z-10 pointer-events-none" />
      </section>

      {/* ═══════════════════════════════════════════════
          DIVISIONS INTRO — Sticky Glass + Header
          ═══════════════════════════════════════════════ */}
      <section className="relative bg-[#050508] z-20" ref={divisionsRef}>
        <div className="max-w-7xl mx-auto px-5 md:px-12 relative flex">
          {/* Left: Sticky Glass Panel (desktop) */}
          <div className="hidden lg:flex w-1/2 h-screen sticky top-0 items-center justify-center pr-20">
            <motion.div className="w-[450px] aspect-square rounded-[2.5rem] border border-white/10 bg-[#050508]/40 backdrop-blur-3xl flex items-center justify-center overflow-hidden relative shadow-[0_0_100px_rgba(255,255,255,0.02)]">
              <div className="absolute inset-0 bg-noise opacity-[0.03]" />

              {/* Subtle pulsing background glow */}
              <motion.div
                className="absolute w-[300px] h-[300px] bg-white/[0.02] rounded-full blur-[80px]"
                animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0.8, 0.5] }}
                transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
              />

              {/* Orbital Rings Map */}
              <motion.div
                className="absolute w-[280px] h-[280px] rounded-full border border-white/[0.08]"
                style={{ rotate: shapeRotate }}
              >
                {/* Connecting Lines and Orbiting Nodes */}
                <div className="absolute inset-0 w-full h-full">
                  {divisions.map((div, i) => {
                    const angle = (i * 360) / divisions.length;
                    return (
                      <div
                        key={i}
                        className="absolute top-1/2 left-1/2 origin-left"
                        style={{
                          width: '140px',
                          transform: `translateY(-50%) rotate(${angle - 90}deg)`
                        }}
                      >
                        {/* Connection line fading outwards */}
                        <div className="w-full h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent" />

                        {/* The Node at the orbit edge */}
                        <div className={`absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 w-3 h-3 rounded-full ${div.dotColor} shadow-[0_0_15px_currentColor] border border-black/50`} />
                      </div>
                    )
                  })}
                </div>
              </motion.div>

              {/* Dashed Scanning Ring */}
              <motion.div
                className="absolute w-[280px] h-[280px] rounded-full border border-dashed border-white/10"
                animate={{ rotate: 360 }}
                transition={{ duration: 30, repeat: Infinity, ease: "linear" }}
              />

              {/* Outer boundary ring */}
              <div className="absolute w-[400px] h-[400px] rounded-full border border-white/[0.02]" />

              {/* Center Core (Logo) */}
              <div className="relative z-10 w-28 h-28 rounded-full bg-[#0a0a0f] border border-white/20 shadow-[0_0_50px_rgba(255,255,255,0.05)] flex items-center justify-center overflow-hidden backdrop-blur-xl group">
                {/* Core hover effect */}
                <div className="absolute inset-0 bg-white/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

                {/* Inner pulse ring */}
                <motion.div
                  className="absolute inset-0 rounded-full border border-white/10"
                  animate={{ scale: [1, 1.3, 1], opacity: [0.8, 0, 0.8] }}
                  transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                />

                {/* GRQ Logo - Perfectly circular and blended */}
                <div className="absolute inset-0 z-20 rounded-full overflow-hidden flex items-center justify-center bg-black">
                  <Image
                    src="/logo.png"
                    alt="GRQ Holdings Core"
                    fill
                    className="object-cover scale-[1.2] drop-shadow-[0_0_15px_rgba(255,255,255,0.2)]"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.opacity = '0';
                    }}
                  />
                </div>
                {/* Fallback label underneath the image in case it fails to load */}
                <span className="absolute text-[10px] font-mono font-bold tracking-widest text-white/20 z-10">CORE</span>
              </div>

              {/* Dynamic Status Text Overlay */}
              <div className="absolute inset-x-0 bottom-6 flex justify-center z-20">
                <div className="bg-black/60 px-4 py-2 rounded-lg border border-white/10 backdrop-blur-md flex items-center gap-3 overflow-hidden">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
                  <div className="h-4 overflow-hidden relative w-32">
                    <motion.div
                      animate={{ y: ["0%", "-80%"] }}
                      transition={{ repeat: Infinity, duration: 8, ease: "linear" }}
                      className="flex flex-col text-[10px] font-mono tracking-[0.2em] text-white/40 uppercase"
                    >
                      <span className="h-4 flex items-center text-emerald-500/80">CORE_ACTIVE</span>
                      <span className="h-4 flex items-center">SYNCING_NODES</span>
                      <span className="h-4 flex items-center">ALLOCATING_LOAD</span>
                      <span className="h-4 flex items-center">ROUTING_DATA</span>
                      <span className="h-4 flex items-center text-emerald-500/80">CORE_ACTIVE</span>
                    </motion.div>
                    <div className="absolute inset-0 bg-gradient-to-b from-[#0a0a0f]/80 via-transparent to-[#0a0a0f]/80 pointer-events-none" />
                  </div>
                </div>
              </div>

              {/* Outer Edge Vignette */}
              <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_40%,#050508_100%)] pointer-events-none" />
            </motion.div>
          </div>

          {/* Right: Section Headers Only */}
          <div className="w-full lg:w-1/2 pb-[15vh] pt-[30vh]">
            <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={fadeUp} className="mb-16">
              <span className="text-[11px] font-mono uppercase tracking-[0.3em] text-zinc-500 mb-6 block">Operating Scope</span>
              <h2 className="text-4xl md:text-5xl font-bold tracking-tighter text-white mb-6">Vertical Domination.</h2>
              <p className="text-lg text-zinc-400 leading-relaxed font-light max-w-lg">
                Five integrated divisions engineered to capture, optimize, and scale every opportunity across the digital landscape.
              </p>
            </motion.div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════
          DIVISIONS DEEP-DIVE — Linear-Style Feature Blocks
          ═══════════════════════════════════════════════ */}
      <section className="relative z-20 bg-[#050508]">
        <div className="max-w-7xl mx-auto px-5 md:px-12">
          {divisions.map((div, i) => (
            <motion.div
              key={i}
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true, margin: "-5%" }}
              variants={stagger}
              className="py-12 md:py-20"
            >
              {/* Two-column: Text Left, Image Right (alternating) */}
              <div className={`flex flex-col ${i % 2 === 0 ? "lg:flex-row" : "lg:flex-row-reverse"} gap-12 lg:gap-20 items-center`}>
                {/* Text */}
                <div className="w-full lg:w-1/2 flex flex-col">
                  <motion.div variants={fadeUp} className="flex items-center gap-3 mb-6">
                    <span className={`w-2 h-2 rounded-full ${div.dotColor}`} />
                    <span className="font-mono text-sm text-zinc-500 tracking-wider">{div.num}</span>
                    <span className="font-mono text-sm text-zinc-500 tracking-wider">{div.tag}</span>
                    <ArrowUpRight className="w-3.5 h-3.5 text-zinc-600" />
                  </motion.div>

                  <motion.h3 variants={fadeUp} className="text-3xl md:text-5xl font-bold tracking-tight text-white mb-6 leading-[1.1]">
                    {div.title}
                  </motion.h3>

                  <motion.p variants={fadeUp} className="text-base md:text-lg text-zinc-400 leading-relaxed font-light mb-8 max-w-xl">
                    {div.desc}
                  </motion.p>

                  <motion.div variants={fadeUp}>
                    <Link href={div.link} className="inline-flex items-center gap-2 text-sm font-medium text-white/70 hover:text-white transition-colors group">
                      Learn more
                      <ChevronRight className="w-4 h-4 text-white/40 group-hover:text-white group-hover:translate-x-0.5 transition-all" />
                    </Link>
                  </motion.div>
                </div>

                {/* Image */}
                <motion.div
                  variants={fadeUp}
                  className="w-full lg:w-1/2"
                >
                  <div className={`relative rounded-2xl overflow-hidden border border-white/[0.06] bg-gradient-to-br ${div.accent} group`}>
                    <div className="absolute inset-0 bg-[#0a0a10]/60 z-10 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                    <Image
                      src={div.img}
                      alt={div.tag}
                      width={800}
                      height={500}
                      className="w-full h-auto object-cover transition-transform duration-700 group-hover:scale-[1.03]"
                    />
                    {/* Subtle glow at bottom */}
                    <div className="absolute bottom-0 inset-x-0 h-1/3 bg-gradient-to-t from-[#050508] to-transparent z-20 pointer-events-none" />
                  </div>
                </motion.div>
              </div>
            </motion.div>
          ))}
        </div>
      </section>

      {/* ═══════════════════════════════════════════════
          EXECUTION METHODOLOGY — Interactive Pipeline
          ═══════════════════════════════════════════════ */}
      <section className="py-24 md:py-32 bg-[#050508] relative z-20 border-t border-white/[0.04] overflow-hidden">
        {/* Subtle background ambient light */}
        <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
        {/* Removed massive conflicting blur here to unify the dark shade */}

        {/* Fixed Monolithic Edge Wireframes */}
        <div className="hidden lg:block absolute inset-0 z-0 pointer-events-none overflow-hidden">
          <GeometricTunnel align="left" />
          <GeometricTunnel align="right" />
        </div>

        <div className="max-w-4xl mx-auto px-5 md:px-12 relative z-10">
          <motion.div
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true, margin: "-10%" }}
            variants={stagger}
            className="text-center mb-24 md:mb-32"
          >
            <motion.span variants={fadeUp} className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-white/10 bg-white/[0.03] text-[10px] font-mono uppercase tracking-[0.2em] text-zinc-400 mb-8">
              <span className="w-1.5 h-1.5 rounded-full bg-white/50" />
              Execution Methodology
            </motion.span>
            <motion.h2 variants={fadeUp} className="text-4xl md:text-6xl lg:text-7xl font-bold tracking-tighter text-white mb-8">
              Systematic<br /><span className="text-transparent bg-clip-text bg-gradient-to-b from-white/60 to-zinc-600">Deployment.</span>
            </motion.h2>
            <motion.p variants={fadeUp} className="text-zinc-400 text-lg md:text-xl max-w-2xl mx-auto font-light leading-relaxed">
              We eliminate guesswork. Every asset acquired or built passes through our rigorous, mathematically-driven growth pipeline.
            </motion.p>
          </motion.div>

          <div className="relative">
            {/* The continuous vertical line */}
            <div className="absolute left-[27px] md:left-1/2 top-0 bottom-0 w-px bg-gradient-to-b from-transparent via-white/10 to-transparent md:-translate-x-1/2" />

            <div className="space-y-12 md:space-y-20">
              {steps.map((step, i) => (
                <motion.div
                  key={i}
                  initial="hidden"
                  whileInView="visible"
                  viewport={{ once: true, margin: "-15%" }}
                  variants={stagger}
                  className={`relative flex flex-col md:flex-row gap-8 md:gap-16 items-start md:items-center ${i % 2 === 0 ? "md:flex-row" : "md:flex-row-reverse"
                    }`}
                >
                  {/* Node Connector (Center) */}
                  <div className="absolute left-[27px] md:left-1/2 -ml-[27px] md:-ml-0 top-0 md:top-1/2 md:-mt-6 w-[54px] h-[54px] flex items-center justify-center z-10 md:-translate-x-1/2">
                    <motion.div
                      className="w-full h-full rounded-full border border-white/20 bg-[#050508] flex items-center justify-center relative shadow-[0_0_30px_rgba(255,255,255,0.05)]"
                      variants={{
                        hidden: { scale: 0.5, opacity: 0 },
                        visible: { scale: 1, opacity: 1, transition: { type: "spring", stiffness: 100, damping: 20 } }
                      }}
                    >
                      <span className="font-mono text-xs text-white/50 tracking-wider">0{i + 1}</span>
                      {/* Inner glowing dot */}
                      <motion.div
                        initial={{ scale: 0 }}
                        whileInView={{ scale: 1 }}
                        transition={{ delay: 0.4, duration: 0.5 }}
                        className="absolute w-1.5 h-1.5 rounded-full bg-white shadow-[0_0_10px_rgba(255,255,255,1)]"
                      />
                    </motion.div>
                  </div>

                  {/* Empty space for alternating layout on Desktop, hidden on Mobile */}
                  <div className="hidden md:block w-1/2" />

                  {/* Content Card */}
                  <motion.div
                    className="w-full md:w-1/2 pl-20 md:pl-0"
                    variants={{
                      hidden: { opacity: 0, x: i % 2 === 0 ? -30 : 30 },
                      visible: { opacity: 1, x: 0, transition: { duration: 0.8, ease } }
                    }}
                  >
                    <div className="group relative bg-[#0a0a0f] border border-white/[0.06] rounded-3xl p-8 md:p-10 hover:bg-[#0c0c12] hover:border-white/15 transition-all duration-500 overflow-hidden">
                      {/* Hover Glow Effect */}
                      <div className={`absolute top-0 ${i % 2 === 0 ? 'right-0 translate-x-1/2' : 'left-0 -translate-x-1/2'} -translate-y-1/2 w-48 h-48 bg-white/5 rounded-full blur-[60px] opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none`} />

                      <div className="relative z-10">
                        <h3 className="text-2xl md:text-3xl font-bold tracking-tight text-white mb-4">
                          {step.title}
                        </h3>
                        <p className="text-base text-zinc-500 leading-relaxed font-light">
                          {step.desc}
                        </p>
                      </div>

                      {/* Accent line at bottom on hover */}
                      <div className="absolute bottom-0 left-0 right-0 h-px scale-x-0 group-hover:scale-x-100 bg-gradient-to-r from-transparent via-white/20 to-transparent transition-transform duration-700 ease-out origin-left" />
                    </div>
                  </motion.div>
                </motion.div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════
          SYSTEM TELEMETRY — Metrics + Terminal
          ═══════════════════════════════════════════════ */}
      <section className="py-20 md:py-32 bg-[#050508] relative z-20 overflow-hidden border-t border-white/[0.04]">
        <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.02)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.02)_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,#000_70%,transparent_100%)] opacity-40" />

        <div className="max-w-7xl mx-auto px-5 md:px-12 relative z-10">
          <motion.div
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true }}
            variants={stagger}
            className="flex flex-col md:flex-row md:items-end justify-between mb-16"
          >
            <motion.div variants={fadeUp}>
              <span className="text-[11px] font-mono uppercase tracking-[0.3em] text-zinc-500 block mb-6">Infrastructure Status</span>
              <h2 className="text-3xl md:text-5xl font-bold tracking-tighter text-white">System Telemetry</h2>
              <p className="text-zinc-500 mt-4 max-w-xl font-light">Real-time performance metrics from active portfolio deployments.</p>
            </motion.div>
            <motion.div variants={fadeIn} className="mt-6 md:mt-0 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <span className="font-mono text-xs tracking-widest text-emerald-500 uppercase">ALL SYSTEMS NOMINAL</span>
            </motion.div>
          </motion.div>

          {/* Metrics Row */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6 mb-10">
            {metrics.map((m, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.5, ease, delay: i * 0.07 }}
                className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6 md:p-8 flex flex-col justify-between min-h-[180px] md:min-h-[220px] hover:bg-white/[0.04] hover:border-white/10 transition-all duration-400 relative overflow-hidden group"
              >
                <div className={`absolute inset-x-0 bottom-0 h-px scale-x-0 group-hover:scale-x-100 transition-transform origin-left duration-500 ${i === 0 ? "bg-emerald-500/40" : i === 1 ? "bg-blue-500/40" : i === 2 ? "bg-white/30" : "bg-purple-500/40"
                  }`} />
                <span className="text-zinc-600 font-mono text-xs uppercase tracking-widest flex items-center gap-2">
                  <span className={`w-1.5 h-1.5 rounded-full ${i === 0 ? "bg-emerald-500/50" : i === 1 ? "bg-blue-500/50" : i === 2 ? "bg-white/50 animate-pulse" : "bg-purple-500/50"
                    }`} />
                  {m.label}
                </span>
                <span className="text-4xl md:text-5xl font-bold text-white tracking-tighter">{m.value}{m.suffix}</span>
              </motion.div>
            ))}
          </div>

          {/* Terminal */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.7, ease }}
            className="w-full bg-[#030305] border border-white/[0.06] rounded-2xl p-5 md:p-7 font-mono text-[10px] md:text-xs h-48 overflow-hidden relative"
          >
            <div className="absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-[#030305] to-transparent z-10 pointer-events-none" />
            <div className="absolute inset-x-0 top-0 h-8 bg-gradient-to-b from-[#030305] to-transparent z-10 pointer-events-none" />
            <motion.div
              animate={{ y: ["0%", "-50%"] }}
              transition={{ repeat: Infinity, ease: "linear", duration: 20 }}
              className="flex flex-col gap-2 opacity-60"
            >
              {[...Array(24)].map((_, i) => {
                const ok = i % 3 === 0;
                const warn = i % 7 === 0;
                return (
                  <div key={i} className={`flex gap-3 ${warn ? "text-amber-600/60" : "text-zinc-800"}`}>
                    <span className="text-zinc-700">[{`0x${(i * 1234567).toString(16).padStart(6, "0")}`.toUpperCase()}]</span>
                    <span className={ok ? "text-emerald-700/60" : warn ? "text-amber-600/50" : "text-white/15"}>
                      {ok ? "EXECUTION_NOMINAL" : warn ? "REALLOC_LOAD_MATRIX" : "SYNC_DATA_NODES"}
                    </span>
                    <span className="hidden md:inline text-zinc-800">-- {(i % 5) * 4 + 5}ms</span>
                    <span className="ml-auto hidden lg:inline text-zinc-800">{warn ? "WARN" : "OK"}</span>
                  </div>
                );
              })}
            </motion.div>
          </motion.div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════
          SYSTEM INQUIRIES — FAQ Section
          ═══════════════════════════════════════════════ */}
      <section className="py-24 md:py-32 bg-[#050508] relative z-20 border-t border-white/[0.04] overflow-hidden">
        {/* Fixed Background Wireframe */}
        <div className="hidden lg:block absolute inset-0 z-0 pointer-events-none overflow-hidden">
          <GeometricTunnel align="right" />
        </div>

        <div className="max-w-7xl mx-auto px-5 md:px-12 relative z-10">
          <motion.div initial="hidden" whileInView="visible" viewport={{ once: true }} variants={stagger} className="text-center mb-16 md:mb-20">
            <motion.span variants={fadeUp} className="text-[11px] font-mono uppercase tracking-[0.3em] text-zinc-500 block mb-6">Inquiries</motion.span>
            <motion.h2 variants={fadeUp} className="text-3xl md:text-5xl lg:text-6xl font-bold tracking-tighter text-white">
              System Directives.
            </motion.h2>
          </motion.div>

          {/* 2x2 Grid of Directives */}
          <motion.div
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true, margin: "-10%" }}
            variants={stagger}
            className="grid grid-cols-1 md:grid-cols-2 gap-6 lg:gap-8"
          >
            <motion.div variants={fadeUp}>
              <SystemDirectiveCard
                index="01"
                question="Does GRQ Holdings offer public investment opportunities?"
                answer="No. GRQ Holdings operates as a private holding entity. We exclusively deploy proprietary capital and do not solicit external retail investments under any circumstance."
              />
            </motion.div>
            <motion.div variants={fadeUp}>
              <SystemDirectiveCard
                index="02"
                question="How does the strategic acquisition process work?"
                answer="We identify high-leverage digital assets presenting clear mathematical inefficiencies. Upon acquisition, assets are integrated into our central operational stack to aggressively eliminate bottlenecks."
              />
            </motion.div>
            <motion.div variants={fadeUp}>
              <SystemDirectiveCard
                index="03"
                question="What technology stack powers the execution methodology?"
                answer="Our infrastructure relies on proprietary, algorithmic pipelines built for latency under 50ms. We utilize real-time data orchestration and automated load balancing across all deployment nodes."
              />
            </motion.div>
            <motion.div variants={fadeUp}>
              <SystemDirectiveCard
                index="04"
                question="Is the Operator Portal accessible to external contractors?"
                answer="The Operator Portal is strictly restricted to vetted internal talent and verified strategic partners. Unauthorized access attempts are continuously monitored and logged."
              />
            </motion.div>
          </motion.div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════
          PORTAL SPLIT — 50/50 CTA
          ═══════════════════════════════════════════════ */}
      <section className="relative z-20 w-full h-[70vh] min-h-[500px] flex flex-col md:flex-row overflow-hidden">
        {/* Left: Obsidian */}
        <motion.div
          className="flex-1 h-full bg-[#050508] relative group cursor-pointer border-r border-white/[0.04]"
          animate={{ flexGrow: hoveredPortal === "left" ? 3 : hoveredPortal === "right" ? 0.5 : 1 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          onMouseEnter={() => setHoveredPortal("left")}
          onMouseLeave={() => setHoveredPortal(null)}
        >
          <div className="absolute inset-0 flex flex-col justify-center p-10 md:p-20 z-10">
            <div className="w-14 h-14 rounded-full bg-white/5 border border-white/20 flex items-center justify-center mb-8 transition-transform duration-500 group-hover:scale-110">
              <Command className="w-7 h-7 text-white" />
            </div>
            <h3 className="text-3xl md:text-5xl font-bold tracking-tighter text-white">Operator Portal</h3>
            <motion.div
              className="mt-5 overflow-hidden max-w-md"
              animate={{ opacity: hoveredPortal === "left" ? 1 : 0, height: hoveredPortal === "left" ? "auto" : 0 }}
              transition={{ duration: 0.35 }}
            >
              <p className="text-base text-zinc-400 mb-6">Access the performance environment and explore available deployment vectors for elite talent.</p>
              <Link href="/careers" className="inline-flex items-center gap-2 px-7 py-3.5 bg-white text-black font-semibold rounded-full text-sm hover:bg-zinc-200 transition-colors">
                Enter Portal <ChevronRight className="w-4 h-4" />
              </Link>
            </motion.div>
          </div>
        </motion.div>

        {/* Right: White */}
        <motion.div
          className="flex-1 h-full bg-white relative group cursor-pointer"
          animate={{ flexGrow: hoveredPortal === "right" ? 3 : hoveredPortal === "left" ? 0.5 : 1 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          onMouseEnter={() => setHoveredPortal("right")}
          onMouseLeave={() => setHoveredPortal(null)}
        >
          <div className="absolute inset-y-0 left-0 w-6 bg-gradient-to-r from-black/10 to-transparent pointer-events-none z-20" />
          <div className="absolute inset-0 flex flex-col justify-center p-10 md:p-20 z-10">
            <div className="w-14 h-14 rounded-full bg-black/5 border border-black/10 flex items-center justify-center mb-8 transition-transform duration-500 group-hover:scale-110">
              <Lock className="w-7 h-7 text-black" />
            </div>
            <h3 className="text-3xl md:text-5xl font-bold tracking-tighter text-black">Strategic Focus</h3>
            <motion.div
              className="mt-5 overflow-hidden max-w-md"
              animate={{ opacity: hoveredPortal === "right" ? 1 : 0, height: hoveredPortal === "right" ? "auto" : 0 }}
              transition={{ duration: 0.35 }}
            >
              <p className="text-base text-zinc-600 mb-6">Submit proposals, investment inquiries, or acquisition directives to the central holding entity.</p>
              <Link href="/corporate-inquiry" className="inline-flex items-center gap-2 px-7 py-3.5 bg-black text-white font-semibold rounded-full text-sm hover:bg-zinc-800 transition-colors">
                Initiate Contact <ChevronRight className="w-4 h-4" />
              </Link>
            </motion.div>
          </div>
        </motion.div>
      </section>
    </main>
  );
}
