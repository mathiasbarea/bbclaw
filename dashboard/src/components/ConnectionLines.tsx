
import { motion } from 'framer-motion';

export function ConnectionLines({ eventTrigger }: { eventTrigger: number }) {
    // We draw some abstract SVGs curves connecting the left side to the columns
    return (
        <div style={{
            position: 'absolute',
            top: 0,
            left: '50%',
            width: '100vw',
            height: '100%',
            transform: 'translateX(-50%)',
            pointerEvents: 'none',
            zIndex: 1,
            opacity: 0.45,
            mixBlendMode: 'screen'
        }}>
            <svg width="100%" height="100%" preserveAspectRatio="none">
                <defs>
                    <linearGradient id="line-grad" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity="0.8" />
                        <stop offset="100%" stopColor="var(--accent-purple)" stopOpacity="0.1" />
                    </linearGradient>
                </defs>

                {/* Static faint path */}
                <path
                    d="M 250 500 C 500 500, 600 200, 800 200"
                    stroke="rgba(118, 132, 255, 0.18)"
                    strokeWidth="2"
                    fill="none"
                />
                <path
                    d="M 250 500 C 500 500, 700 800, 1200 800"
                    stroke="rgba(110, 124, 246, 0.16)"
                    strokeWidth="2"
                    fill="none"
                />

                {/* Animated pulse paths triggered by events */}
                <motion.path
                    key={`pulse-1-${eventTrigger}`}
                    d="M 250 500 C 500 500, 600 200, 800 200"
                    stroke="url(#line-grad)"
                    strokeWidth="4"
                    fill="none"
                    initial={{ pathLength: 0, opacity: 1 }}
                    animate={{ pathLength: 1, opacity: 0 }}
                    transition={{ duration: 1.5, ease: "circOut" }}
                    style={{ filter: 'drop-shadow(0 0 10px var(--accent-primary))' }}
                />
                <motion.path
                    key={`pulse-2-${eventTrigger}`}
                    d="M 250 500 C 500 500, 700 800, 1200 800"
                    stroke="url(#line-grad)"
                    strokeWidth="3"
                    fill="none"
                    initial={{ pathLength: 0, opacity: 1 }}
                    animate={{ pathLength: 1, opacity: 0 }}
                    transition={{ duration: 2, ease: "easeOut", delay: 0.2 }}
                    style={{ filter: 'drop-shadow(0 0 8px var(--accent-purple))' }}
                />
            </svg>
        </div>
    );
}
